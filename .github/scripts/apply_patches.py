#!/usr/bin/env python3
"""Apply local customizations on top of the upstream HA core tesla_fleet files.

This script is called after downloading the upstream files to re-add the
custom switch entities (low_power_mode, keep_accessory_power_on) and the
manifest version field that are specific to this custom component.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

COMPONENT_DIR = Path("custom_components/tesla_fleet")


def patch_manifest() -> None:
    """Ensure manifest.json retains the custom component version field."""
    manifest_path = COMPONENT_DIR / "manifest.json"
    manifest = json.loads(manifest_path.read_text())

    if "version" not in manifest:
        manifest["version"] = "1.0.0"
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
        print("manifest.json: added version field")
    else:
        print("manifest.json: version field already present, skipping")


# ---------------------------------------------------------------------------
# switch.py patches
# ---------------------------------------------------------------------------

SWITCH_IMPORT = "from homeassistant.exceptions import HomeAssistantError\n"

SWITCH_ASSUMED_FIELD = "    assumed_state: bool = False\n"

SWITCH_CUSTOM_DESCRIPTIONS = """\
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
"""

SWITCH_HELPER_FUNCTIONS = '''
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
    inner = b"\\x08\\x01" if on else b""
    # VehicleAction oneof: message field at field_number
    va_tag = _encode_varint((field_number << 3) | 2)
    va_payload = va_tag + _encode_varint(len(inner)) + inner
    # Action.vehicleAction is field 2 (message)
    return b"\\x12" + _encode_varint(len(va_payload)) + va_payload


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

'''

SWITCH_ASSUMED_STATE_INIT = """\
        if description.assumed_state:
            self._attr_assumed_state = True
"""

SWITCH_ASSUMED_STATE_UPDATE = """\
            # For assumed_state entities, keep the last known commanded state
            # rather than resetting to unknown, since the API doesn't report it.
            if not self.entity_description.assumed_state:
                self._attr_is_on = None
"""


def patch_switch() -> None:
    """Re-add the custom power mode switch entities to switch.py."""
    switch_path = COMPONENT_DIR / "switch.py"
    text = switch_path.read_text()
    changed = False

    # 1. Add HomeAssistantError import if missing
    if SWITCH_IMPORT.strip() not in text:
        # Insert before the entity platform import (alphabetical order)
        insert_before = "from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback\n"
        if insert_before in text:
            text = text.replace(insert_before, SWITCH_IMPORT + insert_before)
            changed = True
            print("switch.py: added HomeAssistantError import")

    # 2. Add assumed_state field to TeslaFleetSwitchEntityDescription if missing
    if "assumed_state: bool = False" not in text:
        # Insert before the closing of the dataclass (before the last field or after unique_id)
        insert_after = "    unique_id: str | None = None\n"
        if insert_after in text:
            text = text.replace(insert_after, insert_after + SWITCH_ASSUMED_FIELD)
            changed = True
            print("switch.py: added assumed_state field to dataclass")

    # 3. Add custom switch descriptions if missing
    if "vehicle_state_low_power_mode" not in text:
        # Find the closing paren of the VEHICLE_DESCRIPTIONS tuple and insert before it.
        # The tuple ends with the last entry's closing ")," followed by a line with just ")".
        import re as _re
        # Match the closing of VEHICLE_DESCRIPTIONS: last item's closing ",\n)\n"
        m = _re.search(
            r"scopes=\[Scope\.VEHICLE_CHARGING_CMDS, Scope\.VEHICLE_CMDS\],\n    \),\n\)",
            text,
        )
        if m:
            # Insert after the "    )," line (which is m.end() - 2 to keep "\n)")
            insert_at = m.end() - len("\n)")
            text = text[:insert_at] + "\n" + SWITCH_CUSTOM_DESCRIPTIONS.rstrip("\n") + text[insert_at:]
            changed = True
            print("switch.py: added custom switch descriptions")
        else:
            print("switch.py: WARNING - could not find VEHICLE_DESCRIPTIONS end marker")

    # 4. Add helper functions if missing
    if "_encode_varint" not in text:
        # Insert before the async_setup_entry function
        marker = "\nasync def async_setup_entry(\n"
        if marker in text:
            text = text.replace(marker, SWITCH_HELPER_FUNCTIONS + marker)
            changed = True
            print("switch.py: added helper functions")

    # 5. Add assumed_state handling in __init__ if missing
    if "self._attr_assumed_state = True" not in text:
        # Insert in TeslaFleetVehicleSwitchEntity.__init__ after setting unique_id
        insert_after = "            self._attr_unique_id = f\"{data.vin}-{description.unique_id}\"\n"
        if insert_after in text:
            text = text.replace(insert_after, insert_after + SWITCH_ASSUMED_STATE_INIT)
            changed = True
            print("switch.py: added assumed_state init handling")

    # 6. Update _async_update_attrs to preserve assumed_state value if missing
    if "if not self.entity_description.assumed_state:" not in text:
        old = "            self._attr_is_on = None\n"
        # Only replace the one in _async_update_attrs (check context)
        if old in text:
            text = text.replace(old, SWITCH_ASSUMED_STATE_UPDATE, 1)
            changed = True
            print("switch.py: updated _async_update_attrs for assumed_state")

    if changed:
        switch_path.write_text(text)
    else:
        print("switch.py: no changes needed")


# ---------------------------------------------------------------------------
# strings.json and translations/en.json patches
# ---------------------------------------------------------------------------

CUSTOM_SWITCH_STRINGS_JSON = """\
      "vehicle_state_keep_accessory_power_on": {
        "name": "Keep accessory power on"
      },
      "vehicle_state_low_power_mode": {
        "name": "Low power mode"
      },
"""

CUSTOM_SWITCH_EN_JSON = """\
            "vehicle_state_keep_accessory_power_on": {
                "name": "Keep accessory power on"
            },
            "vehicle_state_low_power_mode": {
                "name": "Low power mode"
            },
"""


def _insert_custom_switch_strings(text: str, custom_block: str) -> tuple[str, bool]:
    """Insert custom switch strings before vehicle_state_sentry_mode."""
    marker = '"vehicle_state_sentry_mode"'
    if "vehicle_state_low_power_mode" in text:
        return text, False
    if marker not in text:
        print(f"  WARNING: could not find marker '{marker}', skipping")
        return text, False
    # Find the position of the marker and insert before it
    idx = text.index(marker)
    # Find the start of the line containing the marker
    line_start = text.rindex("\n", 0, idx) + 1
    text = text[:line_start] + custom_block + text[line_start:]
    return text, True


def patch_strings_json() -> None:
    """Re-add the custom switch entity string entries to strings.json."""
    path = COMPONENT_DIR / "strings.json"
    text = path.read_text()
    text, changed = _insert_custom_switch_strings(text, CUSTOM_SWITCH_STRINGS_JSON)
    if changed:
        path.write_text(text)
        print("strings.json: added custom switch entity strings")
    else:
        print("strings.json: no changes needed")


def patch_en_json() -> None:
    """Re-add the custom switch entity string entries to translations/en.json."""
    path = COMPONENT_DIR / "translations" / "en.json"
    text = path.read_text()
    text, changed = _insert_custom_switch_strings(text, CUSTOM_SWITCH_EN_JSON)
    if changed:
        path.write_text(text)
        print("translations/en.json: added custom switch entity strings")
    else:
        print("translations/en.json: no changes needed")


def sync_en_json_structure() -> None:
    """Keep translations/en.json structurally in sync with strings.json.

    When new keys are added to strings.json by an upstream sync, they need to
    appear in en.json too (even if they initially contain [%key:...%] refs).
    Similarly, keys removed from strings.json should be removed from en.json.
    This preserves existing translated text for unchanged keys.
    """
    strings_path = COMPONENT_DIR / "strings.json"
    en_path = COMPONENT_DIR / "translations" / "en.json"

    strings_data = json.loads(strings_path.read_text())
    en_data = json.loads(en_path.read_text())

    def _sync_dict(src: dict, dst: dict, path: str = "") -> bool:
        """Recursively sync src structure into dst. Returns True if changed."""
        changed = False
        # Add missing keys from src
        for key, value in src.items():
            if key not in dst:
                dst[key] = value
                print(f"  en.json: added missing key '{path}{key}'")
                changed = True
            elif isinstance(value, dict) and isinstance(dst[key], dict):
                if _sync_dict(value, dst[key], f"{path}{key}."):
                    changed = True
        # Remove keys that no longer exist in src
        for key in list(dst.keys()):
            if key not in src:
                del dst[key]
                print(f"  en.json: removed stale key '{path}{key}'")
                changed = True
        return changed

    if _sync_dict(strings_data, en_data):
        en_path.write_text(json.dumps(en_data, indent=4, ensure_ascii=False) + "\n")
        print("translations/en.json: synced structure from strings.json")
    else:
        print("translations/en.json: structure already in sync with strings.json")


def main() -> None:
    """Apply all patches."""
    if not COMPONENT_DIR.exists():
        print(f"ERROR: {COMPONENT_DIR} does not exist", file=sys.stderr)
        sys.exit(1)

    patch_manifest()
    patch_switch()
    patch_strings_json()
    patch_en_json()
    sync_en_json_structure()
    print("All patches applied successfully.")


if __name__ == "__main__":
    main()
