"""
Runs data_collector.run_cycle() every COLLECT_INTERVAL_MIN minutes, but only
Mon-Fri between MARKET_OPEN and MARKET_CLOSE (IST). Also fires ONE immediate
run at startup (so the first deploy has data right away / first run creates
symbols.json as required).

Started automatically by app.py in a background thread so a single Render
web service handles both the dashboard AND the data collection. If you'd
rather isolate collection into its own Render Cron Job / worker, just point
that job at `python data_collector.py` directly and skip importing this file
from app.py.
"""
import threading
import time
from datetime import datetime, time as dtime

import config
import data_collector
import github_sync


def _within_market_hours(now):
    if now.weekday() >= 5:  # Sat/Sun
        return False
    open_t = dtime(*config.MARKET_OPEN)
    close_t = dtime(*config.MARKET_CLOSE)
    return open_t <= now.time() <= close_t


def _loop():
    # immediate first run (creates symbols.json / NSE.json.gz if missing)
    _safe_cycle()

    while True:
        now = datetime.now(config.IST)
        if _within_market_hours(now):
            # sleep until the next quarter-hour boundary
            minutes_to_next = config.COLLECT_INTERVAL_MIN - (now.minute % config.COLLECT_INTERVAL_MIN)
            sleep_s = minutes_to_next * 60 - now.second
        else:
            sleep_s = 300  # check again in 5 min when market's closed
        time.sleep(max(sleep_s, 5))

        now = datetime.now(config.IST)
        if _within_market_hours(now):
            _safe_cycle()


def _safe_cycle():
    try:
        snap = data_collector.run_cycle()
        github_sync.sync(f"data update {snap['collected_at_display']}")
    except Exception as exc:  # noqa: BLE001 - never let the loop die
        print(f"[scheduler] cycle failed: {exc}")


def start_background():
    t = threading.Thread(target=_loop, daemon=True)
    t.start()
    return t


if __name__ == "__main__":
    _loop()
