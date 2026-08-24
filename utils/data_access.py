"""
Thin read-layer between the Dash app and the gzip JSON files written by
data_collector.py. Keeps app.py focused on layout/callbacks.
"""
import glob
import gzip
import json
import os

import config

# Approximate NSE lot sizes — these change every few months (NSE revises
# them quarterly). Update this dict periodically, or replace with a live
# fetch from NSE's lot-size master if you want it to stay current automatically.
LOT_SIZES = {
    "NIFTY": 25,
    "BANKNIFTY": 15,
    "FINNIFTY": 40,
    "RELIANCE": 250,
    "TCS": 150,
    "HDFCBANK": 550,
    "INFY": 400,
    "SBIN": 750,
}
DEFAULT_LOT_SIZE = 500  # fallback for symbols not in the map


def get_lot_size(symbol):
    return LOT_SIZES.get(symbol, DEFAULT_LOT_SIZE)


def load_gz_json(path):
    if not path or not os.path.exists(path):
        return None
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)


def load_master():
    return load_gz_json(config.LATEST_SNAPSHOT_FILE)


def load_symbols():
    if not os.path.exists(config.SYMBOLS_FILE):
        return {"indices": config.INDEX_SYMBOLS, "equities": []}
    with open(config.SYMBOLS_FILE) as f:
        return json.load(f)


def get_symbol_chain(master_snapshot, symbol):
    if not master_snapshot:
        return None
    for s in master_snapshot.get("symbols", []):
        if s["symbol"] == symbol:
            return s
    return None


def list_history_files():
    files = sorted(glob.glob(os.path.join(config.HISTORY_DIR, "*.json.gz")))
    return files


def load_history_series(symbol, max_points=60):
    """
    Returns a time-ordered list of {timestamp, chain_obj} for one symbol,
    scanning the history/ archive (newest max_points snapshots).
    """
    files = list_history_files()[-max_points:]
    series = []
    for fp in files:
        snap = load_gz_json(fp)
        if not snap:
            continue
        chain_obj = get_symbol_chain(snap, symbol)
        if chain_obj:
            series.append({"timestamp": snap.get("collected_at"), "chain": chain_obj})
    return series
