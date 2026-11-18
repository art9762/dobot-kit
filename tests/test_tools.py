"""Tool scenarios with an IO journal; no transport or device is constructed."""

from dataclasses import dataclass, field

import pytest

from dobot_usb.errors import ConnectionError, ProtocolError, RequestTimeout
from dobot_usb.models import ToolState
from dobot_usb.tools import Gripper, Laser, LaserNotArmed, Suction, Tool, ToolOffset


@dataclass
class Receipt:
    state: str = "accepted"


@dataclass
class FakeBackend:
    replies: dict = field(default_factory=dict)
    calls: list = field(default_factory=list)
    failure: Exception | None = None
    receipt: Receipt = field(default_factory=Receipt)

    def read(self, command_name, /, **params):
        self.calls.append(("read", command_name, params))
        if self.failure:
            raise self.failure
        return self.replies[command_name].copy()

    def write(self, command_name, /, *, wait=True, timeout=30.0, queued=None, **params):
        self.calls.append(
            ("write", command_name, params, {"wait": wait, "timeout": timeout, "queued": queued})
        )
        if self.failure:
            raise self.failure
        return self.receipt


def test_pick_and_place_preserves_receipt_and_wait_policy():
    backend = FakeBackend(replies={"suction": {"enabled": True, "active": False}})
    suction = Suction(backend)
    assert backend.calls == []
    assert suction.on(wait=False, timeout=4) is backend.receipt
    assert suction.state() == ToolState(True, False)
    suction.off()
    assert backend.calls == [
        (
            "write",
            "suction",
            {"enabled": True, "active": True},
            {"wait": False, "timeout": 4.0, "queued": None},
        ),
        ("read", "suction", {}),
        (
            "write",
            "suction",
            {"enabled": False, "active": False},
            {"wait": True, "timeout": 30.0, "queued": None},
        ),
    ]
    assert backend.receipt.state == "accepted"  # Group never invents completion.


def test_gripper_open_close_release_are_distinct():
    backend = FakeBackend()
    gripper = Gripper(backend)
    gripper.open()
    gripper.close()
    gripper.release()
    assert [call[2] for call in backend.calls] == [
        {"enabled": True, "active": False},
        {"enabled": True, "active": True},
        {"enabled": False, "active": False},
    ]


def test_tool_selection_never_changes_tcp_or_outputs():
    backend = FakeBackend(
        replies={"end_effector_params": {"x_bias": 10, "y_bias": -2, "z_bias": 3}}
    )
    tool = Tool(backend)
    assert tool.current is None
    for name in ("pen", "laser", "gripper", "suction"):
        assert tool.select(name).name == name
        assert tool.current.name == name
    assert backend.calls == []
    tool.offset(x=10, y=-2, z=3)
    assert tool.get_offset() == ToolOffset(10, -2, 3)
    assert backend.calls[0][1:3] == (
        "end_effector_params",
        {"x_bias": 10.0, "y_bias": -2.0, "z_bias": 3.0},
    )
    with pytest.raises(ValueError):
        tool.select("automatic")
    assert tool.current.name == "suction"


def test_laser_permission_and_output_are_separate():
    backend = FakeBackend(replies={"laser": {"enabled": True, "active": True}})
    laser = Laser(backend, is_session_active=lambda: True)
    with pytest.raises(LaserNotArmed):
        laser.on()
    laser.arm()
    assert laser.armed
    assert backend.calls == []
    laser.on(wait=False)
    laser.disarm()
    assert laser.state() == ToolState(True, True)  # Disarm did not assert a physical shutdown.
    with pytest.raises(LaserNotArmed):
        laser.on()
    laser.off()
    writes = [call for call in backend.calls if call[0] == "write"]
    assert [call[2] for call in writes] == [
        {"enabled": True, "active": True},
        {"enabled": False, "active": False},
    ]
    assert all(call[3]["queued"] is False for call in writes)


def test_session_loss_revokes_permission_without_a_hidden_write():
    backend = FakeBackend()
    active = True
    laser = Laser(backend, is_session_active=lambda: active)
    laser.arm()
    active = False
    assert not laser.armed
    with pytest.raises(ConnectionError):
        laser.arm()
    active = True
    with pytest.raises(LaserNotArmed):
        laser.on()
    assert backend.calls == []
    laser.arm()
    laser.invalidate()  # Owner hook also covers a disconnect not observed by callback.
    assert not laser.armed
    laser.arm()
    laser.close()
    assert not laser.armed
    assert backend.calls == []


def test_laser_off_does_not_require_permission_or_active_session():
    backend = FakeBackend()
    laser = Laser(backend, is_session_active=lambda: False)
    assert laser.off() is backend.receipt
    assert backend.calls[0][2] == {"enabled": False, "active": False}


@pytest.mark.parametrize("operation", ["on", "off", "state"])
def test_laser_backend_failure_revokes_permission_and_does_not_replay(operation):
    backend = FakeBackend(failure=RequestTimeout("uncertain result"))
    laser = Laser(backend, is_session_active=lambda: True)
    laser.arm()
    with pytest.raises(RequestTimeout, match="uncertain result"):
        getattr(laser, operation)()
    assert not laser.armed
    assert len(backend.calls) == 1


def test_session_callback_failure_revokes_permission():
    fail = False

    def session():
        if fail:
            raise ConnectionError("session unknown")
        return True

    laser = Laser(FakeBackend(), is_session_active=session)
    laser.arm()
    fail = True
    with pytest.raises(ConnectionError):
        laser.on()
    fail = False
    assert not laser.armed


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf"), True, "3", 10**500])
def test_invalid_tcp_never_reaches_backend(bad):
    backend = FakeBackend()
    with pytest.raises(ValueError):
        Tool(backend).offset(x=1, y=2, z=bad)
    assert backend.calls == []


@pytest.mark.parametrize(
    "options",
    [{"timeout": 0}, {"timeout": -1}, {"timeout": float("nan")}, {"timeout": True}, {"wait": 1}],
)
def test_invalid_execution_options_do_not_submit(options):
    backend = FakeBackend()
    with pytest.raises(ValueError):
        Suction(backend).on(**options)
    assert backend.calls == []


@pytest.mark.parametrize(
    "reply",
    [{"enabled": True}, {"enabled": 1, "active": False}, {"enabled": False, "active": "false"}],
)
def test_state_does_not_coerce_missing_or_non_boolean_fields(reply):
    with pytest.raises(ProtocolError):
        Gripper(FakeBackend(replies={"gripper": reply})).state()


def test_failed_tcp_update_does_not_change_local_profile():
    backend = FakeBackend(failure=RequestTimeout("uncertain"))
    tool = Tool(backend)
    profile = tool.select("pen")
    with pytest.raises(RequestTimeout):
        tool.offset(x=1, y=2, z=3)
    assert tool.current is profile
    assert len(backend.calls) == 1
