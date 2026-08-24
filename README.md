# OI Dashboard (NIFTY / BANKNIFTY / FINNIFTY + all F&O stocks) — Upstox edition

A 3-tab Dash app, data sourced from the **Upstox API**:
1. **OI Dashboard** — live LTP, PCR, Max-OI Call/Put strikes, top-3 OI-decay strikes, and
   the ATM/OTM decay signal pattern you specified, for NIFTY / BANKNIFTY / FINNIFTY.
2. **Analysis** — searchable dropdown (built from the spot-instrument list captured on first
   run) → Call-side and Put-side OI-decay analysis (ATM, OTM1-3) with lot size & estimated cost.
3. **Historical (Greeks)** — 6 time-series graphs (OI, Change in OI, Volume, IV, Gamma, Theta)
   for the ATM CE/PE of any symbol, built from the 15-min snapshot archive.

Data refreshes every 15 minutes during NSE market hours (09:15–15:30 IST, Mon–Fri) and is
pushed to GitHub automatically after every cycle.

---

## ⚠️ Read this before deploying

1. **Upstox connectivity was not live-tested in the sandbox I built this in** — it can't
   reach `api.upstox.com` / `assets.upstox.com`. The fetch logic in `utils/upstox_client.py`
   is adapted directly from your working script (same token handling, same `/option/contract`
   + `/option/chain` calls, same Greeks-with-BS-fallback extraction) — but test it locally
   with a real `UPSTOX_TOKEN` before deploying with `MOCK_MODE=0`.
2. **Upstox access tokens expire daily** — you'll need to refresh `UPSTOX_TOKEN` (env var,
   editable on Render without a redeploy) or `token.txt` regularly, e.g. via Upstox's login
   flow or your own token-refresh script. This project does not automate that step.
3. **`UPSTOX_INSTRUMENT_MASTER_URL`** in `config.py` points at Upstox's published NSE
   instrument dump — double check this URL against Upstox's current docs before relying on
   it; asset-dump paths have moved before.
4. **Lot sizes** (`utils/data_access.py::LOT_SIZES`) are seeded for a few symbols — NSE
   revises these quarterly. Update the dict as needed.
5. Everything ships with **`MOCK_MODE=1` by default**, so you can run the whole app —
   including realistic OI decay, PCR, signals, and all 6 Greek graphs — with **zero**
   external calls. Flip to `MOCK_MODE=0` once `upstox_client.py` is verified against a real
   token.

---

## Project layout

```
oi-dashboard/
├── app.py                  # Dash app — 3 tabs, all UI + callbacks
├── data_collector.py         # one collection cycle: fetch (Upstox) → analyze → save → github_sync
├── scheduler.py                # background loop: runs data_collector every 15 min, market hours only
├── github_sync.py               # git add/commit/push of data/ to GitHub
├── config.py                      # symbols, paths, Upstox settings, thresholds
├── utils/
│   ├── upstox_client.py             # Upstox auth, instrument master, expiries, chain fetch (+ mock client)
│   ├── oi_analytics.py                # PCR, max-OI, OI-decay%, ATM/OTM signal rule
│   ├── greeks.py                        # Black-Scholes fallback (Upstox usually provides Greeks itself)
│   └── data_access.py                     # read-layer for the Dash app + lot sizes
├── data/
│   ├── NSE.json.gz              # Upstox's own NSE instrument master dump (ALL symbols) —
│   │                               downloaded on first run, refreshed if >24h old
│   ├── symbols.json               # unique underlying list, derived from NSE.json.gz —
│   │                                 ONLY rewritten on first run or if that list changes
│   ├── oi_latest.json.gz            # our own latest computed OI/Greeks/decay snapshot (all symbols)
│   ├── oi_prev.json.gz                # previous cycle's snapshot (used to compute decay%)
│   └── history/                         # one gz file per 15-min cycle — powers Tab 3 plots
├── token.txt                    # optional local fallback for the Upstox token (gitignored)
├── requirements.txt
├── Procfile                   # Render start command
└── render.yaml                  # Render blueprint
```

### How "OI Decay %" is defined here
`decay% = (current_OI − previous_cycle_OI) / previous_cycle_OI × 100`, computed strike-by-strike
between consecutive 15-min snapshots (our own history, not Upstox's day-open `prev_oi`, which
is also captured separately as `prev_oi_upstox` for reference). Negative = unwinding, positive
= buildup. This drives the Top-3 decay tables, the ATM/OTM signal, and the Analysis tab.

### The ATM/OTM signal (Tab 1)
For each side (CE and PE) it checks:
- ATM strike decay < **-5%**
- OTM1 **and** OTM2 (same side) also negative decay
- The **opposite side's** ATM strike showing **positive** decay

If all three hold, it's flagged as a signal row under that index's panel.

### Greeks
Upstox's `/option/chain` response includes `option_greeks` (delta/gamma/vega/theta) and IV per
leg — `utils/upstox_client.extract_leg()` uses those directly, and only falls back to a local
Black-Scholes calculation (`utils/greeks.py`) for whatever field Upstox happens to omit.

---

## Run locally (mock data, no Upstox token needed)

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
MOCK_MODE=1 python data_collector.py     # run a couple of cycles to build history:
MOCK_MODE=1 python data_collector.py
MOCK_MODE=1 python data_collector.py
MOCK_MODE=1 python app.py                # open http://localhost:8050
```

## Switch to real Upstox data

1. Get an Upstox API access token (daily-expiring) and either:
   - `export UPSTOX_TOKEN=your_token_here`, or
   - create `token.txt` in the project root containing just the token.
2. Set `MOCK_MODE=0`.
3. Run `python data_collector.py` locally and confirm `data/NSE.json.gz` (instrument master)
   and `data/oi_latest.json.gz` populate with real data. Watch the console for `get_expiries
   failed` / `get_chain failed` warnings — those include the Upstox HTTP status + response body
   to help debug auth or symbol-key issues.
4. Only once that works locally, deploy with `MOCK_MODE=0` on Render.

---

## Deploy to Render + GitHub

1. **Push this repo to GitHub** (create an empty repo first, e.g. `oi-dashboard`):
   ```bash
   git init
   git add .
   git commit -m "initial commit"
   git branch -M main
   git remote add origin https://github.com/<you>/oi-dashboard.git
   git push -u origin main
   ```

2. **Create a GitHub token** the app will use to push `data/` updates back:
   - GitHub → Settings → Developer settings → Fine-grained tokens → generate one scoped to
     this repo with **Contents: Read and write** permission.

3. **Create the Render Web Service**:
   - New → Web Service → connect your GitHub repo.
   - Render will detect `render.yaml` (or set manually: build `pip install -r requirements.txt`,
     start `gunicorn app:server --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 120`).
   - **Important:** keep `--workers 1`. The 15-min scheduler runs as a background thread inside
     the web process — multiple gunicorn workers would each run their own scheduler and
     duplicate/clash on writes.
   - In the Render dashboard → Environment, set:
     - `UPSTOX_TOKEN` = your current Upstox access token (secret — you'll need to refresh this
       periodically since Upstox tokens expire daily; no redeploy needed to update it)
     - `GITHUB_TOKEN` = the token from step 2 (secret)
     - `GITHUB_REPO_URL` = `https://github.com/<you>/oi-dashboard.git`
     - `MOCK_MODE` = `0` (once you've verified real Upstox access)
     - `GIT_USER_NAME`, `GIT_USER_EMAIL` — optional, used for the automated commits
     - `SYMBOL_WHITELIST` — optional, limits the 15-min universe scan (faster cycles)

4. Deploy. On first boot the app downloads the Upstox instrument master (`NSE.json.gz`),
   derives `symbols.json`, runs one collection cycle (`oi_latest.json.gz` + first `history/`
   file), commits + pushes everything to GitHub, then repeats every 15 minutes during market
   hours.

### Alternative: separate cron worker
If you'd rather not run the scheduler inside the web dyno, set
`DISABLE_INPROCESS_SCHEDULER=1` on the web service and instead add a **Render Cron Job**
(`*/15 9-15 * * 1-5` IST-adjusted) with command `python data_collector.py`. Using GitHub as
the source of truth (as built here) means both the web service and the cron job stay in sync
via `git pull` / `git push` rather than needing shared disk.

### Keeping the Upstox token fresh in production
Since Upstox tokens expire daily, you'll want either:
- a small daily job (Render Cron Job or GitHub Action) that re-runs your login flow and
  updates the `UPSTOX_TOKEN` env var via Render's API, or
- a manual daily update via the Render dashboard.
This project reads the token fresh from the environment on every request, so updating the
env var takes effect immediately — no redeploy needed.

---

## Known gaps / things you'll likely want to tune

- **`EXPIRY_COUNT=1`** by default — each cycle fetches only the nearest expiry per symbol to
  keep 15-min cycles fast across the full F&O universe. Raise it (and extend `app.py`'s Tab 2 /
  Tab 3 to let the user pick an expiry) if you need further-month analysis.
- **Greeks are Upstox-reported first, Black-Scholes fallback second** — see
  `utils/upstox_client.extract_leg()`.
- **Render's free/starter disk is ephemeral** — that's precisely why every cycle pushes to
  GitHub; on restart, you may want to add a small bootstrap step that `git pull`s the latest
  `data/` before serving, so Tab 3 history survives a redeploy. (Not included by default —
  happy to add if you want it.)
- **`utils/upstox_client.download_instrument_master()`** re-downloads on first run and any
  time the local `NSE.json.gz` is >24h old — adjust the staleness window in
  `data_collector.ensure_instrument_master()` if you want it fresher/less frequent.
