"""
Runs ONE collection cycle using Upstox as the data source:
  1. Ensure data/NSE.json.gz (Upstox's NSE instrument master) is present/fresh;
     re-download it if missing or older than 24h.
  2. Derive data/symbols.json (unique underlying list) from it — rewritten
     ONLY on first run or when the underlying list actually changed.
  3. Fetch the nearest-expiry option chain for NIFTY/BANKNIFTY/FINNIFTY +
     every F&O equity symbol (or config.SYMBOL_WHITELIST if set).
  4. Compute PCR, max-OI strikes, OI-decay% vs the previous cycle, Greeks
     (Upstox-provided, Black-Scholes fallback).
  5. Write:
       data/oi_latest.json.gz     <- latest full snapshot (all symbols)
       data/oi_prev.json.gz       <- becomes "previous" for the NEXT cycle
       data/history/<ts>.json.gz  <- append-only archive, powers Tab 3 plots
  6. Push data/ to GitHub (github_sync.sync()).

Triggered every 15 minutes by scheduler.py (or an external Render Cron Job
calling `python data_collector.py`).
"""
import gzip
import json
import os
import sys
import time
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

import config
from utils import upstox_client as ux
from utils import oi_analytics as an


def _load_gz_json(path):
    if not os.path.exists(path):
        return None
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)


def _save_gz_json(path, obj):
    tmp = path + ".tmp"
    with gzip.open(tmp, "wt", encoding="utf-8") as f:
        json.dump(obj, f)
    os.replace(tmp, path)


def ensure_instrument_master():
    """data/NSE.json.gz — download on first run, or if >24h old."""
    path = config.INSTRUMENT_MASTER_FILE
    stale = True
    if os.path.exists(path):
        age = time.time() - os.path.getmtime(path)
        stale = age > 24 * 3600
    if not stale:
        return ux.load_instrument_master()

    if config.MOCK_MODE:
        # write a tiny fake instrument master so the file exists / is inspectable
        fake = [{"underlying_symbol": s, "underlying_key": k}
                for s, k in ux.MockUpstoxClient().get_symbol_map().items()]
        with gzip.open(path, "wt", encoding="utf-8") as f:
            json.dump(fake, f)
        print("  [mock] wrote fake NSE.json.gz instrument master")
        return fake

    print("  Downloading Upstox NSE instrument master -> data/NSE.json.gz ...")
    return ux.download_instrument_master()


def update_symbol_list(sym_to_inst):
    """
    Writes data/symbols.json ONLY on first run, or if the underlying list
    actually changed — never just because a routine 15-min snapshot ran.
    """
    indices = sorted(config.INDEX_SYMBOLS)
    equities = sorted(s for s in sym_to_inst if s not in config.INDEX_SYMBOLS)
    new_list = {"indices": indices, "equities": equities,
                "generated_at": datetime.now(config.IST).isoformat()}

    if os.path.exists(config.SYMBOLS_FILE):
        with open(config.SYMBOLS_FILE) as f:
            existing = json.load(f)
        if existing.get("indices") == indices and existing.get("equities") == equities:
            return existing, False

    with open(config.SYMBOLS_FILE, "w") as f:
        json.dump(new_list, f, indent=2)
    return new_list, True


def _fetch_one(symbol, instrument_key, headers, now_ist, mock_client):
    if mock_client:
        return mock_client.fetch_symbol_chain(symbol, instrument_key, now_ist)
    return ux.fetch_symbol_chain(symbol, instrument_key, headers, now_ist)


def run_cycle():
    now_ist = datetime.now(config.IST)
    print(f"[{now_ist}] Starting collection cycle (MOCK_MODE={config.MOCK_MODE})")

    master = ensure_instrument_master()
    sym_to_inst = ux.build_symbol_map(master) if not config.MOCK_MODE else ux.MockUpstoxClient().get_symbol_map()

    symbol_list, changed = update_symbol_list(sym_to_inst)
    print(f"  symbols.json {'REWRITTEN' if changed else 'unchanged'} "
          f"({len(symbol_list['indices'])} indices, {len(symbol_list['equities'])} equities)")

    headers = None if config.MOCK_MODE else ux.upstox_headers()
    if not config.MOCK_MODE and headers is None:
        print("  ! No Upstox token found (UPSTOX_TOKEN env var / token.txt) — aborting cycle.",
              file=sys.stderr)
        return None

    mock_client = ux.MockUpstoxClient() if config.MOCK_MODE else None

    prev_snapshot = _load_gz_json(config.LATEST_SNAPSHOT_FILE)
    prev_map = {s["symbol"]: s for s in prev_snapshot.get("symbols", [])} if prev_snapshot else {}

    all_symbols = symbol_list["indices"] + symbol_list["equities"]
    results = []
    errors = []

    with ThreadPoolExecutor(max_workers=config.MAX_WORKERS) as pool:
        futures = {
            pool.submit(_fetch_one, sym, sym_to_inst[sym], headers, now_ist, mock_client): sym
            for sym in all_symbols if sym in sym_to_inst
        }
        for fut in as_completed(futures):
            sym = futures[fut]
            try:
                chain_obj = fut.result()
                if not chain_obj:
                    errors.append(f"{sym}: no chain data")
                    continue
                prev_chain_obj = prev_map.get(sym)
                results.append(an.attach_decay(chain_obj, prev_chain_obj))
            except Exception as exc:  # noqa: BLE001 - keep collecting other symbols
                errors.append(f"{sym}: {exc}")

    if errors:
        print(f"  ! {len(errors)} symbol(s) failed, e.g.: {errors[:5]}", file=sys.stderr)

    snapshot = {
        "collected_at": now_ist.isoformat(),
        "collected_at_display": now_ist.strftime("%d-%b-%Y %H:%M:%S IST"),
        "symbols": results,
        "errors": errors,
    }

    if os.path.exists(config.LATEST_SNAPSHOT_FILE):
        os.replace(config.LATEST_SNAPSHOT_FILE, config.PREV_SNAPSHOT_FILE)
    _save_gz_json(config.LATEST_SNAPSHOT_FILE, snapshot)

    fname = now_ist.strftime("%Y%m%d_%H%M") + ".json.gz"
    _save_gz_json(os.path.join(config.HISTORY_DIR, fname), snapshot)

    print(f"  wrote oi_latest.json.gz + history/{fname} ({len(results)} symbols, {len(errors)} errors)")
    return snapshot


if __name__ == "__main__":
    snap = run_cycle()
    if snap:
        try:
            import github_sync
            github_sync.sync(f"data update {snap['collected_at_display']}")
        except Exception as exc:  # noqa: BLE001
            print(f"GitHub sync skipped/failed: {exc}", file=sys.stderr)
