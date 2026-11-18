"""All motion tests use doubles; no port or robot is accessed."""

from dataclasses import dataclass
from typing import Any

import pytest

from dobotkit.models import Point, Pose
from dobotkit.motion import Motion, PTPMode


@dataclass
class Handle:
    name: str


class FakeBackend:
    def __init__(self, events: list[Any]) -> None:
        self.events = events
        self.fail: str | None = None
        self.settings: dict[str, object] = {"r_velocity": 17, "r_acceleration": 23}

    def read(self, name: str, **params: object) -> dict[str, object]:
        self.events.append(("read", name, params))
        return self.settings.copy()

    def write(self, name: str, **params: object) -> Handle:
        self.events.append(("write", name, params))
        if self.fail == name:
            raise RuntimeError("uncertain write")
        return Handle(name)


@pytest.fixture
def setup() -> tuple[Motion, FakeBackend, list[Any]]:
    events: list[Any] = []
    backend = FakeBackend(events)

    def pose() -> Pose:
        events.append("pose")
        return Pose(1, 2, 3, 44, (0, 0, 0, 0))

    motion = Motion(
        backend,
        pose,
        lambda timeout: events.append(("sync", timeout)),
        lambda: events.append("ready"),
    )
    return motion, backend, events


def test_enum_is_full_wire_table() -> None:
    assert [int(mode) for mode in PTPMode] == list(range(10))
    assert PTPMode.MOVL_INC == 7
    assert PTPMode.JUMP_MOVL_XYZ == 9


@pytest.mark.parametrize("path,mode", [("linear", 2), ("joint", 1)])
def test_absolute_mapping_and_handle(setup: Any, path: str, mode: int) -> None:
    motion, _, events = setup
    assert motion.move_to(100, 20, 30, 40, path=path, wait=False).name == "ptp"
    assert events == [
        "ready",
        (
            "write",
            "ptp",
            {
                "mode": mode,
                "x": 100.0,
                "y": 20.0,
                "z": 30.0,
                "r": 40.0,
                "wait": False,
                "timeout": 30,
            },
        ),
    ]


@pytest.mark.parametrize("path,mode", [("linear", 7), ("joint", 8)])
def test_relative_native_without_pose(setup: Any, path: str, mode: int) -> None:
    motion, _, events = setup
    motion.move_by(z=-2, r=3, path=path, wait=False)
    assert events[1][2]["mode"] == mode
    assert events[1][2]["z"] == -2
    assert len(events) == 2


@pytest.mark.parametrize("relative,mode", [(False, 4), (True, 6)])
def test_joints_mapping(setup: Any, relative: bool, mode: int) -> None:
    motion, _, events = setup
    motion.move_joints(1, 2, 3, 4, relative=relative)
    assert events[1][2]["mode"] == mode


def test_omitted_r_orders_barrier_pose_then_ready(setup: Any) -> None:
    motion, _, events = setup
    motion.move_to(1, 2, 3, timeout=4)
    assert events[:3] == [("sync", 4), "pose", "ready"]
    assert events[3][2]["r"] == 44


@pytest.mark.parametrize("target", [Point(5, 6, 7, 8), Pose(5, 6, 7, 8, (9, 10, 11, 12))])
def test_saved_target_preserves_its_own_rotation(setup: Any, target: Point | Pose) -> None:
    motion, _, events = setup
    motion.move_to(target, wait=False)
    assert events == [
        "ready",
        ("write", "ptp", dict(mode=2, x=5, y=6, z=7, r=8, wait=False, timeout=30)),
    ]


def test_mixed_or_incomplete_targets_fail_before_io(setup: Any) -> None:
    motion, _, events = setup
    with pytest.raises(TypeError, match="by itself"):
        motion.move_to(Point(5, 6, 7, 8), r=2)
    with pytest.raises(TypeError, match="x, y and z"):
        motion.move_to(5, 6)
    assert events == []


@pytest.mark.parametrize(
    "call",
    [
        lambda m: m.move_to(1, 2, 3, wait=False),
        lambda m: m.move_to(float("nan"), 2, 3),
        lambda m: m.move_to(1, 2, 3, float("inf")),
        lambda m: m.move_to(1, 2, 3, path="bad"),
        lambda m: m.move_to(1, 2, 3, path=[]),
        lambda m: m.move_to(1, 2, 3, timeout=0),
        lambda m: m.move_to(1, 2, 3, wait=1),
        lambda m: m.move_by(x=True),
        lambda m: m.move_by(z=1e100),
        lambda m: m.move_by(path="jump"),
        lambda m: m.move_joints(1, 2, 3, float("nan")),
        lambda m: m.move_joints(1, 2, 3, 4, relative=1),
        lambda m: m.home(timeout=-1),
        lambda m: m.speed(mm_s=1, mm_s2=2, deg_s=-1),
        lambda m: m.speed(mm_s=1, mm_s2=float("nan")),
        lambda m: m.jump(height=-1, z_limit=1),
        lambda m: m.jump(height=1, z_limit=float("inf")),
        lambda m: m.arc(Point(1, 2, 3, 4), Point(1, 2, 3, float("nan"))),
        lambda m: m.arc((1, 2, 3, 4), Point(1, 2, 3, 4)),
        lambda m: m.line_to(1, 2, 3, velocity=10),
        lambda m: m.line_by(x=float("nan")),
        lambda m: m.jog("x", 1, duration=0),
        lambda m: m.jog("x", 1, duration=6),
        lambda m: m.jog("x", 1, duration=float("nan")),
        lambda m: m.jog("bad", 1, duration=1),
        lambda m: m.jog("x", True, duration=1),
        lambda m: m.jog_stop(is_joint=1),
        lambda m: m.ptp(True, 1, 2, 3, 4),
        lambda m: m.ptp(10, 1, 2, 3, 4),
        lambda m: m.ptp(1.0, 1, 2, 3, 4),
    ],
)
def test_invalid_arguments_do_no_io(setup: Any, call: Any) -> None:
    motion, _, events = setup
    with pytest.raises(ValueError):
        call(motion)
    assert events == []


@pytest.mark.parametrize("mode", [0, 3, 9])
def test_jump_requires_explicit_successful_configuration(setup: Any, mode: int) -> None:
    motion, backend, events = setup
    with pytest.raises(ValueError, match="configure jump"):
        motion.ptp(mode, 1, 2, 3, 4)
    assert events == []
    motion.jump(height=20, z_limit=100)
    assert events[-1] == ("write", "ptp_jump_params", {"jump_height": 20.0, "z_limit": 100.0})
    motion.ptp(mode, 1, 2, 3, 4)
    backend.fail = "ptp_jump_params"
    with pytest.raises(RuntimeError):
        motion.jump(height=30, z_limit=110)
    before = events.copy()
    with pytest.raises(ValueError):
        motion.ptp(mode, 1, 2, 3, 4)
    assert before == events


def test_speed_preserves_rotation_and_never_changes_ratios(setup: Any) -> None:
    motion, _, events = setup
    motion.speed(mm_s=30, mm_s2=60)
    assert events == [
        ("read", "ptp_coordinate_params", {}),
        "ready",
        (
            "write",
            "ptp_coordinate_params",
            {
                "xyz_velocity": 30.0,
                "xyz_acceleration": 60.0,
                "r_velocity": 17.0,
                "r_acceleration": 23.0,
            },
        ),
    ]


def test_speed_reads_only_missing_rotation_fields(setup: Any) -> None:
    motion, _, events = setup
    motion.speed(mm_s=30, mm_s2=60, deg_s=10)
    assert events[-1][2]["r_velocity"] == 10
    assert events[-1][2]["r_acceleration"] == 23
    events.clear()
    motion.speed(mm_s=30, mm_s2=60, deg_s=10, deg_s2=20)
    assert len(events) == 2
    assert events[0] == "ready"


def test_invalid_device_rotation_is_not_written(setup: Any) -> None:
    motion, backend, events = setup
    backend.settings["r_velocity"] = float("nan")
    with pytest.raises(ValueError):
        motion.speed(mm_s=1, mm_s2=2)
    assert events == [("read", "ptp_coordinate_params", {})]


def test_home_and_arc_and_cp_payloads(setup: Any) -> None:
    motion, _, events = setup
    motion.home(wait=False, timeout=60)
    assert events[-1] == ("write", "home", {"reserved": 0, "wait": False, "timeout": 60})
    motion.arc(Point(1, 2, 3, 4), Point(5, 6, 7, 8))
    assert events[-1][2]["cir_point"] == {"x": 1.0, "y": 2.0, "z": 3.0, "r": 4.0}
    assert events[-1][2]["to_point"] == {"x": 5.0, "y": 6.0, "z": 7.0, "r": 8.0}
    motion.line_to(1, 2, 3, wait=False)
    assert events[-1][2] == {
        "mode": 1,
        "x": 1.0,
        "y": 2.0,
        "z": 3.0,
        "power": 0.0,
        "wait": False,
        "timeout": 30,
    }
    motion.line_by(z=2)
    assert events[-1][2]["mode"] == 0


@pytest.mark.parametrize(
    "axis,base,joint",
    [
        ("x", 1, False),
        ("y", 3, False),
        ("z", 5, False),
        ("r", 7, False),
        ("j1", 1, True),
        ("j2", 3, True),
        ("j3", 5, True),
        ("j4", 7, True),
    ],
)
@pytest.mark.parametrize("direction", [1, -1])
def test_bounded_jog_always_stops(
    setup: Any,
    monkeypatch: pytest.MonkeyPatch,
    axis: str,
    base: int,
    joint: bool,
    direction: int,
) -> None:
    motion, _, events = setup
    monkeypatch.setattr(
        "dobotkit.motion.time.sleep", lambda seconds: events.append(("sleep", seconds))
    )
    motion.jog(axis, direction, duration=0.1, timeout=2)
    assert events[:2] == [("sync", 2), "ready"]
    assert events[2] == (
        "write",
        "jog",
        {
            "is_joint": joint,
            "cmd": base + (direction == -1),
            "queued": False,
            "wait": True,
            "timeout": 2,
        },
    )
    assert events[3] == ("sleep", 0.1)
    assert events[4][2]["cmd"] == 0
    assert events[4][2]["queued"] is False


def test_jog_interruption_attempts_stop(setup: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    motion, _, events = setup

    def interrupted(_: float) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr("dobotkit.motion.time.sleep", interrupted)
    with pytest.raises(KeyboardInterrupt):
        motion.jog("x", 1, duration=0.1)
    assert events[-1][2]["cmd"] == 0


def test_uncertain_jog_start_still_attempts_stop(setup: Any) -> None:
    motion, backend, events = setup
    backend.fail = "jog"
    with pytest.raises(RuntimeError):
        motion.jog("x", 1, duration=0.1)
    assert [e[2]["cmd"] for e in events if isinstance(e, tuple) and e[0] == "write"] == [1, 0]


def test_alarm_blocks_action_but_not_jog_stop() -> None:
    events: list[Any] = []
    backend = FakeBackend(events)

    def alarm() -> None:
        raise RuntimeError("alarms present")

    motion = Motion(backend, lambda: Pose(0, 0, 0, 0, (0, 0, 0, 0)), lambda _: None, alarm)
    with pytest.raises(RuntimeError, match="alarms"):
        motion.move_to(1, 2, 3, 4)
    assert events == []
    motion.jog_stop()
    assert events[-1][2]["cmd"] == 0
