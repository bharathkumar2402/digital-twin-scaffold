"""The live simulator's runner (Phase L task L.2): batches, API failures, commands, shutdown.

A fake API (`httpx.MockTransport`) stands in for the real one, so every failure the runner has
to survive can be scripted exactly: the API being down, a 500, an expired token, a forbidden role.
"""

import ast
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from app.ml.live_machine_sim import SENSORS, UNITS, FleetSim, Storyline, calibrate_from_csv
from scripts import live_simulator as ls
from scripts.demo_factory import MACHINES

pytestmark = pytest.mark.skipif(not ls.AI4I_CSV.exists(), reason="needs the real AI4I 2020 file")

TENANT = uuid.UUID("d3500000-0000-4000-8000-000000000001")
FACILITY = str(uuid.uuid4())
ASSET_IDS = {m.key: uuid.uuid4() for m in MACHINES}
WHEN = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


class FakeApi:
    """Scriptable stand-in for the real API. `post_script` answers /telemetry calls in order
    (then falls back to 201); an `Exception` entry is raised as a transport failure."""

    def __init__(self, post_script=()):
        self.post_script = list(post_script)
        self.posts: list[dict] = []
        self.logins = 0
        self.token = "token-1"
        self.login_status = 200

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/login":
            self.logins += 1
            if self.login_status != 200:
                return httpx.Response(self.login_status, json={"detail": "no"})
            self.token = f"token-{self.logins}"
            return httpx.Response(200, json={"access_token": self.token})
        if path == "/facilities":
            return httpx.Response(200, json=[{"id": FACILITY, "name": "Plant"}])
        if path == f"/facilities/{FACILITY}/assets":
            return httpx.Response(
                200, json=[{"id": str(ASSET_IDS[m.key]), "name": m.name} for m in MACHINES]
            )
        if path == "/telemetry":
            step = self.post_script.pop(0) if self.post_script else 201
            if isinstance(step, Exception):
                raise step
            body = json.loads(request.content)
            if step == 201:
                if request.headers.get("authorization") != f"Bearer {self.token}":
                    return httpx.Response(401, json={"detail": "expired"})
                self.posts.append(body)
            return httpx.Response(step, json={"accepted": len(body["readings"])})
        return httpx.Response(404)


def _poster(api: FakeApi) -> ls.Poster:
    client = httpx.Client(transport=httpx.MockTransport(api.handler), base_url="http://api")
    return ls.Poster(client, TENANT, "simulator@twinlabs.com", "pw")


@pytest.fixture(scope="module")
def stats():
    return calibrate_from_csv(ls.AI4I_CSV)


@pytest.fixture(scope="module")
def starts():
    return ls.start_states()


def _sim(stats, starts, mode="off", **kw) -> FleetSim:
    return FleetSim(starts, stats, seed=0, tick_seconds=15, storyline=Storyline(mode=mode), **kw)


def _run(api, stats, starts, *, ticks=3, mode="off", control_dir=None, stop=None, **kw):
    sleeps: list[float] = []
    poster = _poster(api)
    result = ls.run(
        poster,
        _sim(stats, starts, mode),
        ASSET_IDS,
        now=lambda: WHEN,
        sleep=sleeps.append,
        should_stop=stop or (lambda: False),
        max_ticks=ticks,
        control_dir=control_dir,
        **kw,
    )
    return result, sleeps, poster


# ---- the batch --------------------------------------------------------------------------------


def test_start_states_are_the_demos_real_ai4i_records(starts) -> None:
    assert set(starts) == {m.key for m in MACHINES}
    assert all(set(s) == set(SENSORS) for s in starts.values())
    ac1 = starts["AC-1"]  # the standing critical machine is an overstrain failure record
    assert ac1["tool_wear"] * ac1["torque"] > 11000


def test_batch_has_five_sensors_for_every_machine_with_one_shared_timestamp(stats, starts) -> None:
    rows = ls.build_batch(_sim(stats, starts).step(), ASSET_IDS, WHEN)

    assert len(rows) == 5 * len(MACHINES) == 220
    assert len(rows) < ls.MAX_BATCH_ROWS
    assert {r["timestamp"] for r in rows} == {WHEN.isoformat()}
    per_asset: dict[str, list[str]] = {}
    for r in rows:
        per_asset.setdefault(r["asset_id"], []).append(r["sensor_type"])
        assert r["unit"] == UNITS[r["sensor_type"]]
    assert all(sorted(v) == sorted(SENSORS) for v in per_asset.values()), "sensors posted together"


def test_machines_missing_from_the_facility_are_skipped_not_fatal(stats, starts) -> None:
    partial = {k: v for k, v in ASSET_IDS.items() if k != "AC-1"}
    rows = ls.build_batch(_sim(stats, starts).step(), partial, WHEN)
    assert len(rows) == 5 * (len(MACHINES) - 1)


def test_oversized_batch_is_refused_before_it_is_sent(stats, starts) -> None:
    values = _sim(stats, starts).step()
    many = {f"{k}-{i}": v for i in range(5) for k, v in values.items()}
    ids = {k: uuid.uuid4() for k in many}
    with pytest.raises(ls.SimulatorError, match="exceeds"):
        ls.build_batch(many, ids, WHEN)


# ---- normal operation -------------------------------------------------------------------------


def test_happy_path_signs_in_once_and_posts_one_batch_per_tick(stats, starts) -> None:
    api = FakeApi()
    result, sleeps, poster = _run(api, stats, starts, ticks=4)

    assert result == {"ticks": 4, "sent": 4, "failed": 0, "rows": 4 * 220}
    assert [len(p["readings"]) for p in api.posts] == [220] * 4
    assert poster.logins == 1
    assert sleeps == [15, 15, 15]  # no sleep after the final tick


def test_discover_finds_the_facility_and_maps_every_machine(stats, starts) -> None:
    fid, ids = ls.discover(_poster(FakeApi()), None)
    assert fid == FACILITY and ids == ASSET_IDS


def test_discover_with_a_wrong_facility_id_fails_clearly() -> None:
    with pytest.raises(ls.SimulatorError, match="no facility"):
        ls.discover(_poster(FakeApi()), str(uuid.uuid4()))


# ---- failures the simulator must survive ------------------------------------------------------


def test_api_down_backs_off_then_recovers_without_losing_its_place(stats, starts) -> None:
    down = httpx.ConnectError("connection refused")
    api = FakeApi(post_script=[down, down, down, 201, 201])
    sim = _sim(stats, starts)
    sleeps: list[float] = []
    result = ls.run(
        _poster(api), sim, ASSET_IDS, now=lambda: WHEN, sleep=sleeps.append, max_ticks=5
    )

    assert result["failed"] == 3 and result["sent"] == 2 and result["ticks"] == 5
    assert sleeps == [15, 30, 60, 15], "exponential backoff 15, 30, 60 then back to normal"
    assert sim.tick == 5, "the machines kept advancing while the API was down"
    wears = [
        next(r["value"] for r in p["readings"]
             if r["asset_id"] == str(ASSET_IDS["AC-2"]) and r["sensor_type"] == "tool_wear")
        for p in api.posts
    ]  # fmt: skip
    assert wears == sorted(wears)


def test_backoff_is_capped(stats, starts) -> None:
    down = httpx.ConnectError("down")
    api = FakeApi(post_script=[down] * 8)
    _, sleeps, _ = _run(api, stats, starts, ticks=8)
    assert max(sleeps) == ls.MAX_BACKOFF_S


def test_a_500_is_retried_like_an_outage(stats, starts) -> None:
    api = FakeApi(post_script=[500, 201])
    result, sleeps, _ = _run(api, stats, starts, ticks=2)
    assert result["failed"] == 1 and result["sent"] == 1


def test_an_expired_token_triggers_one_re_login_and_the_batch_still_lands(stats, starts) -> None:
    api = FakeApi()
    poster = _poster(api)
    poster.login()
    api.token = "rotated-by-server"  # the token the poster holds is now rejected with 401
    sim = _sim(stats, starts)

    result = ls.run(poster, sim, ASSET_IDS, now=lambda: WHEN, sleep=lambda s: None, max_ticks=1)

    assert result["sent"] == 1 and result["failed"] == 0
    assert poster.logins == 2


def test_a_forbidden_role_stops_the_simulator_with_a_clear_error(stats, starts) -> None:
    api = FakeApi(post_script=[403])
    with pytest.raises(ls.SimulatorError, match="403"):
        _run(api, stats, starts, ticks=3)


def test_a_rejected_payload_stops_the_simulator(stats, starts) -> None:
    with pytest.raises(ls.SimulatorError, match="422"):
        _run(FakeApi(post_script=[422]), stats, starts, ticks=3)


def test_rejected_credentials_stop_the_simulator(stats, starts) -> None:
    api = FakeApi()
    api.login_status = 401
    with pytest.raises(ls.SimulatorError, match="login rejected"):
        _run(api, stats, starts, ticks=1)


# ---- shutdown ---------------------------------------------------------------------------------


def test_stop_request_ends_the_loop_after_the_current_tick(stats, starts) -> None:
    api = FakeApi()
    flag = {"stop": False}
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        flag["stop"] = True  # Ctrl+C arrives while waiting for the next tick

    result = ls.run(
        _poster(api), _sim(stats, starts), ASSET_IDS, now=lambda: WHEN,
        sleep=sleep, should_stop=lambda: flag["stop"], max_ticks=50,
    )  # fmt: skip
    assert result["ticks"] == 1 and len(api.posts) == 1


# ---- injected faults --------------------------------------------------------------------------


def test_inject_command_takes_effect_on_the_next_tick_and_is_consumed(
    stats, starts, tmp_path: Path
) -> None:
    api = FakeApi()
    ls.queue_command(
        tmp_path,
        {"action": "inject", "machine": "PB-1", "fault": "sensor_dropout"},
    )
    result, _, _ = _run(api, stats, starts, ticks=2, control_dir=tmp_path)

    assert list(tmp_path.glob("*.json")) == [], "command files are deleted once applied"
    assert [len(p["readings"]) for p in api.posts] == [215, 215]
    silent = str(ASSET_IDS["PB-1"])
    assert all(r["asset_id"] != silent for p in api.posts for r in p["readings"])
    assert result["sent"] == 2


def test_clear_command_brings_a_silent_machine_back(stats, starts, tmp_path: Path) -> None:
    sim = _sim(stats, starts)
    sim.inject("PB-1", "sensor_dropout")
    ls.queue_command(tmp_path, {"action": "clear", "machine": "PB-1"})
    ls.apply_commands(tmp_path, sim)
    assert "PB-1" in sim.step()


@pytest.mark.parametrize(
    "content",
    [
        "{not json",
        json.dumps({"action": "inject", "machine": "NOPE", "fault": "overstrain"}),
        json.dumps({"action": "inject", "machine": "AC-2", "fault": "meltdown"}),
        json.dumps({"action": "explode", "machine": "AC-2"}),
        json.dumps({"machine": "AC-2"}),
        json.dumps([1, 2, 3]),
    ],
)
def test_bad_command_files_are_ignored_and_removed_without_crashing(
    stats, starts, tmp_path: Path, content: str
) -> None:
    (tmp_path / "1.json").write_text(content, encoding="utf-8")
    sim = _sim(stats, starts)

    ls.apply_commands(tmp_path, sim)

    assert list(tmp_path.glob("*.json")) == []
    assert sim.step()  # still working


def test_cli_rejects_unknown_machine_and_fault(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as bad_machine:
        ls.main(["--control-dir", str(tmp_path), "inject", "NOPE", "overstrain"])
    assert bad_machine.value.code == 2
    with pytest.raises(SystemExit) as bad_fault:
        ls.main(["--control-dir", str(tmp_path), "inject", "AC-2", "meltdown"])
    assert bad_fault.value.code == 2
    assert list(tmp_path.glob("*.json")) == []


def test_cli_inject_queues_a_command_file(tmp_path: Path) -> None:
    assert ls.main(["--control-dir", str(tmp_path), "inject", "AC-2", "overstrain"]) == 0
    (path,) = tmp_path.glob("*.json")
    assert json.loads(path.read_text()) == {
        "action": "inject", "machine": "AC-2", "fault": "overstrain", "duration_s": 120.0,
    }  # fmt: skip


def test_demo_account_constants_match_the_seed_script() -> None:
    from scripts import seed_demo

    assert ls.DEMO_TENANT_ID == seed_demo.DEMO_TENANT_ID
    assert ls.DEMO_SIMULATOR_EMAIL == seed_demo.DEMO_SIMULATOR_EMAIL
    assert ls.DEMO_EXTRA_PASSWORD == seed_demo.DEMO_EXTRA_PASSWORD
    roles = dict(seed_demo.DEMO_EXTRA_ACCOUNTS)
    assert roles[ls.DEMO_SIMULATOR_EMAIL].value == "technician"  # the role D7 lets ingest


# ---- the runner never touches training code or the databases ------------------------------------


def test_runner_does_not_import_training_or_database_code() -> None:
    tree = ast.parse(Path(ls.__file__).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
            imported.update(f"{node.module}.{a.name}" for a in node.names)
    forbidden_prefixes = (
        "app.ml.train",
        "app.ml.rul_train",
        "app.ml.vibration_train",
        "scripts.train_",
        "xgboost",
        "sklearn",
        "sqlalchemy",
        "asyncpg",
        "app.core.db",
        "app.models",
    )
    assert [i for i in imported if i.startswith(forbidden_prefixes)] == []
