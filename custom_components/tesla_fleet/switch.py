"""Switch platform for Tesla Fleet integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from itertools import chain
from typing import Any

from tesla_fleet_api.const import AutoSeat, Scope, Seat

from homeassistant.components.switch import (
    SwitchDeviceClass,
    SwitchEntity,
    SwitchEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType

from . import TeslaFleetConfigEntry
from .entity import TeslaFleetEnergyInfoEntity, TeslaFleetVehicleEntity
from .helpers import handle_command, handle_vehicle_command
from .models import TeslaFleetEnergyData, TeslaFleetVehicleData

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class TeslaFleetSwitchEntityDescription(SwitchEntityDescription):
    """Describes TeslaFleet Switch entity."""

    on_func: Callable
    off_func: Callable
    scopes: list[Scope]
    value_func: Callable[[StateType], bool] = bool
    unique_id: str | None = None
    assumed_state: bool = False


VEHICLE_DESCRIPTIONS: tuple[TeslaFleetSwitchEntityDescription, ...] = (
    TeslaFleetSwitchEntityDescription(
        key="vehicle_state_sentry_mode",
        on_func=lambda api: api.set_sentry_mode(on=True),
        off_func=lambda api: api.set_sentry_mode(on=False),
        scopes=[Scope.VEHICLE_CMDS],
    ),
    TeslaFleetSwitchEntityDescription(
        key="climate_state_auto_seat_climate_left",
        on_func=lambda api: api.remote_auto_seat_climate_request(
            AutoSeat.FRONT_LEFT, True
        ),
        off_func=lambda api: api.remote_auto_seat_climate_request(
            Seat.FRONT_LEFT, False
        ),
        scopes=[Scope.VEHICLE_CMDS],
    ),
    TeslaFleetSwitchEntityDescription(
        key="climate_state_auto_seat_climate_right",
        on_func=lambda api: api.remote_auto_seat_climate_request(
            AutoSeat.FRONT_RIGHT, True
        ),
        off_func=lambda api: api.remote_auto_seat_climate_request(
            AutoSeat.FRONT_RIGHT, False
        ),
        scopes=[Scope.VEHICLE_CMDS],
    ),
    TeslaFleetSwitchEntityDescription(
        key="climate_state_auto_steering_wheel_heat",
        on_func=lambda api: api.remote_auto_steering_wheel_heat_climate_request(
            on=True
        ),
        off_func=lambda api: api.remote_auto_steering_wheel_heat_climate_request(
            on=False
        ),
        scopes=[Scope.VEHICLE_CMDS],
    ),
    TeslaFleetSwitchEntityDescription(
        key="climate_state_defrost_mode",
        on_func=lambda api: api.set_preconditioning_max(on=True, manual_override=False),
        off_func=lambda api: api.set_preconditioning_max(
            on=False, manual_override=False
        ),
        scopes=[Scope.VEHICLE_CMDS],
    ),
    TeslaFleetSwitchEntityDescription(
        key="charge_state_charging_state",
        unique_id="charge_state_user_charge_enable_request",
        on_func=lambda api: api.charge_start(),
        off_func=lambda api: api.charge_stop(),
        value_func=lambda state: state in {"Starting", "Charging"},
        scopes=[Scope.VEHICLE_CHARGING_CMDS, Scope.VEHICLE_CMDS],
    ),
    TeslaFleetSwitchEntityDescription(
        key="vehicle_state_low_power_mode",
        on_func=lambda api: _send_power_mode_command(api, "low_power", True),
        off_func=lambda api: _send_power_mode_command(api, "low_power", False),
        scopes=[Scope.VEHICLE_CMDS],
        assumed_state=True,
    ),
    TeslaFleetSwitchEntityDescription(
        key="vehicle_state_keep_accessory_power_on",
        on_func=lambda api: _send_power_mode_command(api, "accessory", True),
        off_func=lambda api: _send_power_mode_command(api, "accessory", False),
        scopes=[Scope.VEHICLE_CMDS],
        assumed_state=True,
    ),
)


def _encode_varint(value: int) -> bytes:
    """Encode an integer as a protobuf varint."""
    result = b""
    while value > 0x7F:
        result += bytes([(value & 0x7F) | 0x80])
        value >>= 7
    return result + bytes([value])


def _build_power_mode_action(field_number: int, on: bool) -> bytes:
    """Build a serialized protobuf Action for a power mode command.

    Constructs the binary manually to avoid needing proto class definitions
    that are not present in the released tesla-fleet-api package.
    """
    # Inner message: single bool field at field number 1
    inner = b"\x08\x01" if on else b""
    # VehicleAction oneof: message field at field_number
    va_tag = _encode_varint((field_number << 3) | 2)
    va_payload = va_tag + _encode_varint(len(inner)) + inner
    # Action.vehicleAction is field 2 (message)
    return b"\x12" + _encode_varint(len(va_payload)) + va_payload


async def _send_power_mode_command(api: Any, mode: str, on: bool) -> dict[str, Any]:
    """Send a low power mode or keep accessory power mode signed command.

    These commands are only available via the Vehicle Command Protocol (signed
    protobuf), not the REST API. We build the raw protobuf bytes and send them
    directly through the signed command path.
    """
    # VehicleAction field numbers from the Tesla car_server proto:
    # setLowPowerModeAction = 130, setKeepAccessoryPowerModeAction = 138
    field_number = 130 if mode == "low_power" else 138
    payload = _build_power_mode_action(field_number, on)

    if not hasattr(api, "_command"):
        raise HomeAssistantError(
            "Keep accessory power and low power mode commands require the"
            " Vehicle Command Protocol (signed commands). Your vehicle or"
            " account configuration does not support this."
        )

    # Domain.DOMAIN_INFOTAINMENT = 3 (avoids importing the protobuf-generated module,
    # which pylint cannot inspect reliably)
    return await api._command(3, payload)  # noqa: SLF001


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TeslaFleetConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the TeslaFleet Switch platform from a config entry."""

    async_add_entities(
        chain(
            (
                TeslaFleetVehicleSwitchEntity(
                    vehicle, description, entry.runtime_data.scopes
                )
                for vehicle in entry.runtime_data.vehicles
                for description in VEHICLE_DESCRIPTIONS
            ),
            (
                TeslaFleetChargeFromGridSwitchEntity(
                    energysite,
                    entry.runtime_data.scopes,
                )
                for energysite in entry.runtime_data.energysites
                if energysite.info_coordinator.data.get("components_battery")
                and energysite.info_coordinator.data.get("components_solar")
            ),
            (
                TeslaFleetStormModeSwitchEntity(energysite, entry.runtime_data.scopes)
                for energysite in entry.runtime_data.energysites
                if energysite.info_coordinator.data.get("components_storm_mode_capable")
            ),
        )
    )


class TeslaFleetSwitchEntity(SwitchEntity):
    """Base class for all TeslaFleet switch entities."""

    _attr_device_class = SwitchDeviceClass.SWITCH
    entity_description: TeslaFleetSwitchEntityDescription


class TeslaFleetVehicleSwitchEntity(TeslaFleetVehicleEntity, TeslaFleetSwitchEntity):
    """Base class for TeslaFleet vehicle switch entities."""

    def __init__(
        self,
        data: TeslaFleetVehicleData,
        description: TeslaFleetSwitchEntityDescription,
        scopes: list[Scope],
    ) -> None:
        """Initialize the Switch."""
        self.entity_description = description
        self.scoped = any(scope in scopes for scope in description.scopes)
        super().__init__(data, description.key)
        if description.unique_id:
            self._attr_unique_id = f"{data.vin}-{description.unique_id}"
        if description.assumed_state:
            self._attr_assumed_state = True

    def _async_update_attrs(self) -> None:
        """Update the attributes of the sensor."""
        if self._value is None:
            # For assumed_state entities, keep the last known commanded state
            # rather than resetting to unknown, since the API doesn't report it.
            if not self.entity_description.assumed_state:
                self._attr_is_on = None
        else:
            self._attr_is_on = self.entity_description.value_func(self._value)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on the Switch."""
        self.raise_for_read_only(self.entity_description.scopes[0])
        await self.wake_up_if_asleep()
        await handle_vehicle_command(self.entity_description.on_func(self.api))
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the Switch."""
        self.raise_for_read_only(self.entity_description.scopes[0])
        await self.wake_up_if_asleep()
        await handle_vehicle_command(self.entity_description.off_func(self.api))
        self._attr_is_on = False
        self.async_write_ha_state()


class TeslaFleetChargeFromGridSwitchEntity(
    TeslaFleetEnergyInfoEntity, TeslaFleetSwitchEntity
):
    """Entity class for Charge From Grid switch."""

    def __init__(
        self,
        data: TeslaFleetEnergyData,
        scopes: list[Scope],
    ) -> None:
        """Initialize the Switch."""
        self.scoped = Scope.ENERGY_CMDS in scopes
        super().__init__(
            data, "components_disallow_charge_from_grid_with_solar_installed"
        )

    def _async_update_attrs(self) -> None:
        """Update the attributes of the entity."""
        # When disallow_charge_from_grid_with_solar_installed is missing, its Off.
        # But this sensor is flipped to match how the Tesla app works.
        self._attr_is_on = not self.get(self.key, False)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on the Switch."""
        self.raise_for_read_only(Scope.ENERGY_CMDS)
        await handle_command(
            self.api.grid_import_export(
                disallow_charge_from_grid_with_solar_installed=False
            )
        )
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the Switch."""
        self.raise_for_read_only(Scope.ENERGY_CMDS)
        await handle_command(
            self.api.grid_import_export(
                disallow_charge_from_grid_with_solar_installed=True
            )
        )
        self._attr_is_on = False
        self.async_write_ha_state()


class TeslaFleetStormModeSwitchEntity(
    TeslaFleetEnergyInfoEntity, TeslaFleetSwitchEntity
):
    """Entity class for Storm Mode switch."""

    def __init__(
        self,
        data: TeslaFleetEnergyData,
        scopes: list[Scope],
    ) -> None:
        """Initialize the Switch."""
        super().__init__(data, "user_settings_storm_mode_enabled")
        self.scoped = Scope.ENERGY_CMDS in scopes

    def _async_update_attrs(self) -> None:
        """Update the attributes of the sensor."""
        self._attr_available = self._value is not None
        self._attr_is_on = bool(self._value)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on the Switch."""
        self.raise_for_read_only(Scope.ENERGY_CMDS)
        await handle_command(self.api.storm_mode(enabled=True))
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the Switch."""
        self.raise_for_read_only(Scope.ENERGY_CMDS)
        await handle_command(self.api.storm_mode(enabled=False))
        self._attr_is_on = False
        self.async_write_ha_state()
