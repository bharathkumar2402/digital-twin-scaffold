# Progress Log — AI Industrial Facility Digital Twin

This file holds detailed per-task session notes moved out of `CLAUDE.md` to keep
that file short. For the current phase/task status, see the "Current phase" line
in `CLAUDE.md`; this file is the historical record of what each closed task did,
why, and how it was verified.

---

**Status:** Phase 1 (Foundation) and Phase 2 (Map & Asset System, tasks 1–8, issues
2.1–2.8) are fully closed. Phase 3 (ML & Risk Engine) is underway: task 1, "Feature
engineering pipeline" (issue 3.1), task 2, "XGBoost training script" (issue 3.2),
task 3, "Risk inference service" (issue 3.3), task 4, "Anomaly detection" (issue 3.4),
and task 5, "Debounced alert triggering" (issue 3.5), are closed. Next: Phase 3
task 6, "Real-time delivery" (Redis pub/sub → WebSocket server → frontend toast/alert).

Note on 3.5 ("Debounced alert triggering", issue 3.5): builds the batching/cooldown
decision engine from `PROJECT_PLAN.md` §7.1, and, per `PHASE_PLAN.md`'s own task
wording ("implement... *before* wiring it to anything downstream"), stops there
deliberately - not wired into `POST /telemetry`/`ingest_readings`, doesn't enqueue
3.3's `compute_facility_risk_scores`, doesn't publish to Redis pub/sub. The full
5-agent pipeline §7.1 describes batching triggers for doesn't exist yet (Phase 4);
wiring a `True` decision to the lightweight risk re-score and to the browser is
task 3.6's job, same "don't wire ahead of the task that owns it" precedent 3.4's
note set for itself. No new table/migration/RLS - state lives entirely in Redis,
keyed by `tenant_id`+`asset_id`, not Postgres.

New `app/schemas/ml/debounce.py`'s `DebounceDecision` (Pydantic-validated, same
"never return unvalidated output" pattern as `AnomalyCheckResult`/`RiskScoreResult`).
New `app/services/alert_debounce_service.py`: `evaluate_anomaly_batch` filters an
ingest batch's `AnomalyCheckResult`s down to just the flagged (`is_anomaly=True`)
ones - a normal reading never touches Redis at all - then `evaluate_anomaly` applies
two independent Redis-backed rules per §7.1: a per-asset **cooldown**
(`SET NX EX`, 120s, keyed `debounce:cooldown:{tenant_id}:{asset_id}`) where only the
first anomaly since the last trigger (or ever) reports
`should_trigger_light_rescore=True` - every repeat within the cooldown reports
`False`, which is the debounce working as intended, not a bug - and a per-tenant
**batch window** (`INCR`+`EXPIRE`, 15s buckets, within §7.1's documented 10–30s
range, keyed `debounce:window:{tenant_id}:{bucket}`) that counts how many anomalies
land in the current window, informational only for now and reserved for Phase 4's
eventual full-pipeline batching. The cooldown's correctness rests on `SET NX` being
atomic in Redis, not on the 120s TTL alone - two near-simultaneous anomalies for the
same asset can't both see "cooldown not active" and both trigger.

Not one of the four extra-scrutiny categories (no RLS/new table, not Phase 2/4/5),
but the phase plan's own wording calls this "correctness-critical" and asks for
debounce-window/cooldown tests independent of the rest of the pipeline - treated
that as non-optional. `tests/unit/test_alert_debounce_service.py` (10 tests, run
against `fakeredis` for speed/determinism - this module's Redis usage is limited to
`SET NX EX`/`INCR`/`EXPIRE`, which `fakeredis` implements faithfully) covers: first
anomaly for an asset triggers; a repeat within cooldown is suppressed; the phase
plan's own DoD wording directly - 20 rapid repeated anomalies on one asset produce
exactly 1 trigger, not a flood; triggers again once the cooldown key is gone
(simulating TTL expiry by deleting the key rather than sleeping 120 real seconds in
a unit test); different assets don't share cooldown state; different tenants
reusing the same asset UUID don't share cooldown state either (Redis has no
Postgres-RLS-style tenant isolation of its own, so this has to be enforced by key
namespacing, and is tested as its own case); batch-window count increments across
anomalies in the same bucket; and `evaluate_anomaly_batch` skips non-anomalous
readings entirely, including asserting no cooldown key was created for a skipped
reading.

Verified for real, not just against `fakeredis` (per this repo's norm for
infra-touching code, even when not an extra-scrutiny category - see 2.4/2.5's real
MinIO passes): brought up a real `redis:7-alpine` container and drove
`evaluate_anomaly` against it directly - confirmed the cooldown key's real TTL is
120s (not just what the code intends), and fired 10 concurrent `evaluate_anomaly`
calls (`asyncio.gather`) for the same fresh asset to confirm the atomic `SET NX`
race actually holds under real concurrency, not just fakeredis's single-threaded
event loop - exactly 1 of the 10 reported `should_trigger_light_rescore=True`.
Container torn down after.

New dev dependency: `fakeredis>=2.23` (unit-test-only; no new runtime dependency -
`redis.asyncio` was already installed transitively via `celery[redis]`).

10/10 new backend tests pass, 157/157 backend unit tests total, ruff + mypy clean.
(Did not re-run the full cross_tenant/integration testcontainers suite for this
task - no Postgres/RLS/route surface changed, and the new Redis-backed logic is
covered by the fakeredis suite plus the real-container pass above.) This task fully
closes the Phase 3 DoD checkbox "Debounce logic verified by test: rapid repeated
anomalies on one asset do NOT produce a flood of triggers" - the other three DoD
items (risk scores on the map, a <2s browser alert, model-version auditability)
are either already done (model version, from 3.3) or still open pending 3.6
(real-time delivery) and task 7 (map color-coding).

Note on 3.4 ("Anomaly detection", issue 3.4): scoped deliberately narrow, matching
`PHASE_PLAN.md`'s own task split — "Rolling Z-score check on live telemetry as it's
ingested," full stop. Debouncing/cooldown is task 3.5 and Redis pub/sub → WebSocket
delivery is task 3.6; this task doesn't wire anything downstream of detection, per
root CLAUDE.md rule 4 (never wire a raw threshold breach straight to a pipeline run).
No new table/migration — reads the existing RLS-protected `sensor_readings`
hypertable the same way `get_asset_telemetry` (2.8) already does.

New `app/schemas/ml/anomaly.py`'s `AnomalyCheckResult` (Pydantic-validated, same
"never write/return unvalidated ML output" pattern 3.3's `RiskScoreResult` set) is a
*live, per-reading* check, explicitly distinct from 3.1's `SensorWindowStats
.anomaly_count` (a window-local outlier *count* used as a training feature, not a
live per-reading flag — see that schema's docstring, which already called this
distinction out before this task existed). New
`app/services/anomaly_detection_service.py`'s `check_readings_for_anomalies` groups
an ingest batch by `(asset_id, sensor_type)`, computes a rolling baseline (7-day
lookback — deliberately much shorter than 3.1's 30/90/365-day feature windows, since
this answers "what's normal *recently*" not a long-run class stat) via one SQL query
per group, using the group's *earliest* timestamp as the baseline cutoff so the
query can never include any reading from the batch being checked, regardless of
in-batch ordering. Flags `is_anomaly` at `|z_score| > 3.0` (deliberately stricter
than 3.1's window-local 2.0 threshold — a looser bar here would flag routine noise on
every single ingest call, before 3.5's debounce/cooldown logic exists to absorb it),
and only once at least 10 prior readings exist (`MIN_SAMPLE_COUNT`) — thin history
reports `is_anomaly=False` with `sample_count` visible, never a false positive from
an untrustworthy stddev. The z-score/flag math (`_z_score_and_flag`) is split into a
pure function specifically so it's unit-testable without a database. `POST
/telemetry`'s `TelemetryIngestResponse` gained an `anomalies: list[AnomalyCheckResult]`
field (one per ingested reading) — the hook task 3.5 will consume next, not a new
endpoint.

Building this surfaced a real bug, same class as the RLS-GUC issue 2.7 found and
fixed: `telemetry_service.ingest_readings` originally called
`session.commit()` on the insert *before* running the anomaly baseline query.
`scope_session_to_tenant`'s GUC is set via `SET LOCAL` (transaction-scoped, per
`app/core/tenant_context.py`), so committing ends the transaction the GUC lived in —
the anomaly query then ran with no GUC set, and RLS failed closed (silently zero
rows, no error) rather than raising, which would have made every anomaly check
report `sample_count=0`/`is_anomaly=False` regardless of real history. Caught by
this session's own new integration tests (`sample_count == 10` assertions), not
found in production first. Fixed by moving the anomaly check to run *before* commit,
in the same transaction as the insert — safe because the baseline query's own
`timestamp < cutoff` predicate already excludes the batch's own rows independent of
transaction-commit visibility.

Not one of the four extra-scrutiny categories (no RLS policy change, no new table,
not Phase 2/4/5), but per this repo's own precedent (2.7/2.8/3.1) of adding a
cross-tenant isolation check to any new read path over an RLS-protected table anyway:
extended `tests/cross_tenant/test_telemetry_isolation.py` with adversarial cases
before calling this done — an outlier correctly flagged once a real baseline exists,
a normal value correctly not flagged, and (the one that actually matters for
isolation, and the one that caught the commit-ordering bug above) a same-`asset_id`
cross-tenant baseline check: tenant A builds a tight 11-reading baseline, tenant B
posts a single reading for the *same* `asset_id`/`sensor_type` and must see
`sample_count == 0`/`rolling_mean == null`, confirming RLS scopes the anomaly
detector's own baseline query, not just the existing ingest/read paths. Also updated
the pre-existing `test_ingest_returns_accepted_count` (its exact-match assertion on
the response body would otherwise have started failing the moment `anomalies` was
added) to assert the new field's shape too, rather than loosening it away.

18 new backend tests pass (4 schema unit tests, 7 pure z-score/flag-logic unit tests
run without a database, 4 new cross-tenant integration tests including the baseline
isolation case, plus 1 existing cross-tenant test extended for the new response
field), 290/290 backend tests total, ruff + mypy clean. This task doesn't close any
Phase 3 Definition of Done checkbox on its own — "a manually injected anomaly
produces a browser alert in under 2 seconds" needs 3.5's debounce logic and 3.6's
WebSocket delivery on top of this detector, neither of which exist yet.

Note on 3.3 ("Risk inference service", issue 3.3): new `risk_scores` table (migration
0009) — `tenant_id, facility_id, asset_id, score, model_version, factors_json,
computed_at`, matching PROJECT_PLAN.md §5's sketch plus the usual `tenant_id`-and-RLS
gap fix (repo rule 2). Insert-only, never updated in place: "model version recorded
alongside each stored risk score, for auditability" (this phase's DoD item 4) reads as
keeping history, not overwriting a snapshot, so `GET /facilities/{id}/risk-scores`
returns each asset's newest row via `ORDER BY computed_at DESC`, not an upserted
single row per asset. Written from the start with migration 0008's
`NULLIF(current_setting(...), '')::uuid` RLS guard, not the bare-cast bug 0001/0004/0006
originally shipped with. `score`'s 0–100 bound is enforced twice, independently: a
Pydantic `Field(ge=0, le=100)` on the new `app/schemas/ml/risk_score.py`'s
`RiskScoreResult` (the "never write unvalidated data" rule extended to this ML output,
even though it isn't an agent yet — Phase 4's Risk Assessment agent wraps this same
service as a tool later) catches a scaling bug in the app layer, and a
`CHECK (score >= 0 AND score <= 100)` constraint on the table itself catches anything
that reaches the DB by another path.

New `app/services/risk_inference_service.py`: `score_facility` loads the trained
model once per facility (module-level cache keyed by resolved version string, not
re-fetched per asset), pulls every asset's `AssetFeatureSet` via 3.1's
`build_facility_features`, vectorizes through 3.1/3.2's shared `feature_vector.vectorize`
(the single source of truth for column order both training and inference already
vectorize through), scores via `predict_proba`, and refuses outright (`ValueError`, no
rows written) if the loaded model's `feature_names` don't match
`app.ml.feature_vector.FEATURE_NAMES` — a stale or corrupt model artifact fails loudly
here rather than silently scoring against a misaligned column layout. `factors_json`
holds human-readable context (asset age, dependency-neighbor counts, 30-day
per-sensor anomaly counts) for later explainability, not raw model internals.
New `app/workers/risk_tasks.py`: Celery task `compute_facility_risk_scores` on the
main app's celery-worker (same `asyncio.run(...)`-wrapping-async-DB-code pattern
`app/workers/callback_tasks.py` established for issue 2.1), registered via the same
bottom-of-module import trick in `app/core/celery_app.py`. Deliberately *not* wired to
any sensor-threshold breach — root CLAUDE.md rule 4's debounced anomaly-triggered path
is a separate, later task (3.5); this is purely an on-demand trigger.
`POST /facilities/{id}/risk-scores/compute` (tenant_admin/facility_manager/superadmin,
same write-role split as asset placement) enqueues the task and returns 202 with a
task id immediately — it does not check facility ownership itself, since the Celery
task's own `score_facility` call already 404s off the caller's JWT-derived tenant_id,
never a client-supplied one. `GET /facilities/{id}/risk-scores` (any tenant member,
matching the telemetry/features routes) returns the latest score per asset.

Extra scrutiny applied per the workflow (new table + RLS): before building, planned
adversarial cases covering RLS fail-closed with no GUC, cross-tenant INSERT rejection,
a DB-level CHECK-constraint rejection of an out-of-range score bypassing the Pydantic
gate entirely, an asset with zero telemetry scoring cleanly via zero-filled windows, a
mismatched-feature-names model being refused rather than silently scoring, and a
cross-tenant facility_id being unscorable/unreadable — all of which the finished test
suite covers (`tests/cross_tenant/test_risk_scores_rls.py`,
`tests/integration/test_risk_inference_service.py`,
`tests/integration/test_risk_scores_routes.py`, `tests/unit/test_risk_score_schema.py`).

Verified for real, not just mocked (per the extra-scrutiny workflow step): brought up
a real MinIO container and ran 3.2's real training pipeline against it end to end
(2,000 synthetic samples, real `XGBClassifier`, real `save_model_to_minio`), then
brought up real Postgres + Timescale containers, ran both real Alembic chains through
migration 0009, inserted a real tenant/facility/two real assets (one 10-year-old
offline pump, one 1-year-old operational pump) via `app_role` (non-BYPASSRLS)
connections, and called `score_facility` with zero mocking anywhere in the chain — the
real `load_model()` correctly followed the real `latest.json` pointer, and the offline
10-year-old pump scored 23.36 vs. the operational 1-year-old pump's 7.13, confirming
the model's injected latent-risk signal (age + offline neighbors → higher risk, from
3.2's `synthetic_data.py`) actually surfaces through the full real pipeline, not just
that the plumbing type-checks. Confirmed both rows persisted via a fresh, freshly
re-scoped `SELECT` against the real Postgres container. All containers torn down after.

25 new backend tests pass (4 cross-tenant RLS including the CHECK-constraint case, 6
schema unit tests, 6 integration tests for the service including the no-telemetry and
mismatched-feature-names adversarial cases, 7 route tests including RBAC and the
cross-tenant enqueue-ownership case, plus 2 existing tests updated for the new
migration head/table), 276/276 backend tests total, ruff + mypy clean. This task
doesn't close any Phase 3 DoD checkbox outright except item 4 (model version recorded
alongside each stored score) — map color-coding (DoD item 1) is task 7, not here.

Note on 3.2 ("XGBoost training script", issue 3.2): no `maintenance_records`/
historical-failure table exists in this repo (same gap 3.1 flagged), so "train
offline on synthetic data with injected failure patterns" per `PHASE_PLAN.md` means
literally synthetic: no real DB access at all in this task, no new
table/migration/RLS policy. New `app/ml/` package (shared going forward by 3.2 now
and 3.3's inference service later, not duplicated): `feature_vector.py` is the single
source of truth for the model's input shape — `vectorize(AssetFeatureSet) -> np.ndarray`
against a fixed, ordered `FEATURE_NAMES` (91 columns: asset-status one-hot, age,
3 dependency-neighbor counts, plus 7 stats × 4 sensor types × 3 windows, hardcoded to
the same four sensor types `backend/scripts/iot_data_generator.py` produces, since
that's this repo's only real telemetry source) — both training and 3.3's inference
must vectorize through this one function so a trained model's column layout can never
drift from what inference feeds it. `synthetic_data.py` generates `AssetFeatureSet`
instances plus binary failure labels from a documented, injected latent-risk function
(older assets, more offline neighbors, unstable 30-day vibration readings → higher
failure probability; every other feature is uncorrelated noise on purpose, so the
test suite can confirm the trained model actually recovers the injected signal rather
than overfitting noise columns). `train.py` fits an `XGBClassifier`
(`train_risk_model`) and versions the artifacts into a new `ml-models` MinIO bucket
(`save_model_to_minio`/`load_model`): `model.ubj` + `feature_names.json` +
`metrics.json` under a version string (UTC timestamp + short content hash — re-saving
a byte-identical model is idempotent on version rather than manufacturing a fake
distinct one), plus a `latest.json` pointer 3.3 will follow. New
`backend/scripts/train_risk_model.py` (standalone CLI, no DB session, mirrors
`iot_data_generator.py`'s pattern) refuses to publish (`SystemExit`) a model below a
`--min-test-auc` threshold (default 0.75) rather than silently shipping a bad model.
New deps: `numpy`, `scikit-learn`, `xgboost`. A plain `numpy>=1.26` resolved to 2.5.3,
which ships stub syntax `mypy`'s configured `python_version = "3.11"` can't parse;
fixed by capping `numpy>=1.26,<2.1` in `pyproject.toml` (not by relaxing the mypy
config) and reinstalling 2.0.2, so a fresh `pip install` doesn't silently reintroduce
the same `mypy` breakage.

Not one of the extra-scrutiny categories (no RLS/new table, not Phase 2/4/5 tasks),
so no adversarial-test walkthrough — but verified for real anyway per this repo's norm
for storage-touching code: ran `train_risk_model.py` against a real (non-mocked)
`minio/minio` container end to end (3000 synthetic samples, real training, real
`save_model_to_minio` call), confirmed the real bucket held exactly the four expected
keys (`model.ubj`, `feature_names.json`, `metrics.json`, `latest.json`), and
round-tripped `load_model()` against that same real instance — got back the same 91
feature names and the same metrics dict the training run reported. Container torn
down after.

21/21 new backend tests pass (feature-vector shape/ordering/missing-window-zero-fill,
synthetic-data determinism-by-seed and the injected-factor-correlation checks, and
the MinIO save/load round trip against a fake in-memory client), 251/251 backend
tests total, ruff + mypy clean. This task doesn't close any Phase 3 Definition of Done
checkbox on its own (those need 3.3's inference service to put a score anywhere
visible) — model_version auditability (DoD item 4) is set up here (the version string
this task produces is what `risk_scores.model_version` will store) but not wired to
anything yet.

Note on 3.1 ("Feature engineering pipeline", issue 3.1): builds the feature set
`PROJECT_PLAN.md` §4.3's Risk Assessment agent (and 3.2's training script/3.3's
inference task) will consume — no new table/migration, this reads existing data via
two existing RLS-protected databases. Gap flagged and resolved with the user before
building: §4.3 lists "last maintenance date" and "failure rate for that class" as risk
factors, but no `maintenance_records`/historical-failure table exists in this repo (not
scoped in any Phase 1–3 task) — those two factors are deliberately omitted rather than
inventing a table; "adjacent asset failures" is covered instead by
`dependency_neighbor_offline_count`/`dependency_neighbor_maintenance_count`, built from
`asset_dependencies` + each neighbor's current `status`, which does exist.

New `app/schemas/ml/asset_features.py` (`SensorWindowStats`, `AssetFeatureSet`) — a new
`schemas/ml/` subfolder since this is neither a request schema nor an agent-output
schema (`backend/CLAUDE.md`'s split). New `app/services/feature_engineering_service.py`:
`build_asset_features`/`build_facility_features` pull rolling 30/90/365-day stats
(mean/stddev/min/max/count/latest_value per `sensor_type`, matching §4.3's "TimescaleDB
telemetry query (last 30/90/365 days)" tool description) via one parameterized CTE SQL
query per window against `sensor_readings` — no new Python numerics dependency, since
Postgres/Timescale's native aggregates (`avg`, `stddev_samp`) already do this. Each
window also gets a window-local `anomaly_count` (readings >2 std devs from that same
window's mean) — explicitly documented as a feature signal distinct from task 3.4's live
rolling-Z-score anomaly detector, which scores each reading as it's ingested, not a
duplicate of it. Combines this with `assets`/`asset_dependencies` (main DB, separate
physical database from `sensor_readings` — see `app/models/sensor_reading.py`) for
`asset_age_days` (from `installed_date`) and the dependency-neighbor status counts.
New `GET /facilities/{facility_id}/assets/{asset_id}/features` (any authenticated
tenant member, same role pattern as the telemetry route) — both to verify the pipeline
end to end now and for 3.3's inference Celery task to call
`feature_engineering_service.build_asset_features` directly later.

This is the first route to combine both RLS-protected databases in a single request;
not one of the four extra-scrutiny categories in the workflow above, but per 2.8's
precedent (new read path over RLS-protected tables gets a due-diligence isolation
check even without a new policy), added a cross-tenant case anyway:
`tests/integration/test_feature_engineering.py`'s
`test_a_tenant_cannot_read_another_tenants_asset_features` confirms a different
tenant's token against a real facility_id/asset_id gets 404, not another tenant's
feature vector (the explicit `tenant_id` filter on the asset lookup blocks it before
the Timescale query ever runs). Unlike `sensor_readings`, `assets` has a normal
database-wide primary key, so the same-`asset_id`-different-tenant collision test
2.8 ran against `sensor_readings` doesn't apply here — two tenants can't have rows
sharing one `asset_id` in the first place.

Verified for real, not just unit-tested: ran the full test file against real
Postgres + real `timescale/timescaledb` containers (the same dual-container/`app_role`
harness `tests/cross_tenant/test_telemetry_isolation.py` built for 1.7-fix), with real
migrations applied — window-boundary math (readings at 1/40/100/400 days ago land in
exactly the 30/90/365-day windows they should), the empty-window shape (count=0, not an
omitted key), asset age from a real `installed_date`, dependency-neighbor counts in
both edge directions, and the cross-tenant 404 above. Did not additionally drive this
against real Supabase/Timescale Cloud via `uvicorn` the way 2.4/2.5/2.7/2.8 did — this
task isn't one of the extra-scrutiny categories, and the dual real-container run
already exercises the actual RLS policies/migrations/cross-database query path, not
mocks; flagging that distinction here rather than overclaiming a live-cloud run that
didn't happen.

15/15 new backend tests pass (8 schema unit tests, 7 integration tests including the
cross-tenant case), 230/230 backend tests total, ruff + mypy clean. This task doesn't
close any Phase 3 Definition of Done checkbox on its own (those are about visible risk
scores/alerts, which need 3.2's trained model and 3.3's inference service first) — it's
groundwork task 3.2 depends on.

Note on 2.8 (issue 2.8, "Asset detail panel"): no new table/migration — this is a
read-only route over the existing `sensor_readings` hypertable (RLS + migration from
1.6/1.7-fix). New `GET /facilities/{facility_id}/assets/{asset_id}/telemetry` (any
authenticated tenant member, matching the other read-only asset routes) backed by
`telemetry_service.get_asset_telemetry` (explicit `tenant_id` filter as
defense-in-depth alongside RLS, same pattern as every other tenant-scoped service —
see repo rule 2), ordered most-recent-first, `sensor_type` filter and a server-capped
`limit` (default 200, max 500) so a client can't force an unbounded hypertable scan.
`facility_id` stays in the URL for symmetry with the other asset routes but isn't
cross-checked against the reading, for the same reason `POST /telemetry` doesn't:
`sensor_readings` lives on a physically separate TimescaleDB instance with no
cross-database FK to `assets` (see `app/models/sensor_reading.py`) — isolation is RLS
plus the explicit tenant filter, not a facility-ownership join. Extra scrutiny applied
per the workflow (this is a new read path over an RLS-protected table, i.e. "anything
touching RLS policies or cross-tenant tests"): extended `tests/cross_tenant/test_telemetry_isolation.py`
(the same dual-container Postgres+Timescale fixture 1.6/1.7-fix built) with adversarial
cases before treating this as done — ordering/most-recent-first, `sensor_type`
filtering, the 422 on an over-limit request, and (the one that actually matters for
isolation) a same-`asset_id` cross-tenant read: tenant A posts a reading, tenant B's
own valid JWT queries the *same* `asset_id` and gets `[]`, not a 404 or another
tenant's data — confirming RLS blocks the read, not just that the ingest side was
already isolated. Frontend: `useAssetTelemetry.ts` (React Query, mirrors `useAssets.ts`),
`TelemetryChart.tsx` (Recharts `LineChart` — first use of Recharts in this repo, added
as a dependency per the root CLAUDE.md tech-stack list; one line per `sensor_type`
present in the data, oldest-first since the API returns newest-first for the
"most recent N readings" query shape but a time-series chart reads left-to-right), and
`FacilityMapPage.tsx`'s existing `AssetDetailPanel` gained the chart plus a static
"Maintenance history" section — deliberately just placeholder text, not a new
table/endpoint: `PHASE_PLAN.md` task 2.8 calls this a "stub", and real maintenance
scheduling is Phase 4 agent-output territory, not scoped here.

Verified for real, not just unit-tested (per the extra-scrutiny workflow step): ran
the real FastAPI app via `uvicorn` against the real managed Supabase + Timescale Cloud
instances (not testcontainers) and drove the new endpoint end-to-end with two real
tenants — ingested readings as tenant A, confirmed tenant B's GET against the same
`asset_id` returns `[]` (RLS blocking a real managed-DB read, not a mocked one),
confirmed `sensor_type` filtering and the over-limit 422, then cleaned up the temporary
tenants/users/readings afterward (Timescale Cloud's `tsdbadmin` has no BYPASSRLS on
this cluster per the 1.7-fix note, so cleanup had to scope the GUC per tenant before
deleting, not just run an unscoped `DELETE`). Separately, since Recharts' actual pixel
rendering can't be confirmed by Vitest's jsdom-based component tests (no real layout,
so `ResponsiveContainer` needed a `getBoundingClientRect` stub just to mount its
children at all — see `TelemetryChart.test.tsx`), drove `TelemetryChart` in a real
Chrome tab via a temporary route in `App.tsx` (removed before committing) with
synthetic two-sensor-type data: confirmed a real line chart painted with correct axes
and a legend entry per sensor type, no console errors on load.

5/5 new backend tests pass (ordering, `sensor_type` filter, limit cap validation,
missing-auth, and the cross-tenant same-asset-id isolation case), 215/215 backend
tests total, ruff + mypy clean. 4 new frontend tests pass (`useAssetTelemetry.test.tsx`,
`TelemetryChart.test.tsx` incl. the empty-state case), 45/45 frontend tests total,
ESLint + `tsc -b` clean, production `vite build` succeeds. This closes task 2.8's scope
and, with it, all of Phase 2's Definition of Done.

Note on 2.7: new `asset_dependencies` table (migration 0007) — directed edges,
`parent_asset_id` DEPENDS ON `child_asset_id` (child is upstream; this direction is what
Phase 4's cascade simulation will walk to find downstream-impacted assets from a failed
asset, per PHASE_PLAN.md's Phase 4 DoD — documented in `app/models/asset_dependency.py`'s
docstring since getting the direction backwards would be easy and hard to notice later).
Same fail-closed RLS pattern as `assets`/`facility_map_uploads`. Backend:
`app/services/asset_dependency_service.py` (create/list/delete, same
explicit-tenant_id-filter-plus-RLS defense-in-depth as `asset_service.py`) rejects
self-loops (400), duplicate edges (409), and — the one deliberate addition beyond "link
two rows" — cycles (409, via a BFS over existing edges before insert), since an
unconstrained graph would silently break Phase 4's cascade walk later. `app/api/asset_dependencies.py`
(`POST/GET /facilities/{id}/asset-dependencies`, `DELETE .../{dependency_id}`) mirrors
`assets.py`'s role split. Frontend: `useAssetDependencies.ts` (React Query, mirrors
`useAssets.ts`), `DependencyLayer.tsx` (a GeoJSON line layer between parent/child asset
positions — per `frontend/CLAUDE.md`'s "GeoJSON layers, never hand-drawn SVG/CSS shapes"
rule, extended from points to edges), and `FacilityMapPage.tsx` gained a "Link
dependency" mode (click one asset then another to create an edge) plus a
depends-on/depended-on-by list with per-edge "Unlink" in the asset detail panel.

Live-verifying this task (real Postgres, real uvicorn, real Chrome — required since this
touches RLS, per the extra-scrutiny workflow step) surfaced a real, previously-undiscovered
production bug, not introduced by this task but affecting every tenant-scoped table since
Phase 1: Postgres's custom-GUC placeholder mechanism means `current_setting('app.current_tenant_id',
true)` returns `''` (not NULL) on a session/connection that has ever used `SET LOCAL
app.current_tenant_id` once that transaction commits — confirmed directly against a real
Postgres instance (see migration 0008's docstring for the full repro). Every RLS policy's
bare `current_setting(...)::uuid` cast then raises a hard `InvalidTextRepresentationError`
(500) instead of the intended fail-closed "0 rows" — and `session.refresh()` immediately
after `session.commit()` (the pattern `asset_service.create_asset`, `facility_map_service`'s
upload creation, and this task's own `create_dependency` all used) hits exactly this, in a
fresh transaction right after the commit that scoped the GUC. `auth_service.py`'s
`register_user` had already independently discovered and correctly avoided this pattern
(see its comment) but it was never applied consistently. Fixed two ways, together: (1)
migration 0008 (main chain) and a matching migration 0002 (the separate `migrations_timescale/`
chain, `sensor_readings` carries the identical bug) guard every existing policy with
`NULLIF(current_setting(...), '')::uuid` — migration 0007 was written with the guard from
the start; (2) removed the unnecessary `session.refresh()` calls from `asset_service.py`
(`create_asset`, `update_asset`), `facility_map_service.py`, and this task's own
`asset_dependency_service.py` — all redundant anyway, since `app/core/db.py`'s session
factory already sets `expire_on_commit=False`, so server-generated defaults (`id`,
`created_at`) are already correct on the object after commit via Postgres's implicit
RETURNING. New regression test `tests/cross_tenant/test_rls_guc_empty_string_regression.py`
pins the exact same-connection SET-LOCAL-then-commit-then-query sequence against `assets`,
`users`, and `facilities`, asserting 0 rows rather than a crash. This means `create_asset`
(issue 2.6) had likely been silently broken against any real, non-bypass-RLS role since it
was merged — the integration test suite never caught it because its `client` fixture
connects the app as the container's admin/BYPASSRLS role, not `app_role`, so RLS was never
actually evaluated on that path; only the `cross_tenant/` tests use `app_role`, and none of
those exercised a create-then-refresh sequence.

Verified for real, not just unit-tested (per the extra-scrutiny workflow step, since this
touches RLS/cross-tenant tests): brought up a real (non-testcontainer) Postgres container,
ran real `alembic upgrade head` through migration 0008, ran the real FastAPI app via
`uvicorn` connected as `app_role` (not admin) against it, and drove a real login + the
full link-dependency flow in a real Chrome tab (temporary seed data, removed after) —
created two real assets via the "Add asset" map-click flow (confirming the POST 503 → 201
fix), entered "Link dependency" mode and clicked one marker then another (confirming the
two-click state machine and the prompt text), confirmed the dashed line rendered between
them, opened the asset detail panel and confirmed "Depends on: Tank 2" with an "Unlink"
button, clicked it, and confirmed both the line and the list entry disappeared. This
first pass is also what surfaced the RLS bug above — the first attempt 503'd on both
asset creation and dependency creation before the fix, and reproducible with a minimal
SQLAlchemy+asyncpg script isolated from the FastAPI app entirely, ruling out a frontend or
routing cause before touching the RLS policies.

23/23 new backend tests pass (5 cross-tenant RLS for asset_dependencies, 15
integration/RBAC/adversarial — self-loop, duplicate edge, cycle, cross-facility pairing —
for the new routes, 3 for the RLS-GUC regression), 210/210 backend tests total, ruff +
mypy clean. 9 new frontend tests pass (`useAssetDependencies.test.tsx`,
`DependencyLayer.test.tsx` incl. a dangling-edge-dropped case), 41/41 frontend tests
total, ESLint + `tsc -b` clean, production `vite build` succeeds. This closes task 2.7's
scope; Phase 2's overall DoD item "assets can be placed, linked, and clicked for detail"
is now satisfied for placement and linking — full click-for-detail (telemetry chart) is
task 2.8.

Note on 2.6: new `assets` table (migration 0006) — `tenant_id, facility_id, name, type,
x, y, status (enum: operational/maintenance/offline), installed_date, manufacturer,
model`, matching `PROJECT_PLAN.md` §5's sketch plus a `tenant_id` column that sketch
omitted (same gap-and-fix pattern as `sensor_readings` in 1.6 — every tenant_id-bearing
table needs RLS + a cross-tenant test per repo rule 2). `(x, y)` are local-pixel
coordinates in the *same* space the facility's raster tile pyramid uses (documented in
`app/models/asset.py`'s docstring) — not a new coordinate convention. `assets` is the
first table in this repo that's both updatable and end-user-deletable, so
`tests/cross_tenant/test_assets_rls.py` extends the usual read/insert-isolation
coverage (`test_facility_map_uploads_rls.py`'s pattern) with two new adversarial cases:
a cross-tenant `UPDATE` and a cross-tenant `DELETE` against a real, known row id must
both affect zero rows (RLS's `USING` clause blocks them, not just `WITH CHECK` on
writes), not merely error or silently 404 at the API layer alone. Backend:
`app/services/asset_service.py` (create/list/get/update/delete, same
explicit-tenant_id-filter-plus-RLS defense-in-depth as `facility_map_service.py`, plus
a facility-ownership check on create) and `app/api/assets.py`
(`POST/GET /facilities/{id}/assets`, `GET/PATCH/DELETE .../{asset_id}`) — writes
restricted to `tenant_admin`/`facility_manager`/`superadmin`, reads open to any
authenticated tenant member, mirroring `facility_maps.py`'s role split.

Frontend: `useAssets.ts` (React Query list/create/update/delete via the existing
`apiFetch` wrapper), `AssetLayer.tsx` (a GeoJSON circle layer, not a DOM marker — per
`frontend/CLAUDE.md`'s "assets are GeoJSON point layers, never hand-drawn SVG shapes
positioned with CSS" — with click-to-select and mousedown/mousemove/mouseup-driven
drag-to-place), and `mapCoords.ts` (`pixelToLngLat`/`lngLatToPixel`, converting an
asset's local-pixel `(x, y)` to/from the `[lng, lat]` MapLibre needs, routed through
`maplibregl.MercatorCoordinate` rather than hand-deriving the Mercator projection
formula, so it stays self-consistent with how `FacilityMap.tsx`'s raster layer is
actually drawn). `FacilityMapPage.tsx` gained an "Add asset" mode, a new-asset form,
and an asset detail/edit/delete side panel, role-gated the same way as the map-upload
route (`superadmin`/`tenant_admin`/`facility_manager` only; `technician`/`viewer`
stay read-only).

Verified for real, not just unit-tested (per the extra-scrutiny workflow step, since
this touches RLS/cross-tenant tests): brought up a real Postgres container and ran
`test_assets_rls.py`'s adversarial cases directly — confirmed a cross-tenant `UPDATE`
and `DELETE` against a real row id both return `"UPDATE 0"`/`"DELETE 0"` and leave the
row untouched, not just that the API 404s. Separately, since MapLibre marker placement
math can't be verified by Vitest's mocked-`maplibre-gl` unit tests (jsdom has no WebGL,
and the mock doesn't implement real Mercator projection), drove `AssetLayer` in a real
Chrome tab via a temporary harness route (removed before committing) with three fake
assets: confirmed markers render at correct *relative* screen positions and are
color-coded correctly by status (green/orange/red for
operational/maintenance/offline), using `map.fitBounds` to navigate there (at
`RASTER_PROFILE_MAX_ZOOM`/zoom 0, an asset's local-pixel coordinates map to a point so
close to the world's Mercator corner that it isn't visible without zooming in — true
of the floor-plan raster layer itself too, an existing default-view characteristic
from 2.5, not something 2.6 introduced). This live pass caught two real bugs unit
tests with synthetic events didn't: (1) a plain click-and-release on an asset (no real
drag) was still committing a no-op "move" PATCH to the same coordinates — fixed by
gating the move commit on actual screen-pixel displacement past `DRAG_THRESHOLD_PX`
(3px) rather than "any mousemove event fired", since a real click's mousedown/mouseup
can sandwich a sub-pixel jitter mousemove with zero intent to drag; (2) the asset
label's `symbol`/`text-field` layer required a style-level `glyphs` (font) URL that
`FacilityMap.tsx`'s style never sets — MapLibre logged an error and silently dropped
the layer rather than throwing. Since this project's map is deliberately private and
self-hosted with no external services (`PROJECT_PLAN.md` §6) and this repo has no
font/glyphs server to point at, the label layer was dropped rather than pointed at a
public glyphs CDN — asset name/type is still available via the sidebar list and detail
panel, just not as an on-map label; a real icon-by-type sprite sheet (`PROJECT_PLAN.md`
§6's eventual design) is a separate, not-yet-scoped follow-up. Both fixes are covered
by new regression tests in `AssetLayer.test.tsx`, not just fixed ad hoc.

21/21 new backend tests pass (5 cross-tenant RLS incl. the two adversarial
update/delete cases, 16 integration/unit), 190/190 backend tests total, ruff + mypy
clean. 13 new frontend tests pass (`mapCoords.test.ts`'s real-`MercatorCoordinate`
round-trip math, `useAssets.test.tsx`, and `AssetLayer.test.tsx` incl. both live-found
regressions), 32/32 frontend tests total, ESLint + `tsc -b` clean, production
`vite build` succeeds. This closes task 2.6's scope (asset CRUD + click/drag map
placement); Phase 2's overall DoD item "assets can be placed... and clicked for
detail" is satisfied by this task, but "linked" stays open until task 2.7's asset
dependency graph editor.

Note on 2.5: `frontend/` was completely empty before this task (only `CLAUDE.md` and
empty `src/` subfolders) — no `package.json`, no build tooling at all — so this session
bootstraps the whole app: Vite + React 18 + TS, React Query, react-router-dom, a typed
`fetch` wrapper (`src/lib/apiClient.ts`) that keeps the access token in module-scope
memory only (never localStorage, per `frontend/CLAUDE.md`) and does one 401 →
`POST /refresh` → retry using the existing HttpOnly cookie, and a minimal
`AuthProvider`/`useAuth` (`src/hooks/useAuth.tsx`) plus bare `LoginPage` — deliberately
unstyled, since this task's job is proving the map-rendering pipeline works end to end,
not the real login UX. `useFacilityMapUpload` (`src/hooks/useFacilityMapUpload.ts`)
polls `GET /facilities/{id}/map/{uploadId}` (issue 2.4) every 2s while `status` is
pending/processing/sanitized, stopping at tiled/failed/conversion_failed.
`FacilityMap` (`src/components/FacilityMap.tsx`) is the actual MapLibre GL JS canvas:
a raster source off `tile_url_template`, `scheme: "tms"` (gdal2tiles' default y-axis,
not MapLibre's default `"xyz"`), center `[0, 0]`/zoom `0` (gdal2tiles' raster profile
has no real-world CRS — see `app/sandbox/convert.py` — so it centers the tiled image at
a synthetic `[0,0]` origin the same way a normal Mercator pyramid would), and
`maxzoom: 4` hand-kept to match `DEFAULT_MAX_ZOOM` in `app/sandbox/convert.py` (not
exposed via the `FacilityMapUploadStatusResponse` schema — reopening that merged 2.4
contract for one int didn't seem worth it here, but the two need to be kept in sync by
hand if `DEFAULT_MAX_ZOOM` ever changes). Reached at
`/facilities/:facilityId/map/:uploadId` by direct URL, not a facility picker — there's
no facilities-list endpoint on the backend yet (facility CRUD isn't a task anywhere in
`PHASE_PLAN.md`'s Phase 2 list), matching how 2.4 itself was verified, by a direct URL
hit rather than a UI flow. `frontend/Dockerfile` (dev-mode Vite, `npm run dev --host`)
fills in docker-compose's `frontend` service, which already expected a build context and
port 5173 with no Dockerfile present.

A real blocker surfaced building this: MapLibre uploads raster tiles into WebGL
textures, which requires the tile images to clear CORS, or the browser throws a
`SecurityError` on texture upload and nothing renders. The self-hosted `minio/minio`
image this repo uses has no per-bucket S3 CORS API at all — that's an AIStor
(paid-tier)-only feature (confirmed against `minio/minio` upstream issues) — so the
"CORS configured on the bucket separately" follow-up flagged at the end of 2.4's note was
slightly wrong about the mechanism. Fixed with MinIO's server-wide
`MINIO_API_CORS_ALLOW_ORIGIN` env var instead (`docker-compose.yml`'s `minio` service),
scoped to the frontend's dev origin, not `*`. Separately, the FastAPI app had no CORS
middleware configured at all, which would have blocked every browser fetch from the
frontend's origin to the API regardless of the MinIO fix — added `CORSMiddleware` in
`app/main.py` with a new `Settings.frontend_origin` (`http://localhost:5173` default),
`allow_credentials=True` (needed for the refresh cookie) forcing an explicit origin list
rather than `"*"` (browsers reject wildcard-origin + credentials). New
`infra/docker/.env`/`.env.example` entries: `FRONTEND_ORIGIN`,
`MINIO_API_CORS_ALLOW_ORIGIN`.

Verified for real, not just unit-tested: brought up real `minio`+`redis` containers,
uploaded a synthetic tile pyramid (a real, valid small PNG at z0/z1, same
`put_tile_pyramid` code path 2.3/2.4's real verification used) through the anonymous-read
+ now-CORS-enabled bucket, confirmed an anonymous browser `OPTIONS` preflight against a
tile URL returns `Access-Control-Allow-Origin: http://localhost:5173`, then loaded the
real `FacilityMap` component in a real Chrome tab (via a temporary route, removed before
committing) pointed at that tile URL — it rendered the tile with zero console errors (an
earlier pass with a hand-rolled, invalid PNG did correctly reproduce the "image could not
be decoded" failure mode, confirming the test would actually catch a real problem, not
just pass trivially). Separately ran the real FastAPI app (`uvicorn`, real Supabase
credentials from `.env`, no docker build since `backend/Dockerfile` doesn't exist yet —
a pre-existing gap, out of scope here, same as prior sessions running the API directly)
and drove a real login attempt from the browser through `LoginPage` with wrong
credentials: got a clean `"Invalid email or password"` rendered in the UI with no CORS
errors in the console, confirming the full browser → CORS → FastAPI → Supabase → error
response → UI round trip actually works, not just that the pieces type-check. All
containers/dev servers torn down after.

16/16 new frontend tests pass (Vitest + RTL: the API client's 401→refresh→retry logic
including the non-retry cases for `/login`/`/refresh` themselves and a failed refresh;
the polling hook's stop-condition as a pure exported function rather than driving React
Query's timers; the `FacilityMap` component's MapLibre source/layer config with
`maplibre-gl` mocked, since jsdom has no WebGL), ESLint + `tsc -b` clean, production
`vite build` succeeds. 2 new backend tests (`tests/unit/test_cors.py`) cover the new
CORS middleware — allowed origin gets the preflight headers, an unlisted origin doesn't —
167/167 backend tests still pass, ruff + mypy clean. This closes task 2.5's scope; Phase
2's overall DoD ("assets can be placed, linked, and clicked") stays open until task 6+.

Note on 2.4: PHASE_PLAN.md's "wire martin/TiTiler" phrasing turned out not to fit —
flagged and resolved with the user before building (per the workflow's plan-first step):
martin serves PostGIS tables, MBTiles, or PMTiles, not the loose `{z}/{x}/{y}.png` XYZ
tree `gdal2tiles.py -p raster` (task 2.3) actually produces and uploads object-by-object
to MinIO, and TiTiler tiles COGs dynamically — neither is a drop-in for what 2.3 already
built and merged. Repacking into MBTiles/COG would have reopened 2.3's merged pipeline
for no functional gain, so chosen approach: drop the unwired `tile-server: maplibre/martin`
compose stub entirely and serve tiles straight from MinIO's own S3 HTTP API. New
`_anonymous_read_policy` in `app/sandbox/storage.py` sets a bucket policy scoped to
`s3:GetObject` only, only on `facility-map-tiles` (no `ListBucket`, no write/delete, no
effect on the raw-uploads/sanitized buckets) — set on every `put_tile_pyramid` call
(idempotent), not just at bucket creation, so it self-heals if the bucket predates this
code or its policy drifts. New `Settings.minio_public_endpoint`/`minio_public_secure`
(main app config) give a host-facing MinIO address distinct from `minio_endpoint` (the
in-Docker-network one containers use to reach each other) — `Settings.tile_url_template()`
builds the MapLibre-style URL a browser will actually be able to hit. New
`GET /facilities/{facility_id}/map/{upload_id}` (any authenticated tenant member, no role
restriction — read-only, and any viewer will need this once 2.5 lands) returns upload
status plus `tile_prefix`/`tile_url_template` once `status=tiled`, via a new
`get_map_upload` service function (same explicit-tenant_id-filter-plus-RLS
defense-in-depth pattern as `create_map_upload`). No new `tenant_id`-bearing table, so no
new RLS policy/cross-tenant test needed for this task.
Verifying this for real also surfaced a second real gap, same category as several
Phase-1 fixes: the main API/celery-worker containers' `.env` never set `MINIO_ENDPOINT`/
`MINIO_ACCESS_KEY`/`MINIO_SECRET_KEY` at all, silently falling back to
`app/core/config.py`'s defaults (`localhost:9000`/`minioadmin`/`minioadmin`) — inside a
container on the compose network, `localhost:9000` doesn't reach the `minio` service, and
the credentials don't match the real `MINIO_ROOT_USER`/`MINIO_ROOT_PASSWORD` either. This
had been silently masked because every existing test monkeypatches `put_raw_upload`
rather than actually calling MinIO. Fixed by adding the three vars to `.env`/`.env.example`
(mirroring `.env.sandbox.example`'s existing `MINIO_ENDPOINT=minio:9000`), plus
`MINIO_PUBLIC_ENDPOINT=localhost:9000` for the new tile-URL builder.
Verified for real: brought up the `minio` container alone, ran `put_tile_pyramid` against
it directly (a 1×1 PNG tile pyramid, same code path 2.3's Celery task calls), then hit
the resulting tile URL with a plain anonymous `curl` (no credentials) — got back
`200 OK` and real PNG bytes (confirmed via `file`). Also confirmed the policy is scoped
correctly: anonymous `ListBucket` on `facility-map-tiles` → 403, anonymous `PUT` → 403,
and a GET against the unrelated `facility-map-raw-uploads` bucket → 403 (still fully
private). Container torn down after. Known follow-up, not built here: MinIO's Python SDK
(7.2.20) has no CORS API, so cross-origin browser fetches from the MapLibre frontend
(task 2.5) will need CORS configured on the bucket separately when that task lands — out
of scope for "confirm a tile renders via a direct URL hit," which doesn't require CORS
(curl/server-to-server has none of a browser's cross-origin restrictions).
165/165 backend tests pass (11 new: unit tests for the bucket policy shape and the
tile-URL builder, integration tests for the new GET route including a cross-tenant 404
and a viewer-role read), ruff + mypy clean. This closes task 2.4's scope; Phase 2's
overall DoD ("a real floor plan uploads, processes, and renders in-browser") stays open
until 2.5 lands the frontend map.

Note on 2.3: added `app/sandbox/convert.py` inside the existing sandbox package (still
covered generically by `test_sandbox_isolation.py`'s import-root scan — no new forbidden
imports). Pipeline: rasterize the sanitized SVG/DXF/PDF to a single PNG
(`cairosvg`/`ezdxf`+`matplotlib`/`pymupdf` respectively), then shell out to
`gdal2tiles.py -p raster` to build an XYZ tile pyramid. The **raster profile** (not a
geographic one) was a deliberate choice: floor plans have no real-world CRS, and this
profile tiles an arbitrary image in local pixel XYZ coordinates instead of reprojecting to
WGS84 — matches `PROJECT_PLAN.md` §6's "local pixel or UTM, not WGS84" note, and satisfies
Phase 2's DoD item that the coordinate system be documented where it's defined (see
`app/sandbox/convert.py`'s docstring and the `tile_prefix` column comment on
`FacilityMapUpload`). `app/sandbox/tasks.py`'s `receive_map_upload` now chains a new
`convert_sanitized_map` task (same `upload-sandbox` queue) after a successful sanitize;
that task fetches only from the sanitized bucket (`fetch_sanitized_upload`, never raw),
uploads the resulting tile files to a new `facility-map-tiles` bucket via
`put_tile_pyramid`, and reports a terminal `tiled`/`conversion_failed` status back through
the existing `record_sandbox_result` callback path — never raises, so a bad file can't
leave a row stuck at PROCESSING. Migration 0005 adds those two enum values (via an
autocommit block, since `ALTER TYPE ... ADD VALUE` can't run in the same transaction that
uses the value) plus a nullable `tile_prefix` column for task 2.4 to read; no new RLS
policy/cross-tenant test needed since `facility_map_uploads` already has both from 0004,
and this only adds a column and enum values, not a new tenant-scoped table.
Every third-party rasterizer/GDAL call in `convert.py` is imported lazily inside the
function that uses it (never at module load time) — deliberately, so the module stays
importable for unit tests on hosts without GDAL/cairo installed (e.g. Windows dev
machines); those tests inject a fake `TilePyramidBuilder` and monkeypatch `RASTERIZERS`
instead of exercising real native calls. The real pipeline was verified by actually
building `Dockerfile.sandbox` (switched base image from `python:3.11-slim` to
`ghcr.io/osgeo/gdal:ubuntu-small-3.8.4`, which ships a correctly built GDAL +
`gdal2tiles.py`, plus `libcairo2` via apt for `cairosvg`) and running all three formats
through it directly in the container — a real SVG produced a 240-file `{z}/{x}/{y}.png`
pyramid (zoom 0–4), and DXF/PDF rasterization each produced valid PNG bytes. Also
switched PyMuPDF's import from the deprecated `fitz` alias to `import pymupdf`. 87/87
backend unit tests pass (updated `test_models.py` for the new `tile_prefix` column and
`test_telemetry_isolation.py` for the new migration head version), 154/154 total including
cross-tenant/integration against real Postgres containers (confirms migration 0005
actually applies), ruff + mypy clean. This closes task 2.3's scope; Phase 2's overall DoD ("a real floor plan uploads,
processes, and renders in-browser") stays open until 2.4–2.5 land tile serving and the
frontend map.

Note on 2.2: added `app/sandbox/sanitize.py`, entirely inside the isolated sandbox
package from 2.1 (still verified importless of Postgres/main-app code by
`tests/unit/test_sandbox_isolation.py`, 34/34 still passing). SVG: parsed with
`defusedxml.ElementTree` (`forbid_dtd/entities/external=True` — closes an XXE/local-file-
read vector), then strips `<script>`/`<foreignObject>` elements, every `on*` event-handler
attribute, and any `href`/`xlink:href` that isn't a local `#fragment` reference, before
re-serializing. DXF/PDF: no new heavy parsing library added to the sandbox's minimal
image — DXF gets a structural check (alternating group-code/value lines, `SECTION`/`EOF`
sentinels), PDF gets header/trailer framing checks plus a reject on
`/JavaScript`/`/JS`/`/OpenAction`/`/AA` tokens (embedded active content); both pass through
unchanged once validated, since GDAL (task 2.3) does the real parsing. All three
independently re-check the size cap (duplicated constant, not imported from
`app.core.config` — same isolation reasoning as 2.1's Redis URL). `receive_map_upload`
now writes sanitized bytes to a **separate** `facility-map-sanitized` MinIO bucket (never
the raw-uploads bucket) and reports `status=sanitized`/`failed` instead of always
succeeding; a rejected file is reported `failed` rather than raising (an uncaught
exception would leave the DB row stuck at `PROCESSING` forever). Added `defusedxml`
(pure-Python, no C deps) to `Dockerfile.sandbox` and `pyproject.toml` — the sandbox image
still has no SQLAlchemy/asyncpg/FastAPI/DB credentials. 28 new tests (XXE doctype,
embedded script, event handlers, external xlink:href with local-fragment-href preserved
as a control, foreignObject smuggling, malformed XML, non-svg root, corrupt/non-ASCII DXF,
missing EOF sentinel, PDF active-content tokens, missing PDF header/trailer, oversized
input for all three formats, plus task-level tests that a rejected file never reaches
`put_sanitized_upload`) — 145/145 backend tests pass, ruff + mypy clean. This fully closes
task 2.2's scope (sanitize/validate/reject pre-GDAL); Phase 2's overall DoD ("a real floor
plan uploads, processes, and renders in-browser") stays open until 2.3–2.5 land GDAL
conversion, tile serving, and the frontend map.

Note on 2.1: `POST /facilities/{facility_id}/map` (tenant_admin/superadmin only) does the
minimum needed to prove the sandbox isolation boundary, deliberately no GDAL/sanitization
logic yet (that's 2.2/2.3): validates the caller's facility ownership (explicit
`tenant_id` filter, not just RLS — see below), a `.svg/.dxf/.pdf` extension allowlist, and
a 25 MiB size cap, then writes the raw bytes straight to a MinIO bucket and hands off a
narrow `{upload_id, tenant_id, storage_key}` payload by Celery task name onto a dedicated
`upload-sandbox` queue. New `facility_map_uploads` table (tenant_id-scoped, RLS +
`tests/cross_tenant/test_facility_map_uploads_rls.py`, migration 0004) tracks status;
`app/workers/callback_tasks.py` (runs in the main `celery-worker`, has DB creds) is the
only thing that writes a sandbox result back into Postgres via `record_sandbox_result` on
a separate `sandbox-results` queue. `app/sandbox/**` is a self-contained package with its
own Celery app, its own minimal `SandboxSettings` (Redis+MinIO only), and its own
`Dockerfile.sandbox` that `COPY`s nothing but `app/__init__.py` + `app/sandbox/` and
installs only `celery[redis]`+`minio` — no SQLAlchemy/asyncpg/FastAPI, no DB credentials
in its env (`infra/docker/.env.sandbox`, not the shared `.env`), no bind-mounted source.
Isolation is verified structurally, not assumed:
`tests/unit/test_sandbox_isolation.py` statically asserts `app/sandbox/**` never imports
`app.core.db`/`app.models`/`app.services`/`app.api`/SQLAlchemy, asserts
`Dockerfile.sandbox` never references those paths, asserts the compose service loads
`.env.sandbox` (not `.env`) with no `volumes:`, and asserts the sandbox/main Celery apps'
default queues are disjoint. Two real bugs surfaced by writing these tests before the
happy path (see the extra-scrutiny workflow step): (1) the ownership check originally
used `session.get(Facility, facility_id)`, relying entirely on RLS — under an
admin/BYPASSRLS DB connection (which every integration test here uses, matching the
existing harness pattern) another tenant's facility was silently visible and the upload
succeeded instead of 404ing; fixed with an explicit `WHERE tenant_id = :tenant_id` filter
as defense-in-depth. (2) `app/sandbox/config.py` initially imported
`sqlalchemy.engine.URL` to build the Redis DSN — which would have been an `ImportError`
at container runtime since `Dockerfile.sandbox` never installs SQLAlchemy; fixed by
hand-building the URL with `urllib.parse.quote` instead. Added dependencies:
`celery[redis]`, `minio`, `python-multipart` (FastAPI's `UploadFile` needs it). New
`infra/docker/.env.sandbox.example`. 117/117 tests pass (ruff + mypy clean); this task
only partially closes Phase 2's DoD (floor plan doesn't render yet — that needs 2.2–2.5).

Note on 1.7-fix (post-1.7 hardening, before Phase 1 could actually be called closed):
closing the last DoD item ("sensor generator running, rows landing in TimescaleDB")
surfaced three real bugs, only findable by actually running against the live databases —
tests against ephemeral containers had been silently masking all three:
1. **Supabase and TimescaleDB were never actually separate instances.** Migration 0004
   (from 1.6) created `sensor_readings` — including `CREATE EXTENSION timescaledb` — on
   the *same* Postgres connection as tenants/users, with a real FK to `tenants.id`.
   Supabase doesn't support the `timescaledb` extension at all, so this would have failed
   outright the first time anyone ran it against real Supabase. Fixed by splitting into
   two independent Alembic chains — `migrations/` (Supabase: tenants/users/facilities)
   and new `migrations_timescale/` (a real separate Timescale Cloud instance:
   `sensor_readings` only, with its own `app_role` bootstrap, since roles are
   per-cluster). The FK is gone (impossible cross-database) — `tenant_id` is now a bare
   indexed UUID, same pattern as `asset_id`; tenancy is enforced by RLS alone. New
   `app/core/db.py` timescale engine/session, `get_timescale_scoped_session` in
   `tenant_context.py`, `/telemetry` now uses it. New adversarial tests: fail-closed with
   no GUC, cross-tenant INSERT rejected by `WITH CHECK` (matters more now with no FK
   backing it), and the two migration chains don't leak tables/state into each other.
2. **Supabase's direct-connection host (`db.<ref>.supabase.co`) is IPv6-only** and this
   network can't route to it (`getaddrinfo failed`, reproduced identically in two
   different environments). Fixed by switching `POSTGRES_HOST`/`PORT`/`USER` in `.env` to
   Supabase's Session pooler (IPv4-proxied, behaves like a direct connection) — see
   `.env.example` for the compound `<role>.<project-ref>` username format Supavisor
   requires.
3. **`app/core/config.py` built Postgres connection URLs with a raw f-string**, which
   silently corrupts the DSN if the username or password contains a URL-reserved
   character (this project's real Supabase password contains `@`, which is exactly the
   userinfo/host delimiter). Fixed by building all four connection-URL properties with
   `sqlalchemy.engine.URL.create(...).render_as_string(...)`, which percent-encodes each
   component correctly instead of failing silently later.
4. Also discovered while verifying rows landed: Timescale Cloud's admin role
   (`tsdbadmin`) does **not** have BYPASSRLS, unlike Supabase's `postgres` role — an
   unscoped admin query against `sensor_readings` returns zero rows, not everything. Not
   a bug (RLS fail-closed working as designed), but non-obvious enough to note in
   `.env.example` so a future direct-query debugging session isn't misled by it.

After all four fixes: real `alembic upgrade head` succeeded against both real Supabase
and real Timescale Cloud; `scripts/iot_data_generator.py` run for 10s against a locally
started API produced 4×12=48 real rows, confirmed present via a properly tenant-scoped
query against the live Timescale Cloud database. 57/57 cross-tenant+integration tests and
38/38 unit tests still pass (rewrote `tests/cross_tenant/test_telemetry_isolation.py` to
spin up two containers — one per chain — matching the real two-database architecture; six
other cross-tenant/integration test files reverted from the `timescale/timescaledb`
container image back to plain `postgres:16-alpine` now that the main chain no longer
needs the extension).

Note on 1.7: added `backend/scripts/iot_data_generator.py`, a standalone CLI (not part
of the FastAPI app) that logs in via `POST /login` once, then loops posting batches of
synthetic readings to `POST /telemetry` for a configurable set of simulated asset UUIDs
and sensor types (temperature/pressure/vibration/humidity), each with a sine-wave +
Gaussian-noise baseline and an independently-rolled anomaly injection per reading
(`--anomaly-rate`). Moved `httpx` from the `dev` optional-deps group to main
`dependencies` in `pyproject.toml` since the script needs it at runtime, not just in
tests. Not one of the extra-scrutiny categories (no RLS/schema/agent/upload/Phase-5
work), so tests are unit-only and network-free: pure generation/anomaly-magnitude/
batching logic in `tests/unit/test_iot_data_generator.py`, no testcontainers needed.
This does not touch the anomaly-triggered-pipeline debounce logic in rule 4 — it only
posts raw telemetry through the existing validated ingest endpoint, it doesn't trigger
agent runs.

Note on 1.6: `PROJECT_PLAN.md` §5 sketches `sensor_readings` without a `tenant_id`
column, but that conflicts with repo rule 2 (every tenant_id-bearing table needs RLS +
a cross-tenant test) and the "never accept tenant_id as a raw request parameter"
convention — flagged and resolved with the user before building: `sensor_readings` does
have `tenant_id`, with the same fail-closed RLS policy pattern as migration 0001, plus
`tests/cross_tenant/test_telemetry_isolation.py`. Also: `assets` doesn't exist yet (it's
Phase 2 task 6), so `asset_id` is a bare indexed UUID with no FK for now — Phase 2 adds
the FK once `assets` lands. Migration 0004 also adds the `timescaledb` extension and
calls `create_hypertable`, which meant every existing cross-tenant/integration test's
`PostgresContainer("postgres:16-alpine")` had to move to
`timescale/timescaledb:latest-pg16` (that plain image lacks the extension, so `alembic
upgrade head` would otherwise break every one of those tests, not just the new one).
Dropped an HTTP-level "reject NaN" test: standards-compliant JSON can't encode `NaN` at
all, and forcing it through hand-crafted bytes just hits a Starlette quirk
(`allow_nan=False` on error responses) rather than app logic — the validator itself is
covered directly in `tests/unit/test_telemetry_schemas.py`. Phase 1 DoD's "sensor data
generator running, rows visibly landing in TimescaleDB" stays open until task 1.7 adds
the generator.

Note on 1.5: `POST/GET/PATCH /tenants` (superadmin-only) plus two user-creation paths —
`POST /tenants/{tenant_id}/users` (superadmin, arbitrary tenant, for bootstrapping a new
tenant's first admin) and `POST /users` (tenant_admin/superadmin, caller's own tenant).
Found and fixed a real RLS-adjacent gap: the `users` table's `WITH CHECK` policy means a
superadmin's normal tenant-scoped session can't insert into a tenant that isn't their
own, so admin-initiated user creation explicitly re-scopes the session to the *target*
tenant per operation (same pattern `register_user` already used), authorized off the JWT
role claim rather than a DB read. Also found `app_role` only had `SELECT` on `tenants`
(migration 0002) — added migration 0003 granting `INSERT, UPDATE`, since without it the
new tenant-create/update routes would 500 under the app's real runtime role despite
passing integration tests that connect with admin credentials.

Note on 1.4: the `Role` enum already existed on `User` from task 1.1, so this task added
`app/core/rbac.py` (`require_roles(*roles)` dependency factory reading `TenantContext.role`)
and a new `GET /users` route (list users in the caller's tenant, restricted to
`tenant_admin`/`superadmin`) as the working example. Full tenant/user CRUD is task 1.5, not
built here.

Note on 1.3: it also had to fix a real gap found while building it — the app's DB
connection was a superuser (Supabase's `postgres` role, which carries BYPASSRLS), so RLS
was never actually enforced for the app's own queries, only for the isolated test role
from 1.1. Migration 0002 adds a non-bypass `app_role` that `app/core/db.py` now connects
as; `infra/docker/.env` needs `APP_DB_USER`/`APP_DB_PASSWORD` set (see `.env.example`).
Phase 1's "second tenant's user cannot read the first tenant's data" DoD item is only
partially closed by this — proven for `users` via `GET /me`, not yet for a real
asset/facility-scoped route (that lands in tasks 1.5+).

Note: Phase 0's Definition of Done is not fully checked off yet (per project memory:
`docker compose up`, `.env` credentials, and team split are still open) — flagging this
per the resume-check step, not blocking on it since the user explicitly directed starting
Phase 1 task 1.