import runpy
import struct
import sys

import pytest

from dobot_usb import Dobot
from dobot_usb.errors import DobotError
from tests.test_dobot import MagicianDouble


@pytest.fixture
def magician(monkeypatch: pytest.MonkeyPatch) -> MagicianDouble:
    device = MagicianDouble()
    monkeypatch.setattr("serial.Serial", device.open)
    return device


def run_example(monkeypatch: pytest.MonkeyPatch, *args: str) -> None:
    monkeypatch.setattr(sys, "argv", ["check_robot.py", "--port", "test-port", *args])
    from pathlib import Path

    runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "examples/check_robot.py"), run_name="__main__"
    )


def test_position_example_is_read_only(
    magician: MagicianDouble, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_example(monkeypatch, "position")
    assert magician.writes == []


def test_motion_example_cancel_does_not_even_change_speed(
    magician: MagicianDouble, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("builtins.input", lambda _: "no")
    run_example(monkeypatch, "motion", "--axis", "x")
    assert magician.writes == []


@pytest.mark.parametrize("offset", ["nan", "inf", "0", "6", "-6"])
def test_motion_example_bad_offset_opens_no_port(
    magician: MagicianDouble, monkeypatch: pytest.MonkeyPatch, offset: str
) -> None:
    with pytest.raises(SystemExit) as error:
        run_example(monkeypatch, "motion", "--axis", "x", f"--offset={offset}")
    assert error.value.code == 2
    assert magician.device is None


def test_motion_example_confirmation_moves_then_returns(
    magician: MagicianDouble, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("builtins.input", lambda _: "MOVE")
    monkeypatch.setattr("time.sleep", lambda _: None)
    run_example(monkeypatch, "motion", "--axis", "x", "--offset=2")
    assert [f.command for f in magician.writes] == [242, 245, 83, 240, 84, 84]
    assert moves(magician) == [(202, 5, 50, 30), (200, 5, 50, 30)]
    assert magician.pose == (200, 5, 50, 30)


def moves(magician: MagicianDouble) -> list[tuple[float, ...]]:
    return [struct.unpack("<4f", f.params[1:]) for f in magician.writes if f.command == 84]


def test_motion_example_rejects_pose_drift_during_confirmation(
    magician: MagicianDouble, monkeypatch: pytest.MonkeyPatch
) -> None:
    def confirm(_: str) -> str:
        magician.pose = (200, 10, 50, 30)
        return "MOVE"

    monkeypatch.setattr("builtins.input", confirm)
    run_example(monkeypatch, "motion", "--axis", "x")
    assert magician.writes == []


def test_motion_example_rejects_pose_drift_after_speed(
    magician: MagicianDouble, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = Dobot.speed

    def drift(robot: Dobot, percent: float) -> None:
        original(robot, percent)
        magician.pose = (200, 10, 50, 30)

    monkeypatch.setattr("builtins.input", lambda _: "MOVE")
    monkeypatch.setattr(Dobot, "speed", drift)
    with pytest.raises(DobotError, match="no move sent"):
        run_example(monkeypatch, "motion", "--axis", "x")
    assert moves(magician) == []


def test_motion_example_rejects_pose_drift_before_return(
    magician: MagicianDouble, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = Dobot.move

    def drift(robot: Dobot, x: float, y: float, z: float, angle: float | None = None) -> None:
        original(robot, x, y, z, angle)
        magician.pose = (202, 10, 50, 30)

    monkeypatch.setattr("builtins.input", lambda _: "MOVE")
    monkeypatch.setattr("time.sleep", lambda _: None)
    monkeypatch.setattr(Dobot, "move", drift)
    with pytest.raises(DobotError, match="no return move sent"):
        run_example(monkeypatch, "motion", "--axis", "x")
    assert moves(magician) == [(202, 5, 50, 30)]


def test_color_example_does_not_move_or_touch_queue(
    magician: MagicianDouble, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("time.sleep", lambda _: None)
    run_example(monkeypatch, "color", "--sensor-port=GP2", "--sensor-version=2")
    assert [f.command for f in magician.writes] == [137]
    assert [f.command for f in magician.requests].count(137) == 11
