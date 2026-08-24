"""
Black-Scholes Greeks — NSE's option-chain API gives us OI, Change in OI,
Volume, IV and LTP but NOT Gamma / Theta / Vega, so we derive those locally
from the IV NSE reports. Standard Black-Scholes (European, no dividend yield
adjustment beyond the index's own carry) is a reasonable approximation for
index/stock options traded on NSE.
"""
import math
from datetime import datetime
from config import RISK_FREE_RATE


def _norm_pdf(x):
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


def _norm_cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def time_to_expiry_years(expiry_date: datetime, now: datetime) -> float:
    seconds = max((expiry_date - now).total_seconds(), 60)  # floor to avoid /0
    return seconds / (365.0 * 24 * 3600)


def bs_greeks(spot, strike, t_years, iv_pct, option_type, r=RISK_FREE_RATE):
    """
    FALLBACK ONLY — Upstox's option-chain response normally already includes
    option_greeks (delta/gamma/vega/theta) per leg, computed exchange-side.
    This is used only when Upstox omits greeks for a particular leg.

    Returns dict(delta, gamma, theta, vega) for one option leg.
    iv_pct: implied vol in percent (e.g. 14.5). theta is per-day (/365).
    """
    if spot <= 0 or strike <= 0 or t_years <= 0 or iv_pct is None or iv_pct <= 0:
        return {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0}

    sigma = iv_pct / 100.0
    try:
        d1 = (math.log(spot / strike) + (r + 0.5 * sigma ** 2) * t_years) / (sigma * math.sqrt(t_years))
        d2 = d1 - sigma * math.sqrt(t_years)
    except (ValueError, ZeroDivisionError):
        return {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0}

    gamma = _norm_pdf(d1) / (spot * sigma * math.sqrt(t_years))
    vega = spot * _norm_pdf(d1) * math.sqrt(t_years) / 100.0  # per 1% IV move

    if option_type == "CE":
        delta = _norm_cdf(d1)
        theta = (
            -(spot * _norm_pdf(d1) * sigma) / (2 * math.sqrt(t_years))
            - r * strike * math.exp(-r * t_years) * _norm_cdf(d2)
        ) / 365.0
    else:  # PE
        delta = _norm_cdf(d1) - 1
        theta = (
            -(spot * _norm_pdf(d1) * sigma) / (2 * math.sqrt(t_years))
            + r * strike * math.exp(-r * t_years) * _norm_cdf(-d2)
        ) / 365.0

    return {"delta": round(delta, 4), "gamma": round(gamma, 6),
            "theta": round(theta, 4), "vega": round(vega, 4)}
