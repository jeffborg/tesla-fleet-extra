"""Read low power / keep accessory power state from the vehicle_data protobuf.

These two settings are not present in the Fleet API's JSON ``vehicle_data``.
They *are* in the base64-encoded protobuf snapshot Tesla returns when the
``vehicle_data_only`` endpoint is requested (the same blob the mobile app
reads). Within that ``CarServer.VehicleData`` message:

* ``charge_state`` (top-level field 3) → field 191 = low power mode (bool)
* ``charge_state`` (top-level field 3) → field 194 = keep accessory power (bool)

Both fields are undocumented in Tesla's published ``vehicle.proto``, so we
decode them straight from the protobuf wire format rather than relying on
generated classes (which omit unknown fields). Any decode failure returns an
empty mapping, so the switches fall back to their assumed state.

This is a fork-only module — it is not part of HA core and is left untouched by
the upstream sync.
"""

from __future__ import annotations

import base64
import binascii

# Endpoint that makes the Fleet API include the base64 protobuf snapshot
# alongside the usual JSON. Passed as a raw string (the library's
# VehicleDataEndpoint enum does not define it).
POWER_MODE_ENDPOINT = "vehicle_data_only"

_CHARGE_STATE_FIELD = 3
_LOW_POWER_MODE_FIELD = 191
_KEEP_ACCESSORY_POWER_FIELD = 194

# Coordinator data keys the two switch entities read.
LOW_POWER_MODE_KEY = "vehicle_state_low_power_mode"
KEEP_ACCESSORY_POWER_KEY = "vehicle_state_keep_accessory_power_on"


def _read_varint(buf: bytes, i: int) -> tuple[int, int]:
    shift = result = 0
    while True:
        byte = buf[i]
        i += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, i
        shift += 7


def _skip(buf: bytes, i: int, wire: int) -> int:
    if wire == 0:
        _, i = _read_varint(buf, i)
    elif wire == 1:
        i += 8
    elif wire == 2:
        length, i = _read_varint(buf, i)
        i += length
    elif wire == 5:
        i += 4
    else:
        raise ValueError(f"unsupported wire type {wire}")
    return i


def _message_field(buf: bytes, target: int) -> bytes | None:
    """Return the raw bytes of a length-delimited field, or None."""
    i, n = 0, len(buf)
    while i < n:
        tag, i = _read_varint(buf, i)
        field, wire = tag >> 3, tag & 7
        if field == target and wire == 2:
            length, i = _read_varint(buf, i)
            return buf[i : i + length]
        i = _skip(buf, i, wire)
    return None


def _varint_field(buf: bytes, target: int) -> int | None:
    """Return the varint value of a field, or None if absent."""
    i, n = 0, len(buf)
    while i < n:
        tag, i = _read_varint(buf, i)
        field, wire = tag >> 3, tag & 7
        if field == target and wire == 0:
            value, _ = _read_varint(buf, i)
            return value
        i = _skip(buf, i, wire)
    return None


def decode_power_modes(vehicle_data_b64: str | None) -> dict[str, bool]:
    """Extract the two power-mode booleans from the vehicle_data protobuf.

    Returns an empty mapping if the blob is missing or cannot be decoded, so
    callers keep whatever state they already have.
    """
    if not vehicle_data_b64:
        return {}
    try:
        blob = base64.b64decode(vehicle_data_b64)
        charge_state = _message_field(blob, _CHARGE_STATE_FIELD)
        if charge_state is None:
            return {}
        low_power = _varint_field(charge_state, _LOW_POWER_MODE_FIELD)
        keep_accessory = _varint_field(charge_state, _KEEP_ACCESSORY_POWER_FIELD)
    except (ValueError, IndexError, binascii.Error):
        return {}

    result: dict[str, bool] = {}
    if low_power is not None:
        result[LOW_POWER_MODE_KEY] = bool(low_power)
    if keep_accessory is not None:
        result[KEEP_ACCESSORY_POWER_KEY] = bool(keep_accessory)
    return result
