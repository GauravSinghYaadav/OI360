"""
OI Dashboard — Dash app, 3 tabs:
  1. OI Dashboard  - live NIFTY / BANKNIFTY / FINNIFTY monitor
  2. Analysis      - per-symbol Call-side / Put-side OI decay analysis
  3. Historical    - 6-graph time series (OI, ChgOI, Volume, IV, Gamma, Theta)

Run locally:   python app.py
Run on Render: gunicorn app:server
"""
import os
from datetime import datetime

import dash
from dash import dcc, html, Input, Output, State, callback_context
import plotly.graph_objects as go

import config
from utils import data_access as da
from utils import oi_analytics as an

app = dash.Dash(__name__, title="OI Dashboard", suppress_callback_exceptions=True)
server = app.server  # gunicorn entrypoint

# Start the 15-min collector loop in-process (single Render web service).
# Set DISABLE_INPROCESS_SCHEDULER=1 if you run collection as a separate
# Render Cron Job / worker instead.
if os.environ.get("DISABLE_INPROCESS_SCHEDULER", "0") != "1":
    import scheduler
    scheduler.start_background()

COLORS = {
    "bg": "#0b0e14",
    "card": "#141922",
    "text": "#e6e9ef",
    "muted": "#8b93a7",
    "accent": "#33c9a3",
    "accent2": "#e0575b",
    "border": "#232a38",
}

# ---------------------------------------------------------------------------
# Layout helpers
# ---------------------------------------------------------------------------
def card(children, style=None):
    base = {
        "background": COLORS["card"], "border": f"1px solid {COLORS['border']}",
        "borderRadius": "12px", "padding": "16px 20px", "marginBottom": "16px",
    }
    base.update(style or {})
    return html.Div(children, style=base)


def stat(label, value, color=None):
    return html.Div([
        html.Div(label, style={"fontSize": "11px", "color": COLORS["muted"], "textTransform": "uppercase",
                                "letterSpacing": "0.05em"}),
        html.Div(str(value), style={"fontSize": "22px", "fontWeight": 700,
                                     "color": color or COLORS["text"], "marginTop": "2px"}),
    ], style={"minWidth": "120px"})


def decay_color(v):
    if v is None:
        return COLORS["muted"]
    return COLORS["accent2"] if v < 0 else COLORS["accent"]


# ---------------------------------------------------------------------------
# TAB 1 — Live OI Dashboard (indices)
# ---------------------------------------------------------------------------
def index_panel(symbol, chain_obj):
    if not chain_obj:
        return card(html.Div(f"{symbol}: no data yet — waiting for first collection cycle.",
                              style={"color": COLORS["muted"]}))

    pcr_val = an.pcr(chain_obj)
    ce_max, pe_max = an.max_oi_strikes(chain_obj)
    top_ce = an.top_decay_strikes(chain_obj, "CE", 3)
    top_pe = an.top_decay_strikes(chain_obj, "PE", 3)
    signals = an.evaluate_decay_signal(chain_obj)

    def decay_rows(rows, side):
        if not rows:
            return html.Div("No decay data yet (needs 2 collection cycles).",
                             style={"color": COLORS["muted"], "fontSize": "13px"})
        return html.Table([
            html.Thead(html.Tr([html.Th("Strike"), html.Th("OI"), html.Th("Decay %")])),
            html.Tbody([
                html.Tr([
                    html.Td(r["strike"]), html.Td(f'{r["oi"]:,}'),
                    html.Td(f'{r["decay"]:+.2f}%', style={"color": decay_color(r["decay"])}),
                ]) for r in rows
            ])
        ], style={"width": "100%", "fontSize": "13px", "borderCollapse": "collapse"})

    signal_block = html.Div(style={"marginTop": "10px"})
    if signals:
        signal_block = html.Div([
            html.Div([
                html.Span(f"⚠ {s['side']} ATM {s['atm_strike']} decay {s['atm_decay']:+.2f}% "
                          f"| OTM1 {s['otm1_decay']:+.2f}% | OTM2 {s['otm2_decay']:+.2f}% "
                          f"| opposite {s['opposite_side']} ATM {s['opposite_atm_decay']:+.2f}%",
                          style={"color": COLORS["accent2"], "fontSize": "13px"})
            ], style={"marginBottom": "4px"}) for s in signals
        ])
    else:
        signal_block = html.Div("No ATM/OTM decay signal right now.",
                                 style={"color": COLORS["muted"], "fontSize": "13px"})

    return card([
        html.Div(symbol, style={"fontSize": "16px", "fontWeight": 700, "marginBottom": "10px",
                                 "color": COLORS["accent"]}),
        html.Div([
            stat("LTP", f'{chain_obj["spot"]:.2f}' if chain_obj["spot"] else "—"),
            stat("PCR", pcr_val if pcr_val is not None else "—",
                 COLORS["accent"] if (pcr_val or 0) >= 1 else COLORS["accent2"]),
            stat("Max OI Call (resistance)", f'{ce_max[0]}' if ce_max else "—"),
            stat("Max OI Put (support)", f'{pe_max[0]}' if pe_max else "—"),
        ], style={"display": "flex", "gap": "28px", "flexWrap": "wrap", "marginBottom": "14px"}),

        html.Div([
            html.Div([html.Div("Top-3 OI-Decay CALL strikes", style={"fontWeight": 600, "marginBottom": "6px"}),
                      decay_rows(top_ce, "CE")], style={"flex": 1, "minWidth": "220px"}),
            html.Div([html.Div("Top-3 OI-Decay PUT strikes", style={"fontWeight": 600, "marginBottom": "6px"}),
                      decay_rows(top_pe, "PE")], style={"flex": 1, "minWidth": "220px"}),
        ], style={"display": "flex", "gap": "24px", "flexWrap": "wrap"}),

        html.Div("ATM/OTM Decay Signal (ATM < -5% decay, OTM1+OTM2 same-side negative, opposite ATM positive)",
                  style={"fontWeight": 600, "marginTop": "14px", "fontSize": "13px", "color": COLORS["muted"]}),
        signal_block,
    ])


def tab1_layout():
    return html.Div([
        html.Div(id="tab1-clock", style={"marginBottom": "14px"}),
        html.Div(id="tab1-panels"),
        dcc.Interval(id="tab1-interval", interval=15 * 1000, n_intervals=0),  # refresh UI every 15s
    ])


# ---------------------------------------------------------------------------
# TAB 2 — Analysis (per-symbol Call side / Put side)
# ---------------------------------------------------------------------------
def tab2_layout():
    symbols = da.load_symbols()
    options = [{"label": f"{s} (Index)", "value": s} for s in symbols.get("indices", [])] + \
              [{"label": s, "value": s} for s in symbols.get("equities", [])]
    return html.Div([
        card([
            html.Div("Select symbol", style={"marginBottom": "6px", "color": COLORS["muted"], "fontSize": "13px"}),
            dcc.Dropdown(id="tab2-symbol", options=options, placeholder="Search a symbol…",
                         style={"color": "#000"}),
        ]),
        html.Div(id="tab2-content"),
    ])


def side_analysis_block(title, chain_obj, side, opposite_side):
    offsets = an.strikes_by_offset(chain_obj)
    otm_offsets = an.otm_offsets_for_side(side)

    def leg_row(label, off):
        row = offsets.get(off, {})
        leg = row.get(side, {})
        if not row:
            return html.Tr([html.Td(label), html.Td("—"), html.Td("—"), html.Td("—")])
        return html.Tr([
            html.Td(label), html.Td(row.get("strike", "—")),
            html.Td(f'{leg.get("oi", 0):,}'),
            html.Td(f'{leg.get("decay"):+.2f}%' if leg.get("decay") is not None else "—",
                     style={"color": decay_color(leg.get("decay"))}),
        ])

    atm_row = offsets.get(0, {})
    opp_leg = atm_row.get(opposite_side, {})

    return card([
        html.Div(title, style={"fontWeight": 700, "fontSize": "15px", "marginBottom": "10px",
                                "color": COLORS["accent"]}),
        html.Table([
            html.Thead(html.Tr([html.Th("Level"), html.Th("Strike"), html.Th("OI"), html.Th("Decay %")])),
            html.Tbody([
                leg_row("ATM", 0),
                leg_row("OTM1", otm_offsets[0]),
                leg_row("OTM2", otm_offsets[1]),
                leg_row("OTM3", otm_offsets[2]),
            ]),
        ], style={"width": "100%", "fontSize": "13px", "borderCollapse": "collapse"}),
        html.Div([
            html.Span(f"Opposite side ({opposite_side}) ATM decay: ", style={"color": COLORS["muted"]}),
            html.Span(f'{opp_leg.get("decay"):+.2f}%' if opp_leg.get("decay") is not None else "—",
                      style={"color": decay_color(opp_leg.get("decay")), "fontWeight": 700}),
        ], style={"marginTop": "10px", "fontSize": "13px"}),
    ], style={"flex": 1, "minWidth": "320px"})


def build_tab2_content(symbol):
    master = da.load_master()
    chain_obj = da.get_symbol_chain(master, symbol)
    if not chain_obj:
        return html.Div("No data yet for this symbol.", style={"color": COLORS["muted"]})

    offsets = an.strikes_by_offset(chain_obj)
    atm_row = offsets.get(0, {})
    ce = atm_row.get("CE", {})
    pe = atm_row.get("PE", {})
    lot = da.get_lot_size(symbol)

    top_block = card([
        html.Div(symbol, style={"fontWeight": 700, "fontSize": "18px", "color": COLORS["accent"]}),
        html.Div([
            stat("Spot LTP", f'{chain_obj["spot"]:.2f}' if chain_obj["spot"] else "—"),
            stat("ATM Strike", atm_row.get("strike", "—")),
            stat("Lot Size", lot),
            stat("Call ATM LTP", ce.get("ltp", "—")),
            stat("Put ATM LTP", pe.get("ltp", "—")),
            stat("Est. Cost (Call)", f'{lot * ce.get("ltp", 0):,.0f}' if ce.get("ltp") else "—"),
            stat("Est. Cost (Put)", f'{lot * pe.get("ltp", 0):,.0f}' if pe.get("ltp") else "—"),
        ], style={"display": "flex", "gap": "26px", "flexWrap": "wrap", "marginTop": "10px"}),
    ])

    two_col = html.Div([
        side_analysis_block("CALL Side Analysis", chain_obj, "CE", "PE"),
        side_analysis_block("PUT Side Analysis", chain_obj, "PE", "CE"),
    ], style={"display": "flex", "gap": "20px", "flexWrap": "wrap"})

    return html.Div([top_block, two_col])


# ---------------------------------------------------------------------------
# TAB 3 — Historical Greeks / OI plots
# ---------------------------------------------------------------------------
def tab3_layout():
    symbols = da.load_symbols()
    options = [{"label": f"{s} (Index)", "value": s} for s in symbols.get("indices", [])] + \
              [{"label": s, "value": s} for s in symbols.get("equities", [])]
    return html.Div([
        card([
            html.Div("Select symbol", style={"marginBottom": "6px", "color": COLORS["muted"], "fontSize": "13px"}),
            dcc.Dropdown(id="tab3-symbol", options=options, placeholder="Search a symbol…",
                         style={"color": "#000"}),
        ]),
        html.Div(id="tab3-content"),
    ])


def _empty_fig(title):
    fig = go.Figure()
    fig.update_layout(title=title, template="plotly_dark", height=320,
                       paper_bgcolor=COLORS["card"], plot_bgcolor=COLORS["card"])
    return fig


def _timeseries_fig(title, x, y_ce, y_pe, ytitle):
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=x, y=y_ce, mode="lines+markers", name="ATM CE", line={"color": COLORS["accent"]}))
    fig.add_trace(go.Scatter(x=x, y=y_pe, mode="lines+markers", name="ATM PE", line={"color": COLORS["accent2"]}))
    fig.update_layout(title=title, template="plotly_dark", height=320, yaxis_title=ytitle,
                       paper_bgcolor=COLORS["card"], plot_bgcolor=COLORS["card"],
                       margin={"t": 40, "b": 30, "l": 50, "r": 20})
    return fig


def build_tab3_content(symbol):
    series = da.load_history_series(symbol)
    if not series:
        empty = [dcc.Graph(figure=_empty_fig(t)) for t in
                 ["Open Interest", "Change in OI", "Volume", "Implied Volatility", "Gamma", "Theta"]]
        return html.Div([card("No history yet — data accumulates every 15-min collection cycle.")] +
                         [html.Div(g, style={"width": "48%", "display": "inline-block", "verticalAlign": "top"})
                          for g in empty])

    x, oi_ce, oi_pe, chg_ce, chg_pe, vol_ce, vol_pe = [], [], [], [], [], [], []
    iv_ce, iv_pe, gamma_ce, gamma_pe, theta_ce, theta_pe = [], [], [], [], [], []

    for point in series:
        chain_obj = point["chain"]
        offsets = an.strikes_by_offset(chain_obj)
        atm_row = offsets.get(0, {})
        ce, pe = atm_row.get("CE", {}), atm_row.get("PE", {})
        x.append(point["timestamp"])
        oi_ce.append(ce.get("oi")); oi_pe.append(pe.get("oi"))
        chg_ce.append(ce.get("changeOi")); chg_pe.append(pe.get("changeOi"))
        vol_ce.append(ce.get("volume")); vol_pe.append(pe.get("volume"))
        iv_ce.append(ce.get("iv")); iv_pe.append(pe.get("iv"))
        gamma_ce.append(ce.get("gamma")); gamma_pe.append(pe.get("gamma"))
        theta_ce.append(ce.get("theta")); theta_pe.append(pe.get("theta"))

    graphs = [
        _timeseries_fig("Open Interest (ATM)", x, oi_ce, oi_pe, "OI"),
        _timeseries_fig("Change in OI (ATM)", x, chg_ce, chg_pe, "Chg OI"),
        _timeseries_fig("Volume (ATM)", x, vol_ce, vol_pe, "Volume"),
        _timeseries_fig("Implied Volatility (ATM)", x, iv_ce, iv_pe, "IV %"),
        _timeseries_fig("Gamma (ATM)", x, gamma_ce, gamma_pe, "Gamma"),
        _timeseries_fig("Theta (ATM)", x, theta_ce, theta_pe, "Theta/day"),
    ]
    return html.Div([
        html.Div(dcc.Graph(figure=g), style={"width": "48%", "display": "inline-block", "verticalAlign": "top",
                                              "margin": "1%"})
        for g in graphs
    ])


# ---------------------------------------------------------------------------
# App shell
# ---------------------------------------------------------------------------
app.layout = html.Div([
    html.Div([
        html.Div("📊 OI Dashboard", style={"fontSize": "22px", "fontWeight": 800}),
        html.Div(id="header-clock", style={"color": COLORS["muted"], "fontSize": "13px"}),
    ], style={"display": "flex", "justifyContent": "space-between", "alignItems": "center",
              "padding": "16px 24px", "borderBottom": f"1px solid {COLORS['border']}"}),

    dcc.Tabs(id="tabs", value="tab1", children=[
        dcc.Tab(label="OI Dashboard", value="tab1"),
        dcc.Tab(label="Analysis", value="tab2"),
        dcc.Tab(label="Historical (Greeks)", value="tab3"),
    ], style={"padding": "0 24px"}),

    html.Div(id="tab-content", style={"padding": "20px 24px"}),
    dcc.Interval(id="clock-interval", interval=1000, n_intervals=0),
], style={"background": COLORS["bg"], "color": COLORS["text"], "minHeight": "100vh",
          "fontFamily": "Inter, -apple-system, sans-serif"})


@app.callback(Output("header-clock", "children"), Input("clock-interval", "n_intervals"))
def update_clock(_):
    now = datetime.now(config.IST)
    master = da.load_master()
    last_updated = master.get("collected_at_display") if master else "no data yet"
    return f"IST {now.strftime('%d-%b-%Y %H:%M:%S')}  ·  Last data update: {last_updated}"


@app.callback(Output("tab-content", "children"), Input("tabs", "value"))
def render_tab(tab):
    if tab == "tab1":
        return tab1_layout()
    if tab == "tab2":
        return tab2_layout()
    if tab == "tab3":
        return tab3_layout()
    return html.Div()


@app.callback(Output("tab1-panels", "children"), Input("tab1-interval", "n_intervals"))
def update_tab1(_):
    master = da.load_master()
    panels = []
    for sym in config.INDEX_SYMBOLS:
        chain_obj = da.get_symbol_chain(master, sym) if master else None
        panels.append(index_panel(sym, chain_obj))
    return panels


@app.callback(Output("tab2-content", "children"), Input("tab2-symbol", "value"))
def update_tab2(symbol):
    if not symbol:
        return html.Div("Pick a symbol above to see Call-side / Put-side OI decay analysis.",
                         style={"color": COLORS["muted"]})
    return build_tab2_content(symbol)


@app.callback(Output("tab3-content", "children"), Input("tab3-symbol", "value"))
def update_tab3(symbol):
    if not symbol:
        return html.Div("Pick a symbol above to see historical OI / Greeks plots.",
                         style={"color": COLORS["muted"]})
    return build_tab3_content(symbol)


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=int(os.environ.get("PORT", 8050)))
