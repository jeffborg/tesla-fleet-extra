"""Tests for the upstream-sync patch script (.github/scripts/apply_patches.py).

The script has no Home Assistant dependency, so these tests import it directly
by path. Network access is avoided by stubbing the upstream fetch.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent
SCRIPT = REPO / ".github" / "scripts" / "apply_patches.py"


def _load():
    spec = importlib.util.spec_from_file_location("apply_patches", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ap = _load()


def test_replace_once_requires_single_match() -> None:
    assert ap._replace_once("x a y", "a", "b", "t") == "x b y"
    with pytest.raises(ap.PatchError):
        ap._replace_once("a a", "a", "b", "t")
    with pytest.raises(ap.PatchError):
        ap._replace_once("none", "a", "b", "t")


def test_reference_resolver(monkeypatch) -> None:
    ap._strings_cache.clear()

    def fake_fetch(url: str) -> dict:
        if url.endswith("/components/climate/strings.json"):
            return {"title": "Climate"}
        if url.endswith("/strings.json"):  # homeassistant/strings.json
            return {
                "common": {
                    "state": {"charging": "Charging"},
                    "generic": {"nested": "[%key:common::state::charging%]"},
                }
            }
        raise AssertionError(f"unexpected fetch: {url}")

    monkeypatch.setattr(ap, "_fetch_json", fake_fetch)
    self_strings = {"entity": {"switch": {"x": {"name": "Local"}}}}

    assert ap._resolve_str("[%key:common::state::charging%]", self_strings) == "Charging"
    assert ap._resolve_str("[%key:component::climate::title%]", self_strings) == "Climate"
    # A reference whose target is itself a reference resolves transitively.
    assert ap._resolve_str("[%key:common::generic::nested%]", self_strings) == "Charging"
    # References to our own component resolve against the passed-in strings.
    resolved = ap._resolve_str(
        "[%key:component::tesla_fleet::entity::switch::x::name%]", self_strings
    )
    assert resolved == "Local"
    # References embedded in surrounding text are substituted in place.
    assert (
        ap._resolve_str("a [%key:component::climate::title%] b", self_strings)
        == "a Climate b"
    )
    with pytest.raises(ap.PatchError):
        ap._resolve_str("[%key:bogus::x%]", self_strings)
    # A reference to a missing key fails loudly as a PatchError, not KeyError.
    with pytest.raises(ap.PatchError):
        ap._resolve_str("[%key:common::state::nope%]", self_strings)


def test_manifest_patch_adds_default_version_and_is_idempotent(
    tmp_path, monkeypatch
) -> None:
    comp = tmp_path / "tesla_fleet"
    comp.mkdir()
    (comp / "manifest.json").write_text(
        json.dumps({"domain": "tesla_fleet", "requirements": ["tesla-fleet-api==1.7.2"]})
    )
    monkeypatch.setattr(ap, "COMPONENT_DIR", comp)
    # No previously-committed version -> falls back to the default.
    monkeypatch.setattr(ap, "_committed_manifest_version", lambda: None)

    ap.patch_manifest()
    assert json.loads((comp / "manifest.json").read_text())["version"] == "1.0.0"

    once = (comp / "manifest.json").read_text()
    ap.patch_manifest()  # second run must not change anything
    assert (comp / "manifest.json").read_text() == once


def test_manifest_patch_preserves_committed_version(tmp_path, monkeypatch) -> None:
    # A sync must not reset a released version back to the default.
    comp = tmp_path / "tesla_fleet"
    comp.mkdir()
    (comp / "manifest.json").write_text(
        json.dumps({"domain": "tesla_fleet", "requirements": ["tesla-fleet-api==1.7.2"]})
    )
    monkeypatch.setattr(ap, "COMPONENT_DIR", comp)
    monkeypatch.setattr(ap, "_committed_manifest_version", lambda: "1.4.2")

    ap.patch_manifest()
    assert json.loads((comp / "manifest.json").read_text())["version"] == "1.4.2"


def test_manifest_patch_applies_fork_fields(tmp_path, monkeypatch) -> None:
    # documentation must be a custom URL (hassfest) and issue_tracker present
    # (HACS); both must survive an upstream overwrite.
    comp = tmp_path / "tesla_fleet"
    comp.mkdir()
    (comp / "manifest.json").write_text(
        json.dumps(
            {
                "domain": "tesla_fleet",
                "name": "Tesla Fleet",
                "documentation": "https://www.home-assistant.io/integrations/tesla_fleet",
                "requirements": ["tesla-fleet-api==1.7.2"],
            }
        )
    )
    monkeypatch.setattr(ap, "COMPONENT_DIR", comp)
    monkeypatch.setattr(ap, "_committed_manifest_version", lambda: None)

    ap.patch_manifest()
    data = json.loads((comp / "manifest.json").read_text())
    assert data["documentation"] == ap.FORK_URL
    assert data["issue_tracker"] == f"{ap.FORK_URL}/issues"
    # domain/name first, then the remaining keys in sorted order.
    keys = list(data)
    assert keys[:2] == ["domain", "name"]
    assert keys[2:] == sorted(keys[2:])


def test_switch_patch_is_idempotent_on_shipped_file(tmp_path, monkeypatch) -> None:
    comp = tmp_path / "tesla_fleet"
    comp.mkdir()
    shipped = (REPO / "custom_components" / "tesla_fleet" / "switch.py").read_text()
    (comp / "switch.py").write_text(shipped)
    monkeypatch.setattr(ap, "COMPONENT_DIR", comp)

    ap.patch_switch()  # already customized -> no-op
    assert (comp / "switch.py").read_text() == shipped


def test_switch_patch_reinjects_customizations(tmp_path, monkeypatch) -> None:
    # Golden copy of HA core's pristine switch.py (no customizations). This is
    # the whole point of the sync automation: re-add the switches afterwards.
    comp = tmp_path / "tesla_fleet"
    comp.mkdir()
    pristine = (Path(__file__).parent / "fixtures" / "core_switch.py").read_text()
    assert "vehicle_state_low_power_mode" not in pristine
    (comp / "switch.py").write_text(pristine)
    monkeypatch.setattr(ap, "COMPONENT_DIR", comp)

    ap.patch_switch()
    result = (comp / "switch.py").read_text()

    # Every anchor the patcher targets must have landed.
    assert "vehicle_state_low_power_mode" in result
    assert "vehicle_state_keep_accessory_power_on" in result
    assert "signing_required: bool = False" in result
    assert "api.set_low_power_mode(on=True)" in result
    assert "api.set_keep_accessory_power_mode(on=False)" in result
    assert "if vehicle.signing or not description.signing_required" in result
    assert "if description.assumed_state:" in result
    assert "if not self.entity_description.assumed_state:" in result
    # The result must be valid Python.
    compile(result, "switch.py", "exec")

    # And re-running the patcher is a no-op.
    ap.patch_switch()
    assert (comp / "switch.py").read_text() == result


def test_switch_patch_fails_loudly_on_missing_anchor(tmp_path, monkeypatch) -> None:
    comp = tmp_path / "tesla_fleet"
    comp.mkdir()
    (comp / "switch.py").write_text("# an upstream file with no known anchors\n")
    monkeypatch.setattr(ap, "COMPONENT_DIR", comp)

    with pytest.raises(ap.PatchError):
        ap.patch_switch()
