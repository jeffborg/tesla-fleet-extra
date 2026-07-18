"""Behavioural tests for the custom power-mode switches.

These import the platform module, so they require Home Assistant and
tesla-fleet-api to be installed (see requirements_test.txt).
"""

from __future__ import annotations

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.tesla_fleet import switch
from tesla_fleet_api.const import Scope

CUSTOM_KEYS = (
    "vehicle_state_low_power_mode",
    "vehicle_state_keep_accessory_power_on",
)


def _description(key: str):
    return next(d for d in switch.VEHICLE_DESCRIPTIONS if d.key == key)


@pytest.mark.parametrize("key", CUSTOM_KEYS)
def test_custom_switch_is_registered_as_assumed_state(key: str) -> None:
    description = _description(key)
    assert description.assumed_state is True
    assert Scope.VEHICLE_CMDS in description.scopes


class _FakeSignedApi:
    """Stands in for a VehicleSigned api object."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, bool]] = []

    async def set_low_power_mode(self, on: bool) -> dict:
        self.calls.append(("low_power", on))
        return {"response": {"result": True}}

    async def set_keep_accessory_power_mode(self, on: bool) -> dict:
        self.calls.append(("accessory", on))
        return {"response": {"result": True}}


async def test_low_power_on_calls_public_method() -> None:
    api = _FakeSignedApi()
    result = await _description("vehicle_state_low_power_mode").on_func(api)
    assert api.calls == [("low_power", True)]
    assert result["response"]["result"] is True


async def test_keep_accessory_off_calls_public_method() -> None:
    api = _FakeSignedApi()
    await _description("vehicle_state_keep_accessory_power_on").off_func(api)
    assert api.calls == [("accessory", False)]


async def test_unsigned_vehicle_raises_home_assistant_error() -> None:
    class _UnsignedApi:
        """A plain fleet api without signed power-mode commands."""

    with pytest.raises(HomeAssistantError):
        await switch._set_power_mode(_UnsignedApi(), "set_low_power_mode", True)
