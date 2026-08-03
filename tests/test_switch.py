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


def test_power_mode_switches_use_optimistic_subclass() -> None:
    # The two signed power-mode switches must be the optimistic subclass so a
    # cached/offline read can't revert a just-issued command; the other
    # (JSON-backed) switches stay on the base class.
    assert issubclass(
        switch.TeslaFleetPowerModeSwitchEntity, switch.TeslaFleetVehicleSwitchEntity
    )
    cls_for = {
        d.key: (
            switch.TeslaFleetPowerModeSwitchEntity
            if d.signing_required
            else switch.TeslaFleetVehicleSwitchEntity
        )
        for d in switch.VEHICLE_DESCRIPTIONS
    }
    for key in CUSTOM_SWITCHES:
        assert cls_for[key] is switch.TeslaFleetPowerModeSwitchEntity
    assert cls_for["vehicle_state_sentry_mode"] is switch.TeslaFleetVehicleSwitchEntity


@pytest.mark.parametrize("key", CUSTOM_SWITCHES)
@pytest.mark.parametrize("turn_on", [True, False])
async def test_power_mode_switch_marks_optimistic_state(
    key: str, turn_on: bool
) -> None:
    # After a successful command the switch must persist the optimistic value on
    # the coordinator (both the tracker and the data), or the next offline poll
    # reverts it — the reported stale-state bug.
    from types import SimpleNamespace

    marks: list[tuple[str, bool]] = []
    coordinator = SimpleNamespace(
        data={key: not turn_on},
        mark_power_mode=lambda k, v: marks.append((k, v)),
    )

    entity = switch.TeslaFleetPowerModeSwitchEntity.__new__(
        switch.TeslaFleetPowerModeSwitchEntity
    )
    entity.entity_description = _description(key)
    entity.key = key
    entity.coordinator = coordinator
    entity.scoped = True
    entity.api = _FakeSignedApi()
    entity.raise_for_read_only = lambda scope: None
    entity.wake_up_if_asleep = _noop
    entity.async_write_ha_state = lambda: None

    if turn_on:
        await entity.async_turn_on()
    else:
        await entity.async_turn_off()

    assert entity._attr_is_on is turn_on
    assert marks == [(key, turn_on)]


async def _noop(*args, **kwargs) -> None:
    return None


def test_coordinator_mark_power_mode_persists_both_places() -> None:
    # End-to-end contract for the stale-state fix: mark_power_mode must update
    # self.data (so the offline early-return, which returns self.data verbatim,
    # keeps the value) AND the tracker (so a cached read can't revert it).
    from custom_components.tesla_fleet import coordinator as coord
    from custom_components.tesla_fleet.power_mode import PowerModeTracker

    key = "vehicle_state_keep_accessory_power_on"
    c = coord.TeslaFleetVehicleDataCoordinator.__new__(
        coord.TeslaFleetVehicleDataCoordinator
    )
    c.power_modes = PowerModeTracker()
    c.data = {key: True}  # last known real state: on

    c.mark_power_mode(key, False)

    # self.data reflects the optimistic off (offline refresh returns this).
    assert c.data[key] is False
    # A stale/cached read (old timestamp) via the tracker cannot revert it.
    from tests.test_power_mode import _make

    assert c.power_modes.update(_make(keep=1), 1) == {key: False}


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
