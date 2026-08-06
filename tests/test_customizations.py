"""Static checks on the fork's intentional customizations.

These run without Home Assistant installed — they inspect the shipped files
directly, so they catch a broken sync (dropped switch, unresolved translation
reference, wrong dependency pin) even when the full test environment is not
available.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

COMPONENT = Path(__file__).parent.parent / "custom_components" / "tesla_fleet"

CUSTOM_SWITCH_KEYS = (
    "vehicle_state_low_power_mode",
    "vehicle_state_keep_accessory_power_on",
)
CUSTOM_SWITCH_NAMES = {
    "vehicle_state_low_power_mode": "Low power mode",
    "vehicle_state_keep_accessory_power_on": "Keep accessory power on",
}


def _load_json(name: str) -> dict:
    return json.loads((COMPONENT / name).read_text())


def test_manifest_pins_dependency_with_power_mode_methods() -> None:
    manifest = _load_json("manifest.json")
    # The power-mode commands (set_low_power_mode / set_keep_accessory_power_mode)
    # need tesla-fleet-api >= 1.7.2. apply_patches floors the pin to that but
    # never downgrades a newer core pin, so assert the floor rather than an exact
    # value (core has since moved past 1.7.2, e.g. 1.7.6).
    pins = [
        r for r in manifest["requirements"] if r.startswith("tesla-fleet-api==")
    ]
    assert len(pins) == 1, manifest["requirements"]
    version = tuple(int(p) for p in pins[0].split("==", 1)[1].split("."))
    assert version >= (1, 7, 2), pins[0]


def test_manifest_has_custom_component_version() -> None:
    # Required for HACS / custom-component loading; must be valid semver so the
    # release workflow's tag-vs-version check works.
    version = _load_json("manifest.json")["version"]
    assert re.fullmatch(r"\d+\.\d+\.\d+", version), version


def test_switch_source_uses_public_power_mode_api() -> None:
    src = (COMPONENT / "switch.py").read_text()
    for key in CUSTOM_SWITCH_KEYS:
        assert key in src, f"missing custom switch {key}"
    assert "set_low_power_mode" in src
    assert "set_keep_accessory_power_mode" in src
    # The private/protobuf hack must not come back.
    assert "._command(" not in src
    assert "_encode_varint" not in src
    assert "protobuf" not in src


def test_switch_persists_optimistic_power_mode_state() -> None:
    # The stale-state fix: the two power-mode switches must be the optimistic
    # subclass that records the command on the coordinator, so a cached/offline
    # read can't revert a just-issued toggle. Must survive an upstream sync.
    src = (COMPONENT / "switch.py").read_text()
    assert "class TeslaFleetPowerModeSwitchEntity" in src
    assert "self.coordinator.mark_power_mode(self.key, True)" in src
    assert "self.coordinator.mark_power_mode(self.key, False)" in src
    assert "if description.signing_required" in src


def test_coordinator_marks_optimistic_power_mode() -> None:
    # Counterpart to the switch: the coordinator persists the optimistic value
    # into both the tracker and self.data so the offline early-return keeps it.
    src = (COMPONENT / "coordinator.py").read_text()
    assert "def mark_power_mode(self, key: str, value: bool)" in src
    assert "set_optimistic(" in src


def test_power_mode_tracker_holds_pending_command() -> None:
    # The tracker must implement the optimistic/pending guard that keeps a
    # just-issued command until a fresh capture confirms it.
    src = (COMPONENT / "power_mode.py").read_text()
    assert "def set_optimistic(" in src
    assert "_pending_until" in src


def test_init_logs_per_vehicle_command_signing() -> None:
    # Fork-only diagnostic (issue #17): the raw command_signing value is logged
    # at debug so a user whose power-mode switches are missing can see what
    # Tesla returned for their VIN. Must survive an upstream sync.
    src = (COMPONENT / "__init__.py").read_text()
    assert "command_signing=%r" in src
    assert "LOGGER.debug(" in src


def test_init_treats_allowed_command_signing_as_signing() -> None:
    # Issue #19: Tesla returns command_signing="allowed" (e.g. 2025 Model 3) in
    # addition to "required"/"not_required". Both "required" and "allowed" mean
    # the vehicle supports the Vehicle Command Protocol, so the signed-only
    # power-mode switches must be offered for both. Must survive an upstream sync
    # (core only checks == "required").
    src = (COMPONENT / "__init__.py").read_text()
    assert 'product["command_signing"] in ("required", "allowed")' in src
    assert 'product["command_signing"] == "required"' not in src


def test_strings_have_custom_switch_names() -> None:
    switch = _load_json("strings.json")["entity"]["switch"]
    for key, name in CUSTOM_SWITCH_NAMES.items():
        assert switch[key]["name"] == name


def test_translations_have_custom_switch_names() -> None:
    switch = _load_json("translations/en.json")["entity"]["switch"]
    for key, name in CUSTOM_SWITCH_NAMES.items():
        assert switch[key]["name"] == name


def test_translations_have_no_unresolved_references() -> None:
    # Custom components must ship a fully resolved en.json; HA does not resolve
    # [%key:...%] references at runtime.
    text = (COMPONENT / "translations" / "en.json").read_text()
    assert not re.search(r"\[%key:", text)


def test_strings_and_translations_share_structure() -> None:
    def shape(node):
        if isinstance(node, dict):
            return {k: shape(v) for k, v in sorted(node.items())}
        return None

    assert shape(_load_json("strings.json")) == shape(_load_json("translations/en.json"))
