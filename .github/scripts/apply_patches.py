#!/usr/bin/env python3
"""Re-apply this fork's customizations on top of freshly-synced HA core files.

The upstream sync workflow downloads ``custom_components/tesla_fleet`` verbatim
from ``home-assistant/core``. This script then re-adds the small set of changes
that make this a custom component:

* ``manifest.json`` — the ``version`` field custom components require.
* ``switch.py`` — the two extra vehicle switches (low power mode / keep
  accessory power) sent via the public ``tesla-fleet-api`` power-mode methods.
* ``strings.json`` — the entity names for those two switches.
* ``translations/en.json`` — regenerated from ``strings.json`` with all
  ``[%key:...%]`` references resolved (HA core ships this via build tooling;
  custom components must ship a resolved file themselves).

The switch patches are string based and idempotent: re-running the script is a
no-op once the customizations are present, and a missing anchor is reported
loudly (and fails the run) so upstream refactors do not silently drop a switch.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

COMPONENT_DIR = Path("custom_components/tesla_fleet")
UPSTREAM_REF = os.environ.get("UPSTREAM_REF", "dev")
RAW_BASE = (
    f"https://raw.githubusercontent.com/home-assistant/core/{UPSTREAM_REF}/homeassistant"
)


class PatchError(RuntimeError):
    """Raised when an expected anchor is missing from an upstream file."""


# ---------------------------------------------------------------------------
# manifest.json
# ---------------------------------------------------------------------------


DEFAULT_VERSION = "1.0.0"
FORK_URL = "https://github.com/jeffborg/tesla-fleet-extra"


def _committed_manifest_version() -> str | None:
    """Return the manifest ``version`` from the last commit, if any.

    The upstream download overwrites manifest.json with core's copy (which has
    no ``version``), so we recover the maintainer's released version from git
    rather than resetting it — otherwise every sync would clobber the version
    that the release workflow relies on.
    """
    rel = f"{COMPONENT_DIR.as_posix()}/manifest.json"
    try:
        out = subprocess.run(
            ["git", "show", f"HEAD:{rel}"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        return json.loads(out).get("version")
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return None


def patch_manifest() -> None:
    """Re-apply the fork's manifest fields after an upstream overwrite.

    Core's manifest points ``documentation`` at home-assistant.io, has no
    ``issue_tracker`` and no ``version``. hassfest (custom mode) requires a
    custom documentation URL, HACS requires an issue tracker, and custom
    components need a version — so re-add all three, preserving whatever
    version was previously committed (the release source of truth).
    """
    path = COMPONENT_DIR / "manifest.json"
    manifest = json.loads(path.read_text())

    changed = False
    fork_fields = {
        "documentation": FORK_URL,
        "issue_tracker": f"{FORK_URL}/issues",
    }
    for key, value in fork_fields.items():
        if manifest.get(key) != value:
            manifest[key] = value
            changed = True
    if "version" not in manifest:
        manifest["version"] = _committed_manifest_version() or DEFAULT_VERSION
        changed = True

    if not changed:
        print("manifest.json: fork fields already present")
        return

    # Canonical hassfest key order: domain and name first, then the rest sorted.
    ordered = {k: manifest.pop(k) for k in ("domain", "name") if k in manifest}
    ordered.update({k: manifest[k] for k in sorted(manifest)})
    path.write_text(json.dumps(ordered, indent=2) + "\n")
    print(f"manifest.json: applied fork fields (version {ordered.get('version')})")


# ---------------------------------------------------------------------------
# switch.py
# ---------------------------------------------------------------------------

SWITCH_SIGNING_FIELD = "    signing_required: bool = False\n"

SWITCH_CUSTOM_DESCRIPTIONS = """\
    TeslaFleetSwitchEntityDescription(
        key="vehicle_state_low_power_mode",
        on_func=lambda api: api.set_low_power_mode(on=True),
        off_func=lambda api: api.set_low_power_mode(on=False),
        scopes=[Scope.VEHICLE_CMDS],
        signing_required=True,
    ),
    TeslaFleetSwitchEntityDescription(
        key="vehicle_state_keep_accessory_power_on",
        on_func=lambda api: api.set_keep_accessory_power_mode(on=True),
        off_func=lambda api: api.set_keep_accessory_power_mode(on=False),
        scopes=[Scope.VEHICLE_CMDS],
        signing_required=True,
    ),
"""

# Only create the signed-only switches for vehicles that require command
# signing (their VehicleSigned api exposes the set_*_mode methods).
SWITCH_SETUP_FILTER = """\
                for description in VEHICLE_DESCRIPTIONS
                # Signed-only commands (low power / keep accessory power) are
                # only offered on vehicles that require command signing.
                if vehicle.signing or not description.signing_required
"""


def _replace_once(text: str, old: str, new: str, what: str) -> str:
    """Replace ``old`` with ``new`` exactly once or raise PatchError."""
    count = text.count(old)
    if count != 1:
        raise PatchError(
            f"expected exactly one anchor for {what!r}, found {count}"
        )
    return text.replace(old, new)


def patch_switch() -> None:
    """Re-add the low power / keep accessory power switch entities."""
    path = COMPONENT_DIR / "switch.py"
    text = path.read_text()

    if "vehicle_state_low_power_mode" in text:
        print("switch.py: customizations already present")
        return

    # 1. signing_required field on the description dataclass
    text = _replace_once(
        text,
        "    unique_id: str | None = None\n",
        "    unique_id: str | None = None\n" + SWITCH_SIGNING_FIELD,
        "signing_required dataclass field",
    )

    # 2. Custom switch descriptions, appended to VEHICLE_DESCRIPTIONS
    m = re.search(
        r"scopes=\[Scope\.VEHICLE_CHARGING_CMDS,\s*Scope\.VEHICLE_CMDS\],\s*\n\s*\),\n\)",
        text,
    )
    if not m:
        raise PatchError("switch.py: could not find end of VEHICLE_DESCRIPTIONS")
    closing = text.rindex(")", m.start(), m.end())
    text = text[:closing] + SWITCH_CUSTOM_DESCRIPTIONS + text[closing:]

    # 3. Only build the signed-only switches for signing-capable vehicles
    text = _replace_once(
        text,
        "                for description in VEHICLE_DESCRIPTIONS\n",
        SWITCH_SETUP_FILTER,
        "async_setup_entry signing filter",
    )

    path.write_text(text)
    print("switch.py: re-applied custom switch entities")


# ---------------------------------------------------------------------------
# coordinator.py
# ---------------------------------------------------------------------------

COORD_IMPORT = (
    "from .const import DOMAIN, ENERGY_HISTORY_FIELDS, LOGGER, TeslaFleetState\n"
)
COORD_IMPORT_NEW = (
    COORD_IMPORT + "from .power_mode import POWER_MODE_ENDPOINT, decode_power_modes\n"
)

COORD_ENDPOINTS_OLD = (
    "            response = await self.api.vehicle_data(endpoints=self.endpoints)\n"
)
COORD_ENDPOINTS_NEW = """\
            try:
                response = await self.api.vehicle_data(
                    endpoints=[*self.endpoints, POWER_MODE_ENDPOINT]
                )
            except (
                VehicleOffline,
                RateLimited,
                InvalidToken,
                OAuthExpired,
                LoginRequired,
            ):
                # Expected errors — let the outer handlers deal with them; don't
                # retry (avoids doubling API calls, e.g. while rate limited).
                raise
            except TeslaFleetError:
                # Only an unexpected error (e.g. the vehicle_data_combo endpoint
                # being rejected) reaches here; retry without it so power-mode
                # state is the only thing lost, not the whole coordinator.
                response = await self.api.vehicle_data(endpoints=self.endpoints)
"""

COORD_RETURN_OLD = (
    "                    self.update_interval = VEHICLE_WAIT\n\n        return flatten(data)\n"
)
COORD_RETURN_NEW = """\
                    self.update_interval = VEHICLE_WAIT

        # Low power / keep accessory power live only in the protobuf snapshot
        # (vehicle_data_combo endpoint), not the JSON. Decode and merge them in.
        vehicle_data_pb = data.pop("vehicle_data", None)
        result = flatten(data)
        result.update(decode_power_modes(vehicle_data_pb))
        return result
"""


def patch_coordinator() -> None:
    """Read low power / keep accessory power from the vehicle_data protobuf.

    Requests the vehicle_data_combo endpoint and merges the decoded booleans
    (from the fork-only power_mode module) into the coordinator data.
    """
    path = COMPONENT_DIR / "coordinator.py"
    text = path.read_text()
    if "from .power_mode import" in text:
        print("coordinator.py: customizations already present")
        return
    text = _replace_once(text, COORD_IMPORT, COORD_IMPORT_NEW, "power_mode import")
    text = _replace_once(
        text, COORD_ENDPOINTS_OLD, COORD_ENDPOINTS_NEW, "vehicle_data_combo endpoint"
    )
    text = _replace_once(
        text, COORD_RETURN_OLD, COORD_RETURN_NEW, "power-mode decode"
    )
    path.write_text(text)
    print("coordinator.py: re-applied power-mode reading")


# ---------------------------------------------------------------------------
# strings.json + translations/en.json
# ---------------------------------------------------------------------------

CUSTOM_SWITCH_NAMES = {
    "vehicle_state_keep_accessory_power_on": {"name": "Keep accessory power on"},
    "vehicle_state_low_power_mode": {"name": "Low power mode"},
}


def patch_strings() -> None:
    """Add the custom switch entity names to strings.json (kept sorted)."""
    path = COMPONENT_DIR / "strings.json"
    data = json.loads(path.read_text())
    switch = data.setdefault("entity", {}).setdefault("switch", {})
    added = [k for k in CUSTOM_SWITCH_NAMES if k not in switch]
    switch.update(CUSTOM_SWITCH_NAMES)
    data["entity"]["switch"] = dict(sorted(switch.items()))
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    print(
        "strings.json: "
        + (f"added {', '.join(added)}" if added else "custom names already present")
    )


# --- reference resolution for translations/en.json -------------------------

REF_RE = re.compile(r"\[%key:([^%]+)%\]")
_strings_cache: dict[str, dict] = {}


def _fetch_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310 - fixed github host
        return json.loads(resp.read().decode())


def _common_strings() -> dict:
    if "__common__" not in _strings_cache:
        _strings_cache["__common__"] = _fetch_json(f"{RAW_BASE}/strings.json").get(
            "common", {}
        )
    return _strings_cache["__common__"]


def _component_strings(name: str, self_strings: dict) -> dict:
    if name == "tesla_fleet":
        return self_strings
    if name not in _strings_cache:
        _strings_cache[name] = _fetch_json(
            f"{RAW_BASE}/components/{name}/strings.json"
        )
    return _strings_cache[name]


def _lookup(ref: str, self_strings: dict):
    parts = ref.split("::")
    if parts[0] == "common":
        base, path = _common_strings(), parts[1:]
    elif parts[0] == "component":
        base, path = _component_strings(parts[1], self_strings), parts[2:]
    else:
        raise PatchError(f"unknown translation reference namespace: {ref}")
    cur = base
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            raise PatchError(f"unresolved translation reference: [%key:{ref}%]")
        cur = cur[key]
    if not isinstance(cur, str):
        raise PatchError(f"translation reference is not a leaf string: [%key:{ref}%]")
    return cur


def _resolve_str(value: str, self_strings: dict, depth: int = 0) -> str:
    if depth > 25:
        raise PatchError(f"translation reference nested too deeply: {value}")
    while (m := REF_RE.search(value)) is not None:
        target = _resolve_str(_lookup(m.group(1), self_strings), self_strings, depth + 1)
        value = value[: m.start()] + target + value[m.end() :]
    return value


def _resolve_tree(node, self_strings):
    if isinstance(node, dict):
        return {k: _resolve_tree(v, self_strings) for k, v in node.items()}
    if isinstance(node, str):
        return _resolve_str(node, self_strings)
    return node


def generate_en_json() -> None:
    """Regenerate translations/en.json from strings.json with refs resolved."""
    strings = json.loads((COMPONENT_DIR / "strings.json").read_text())
    resolved = _resolve_tree(strings, strings)
    path = COMPONENT_DIR / "translations" / "en.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(resolved, indent=4, ensure_ascii=False) + "\n")
    print("translations/en.json: regenerated from strings.json")


def main() -> None:
    """Apply all patches."""
    if not COMPONENT_DIR.exists():
        print(f"ERROR: {COMPONENT_DIR} does not exist", file=sys.stderr)
        sys.exit(1)
    patch_manifest()
    patch_switch()
    patch_coordinator()
    patch_strings()
    generate_en_json()
    print("All patches applied successfully.")


if __name__ == "__main__":
    main()
