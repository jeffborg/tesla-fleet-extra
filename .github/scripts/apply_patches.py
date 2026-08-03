#!/usr/bin/env python3
"""Re-apply this fork's customizations on top of freshly-synced HA core files.

The upstream sync workflow downloads ``custom_components/tesla_fleet`` verbatim
from ``home-assistant/core``. This script then re-adds the small set of changes
that make this a custom component:

* ``manifest.json`` — the ``version`` field custom components require.
* ``switch.py`` — the two extra vehicle switches (low power mode / keep
  accessory power) sent via the public ``tesla-fleet-api`` power-mode methods.
* ``__init__.py`` — treat ``command_signing == "allowed"`` as signing-capable
  (not just ``"required"``) so the switches appear on those vehicles, plus a
  debug log of the raw per-vehicle ``command_signing`` value so a user whose
  power-mode switches are missing can diagnose why.
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
UPSTREAM_REF = os.environ.get("UPSTREAM_REF", "2026.7.2")
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
# The power-mode switch commands (set_low_power_mode / set_keep_accessory_power_mode)
# need tesla-fleet-api >= this, which is newer than older HA releases pin. Used
# as a floor: bump the pin up to it, but never downgrade a newer core pin.
MIN_TESLA_FLEET_API = (1, 7, 2)


def _pinned_version(requirement: str) -> tuple[int, ...] | None:
    """Return the ==-pinned version tuple of a requirement, or None."""
    name, _, version = requirement.partition("==")
    if name.strip() != "tesla-fleet-api" or not version:
        return None
    try:
        return tuple(int(p) for p in version.strip().split("."))
    except ValueError:
        return None


def _floor_tesla_fleet_api(requirements: list[str]) -> list[str]:
    """Ensure tesla-fleet-api is pinned to at least MIN_TESLA_FLEET_API.

    Bumps the pin up to the floor when core pins lower (older releases pin a
    version without the power-mode methods) but keeps a newer core pin, and
    leaves any other requirements untouched.
    """
    floor = ".".join(str(p) for p in MIN_TESLA_FLEET_API)
    result = list(requirements)
    for i, req in enumerate(result):
        pinned = _pinned_version(req)
        if pinned is not None:
            if pinned < MIN_TESLA_FLEET_API:
                result[i] = f"tesla-fleet-api=={floor}"
            return result
    result.append(f"tesla-fleet-api=={floor}")
    return result


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
    floored = _floor_tesla_fleet_api(manifest.get("requirements", []))
    if manifest.get("requirements") != floored:
        manifest["requirements"] = floored
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

# Route the signed-only switches through the optimistic subclass so a
# just-issued command isn't reverted by a cached/offline read.
SWITCH_SETUP_CLASS_OLD = (
    "                TeslaFleetVehicleSwitchEntity(\n"
    "                    vehicle, description, entry.runtime_data.scopes\n"
    "                )\n"
)
SWITCH_SETUP_CLASS_NEW = (
    "                (\n"
    "                    TeslaFleetPowerModeSwitchEntity\n"
    "                    if description.signing_required\n"
    "                    else TeslaFleetVehicleSwitchEntity\n"
    "                )(vehicle, description, entry.runtime_data.scopes)\n"
)

# Subclass inserted ahead of the first energy switch class. Its real state
# comes from the lagging/cached vehicle_data protobuf, so it persists the
# optimistic value on the coordinator after a successful command.
SWITCH_POWER_MODE_ENTITY = '''\
class TeslaFleetPowerModeSwitchEntity(TeslaFleetVehicleSwitchEntity):
    """Signed low-power / keep-accessory-power switch.

    Its real state comes from the vehicle_data snapshot, which lags and is
    cached while the car sleeps. After a successful command, record the
    optimistic value on the coordinator so a later cached/offline read can't
    revert a command the user just issued until a fresh capture confirms it.
    """

    @override
    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on and record the optimistic power-mode state."""
        await super().async_turn_on(**kwargs)
        self.coordinator.mark_power_mode(self.key, True)

    @override
    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off and record the optimistic power-mode state."""
        await super().async_turn_off(**kwargs)
        self.coordinator.mark_power_mode(self.key, False)


'''

SWITCH_ENERGY_CLASS_ANCHOR = "class TeslaFleetChargeFromGridSwitchEntity(\n"


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

    # 4. Route the signed-only switches through the optimistic subclass
    text = _replace_once(
        text, SWITCH_SETUP_CLASS_OLD, SWITCH_SETUP_CLASS_NEW, "power-mode entity class"
    )

    # 5. Insert the optimistic power-mode switch subclass
    text = _replace_once(
        text,
        SWITCH_ENERGY_CLASS_ANCHOR,
        SWITCH_POWER_MODE_ENTITY + SWITCH_ENERGY_CLASS_ANCHOR,
        "power-mode switch subclass",
    )

    path.write_text(text)
    print("switch.py: re-applied custom switch entities")


# ---------------------------------------------------------------------------
# __init__.py
# ---------------------------------------------------------------------------

INIT_SIGNING_ANCHOR = '            signing = product["command_signing"] == "required"\n'
INIT_SIGNING_NEW = (
    '            signing = product["command_signing"] in ("required", "allowed")\n'
    "            # Fork-only diagnostic (issues #17 / #19): the low power / keep\n"
    "            # accessory power switches are only offered when the vehicle is\n"
    "            # command-signing capable. Tesla returns \"required\", \"allowed\", or\n"
    "            # \"not_required\"; both \"required\" and \"allowed\" mean the vehicle\n"
    "            # supports the Vehicle Command Protocol, so treat both as signing\n"
    "            # (core only checks \"required\", which hides the switches on\n"
    "            # \"allowed\" vehicles such as the 2025 Model 3). Log the raw value so\n"
    "            # a user whose switches are missing can confirm what Tesla returns\n"
    "            # for their VIN.\n"
    "            LOGGER.debug(\n"
    '                "Vehicle %s command_signing=%r -> power-mode switches %s",\n'
    "                vin,\n"
    '                product.get("command_signing"),\n'
    '                "offered" if signing else "hidden",\n'
    "            )\n"
)


def patch_init() -> None:
    """Widen the ``signing`` check and add a per-vehicle debug log.

    Upstream computes ``signing`` as ``command_signing == "required"`` and never
    logs the raw value. Tesla also returns ``"allowed"`` (e.g. the 2025 Model 3),
    which likewise means the vehicle supports the Vehicle Command Protocol, so we
    widen the check to ``in ("required", "allowed")`` (issue #19). We also add a
    debug line right after the check so a user whose power-mode switches are
    missing can tell what Tesla returned for their VIN (issue #17).
    """
    path = COMPONENT_DIR / "__init__.py"
    text = path.read_text()
    if "command_signing=%r" in text:
        print("__init__.py: customizations already present")
        return
    text = _replace_once(
        text, INIT_SIGNING_ANCHOR, INIT_SIGNING_NEW, "command_signing debug log"
    )
    path.write_text(text)
    print("__init__.py: re-applied command_signing debug log")


# ---------------------------------------------------------------------------
# coordinator.py
# ---------------------------------------------------------------------------

COORD_IMPORT = (
    "from .const import DOMAIN, ENERGY_HISTORY_FIELDS, LOGGER, TeslaFleetState\n"
)
COORD_IMPORT_NEW = (
    COORD_IMPORT + "from .power_mode import POWER_MODE_ENDPOINT, PowerModeTracker\n"
)

COORD_INIT_OLD = (
    "        self.data = flatten(product)\n"
    "        self.updated_once = False\n"
    "        self.last_active = datetime.now()"
    "  # pylint: disable=home-assistant-enforce-naive-now\n"
)
COORD_INIT_NEW = (
    "        self.data = flatten(product)\n"
    "        self.power_modes = PowerModeTracker()\n"
    "        self.updated_once = False\n"
    "        self.last_active = datetime.now()"
    "  # pylint: disable=home-assistant-enforce-naive-now\n"
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
        # (vehicle_data_combo endpoint), not the JSON. Merge in the decoded
        # state, but only from a fresh capture: an asleep car returns a cached
        # charge_state with a stale timestamp that must not flip the switches.
        vehicle_data_pb = data.pop("vehicle_data", None)
        result = flatten(data)
        timestamp = result.get("charge_state_timestamp")
        timestamp = timestamp if isinstance(timestamp, int) else 0
        result.update(self.power_modes.update(vehicle_data_pb, timestamp))
        return result
"""

# The optimistic-command persistence method, appended to the vehicle
# coordinator (right after its _async_update_data return).
COORD_MARK_OLD = "        result.update(self.power_modes.update(vehicle_data_pb, timestamp))\n        return result\n"
COORD_MARK_NEW = COORD_MARK_OLD + '''
    def mark_power_mode(self, key: str, value: bool) -> None:
        """Record an optimistic power-mode value from a just-issued command.

        The switch toggles optimistically, but its real state comes from the
        vehicle_data protobuf, which lags and is cached while the car sleeps.
        Persist the value into both the coordinator data (so the next
        offline/asleep refresh, which returns ``self.data`` verbatim, keeps it)
        and the tracker (so a cached read can't revert it until a fresh capture
        at/after the command confirms real state).
        """
        self.power_modes.set_optimistic({key: value}, int(time() * 1000))
        if self.data is not None:
            self.data[key] = value
'''


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
    text = _replace_once(text, COORD_INIT_OLD, COORD_INIT_NEW, "PowerModeTracker init")
    text = _replace_once(
        text, COORD_ENDPOINTS_OLD, COORD_ENDPOINTS_NEW, "vehicle_data_combo endpoint"
    )
    text = _replace_once(
        text, COORD_RETURN_OLD, COORD_RETURN_NEW, "power-mode decode"
    )
    text = _replace_once(
        text, COORD_MARK_OLD, COORD_MARK_NEW, "mark_power_mode method"
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
    patch_init()
    patch_coordinator()
    patch_strings()
    generate_en_json()
    print("All patches applied successfully.")


if __name__ == "__main__":
    main()
