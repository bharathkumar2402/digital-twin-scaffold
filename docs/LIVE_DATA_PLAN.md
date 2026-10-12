# Live data loop — plan and decisions (Phase L)

Written 2026-10-12 so work can resume in a new session. **Status: L.0 measured (see "Baseline measurements"); nothing else built yet. Next: L.1.**
Read this, then `docs/PHASE_PLAN.md` ("Phase L") for the task list, then start at **L.0**.

## Why

The product is meant to replicate a real facility. Today the demo is a **static snapshot**:
`scripts/demo_setup.py` writes all telemetry, scores and one agent run once, and nothing changes
afterwards. A real system has data arriving continuously, models re-scoring it, alerts firing, and
screens updating on their own. Phase L closes that loop with simulated (but realistic) data.

What already exists (do not rebuild): telemetry ingest `POST /telemetry` with a live rolling Z-score
anomaly check, the debounce engine (15 s batch window, 120 s per-asset cooldown), Redis pub/sub and a
per-tenant WebSocket that shows alert toasts; the Celery task `compute_facility_risk_scores`; the
5-agent pipeline; and `scripts/iot_data_generator.py` (a stateless generator, see "Gotchas").

What is missing: a realistic running data source, **scheduled** scoring (no Celery beat exists), a push
event so screens refresh themselves, stale-data awareness, and automatic (debounced) agent analysis.

## Decisions (made by the assistant for the user: "decide what is best for the demo")

| # | Decision | Value | Reason |
|---|---|---|---|
| D1 | Simulator tick | **15 s** per machine (`SIM_TICK_SECONDS`, env/flag) | Feels live; ~220 rows / 15 s = ~15 rows/s = ~1.3 M rows/day (~130 MB/day raw). 1 s would be ~19 M rows/day: needless. |
| D2 | Clock | **Real timestamps, no fake clock.** Only the *degradation storyline* is time-compressed (`STORY_MINUTES`, default 15) | Honest data, visible change in a short demo. |
| D3 | Storyline | **On by default, `once`**: after `STORY_DELAY` (3 min) the healthy **Air Compressor AC-2** degrades (tool wear up, temps up, vibration ramp, RUL falls) until it crosses high risk, then holds. `--storyline off|once|loop`, plus `inject <machine> <fault>` | One reliable, repeatable demo moment; AC-1 stays the standing critical case. |
| D4 | Scoring cadence | **60 s**, skipped when no new readings since the last score | Cheap, near-real-time, no wasted rows. |
| D5 | UI refresh | **Push** (`scores_updated` event over the existing WebSocket) with **60 s polling fallback**; "updated N min ago"; **stale = no reading for 5 min** shown as its own status, never as healthy | Real systems must show a dead sensor. |
| D6 | Auto analysis | In the **Celery worker** (not the API request), only on a debounced trigger (a machine newly enters high risk / alert cluster), **at most one run per facility per 10 min**, Redis lock so runs never overlap, plus one at start-up if none exists | CLAUDE.md rule 4; also removes the 28-30 s API-bound run. |
| D7 | Ingest security | **Restrict `POST /telemetry` to technician, facility_manager, tenant_admin, superadmin** (viewer = read-only). The simulator signs in as a seeded technician-role account | Today ANY authenticated role (a viewer!) can write sensor data. Real devices need API keys (future work, not in scope). |
| D8 | Demo accounts | Seed **a technician and a viewer** account next to the manager (also closes the "technician view never seen in a browser" gap) | Needed for D7 and for testing the role-based UI live. |
| D9 | Order | **Phase L first, then Phase 5 RLS audit** | Highest demo value; Phase L adds no tenant table, so no new RLS work, and the audit afterwards covers the new paths. |
| D10 | Data growth | Timescale **retention 14 days**, retention only (L.0 proved compression cannot be used with RLS) | Bounded disk. |

## Architecture (target)

```
 live_simulator ──POST /telemetry (every 15 s, technician token)──▶ API
                                                                    │ ingest + Z-score anomaly check
                                                                    ├─ debounce ─▶ Redis pub/sub ─▶ WebSocket ─▶ toast (exists)
                                                                    ▼
                                                              Timescale (sensor_readings, RLS)
 Celery beat (60 s) ─▶ score_all_facilities ─▶ risk_scores (insert-only history)
        │                       │ skip if no new readings; Redis lock
        │                       └─▶ publish "scores_updated" ─▶ WebSocket ─▶ React Query invalidate ─▶ screens refresh
        └─ on debounced trigger ─▶ run_scheduled_analysis (worker, lock, <=1 per 10 min) ─▶ agent_runs ─▶ Work/Analysis update
```

## Gotchas the next session must not rediscover

1. **The old generator is stateless.** `scripts/iot_data_generator.py` makes independent sine+noise values
   per sensor. The risk model was trained on single *coherent* AI4I snapshots (process temp ≈ air temp + 10 K;
   torque/speed related; tool wear only ever increases). Feeding it independent values gives meaningless
   scores. The new simulator must keep **per-machine state** and reproduce the real AI4I joint relationships,
   **calibrated from `docs/DATASETS.md` statistics, not arbitrary constants** (CLAUDE.md rule 8). Never train on
   simulator output.
2. **Scoring uses the latest coherent 5-sensor snapshot** (`build_snapshot_features`, no time cutoff; readings
   must be within 5 min of each other). The simulator must post the five AI4I sensors together in one batch.
3. **Remaining life (RUL)** needs engine-style channels (24) as a window; **vibration** uses 20,480-sample
   waveforms stored in MinIO (`waveform_store`), written today only by `demo_setup`, there is no upload API.
   Both are **Phase L stretch** (L.10), not v1.
4. **No Celery beat exists.** Add it in `app/core/celery_app.py` (`beat_schedule`) and run it as a host process
   like the worker (the demo compose file only contains infrastructure; API, worker and frontend run from the host,
   see `docs/DEMO.md`).
5. **History at 60 s cadence**: `risk-history?limit_per_asset=20` would now span only ~20 minutes. L.5 adds
   downsampling (`bucket=hour|day`, newest score per bucket) so multi-day trends survive. Also replace
   `get_latest_risk_scores` (loads ALL rows into memory) with `DISTINCT ON`.
6. **TimescaleDB compression vs row-level security**: compression (and continuous aggregates) have historically
   not worked on tables with RLS enabled. `sensor_readings` has RLS. **Verify in L.0 before promising it**; the
   fallback is retention only (drop old chunks).
7. The Chrome automation tab throttles timers, so axe/MapLibre behave oddly there. Use Playwright (`npm run
   e2e`, installed Chrome) for browser verification.
8. Windows/PowerShell quirks: edit files with the editor tools, not heredoc-heavy shell scripts; never stage the
   line-ending-only files listed in CLAUDE.md.

## Performance budget (acceptance numbers; measure in L.0, enforce in tests/e2e)

| What | Budget |
|---|---|
| Ingest of one 220-row batch | p95 < 200 ms; API p95 for other routes not worse by > 20 % with the simulator on |
| Scoring 44 assets | < 10 s (to be measured; the full demo setup scoring+history took ~23 s incl. model load and 760 predictions) |
| Score -> screen | < 2 s after `scores_updated` |
| Agent run | in the worker; never blocks an API request; <= 1 / 10 min / facility |
| DB growth | <= ~150 MB/day raw (retention 14 days) |
| Stale detection | an asset with no reading for 5 min shows "No recent data" within one refresh |

## Baseline measurements (Phase L task L.0, 2026-10-11)

Run with `python -m scripts.measure_live_load` (scratch tenant of 44 machines in the demo stack, 220-row batches =
44 machines x 5 AI4I sensors; filler rows are timing-only, never training data). Windows dev machine, Docker Desktop,
API on uvicorn. Raw JSON: `backend/data/processed/measure_live_load.json`. Rows stored are for the one tenant.

| Rows stored | Ingest 220 rows, p50 / p95 | Ingest ceiling | Scoring 44 assets | Read routes p95 (idle) |
|---|---|---|---|---|
| 30 k | 342 / 396 ms | 578 rows/s | 8.6-9.7 s | 14-29 ms |
| 300 k | (not measured) | | 9.2-9.7 s | 14-19 ms |
| 1 M | **2080 / 2342 ms** | **103 rows/s** | **15.9-16.3 s** | 12-31 ms |

Other results:
- Read routes **under concurrent ingest** (at 1 M rows): p95 10-29 ms, no worse than idle. OK.
- A 15 s tick needs 14.7 rows/s. Ceiling at 1 M rows is 103 rows/s: 7x headroom, shrinking as data grows.
- **Compression with RLS (TimescaleDB 2.29.2): impossible in both orders.** Enabling compression on an RLS table
  fails (`columnstore cannot be used on table with row security`) and so does enabling RLS on a compressed one
  (`operation not supported on hypertables that have columnstore enabled`). The no-RLS control compresses (3/3
  chunks). An RLS-only table stays isolated (tenant A 3000 rows, B 3000, no tenant 0).
- **Not measured:** the 300 k ingest row and the 3 M tier. 3 M rows took Docker Desktop down on this machine, so the
  script defaults to 1 M (`--max-rows 3000000` opts in).
- Environment note: on this Windows machine `localhost:9000` (MinIO) intermittently stalls while `127.0.0.1:9000`
  is instant; set `MINIO_ENDPOINT=127.0.0.1:9000` when running scripts if scoring hangs.

Verdict against the budget above: ingest p95 **fails** (budget 200 ms) and gets worse with data; scoring **fails**
(budget 10 s) and also grows (9 s -> 16 s from 30 k to 1 M rows); read routes pass.

Findings that change later tasks (D1-D10 stay as decided unless noted):
- **D10:** **retention only** (14 days). Do not plan compression or continuous aggregates on `sensor_readings` while
  it has RLS. Any rollups must be ordinary tables with their own RLS policy.
- **Ingest scales badly (fix in L.2, before the simulator runs for days):** `check_readings_for_anomalies` runs one
  baseline query per (asset, sensor) group, 220 per batch, and each query gets slower as rows accumulate (6x slower
  from 30 k to 1 M). At the 14-day retention size (~18 M rows at D1's rate) the 15 s tick would not keep up.
  L.2's plan step must pick: one grouped baseline query, a bounded time window on the baseline, or both, with
  tests. This is a product change, so it was not done in L.0.
- **Scoring grows with data (affects L.4 / D4):** 9 s -> 16 s for 44 assets. A 60 s cadence with an overlap lock
  still fits, but L.4 must profile the cause (the 30/90/365-day feature windows are the suspect; unverified) and cap
  the windows, and the "skip when no new readings" rule is mandatory.

## After L.2b (ingest anomaly-baseline fix, 2026-10-12)

Same script, same scratch tenant, re-run on the demo stack (`measure_live_load_after_L2b.json`; the L.0 baseline file is kept):

| Rows stored | Ingest 220 rows, p50 / p95 (was) | Ingest ceiling (was) |
|---|---|---|
| 30 k | **119 / 151 ms** (342 / 396) | 1658 rows/s (578) |
| 1 M | **122 / 178 ms** (2080 / 2342) | 1696 rows/s (103) |

Ingest p95 is now under the 200 ms budget and **no longer grows with stored rows** (30 k -> 1 M: 151 -> 178 ms).
Read routes under concurrent ingest: p95 16-39 ms. Scoring 44 assets is unchanged by L.2b: 9.2 s at 30 k rows, 13.6-13.8 s at 1 M
(still over the 10 s budget; that is L.4). How it was done: one batched `unnest` + `LATERAL` statement reading at most
`BASELINE_MAX_SAMPLES` = 200 newest readings per (asset, sensor) through the new index
`ix_sensor_readings_asset_sensor_time` (Timescale migration 0003). The cap was chosen by measurement on 1 M rows:
the baseline statement took ~79 ms at 500, ~37 ms at 200, ~24 ms at 100. Behavior change: with more than 200
readings in the window the baseline is the newest 200 (about 50 minutes at the demo tick) instead of the whole 7 days;
shorter histories give identical statistics (tested against the old query).

Two things found while measuring that future tasks must know:
- **TimescaleDB 2.29 SkipScan + row-level security crash.** With the new index present, any `SELECT DISTINCT` / `DISTINCT ON`
  over `sensor_readings` run as `app_role` can fail with `unsupported subplan type for SkipScan: Result`. It broke the two
  scoring queries in `feature_engineering_service.py` (caught while re-measuring, not by the original tests); both were
  rewritten as `LATERAL ... ORDER BY timestamp DESC LIMIT 1` lookups and a regression test reproduces the crash with the
  old SQL. **Do not write DISTINCT / DISTINCT ON against `sensor_readings`** (L.3's "last reading time" and anything later)
  - use a LATERAL newest-row lookup or `max(timestamp)` with GROUP BY.
- **Cold-cache stalls after a bulk load:** the first batches after loading ~1 M rows can take ~1 s, and one run saw a single
  11 s stall (not reproduced; probably a checkpoint right after the bulk COPY). It does not occur in steady state.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Scoring cost grows with data (30/90/365-day windows) | Measure in L.0; rollups/retention; cap windows if needed |
| Compression incompatible with RLS | Check in L.0; retention-only fallback |
| Alert / agent storms | Existing debounce (rule 4) + Redis lock + 10 min floor |
| Simulator outlives the API / API restarts | Backoff + re-login; tests |
| Simulated data mistaken for real | Label the demo as simulator-driven (UI footer note + docs) |
| Viewer can write telemetry today | D7 / L.1 |

## Out of scope (say so; do not build here)

Real device protocols (MQTT/OPC-UA gateway), per-device API keys, multi-region, the Appendix-A HA data layer
(CLAUDE.md rule 5), live vibration waveform upload and live RUL channels (stretch L.10).

## Definition of Done for Phase L

With the stack up and the simulator running, **with no clicks and no reloads**:
1. The telemetry chart of an open machine keeps extending and the asset list's "updated" time advances.
2. Within ~5 minutes the storyline machine (AC-2) climbs, a toast fires, it appears in **Needs attention**, its
   trend chart shows the climb, and an automatic analysis updates the **Work** tab.
3. Stopping the simulator makes every asset show **No recent data** within ~6 minutes; restarting recovers it.
4. An injected anomaly burst produces **one** alert per cooldown and **at most one** analysis per 10 min.
5. A viewer cannot ingest telemetry (403); cross-tenant tests unchanged and green; `ruff`, `mypy`, `eslint`,
   unit/integration/e2e all green.
6. The performance budget above is met and recorded in `docs/PROGRESS_LOG.md`.

## How to resume in a new session

1. Read `CLAUDE.md` (Current phase), this file, and the "Phase L" section of `docs/PHASE_PLAN.md`.
2. Do the resume check (`git status`, `git log`); start the demo stack per `docs/DEMO.md` (Docker Desktop first).
3. Say: **"Start Phase L task L.0"** and follow the standard task workflow (plan first, extra scrutiny where
   marked, human gate before every commit).
4. Demo login: use the **Use demo account** button (credentials are in `docs/DEMO.md`).
