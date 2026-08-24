"""
Central configuration for the OI Dashboard project (Upstox data source).
"""
import os
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
HISTORY_DIR = os.path.join(DATA_DIR, "history")              # per-cycle snapshots (Tab 3 plots)

# data/NSE.json.gz = Upstox's own NSE instrument master dump (ALL NSE
# instruments/symbols), downloaded on first run and re-downloaded whenever
# we detect it's stale/changed. This is the file your original spec meant
# by "put all symbols in NSE.json.gz".
INSTRUMENT_MASTER_FILE = os.path.join(DATA_DIR, "NSE.json.gz")
SYMBOLS_FILE = os.path.join(DATA_DIR, "symbols.json")        # unique underlying list, derived from the above

# Our OWN computed OI/Greeks/decay snapshots (separate from the Upstox
# instrument master above, to avoid any filename collision).
LATEST_SNAPSHOT_FILE = os.path.join(DATA_DIR, "oi_latest.json.gz")
PREV_SNAPSHOT_FILE = os.path.join(DATA_DIR, "oi_prev.json.gz")

os.makedirs(HISTORY_DIR, exist_ok=True)

# ---------------------------------------------------------------------------
# Indices shown live on Tab 1. Upstox identifies indices by instrument_key,
# not a tradeable equity symbol, so these are mapped explicitly.
# ---------------------------------------------------------------------------
INDEX_SYMBOLS = ["NIFTY", "BANKNIFTY", "FINNIFTY"]
INDEX_INSTRUMENT_KEYS = {
    "NIFTY": "NSE_INDEX|Nifty 50",
    "BANKNIFTY": "NSE_INDEX|Nifty Bank",
    "FINNIFTY": "NSE_INDEX|Nifty Fin Service",
}

# ---------------------------------------------------------------------------
# IST timezone + market hours
# ---------------------------------------------------------------------------
IST = ZoneInfo("Asia/Kolkata")
MARKET_OPEN = (9, 15)
MARKET_CLOSE = (15, 30)
COLLECT_INTERVAL_MIN = 15

# ---------------------------------------------------------------------------
# OI-decay thresholds
# ---------------------------------------------------------------------------
ATM_DECAY_ALERT_THRESHOLD = -5.0
OTM_LEVELS = [1, 2, 3]

# ---------------------------------------------------------------------------
# Upstox API
# ---------------------------------------------------------------------------
UPSTOX_BASE_URL = "https://api.upstox.com/v2"
# Official Upstox complete-instrument dump for the NSE segment. Verify this
# URL against Upstox's current docs before relying on it — Upstox has moved
# instrument-dump paths before.
UPSTOX_INSTRUMENT_MASTER_URL = os.environ.get(
    "UPSTOX_INSTRUMENT_MASTER_URL",
    "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz",
)
UPSTOX_TOKEN = os.environ.get("UPSTOX_TOKEN", "")
TOKEN_FILE_PATH = os.environ.get("TOKEN_FILE_PATH", os.path.join(BASE_DIR, "token.txt"))

EXPIRY_COUNT = int(os.environ.get("EXPIRY_COUNT", "1"))  # how many expiries to fetch per symbol per cycle
MAX_WORKERS = int(os.environ.get("MAX_WORKERS", "8"))

# Optional: restrict the market-wide 15-min scan to a subset of underlyings
# (e.g. "NIFTY,BANKNIFTY,FINNIFTY,RELIANCE,TCS"). Empty = full F&O universe
# from the instrument master (slower per cycle, fine since it's background work).
SYMBOL_WHITELIST = [
    s.strip().upper() for s in os.environ.get("SYMBOL_WHITELIST", "").split(",") if s.strip()
]

# ---------------------------------------------------------------------------
# Black-Scholes fallback (used only when Upstox doesn't return option_greeks
# for a leg — Upstox normally DOES provide delta/gamma/vega/theta/iv directly)
# ---------------------------------------------------------------------------
RISK_FREE_RATE = float(os.environ.get("RISK_FREE_RATE", "0.07"))

# ---------------------------------------------------------------------------
# GitHub sync
# ---------------------------------------------------------------------------
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO_URL = os.environ.get("GITHUB_REPO_URL", "")
GIT_USER_NAME = os.environ.get("GIT_USER_NAME", "oi-bot")
GIT_USER_EMAIL = os.environ.get("GIT_USER_EMAIL", "oi-bot@example.com")

# ---------------------------------------------------------------------------
# Mock mode — lets the app + collector run WITHOUT hitting Upstox/live token.
# Set MOCK_MODE=0 once UPSTOX_TOKEN is set and upstox_client is verified.
# ---------------------------------------------------------------------------
MOCK_MODE = os.environ.get("MOCK_MODE", "1") == "1"
