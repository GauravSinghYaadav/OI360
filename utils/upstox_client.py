"""
Upstox data-fetching layer — adapted directly from your existing script's
fetch logic (get_access_token / get_expiries / get_chain / extract_leg),
wired into this project's collector/analytics/storage structure.

NOT live-tested in this sandbox (assets.upstox.com / api.upstox.com are not
reachable from here) — verify locally with a real UPSTOX_TOKEN before
deploying with MOCK_MODE=0.
"""
import gzip
import json
import logging
import os
import time
from datetime import datetime

import requests

import config
from utils.greeks import bs_greeks, time_to_expiry_years

log = logging.getLogger("upstox-client")


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
def get_access_token():
    """Env var first (editable on Render without a redeploy), else token.txt."""
    token = config.UPSTOX_TOKEN.strip() if config.UPSTOX_TOKEN else ""
    if token:
        return token
    if os.path.exists(config.TOKEN_FILE_PATH):
        with open(config.TOKEN_FILE_PATH) as f:
            token = f.read().strip()
        if token:
            return token
    return None


def upstox_headers():
    token = get_access_token()
    if not token:
        return None
    return {"Accept": "application/json", "Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Instrument master (this becomes data/NSE.json.gz per your spec)
# ---------------------------------------------------------------------------
def download_instrument_master():
    """
    Downloads Upstox's complete NSE instrument dump and saves it to
    config.INSTRUMENT_MASTER_FILE (data/NSE.json.gz). This is the file every
    symbol/instrument_key lookup is built from.
    """
    resp = requests.get(config.UPSTOX_INSTRUMENT_MASTER_URL, timeout=60)
    resp.raise_for_status()
    tmp = config.INSTRUMENT_MASTER_FILE + ".tmp"
    with open(tmp, "wb") as f:
        f.write(resp.content)
    os.replace(tmp, config.INSTRUMENT_MASTER_FILE)
    return load_instrument_master()


def load_instrument_master():
    if not os.path.exists(config.INSTRUMENT_MASTER_FILE):
        return None
    with gzip.open(config.INSTRUMENT_MASTER_FILE, "rt", encoding="utf-8") as f:
        return json.load(f)


def build_symbol_map(master=None):
    """{ underlying_symbol: underlying_key } for every F&O underlying."""
    master = master if master is not None else load_instrument_master()
    if not master:
        return {}
    sym_to_inst = {}
    for x in master:
        sy = x.get("underlying_symbol") or x.get("name")
        uk = x.get("underlying_key") or x.get("asset_key")
        if sy and uk and sy not in sym_to_inst:
            sym_to_inst[sy] = uk
    # indices aren't always well-represented in the equity dump — force them in
    sym_to_inst.update(config.INDEX_INSTRUMENT_KEYS)
    if config.SYMBOL_WHITELIST:
        sym_to_inst = {k: v for k, v in sym_to_inst.items() if k in config.SYMBOL_WHITELIST}
    return sym_to_inst


# ---------------------------------------------------------------------------
# Expiries / chain
# ---------------------------------------------------------------------------
def _safe_expiry(raw):
    try:
        if isinstance(raw, str):
            return raw[:10]
        if raw > 1e12:
            return datetime.utcfromtimestamp(raw / 1000).strftime("%Y-%m-%d")
        return datetime.utcfromtimestamp(raw).strftime("%Y-%m-%d")
    except Exception:
        return None


def get_expiries(instrument_key, headers):
    r = requests.get(f"{config.UPSTOX_BASE_URL}/option/contract", headers=headers,
                      params={"instrument_key": instrument_key}, timeout=15)
    if r.status_code != 200:
        log.warning("get_expiries failed for %s: HTTP %s %s", instrument_key, r.status_code, r.text[:200])
        return []
    out = [_safe_expiry(d.get("expiry")) for d in r.json().get("data", [])]
    return sorted({e for e in out if e})


def get_chain(instrument_key, expiry, headers):
    r = requests.get(f"{config.UPSTOX_BASE_URL}/option/chain", headers=headers,
                      params={"instrument_key": instrument_key, "expiry_date": expiry}, timeout=20)
    if r.status_code != 200:
        log.warning("get_chain failed for %s/%s: HTTP %s %s", instrument_key, expiry, r.status_code, r.text[:200])
        return []
    return r.json().get("data", [])


def years_to_expiry(expiry_str, now_ist):
    expiry_close = datetime.strptime(expiry_str, "%Y-%m-%d").replace(hour=15, minute=30)
    return time_to_expiry_years(expiry_close, now_ist.replace(tzinfo=None))


def extract_leg(leg, spot, strike, t_years, option_type):
    """Prefer Upstox's own option_greeks/iv; fall back to Black-Scholes only
    for whatever fields Upstox didn't return."""
    md = leg.get("market_data", {}) or {}
    og = leg.get("option_greeks", {}) or {}

    oi = md.get("oi", 0) or 0
    prev_oi = md.get("prev_oi", 0) or 0   # Upstox's own day-over-day reference
    ltp = md.get("ltp", 0) or 0
    volume = md.get("volume", 0) or 0
    iv = og.get("iv", md.get("iv", 0)) or 0

    delta, gamma, vega, theta = og.get("delta"), og.get("gamma"), og.get("vega"), og.get("theta")
    if None in (delta, gamma, vega, theta):
        computed = bs_greeks(spot, strike, t_years, iv, option_type)
        delta = delta if delta is not None else computed["delta"]
        gamma = gamma if gamma is not None else computed["gamma"]
        vega = vega if vega is not None else computed["vega"]
        theta = theta if theta is not None else computed["theta"]

    return {
        "oi": oi, "prev_oi_upstox": prev_oi,          # Upstox's own (usually day-open) reference OI
        "volume": volume, "ltp": ltp, "iv": round(float(iv), 2),
        "delta": round(float(delta), 4), "gamma": round(float(gamma), 6),
        "vega": round(float(vega), 4), "theta": round(float(theta), 4),
        # 'decay' (our own 15-min-cycle-over-cycle OI decay %) is attached
        # later in oi_analytics.attach_decay, not here.
    }


def fetch_symbol_chain(symbol, instrument_key, headers, now_ist):
    """Fetch the nearest EXPIRY_COUNT expiries for one symbol and return a
    normalized chain_obj compatible with oi_analytics.py (same shape our
    NSE-based normalize_chain used to produce)."""
    expiries = get_expiries(instrument_key, headers)
    if not expiries:
        return None
    expiries = expiries[: config.EXPIRY_COUNT]
    expiry = expiries[0]  # nearest expiry drives the Tab1/2/3 "current" view

    raw = get_chain(instrument_key, expiry, headers)
    if not raw:
        return None

    spot = float(raw[0].get("underlying_spot_price", 0) or 0)
    t_years = years_to_expiry(expiry, now_ist)
    strike_gap = _infer_strike_gap(raw)

    chain = []
    for x in raw:
        strike = x.get("strike_price", 0)
        row = {"strike": strike, "expiry": expiry}
        ce_leg = x.get("call_options") or {}
        pe_leg = x.get("put_options") or {}
        if ce_leg:
            row["CE"] = extract_leg(ce_leg, spot, strike, t_years, "CE")
        if pe_leg:
            row["PE"] = extract_leg(pe_leg, spot, strike, t_years, "PE")
        chain.append(row)
    chain.sort(key=lambda r: r["strike"])

    return {
        "symbol": symbol, "spot": spot, "timestamp": now_ist.isoformat(),
        "strike_gap": strike_gap, "expiry": expiry, "all_expiries": expiries,
        "chain": chain,
    }


def _infer_strike_gap(raw_rows):
    strikes = sorted({r.get("strike_price") for r in raw_rows if r.get("strike_price")})
    if len(strikes) < 2:
        return 50
    diffs = [b - a for a, b in zip(strikes, strikes[1:]) if b - a > 0]
    return min(diffs) if diffs else 50


# ---------------------------------------------------------------------------
# Mock client — used when config.MOCK_MODE=1
# ---------------------------------------------------------------------------
class MockUpstoxClient:
    def __init__(self):
        import random
        self.random = random

    def get_symbol_map(self):
        return {
            "NIFTY": "NSE_INDEX|Nifty 50", "BANKNIFTY": "NSE_INDEX|Nifty Bank",
            "FINNIFTY": "NSE_INDEX|Nifty Fin Service",
            "RELIANCE": "NSE_EQ|RELIANCE", "TCS": "NSE_EQ|TCS", "HDFCBANK": "NSE_EQ|HDFCBANK",
            "INFY": "NSE_EQ|INFY", "SBIN": "NSE_EQ|SBIN",
        }

    def fetch_symbol_chain(self, symbol, instrument_key, now_ist):
        r = self.random
        spot_map = {"NIFTY": 24800, "BANKNIFTY": 51200, "FINNIFTY": 23600}
        gap_map = {"NIFTY": 50, "BANKNIFTY": 100, "FINNIFTY": 50}
        spot = spot_map.get(symbol, r.uniform(500, 4000)) * r.uniform(0.995, 1.005)
        gap = gap_map.get(symbol, 20 if spot < 1000 else 50)
        atm = round(spot / gap) * gap
        expiry = "2026-08-28"

        chain = []
        for i in range(-10, 11):
            strike = atm + i * gap
            row = {"strike": strike, "expiry": expiry}
            for side in ("CE", "PE"):
                dist = abs(i)
                base_oi = max(int(r.gauss(500000, 200000) / (dist + 1)), 1000)
                row[side] = {
                    "oi": base_oi, "prev_oi_upstox": int(base_oi * r.uniform(0.7, 1.3)),
                    "volume": int(base_oi * r.uniform(0.1, 0.6)),
                    "ltp": round(max(spot - strike, 0) + r.uniform(5, 80), 2) if side == "CE"
                           else round(max(strike - spot, 0) + r.uniform(5, 80), 2),
                    "iv": round(r.uniform(11, 22), 2),
                    "delta": round(r.uniform(-1, 1), 4), "gamma": round(r.uniform(0, 0.001), 6),
                    "vega": round(r.uniform(0, 10), 4), "theta": round(r.uniform(-30, -1), 4),
                }
            chain.append(row)

        return {"symbol": symbol, "spot": spot, "timestamp": now_ist.isoformat(),
                "strike_gap": gap, "expiry": expiry, "all_expiries": [expiry], "chain": chain}


def get_client():
    return MockUpstoxClient() if config.MOCK_MODE else "REAL"  # real path uses module functions directly
