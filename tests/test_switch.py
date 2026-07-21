"""Behavioural tests for the custom power-mode switches.

These import the platform module, so they require Home Assistant and
tesla-fleet-api to be installed (see requirements_test.txt).
"""

from __future__ import annotations

import inspect

import pytest
from tesla_fleet_api.const import Scope
from tesla_fleet_api.tesla.vehicle.commands import Commands

from custom_components.tesla_fleet import switch

# key -> library method name the switch drives
CUSTOM_SWITCHES = {
    "vehicle_state_low_power_mode": "set_low_power_mode",
    "vehicle_state_keep_accessory_power_on": "set_keep_accessory_power_mode",
}


def _description(key: str):
    return next(d for d in switch.VEHICLE_DESCRIPTIONS if d.key == key)


@pytest.mark.parametrize("key", CUSTOM_SWITCHES)
def test_custom_switch_is_signing_gated(key: str) -> None:
    description = _description(key)
    assert description.signing_required is True
    assert Scope.VEHICLE_CMDS in description.scopes


def test_only_power_mode_switches_require_signing() -> None:
    # The signing gate in async_setup_entry must apply to exactly the two
    # signed-only switches and nothing else.
    gated = {d.key for d in switch.VEHICLE_DESCRIPTIONS if d.signing_required}
    assert gated == set(CUSTOM_SWITCHES)


def test_non_signing_vehicle_excludes_power_mode_switches() -> None:
    # Reproduces the async_setup_entry comprehension predicate for a vehicle
    # that does not require command signing (vehicle.signing is False).
    created = [
        d.key for d in switch.VEHICLE_DESCRIPTIONS if not d.signing_required
    ]
    assert not (set(created) & set(CUSTOM_SWITCHES))


class _FakeSignedApi:
    """Stands in for a VehicleSigned api object."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, bool]] = []

    async def set_low_power_mode(self, on: bool) -> dict:
        self.calls.append(("set_low_power_mode", on))
        return {"response": {"result": True}}

    async def set_keep_accessory_power_mode(self, on: bool) -> dict:
        self.calls.append(("set_keep_accessory_power_mode", on))
        return {"response": {"result": True}}


@pytest.mark.parametrize(("key", "method"), CUSTOM_SWITCHES.items())
@pytest.mark.parametrize("turn_on", [True, False])
async def test_switch_routes_to_public_method(
    key: str, method: str, turn_on: bool
) -> None:
    api = _FakeSignedApi()
    description = _description(key)
    func = description.on_func if turn_on else description.off_func
    result = await func(api)
    assert api.calls == [(method, turn_on)]
    assert result["response"]["result"] is True


@pytest.mark.parametrize("method", sorted(set(CUSTOM_SWITCHES.values())))
def test_library_methods_accept_on_kwarg(method: str) -> None:
    # Lock the contract the lambdas depend on: the real library methods are
    # signed commands that take a single ``on`` argument.
    params = inspect.signature(getattr(Commands, method)).parameters
    assert list(params) == ["self", "on"]


@pytest.mark.parametrize("key", CUSTOM_SWITCHES)
def test_switch_reflects_decoded_coordinator_state(key: str) -> None:
    # The coordinator merges the decoded protobuf booleans under the switch
    # keys; the entity must surface them as is_on, and report unknown (None)
    # when the key is absent (real state, not an assumed toggle).
    from types import SimpleNamespace

    entity = switch.TeslaFleetVehicleSwitchEntity.__new__(
        switch.TeslaFleetVehicleSwitchEntity
    )
    entity.entity_description = _description(key)
    entity.key = key
    entity._attr_is_on = None
    entity.coordinator = SimpleNamespace(data={key: True})

    entity._async_update_attrs()
    assert entity._attr_is_on is True

    entity.coordinator.data[key] = False
    entity._async_update_attrs()
    assert entity._attr_is_on is False

    # Key absent this cycle -> unknown (no assumed-state carry-over).
    entity.coordinator.data.clear()
    entity._async_update_attrs()
    assert entity._attr_is_on is None
