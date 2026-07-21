"""Tests for the vehicle_data protobuf power-mode decoder.

The module has no Home Assistant dependency, so it is imported by path and the
protobuf inputs are built synthetically (no real vehicle data / VIN needed).
"""

from __future__ import annotations

import base64
import importlib.util
from pathlib import Path

MODULE = (
    Path(__file__).parent.parent
    / "custom_components"
    / "tesla_fleet"
    / "power_mode.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("power_mode", MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pm = _load()


def _varint(n: int) -> bytes:
    out = b""
    while n > 0x7F:
        out += bytes([(n & 0x7F) | 0x80])
        n >>= 7
    return out + bytes([n])


def _field(num: int, value: int) -> bytes:
    """A varint field."""
    return _varint(num << 3) + _varint(value)


def _submessage(num: int, payload: bytes) -> bytes:
    return _varint((num << 3) | 2) + _varint(len(payload)) + payload


def _make(low=None, keep=None, include_battery=True, charge_field=3) -> str:
    inner = b""
    if include_battery:
        inner += _field(114, 47)  # battery_level — must be ignored
    if low is not None:
        inner += _field(191, low)
    if keep is not None:
        inner += _field(194, keep)
    blob = _submessage(charge_field, inner)
    return base64.b64encode(blob).decode()


def test_decodes_both_flags() -> None:
    assert pm.decode_power_modes(_make(low=1, keep=0)) == {
        "vehicle_state_low_power_mode": True,
        "vehicle_state_keep_accessory_power_on": False,
    }
    assert pm.decode_power_modes(_make(low=0, keep=1)) == {
        "vehicle_state_low_power_mode": False,
        "vehicle_state_keep_accessory_power_on": True,
    }


def test_only_present_fields_are_returned() -> None:
    assert pm.decode_power_modes(_make(low=1)) == {
        "vehicle_state_low_power_mode": True
    }
    # charge_state present but neither power field set
    assert pm.decode_power_modes(_make()) == {}


def test_ignores_other_fields_and_missing_charge_state() -> None:
    # battery_level (114) must not be mistaken for a power flag
    assert pm.decode_power_modes(_make(include_battery=True)) == {}
    # top-level message without charge_state (field 3)
    assert pm.decode_power_modes(_make(low=1, charge_field=9)) == {}


def test_bad_input_returns_empty() -> None:
    assert pm.decode_power_modes(None) == {}
    assert pm.decode_power_modes("") == {}
    assert pm.decode_power_modes("not valid base64 @@@") == {}
    assert pm.decode_power_modes(base64.b64encode(b"\xff\xff\xff").decode()) == {}


def test_malformed_protobuf_is_safe() -> None:
    # Length-delimited field claiming more bytes than present (truncated).
    truncated = _varint((3 << 3) | 2) + _varint(50) + b"\x08"
    assert pm.decode_power_modes(base64.b64encode(truncated).decode()) == {}
    # Unsupported wire type (group start = 3) inside charge_state.
    grouped = _submessage(3, _varint((5 << 3) | 3))
    assert pm.decode_power_modes(base64.b64encode(grouped).decode()) == {}
    # Truncated varint (continuation bit set, no following byte).
    bad_varint = _submessage(3, _varint((191 << 3) | 0) + b"\x80")
    assert pm.decode_power_modes(base64.b64encode(bad_varint).decode()) == {}


def test_power_field_with_wrong_wire_type_is_ignored() -> None:
    # field 191 encoded as length-delimited (wire 2) instead of a varint.
    blob = _submessage(3, _submessage(191, b"\x00"))
    assert pm.decode_power_modes(base64.b64encode(blob).decode()) == {}


def test_tracker_updates_on_fresh_capture_only() -> None:
    tracker = pm.PowerModeTracker()
    on = _make(low=1, keep=1)
    off = _make(low=0, keep=0)

    # First capture (ts=100) is trusted.
    assert tracker.update(on, 100) == {
        "vehicle_state_low_power_mode": True,
        "vehicle_state_keep_accessory_power_on": True,
    }
    # A newer capture (ts=200) with the settings off updates the state.
    assert tracker.update(off, 200) == {
        "vehicle_state_low_power_mode": False,
        "vehicle_state_keep_accessory_power_on": False,
    }
    # A STALE/cached read (older ts) must NOT flip the switches back.
    assert tracker.update(on, 150) == {
        "vehicle_state_low_power_mode": False,
        "vehicle_state_keep_accessory_power_on": False,
    }
    # Same timestamp (repeated cached read) also holds.
    assert tracker.update(on, 200) == {
        "vehicle_state_low_power_mode": False,
        "vehicle_state_keep_accessory_power_on": False,
    }


def test_tracker_holds_last_value_when_data_absent() -> None:
    tracker = pm.PowerModeTracker()
    tracker.update(_make(low=1), 100)
    # No protobuf this cycle (e.g. decode gap) -> keep last known.
    assert tracker.update(None, 200) == {"vehicle_state_low_power_mode": True}
    # A newer capture still updates.
    assert tracker.update(_make(low=0), 300) == {"vehicle_state_low_power_mode": False}


def test_tracker_updates_when_no_timestamp() -> None:
    # timestamp==0 (missing) should still update rather than get stuck.
    tracker = pm.PowerModeTracker()
    assert tracker.update(_make(low=1), 0) == {"vehicle_state_low_power_mode": True}
    assert tracker.update(_make(low=0), 0) == {"vehicle_state_low_power_mode": False}


def test_coordinator_merge_contract() -> None:
    # Mirrors what coordinator._async_update_data does: pop the protobuf, decode
    # it, and merge the booleans into the (flattened) result dict.
    data = {"charge_state": {"battery_level": 50}, "vehicle_data": _make(low=1, keep=0)}
    raw = data.pop("vehicle_data", None)
    merged: dict = {}
    merged.update(pm.decode_power_modes(raw))
    assert "vehicle_data" not in data
    assert merged["vehicle_state_low_power_mode"] is True
    assert merged["vehicle_state_keep_accessory_power_on"] is False
