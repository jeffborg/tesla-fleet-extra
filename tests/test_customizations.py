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
    # 1.7.2 is the first release exposing set_low_power_mode /
    # set_keep_accessory_power_mode and the version HA core pins.
    assert manifest["requirements"] == ["tesla-fleet-api==1.7.2"]


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
