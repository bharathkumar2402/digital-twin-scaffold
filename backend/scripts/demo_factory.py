"""The Meridian Components Plant: one realistic multi-floor factory used to test everything.

A single specification (machines, where they stand, what they depend on, and what state each is
in) that the demo setup, the floor-plan drawing, and the end-to-end tests all read, so every
feature is exercised on the same plant. 44 machines over three floors:

    L0 Ground  - utilities room (boiler, chillers, compressors, pumps), machining hall (CNCs,
                 hydraulics, coolant, chip conveyor), receiving and logistics (buffer conveyor,
                 hoist)
    L1 First   - assembly (conveyors, robot cells, pick-and-place), coating (paint booth, oven,
                 exhaust, make-up air), test and packing
    L2 Roof    - air handlers, exhaust, cooling towers, and the electrical yard

A link `(dependent, upstream, kind)` means the dependent machine stops if its upstream does (the
repo's convention: the dependent is the "parent", its upstream the "child"). Links cross floors:
cooling towers on the roof feed chillers on the ground floor, the hoist carries parts up to
assembly.

Each machine's scenario fields decide what the platform sees:
  * `snapshot`  - which real held-out AI4I record is its latest sensor snapshot (so the risk model
                  scores a genuine failure or a genuine healthy record, never an invented value)
  * `vibration` - waveforms: "healthy" (flat) or "failing" (degrade late in the run and raise the
                  alarm). Only these two: the detector responds to late-stage degradation only
                  (the IMS growth curve is flat until about 90 % of life), so a half-worn bearing
                  is indistinguishable from a healthy one.
  * `rul_health`- how far through its life its engine-style telemetry is (gives it an RUL)
"""

from __future__ import annotations

from dataclasses import dataclass

PLAN_W, PLAN_H = 8192, 5632
PLANT_NAME = "Meridian Components Plant"
TILE_PREFIX_ROOT = "demo/meridian"

# (failure?, nth matching held-out row, failure mode or None)
Snapshot = tuple[bool, int, str | None]


@dataclass(frozen=True)
class Machine:
    key: str
    name: str
    type: str
    floor: int
    x: float
    y: float
    area: str
    snapshot: Snapshot = (False, 0, None)
    vibration: str | None = None  # None = no waveforms, "healthy", or "failing" (ramps to an alarm)
    vib_channels: int = 1
    rul_health: float | None = None
    note: str = ""


@dataclass(frozen=True)
class Area:
    floor: int
    label: str
    rect: tuple[int, int, int, int]
    color: str


@dataclass(frozen=True)
class Floor:
    level: int
    name: str


FLOORS: list[Floor] = [
    Floor(0, "Ground floor"),
    Floor(1, "First floor"),
    Floor(2, "Roof plant deck"),
]

AREAS: list[Area] = [
    Area(0, "UTILITIES ROOM", (300, 800, 2800, 3300), "#eaf2fb"),
    Area(0, "MACHINING HALL", (3100, 800, 7900, 3300), "#f1f5f9"),
    Area(0, "RECEIVING & LOGISTICS", (300, 3600, 5200, 5300), "#eef6f0"),
    Area(0, "SPARE PARTS STORE", (5500, 3600, 7900, 5300), "#faf5e8"),
    Area(1, "ASSEMBLY HALL", (300, 800, 5300, 3300), "#eef3fa"),
    Area(1, "COATING", (5600, 800, 7900, 3300), "#fdf1e3"),
    Area(1, "TEST & PACKING", (300, 3600, 7900, 5300), "#eef6f0"),
    Area(2, "HVAC PLANT", (300, 800, 4300, 3300), "#eaf2fb"),
    Area(2, "COOLING TOWERS", (4600, 800, 7900, 3300), "#e9f6f5"),
    Area(2, "ELECTRICAL YARD", (300, 3600, 7900, 5300), "#fbf3e6"),
]

MACHINES: list[Machine] = [
    # ---- L0 utilities room -------------------------------------------------------------------
    Machine("B-1", "Boiler B-1", "boiler", 0, 700, 1500, "UTILITIES ROOM", (False, 3, None)),
    Machine(
        "CH-1",
        "Chiller CH-1",
        "chiller",
        0,
        1500,
        1250,
        "UTILITIES ROOM",
        (False, 5, None),
        vibration="healthy",
    ),  # fmt: skip
    Machine(
        "CH-2",
        "Chiller CH-2",
        "chiller",
        0,
        1500,
        2150,
        "UTILITIES ROOM",
        (False, 8, None),
        vibration="healthy",
        rul_health=0.45,
        note="mid-life chiller",
    ),  # fmt: skip
    Machine(
        "CWP-1",
        "Cooling Water Pump CWP-1",
        "pump",
        0,
        700,
        2500,
        "UTILITIES ROOM",
        (False, 11, None),
        vibration="healthy",
    ),  # fmt: skip
    Machine(
        "CWP-2",
        "Cooling Water Pump CWP-2",
        "pump",
        0,
        700,
        3000,
        "UTILITIES ROOM",
        (False, 14, None),
        vibration="failing",
        vib_channels=2,
        note="early warning: bearing vibration alarm while risk is still low",
    ),  # fmt: skip
    Machine(
        "AC-1",
        "Air Compressor AC-1",
        "air_compressor",
        0,
        2300,
        1150,
        "UTILITIES ROOM",
        (True, 0, "OSF"),
        vibration="failing",
        vib_channels=2,
        rul_health=1.0,
        note="failing: overstrain failure record, alarm, ~4 cycles of life left",
    ),  # fmt: skip
    Machine(
        "AC-2",
        "Air Compressor AC-2",
        "air_compressor",
        0,
        2300,
        1900,
        "UTILITIES ROOM",
        (False, 17, None),
        vibration="healthy",
        rul_health=0.2,
    ),  # fmt: skip
    Machine(
        "AC-3",
        "Air Compressor AC-3",
        "air_compressor",
        0,
        2300,
        2650,
        "UTILITIES ROOM",
        (False, 20, None),
        vibration="healthy",
        rul_health=0.05,
    ),  # fmt: skip
    # ---- L0 machining hall -------------------------------------------------------------------
    Machine(
        "CNC-1",
        "CNC Machine CNC-1",
        "cnc_machine",
        0,
        3800,
        1500,
        "MACHINING HALL",
        (False, 23, None),
    ),
    Machine(
        "CNC-2",
        "CNC Machine CNC-2",
        "cnc_machine",
        0,
        5000,
        1500,
        "MACHINING HALL",
        (True, 0, "TWF"),
        vibration="failing",
        note="tool wear failure record, spindle vibration alarm",
    ),  # fmt: skip
    Machine(
        "CNC-3",
        "CNC Machine CNC-3",
        "cnc_machine",
        0,
        3800,
        2500,
        "MACHINING HALL",
        (False, 26, None),
    ),
    Machine(
        "CNC-4",
        "CNC Machine CNC-4",
        "cnc_machine",
        0,
        5000,
        2500,
        "MACHINING HALL",
        (False, 29, None),
    ),
    Machine(
        "HPU-1",
        "Hydraulic Power Unit HPU-1",
        "hydraulic_unit",
        0,
        6200,
        1200,
        "MACHINING HALL",
        (False, 32, None),
    ),  # fmt: skip
    Machine(
        "CP-1", "Coolant Pump CP-1", "pump", 0, 6200, 2000, "MACHINING HALL", (False, 35, None)
    ),
    Machine(
        "CC-1", "Chip Conveyor CC-1", "conveyor", 0, 7000, 2600, "MACHINING HALL", (False, 38, None)
    ),
    # ---- L0 receiving & logistics ------------------------------------------------------------
    Machine(
        "BC-1",
        "Buffer Conveyor BC-1",
        "conveyor",
        0,
        2200,
        4500,
        "RECEIVING & LOGISTICS",
        (False, 41, None),
    ),  # fmt: skip
    Machine(
        "MH-1",
        "Material Hoist MH-1",
        "hoist",
        0,
        4200,
        4500,
        "RECEIVING & LOGISTICS",
        (False, 44, None),
        note="carries parts up to the assembly floor",
    ),  # fmt: skip
    # ---- L1 assembly -------------------------------------------------------------------------
    Machine("CV-1", "Conveyor CV-1", "conveyor", 1, 1000, 1400, "ASSEMBLY HALL", (False, 47, None)),
    Machine("CV-2", "Conveyor CV-2", "conveyor", 1, 2500, 1400, "ASSEMBLY HALL", (False, 50, None)),
    Machine("CV-3", "Conveyor CV-3", "conveyor", 1, 4000, 1400, "ASSEMBLY HALL", (False, 53, None)),
    Machine(
        "R-1", "Robot Cell R-1", "robot_cell", 1, 1000, 2500, "ASSEMBLY HALL", (False, 56, None)
    ),
    Machine(
        "R-2", "Robot Cell R-2", "robot_cell", 1, 2500, 2500, "ASSEMBLY HALL", (False, 59, None)
    ),
    Machine(
        "R-3", "Robot Cell R-3", "robot_cell", 1, 4000, 2500, "ASSEMBLY HALL", (False, 62, None)
    ),
    Machine(
        "PP-1",
        "Pick and Place PP-1",
        "pick_place",
        1,
        4800,
        1950,
        "ASSEMBLY HALL",
        (False, 65, None),
    ),
    # ---- L1 coating --------------------------------------------------------------------------
    Machine("PB-1", "Paint Booth PB-1", "paint_booth", 1, 6300, 1400, "COATING", (False, 68, None)),
    Machine("CO-1", "Curing Oven CO-1", "oven", 1, 7200, 1400, "COATING", (False, 71, None)),
    Machine(
        "EF-1",
        "Exhaust Fan EF-1",
        "fan",
        1,
        6300,
        2500,
        "COATING",
        (False, 74, None),
        vibration="healthy",
    ),  # fmt: skip
    Machine(
        "AMU-1",
        "Make-up Air Unit AMU-1",
        "air_handler",
        1,
        7200,
        2500,
        "COATING",
        (False, 77, None),
    ),
    # ---- L1 test & packing -------------------------------------------------------------------
    Machine(
        "EOL-1",
        "End-of-Line Tester EOL-1",
        "tester",
        1,
        1200,
        4500,
        "TEST & PACKING",
        (False, 80, None),
    ),
    Machine(
        "LT-1", "Leak Tester LT-1", "tester", 1, 2700, 4500, "TEST & PACKING", (False, 83, None)
    ),
    Machine(
        "PK-1", "Packing Machine PK-1", "packer", 1, 4200, 4500, "TEST & PACKING", (False, 86, None)
    ),
    Machine("LB-1", "Labeller LB-1", "packer", 1, 5700, 4500, "TEST & PACKING", (False, 89, None)),
    Machine(
        "PL-1", "Palletizer PL-1", "palletizer", 1, 7000, 4500, "TEST & PACKING", (False, 92, None)
    ),
    # ---- L2 HVAC plant -----------------------------------------------------------------------
    Machine(
        "AHU-1",
        "Air Handler AHU-1",
        "air_handler",
        2,
        1100,
        1700,
        "HVAC PLANT",
        (False, 95, None),
        vibration="healthy",
    ),  # fmt: skip
    Machine(
        "AHU-2",
        "Air Handler AHU-2",
        "air_handler",
        2,
        2400,
        1700,
        "HVAC PLANT",
        (False, 98, None),
        vibration="healthy",
        note="waveforms monitored, healthy",
    ),  # fmt: skip
    Machine("EXF-1", "Exhaust Fan EXF-1", "fan", 2, 3500, 1700, "HVAC PLANT", (False, 101, None)),
    Machine(
        "CHWP-1",
        "Chilled Water Pump CHWP-1",
        "pump",
        2,
        1100,
        2700,
        "HVAC PLANT",
        (False, 104, None),
    ),
    # ---- L2 cooling towers -------------------------------------------------------------------
    Machine(
        "CT-1",
        "Cooling Tower CT-1",
        "cooling_tower",
        2,
        5400,
        1600,
        "COOLING TOWERS",
        (False, 107, None),
        vibration="healthy",
    ),  # fmt: skip
    Machine(
        "CT-2",
        "Cooling Tower CT-2",
        "cooling_tower",
        2,
        6800,
        1600,
        "COOLING TOWERS",
        (True, 0, "PWF"),
        note="power failure record",
    ),  # fmt: skip
    Machine("TP-1", "Tower Pump TP-1", "pump", 2, 5400, 2700, "COOLING TOWERS", (False, 110, None)),
    # ---- L2 electrical yard ------------------------------------------------------------------
    Machine(
        "TR-1",
        "Transformer TR-1",
        "transformer",
        2,
        1300,
        4500,
        "ELECTRICAL YARD",
        (False, 113, None),
    ),
    Machine(
        "SG-1",
        "Main Switchgear SG-1",
        "switchgear",
        2,
        2900,
        4500,
        "ELECTRICAL YARD",
        (False, 116, None),
        note="single point of failure for most of the plant",
    ),  # fmt: skip
    Machine("UPS-1", "UPS-1", "ups", 2, 4500, 4500, "ELECTRICAL YARD", (False, 119, None)),
    Machine(
        "GEN-1",
        "Standby Generator GEN-1",
        "generator",
        2,
        6500,
        4500,
        "ELECTRICAL YARD",
        (False, 122, None),
        vibration="healthy",
        rul_health=0.7,
    ),  # fmt: skip
]

# (dependent, upstream, kind): the dependent stops when its upstream does.
LINKS: list[tuple[str, str, str]] = [
    # power
    ("SG-1", "TR-1", "power"),
    ("UPS-1", "SG-1", "power"),
    *[
        (m, "SG-1", "power")
        for m in (
            "CH-1",
            "CH-2",
            "AC-1",
            "AC-2",
            "AC-3",
            "HPU-1",
            "CT-1",
            "CT-2",
            "TP-1",
            "B-1",
            "EF-1",
        )
    ],  # fmt: skip
    # cooling water (towers on the roof feed the chillers on the ground floor)
    ("TP-1", "CT-1", "water"),
    ("CH-1", "TP-1", "water"),
    ("CH-2", "TP-1", "water"),
    ("CH-1", "CWP-1", "water"),
    ("CH-2", "CWP-2", "water"),
    ("AHU-1", "CH-1", "water"),
    ("AHU-2", "CH-2", "water"),
    ("AHU-1", "CHWP-1", "water"),
    ("AHU-2", "CHWP-1", "water"),
    # machining services
    *[(c, "HPU-1", "hydraulic") for c in ("CNC-1", "CNC-2", "CNC-3", "CNC-4")],
    *[(c, "CP-1", "water") for c in ("CNC-1", "CNC-2", "CNC-3", "CNC-4")],
    ("CNC-1", "CC-1", "material"),
    ("CNC-2", "CC-1", "material"),
    # steam
    ("CO-1", "B-1", "steam"),
    # compressed air (a failing compressor takes the lines it feeds down)
    *[(m, "AC-1", "air") for m in ("R-1", "R-2", "R-3", "PP-1")],
    *[(m, "AC-2", "air") for m in ("PB-1", "LT-1")],
    *[(m, "AC-3", "air") for m in ("PK-1", "PL-1")],
    # coating
    ("PB-1", "EF-1", "exhaust"),
    ("PB-1", "AMU-1", "exhaust"),
    ("CO-1", "PB-1", "material"),
    # material flow: machining -> buffer -> hoist -> assembly -> coating/test -> packing
    ("BC-1", "CNC-3", "material"),
    ("BC-1", "CNC-4", "material"),
    ("MH-1", "BC-1", "material"),
    ("CV-1", "MH-1", "material"),
    ("CV-2", "CV-1", "material"),
    ("CV-3", "CV-2", "material"),
    ("R-1", "CV-1", "material"),
    ("R-2", "CV-2", "material"),
    ("R-3", "CV-3", "material"),
    ("PP-1", "CV-3", "material"),
    ("EOL-1", "PP-1", "material"),
    ("EOL-1", "CO-1", "material"),
    ("LT-1", "EOL-1", "material"),
    ("PK-1", "LT-1", "material"),
    ("LB-1", "PK-1", "material"),
    ("PL-1", "LB-1", "material"),
]

LINK_COLORS: dict[str, str] = {
    "power": "#dc2626",
    "water": "#2563eb",
    "hydraulic": "#7c3aed",
    "steam": "#f59e0b",
    "air": "#0891b2",
    "exhaust": "#64748b",
    "material": "#16a34a",
}

BY_KEY: dict[str, Machine] = {m.key: m for m in MACHINES}
BY_NAME: dict[str, Machine] = {m.name: m for m in MACHINES}


def machines_on(level: int) -> list[Machine]:
    return [m for m in MACHINES if m.floor == level]


def tile_prefix(level: int) -> str:
    return f"{TILE_PREFIX_ROOT}/L{level}"


def downstream_of(key: str) -> set[str]:
    """Every machine that (transitively) stops if `key` fails - the expected cascade."""
    dependents: dict[str, set[str]] = {}
    for dependent, upstream, _ in LINKS:
        dependents.setdefault(upstream, set()).add(dependent)
    seen: set[str] = set()
    stack = [key]
    while stack:
        for nxt in dependents.get(stack.pop(), ()):
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return seen
