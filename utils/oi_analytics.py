"""
Core OI analytics used by both the collector (to store derived fields) and
the dashboard (to render Tab 1 / Tab 2).

"OI Decay %" definition used throughout this project:
    decay% = (current_OI - previous_cycle_OI) / previous_cycle_OI * 100
i.e. the % change in open interest strike-by-strike between this 15-min
snapshot and the previous one. Negative = OI unwinding, Positive = OI buildup.
"""
from config import ATM_DECAY_ALERT_THRESHOLD, OTM_LEVELS


# NOTE: chain_obj here is produced directly by
# utils.upstox_client.fetch_symbol_chain() / MockUpstoxClient.fetch_symbol_chain(),
# already in the shape this module expects:
#   {"symbol", "spot", "strike_gap", "chain": [{"strike", "CE": {...}, "PE": {...}}, ...]}
# so no separate normalize step is needed for the Upstox data source.


def atm_strike(chain_obj):
    spot = chain_obj["spot"]
    gap = chain_obj["strike_gap"]
    if not spot:
        return None
    return round(spot / gap) * gap


def pcr(chain_obj):
    total_ce = sum((r.get("CE", {}).get("oi", 0)) for r in chain_obj["chain"])
    total_pe = sum((r.get("PE", {}).get("oi", 0)) for r in chain_obj["chain"])
    if total_ce == 0:
        return None
    return round(total_pe / total_ce, 3)


def max_oi_strikes(chain_obj):
    """Returns (max_oi_call_strike, max_oi_put_strike) - support/resistance."""
    ce_best, pe_best = None, None
    for r in chain_obj["chain"]:
        ce = r.get("CE", {}).get("oi", 0)
        pe = r.get("PE", {}).get("oi", 0)
        if ce_best is None or ce > ce_best[1]:
            ce_best = (r["strike"], ce)
        if pe_best is None or pe > pe_best[1]:
            pe_best = (r["strike"], pe)
    return ce_best, pe_best


def _decay_pct(cur_oi, prev_oi):
    if not prev_oi:
        return None
    return round((cur_oi - prev_oi) / prev_oi * 100, 2)


def attach_decay(cur_chain_obj, prev_chain_obj):
    """
    Mutates a COPY of cur_chain_obj, adding 'decay' field to each CE/PE leg
    by comparing against prev_chain_obj (previous 15-min snapshot). If there
    is no previous snapshot yet (first run), decay is None everywhere.
    """
    prev_map = {}
    if prev_chain_obj:
        prev_map = {r["strike"]: r for r in prev_chain_obj["chain"]}

    out_chain = []
    for r in cur_chain_obj["chain"]:
        new_row = dict(r)
        for side in ("CE", "PE"):
            leg = r.get(side)
            if not leg:
                continue
            new_leg = dict(leg)
            prev_row = prev_map.get(r["strike"])
            prev_oi = prev_row.get(side, {}).get("oi") if prev_row else None
            new_leg["decay"] = _decay_pct(leg.get("oi", 0), prev_oi)
            # absolute change in OI vs the previous 15-min cycle (used by Tab 3's
            # "Change in OI" graph) — distinct from Upstox's own prev_oi_upstox,
            # which is typically the previous trading day's reference OI.
            new_leg["changeOi"] = (leg.get("oi", 0) - prev_oi) if prev_oi is not None else None
            new_row[side] = new_leg
        out_chain.append(new_row)

    result = dict(cur_chain_obj)
    result["chain"] = out_chain
    return result


def top_decay_strikes(chain_obj, side, n=3):
    """Top-N strikes with the most NEGATIVE decay (heaviest unwinding) for CE or PE."""
    rows = [r for r in chain_obj["chain"] if r.get(side, {}).get("decay") is not None]
    rows.sort(key=lambda r: r[side]["decay"])
    return [{"strike": r["strike"], "decay": r[side]["decay"], "oi": r[side]["oi"]} for r in rows[:n]]


def strikes_by_offset(chain_obj):
    """
    Returns a dict keyed by offset from ATM in units of strike_gap:
    {-3: row, -2: row, -1: row, 0: row(ATM), 1: row, 2: row, 3: row}
    Positive offset = higher strike (OTM for CE / ITM for PE), and vice versa.
    """
    atm = atm_strike(chain_obj)
    gap = chain_obj["strike_gap"]
    if atm is None:
        return {}
    by_strike = {r["strike"]: r for r in chain_obj["chain"]}
    out = {}
    for off in range(-3, 4):
        k = atm + off * gap
        if k in by_strike:
            out[off] = by_strike[k]
    return out


def otm_offsets_for_side(side):
    """CE OTM strikes are ABOVE spot (positive offsets); PE OTM strikes are BELOW (negative)."""
    return OTM_LEVELS if side == "CE" else [-x for x in OTM_LEVELS]


def evaluate_decay_signal(chain_obj):
    """
    Implements the pattern you described:
      - ATM strike (either side) showing OI-decay < -5%  -> flagged
      - its OTM1 & OTM2 on the SAME side also negative decay
      - the OPPOSITE side's ATM strike showing POSITIVE decay
    Returns a list of signal dicts, one per side that matches, e.g.:
      {"side": "CE", "atm_decay": -7.2, "otm1_decay": -3.1, "otm2_decay": -1.4,
       "opposite_atm_side": "PE", "opposite_atm_decay": 4.5, "triggered": True}
    """
    offsets = strikes_by_offset(chain_obj)
    signals = []
    if 0 not in offsets:
        return signals

    for side, opp_side in (("CE", "PE"), ("PE", "CE")):
        atm_row = offsets.get(0, {})
        atm_leg = atm_row.get(side, {})
        atm_decay = atm_leg.get("decay")
        if atm_decay is None or atm_decay >= ATM_DECAY_ALERT_THRESHOLD:
            continue  # ATM condition (< -5%) not met

        otm_offsets = otm_offsets_for_side(side)[:2]  # OTM1, OTM2
        otm_decays = []
        ok = True
        for off in otm_offsets:
            leg = offsets.get(off, {}).get(side, {})
            d = leg.get("decay")
            otm_decays.append(d)
            if d is None or d >= 0:
                ok = False

        opp_leg = atm_row.get(opp_side, {})
        opp_decay = opp_leg.get("decay")
        if opp_decay is None or opp_decay <= 0:
            ok = False

        if ok:
            signals.append({
                "side": side,
                "atm_strike": atm_row["strike"],
                "atm_decay": atm_decay,
                "otm1_decay": otm_decays[0] if len(otm_decays) > 0 else None,
                "otm2_decay": otm_decays[1] if len(otm_decays) > 1 else None,
                "opposite_side": opp_side,
                "opposite_atm_decay": opp_decay,
                "triggered": True,
            })
    return signals
