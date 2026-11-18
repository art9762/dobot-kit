"""Offline tests must explicitly replace serial IO; never touch a real port."""

import pytest
import serial


@pytest.fixture(autouse=True)
def forbid_unmocked_serial(monkeypatch: pytest.MonkeyPatch) -> None:
    def blocked(*args: object, **kwargs: object) -> None:
        raise AssertionError("Real serial access is forbidden in offline tests")

    monkeypatch.setattr(serial, "Serial", blocked)
