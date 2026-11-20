import runpy
import sys

import pytest

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
    assert magician.pose == (200, 5, 50, 30)


def test_color_example_does_not_move_or_touch_queue(
    magician: MagicianDouble, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("time.sleep", lambda _: None)
    run_example(monkeypatch, "color", "--sensor-port=GP2", "--sensor-version=2")
    assert [f.command for f in magician.writes] == [137]
    assert [f.command for f in magician.requests].count(137) == 11
