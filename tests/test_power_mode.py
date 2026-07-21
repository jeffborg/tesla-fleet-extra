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
