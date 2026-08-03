# AGENTS.md

Guidance for AI agents (and humans) working in this repository.

## What this repo is

This is a **HACS custom component** that ships a fork of Home Assistant's
built-in [`tesla_fleet`](https://www.home-assistant.io/integrations/tesla_fleet)
integration. It keeps the `tesla_fleet` domain so that, when installed, it
**overrides the built-in integration** and adds two extra vehicle switches that
core does not (yet) ship:

- **Keep accessory power on** — keeps 12V accessory power available while parked.
- **Low power mode** — reduces standby power consumption while parked.

Everything else in `custom_components/tesla_fleet/` is intended to be a faithful
copy of the upstream core integration.

## Layout

```
custom_components/tesla_fleet/   # the integration (fork of HA core)
hacs.json                        # HACS metadata
README.md                        # user-facing install/usage docs
AGENTS.md / CLAUDE.md            # this file
```

## The golden rule: stay close to upstream

The only intentional differences from HA core's `tesla_fleet` are:

1. **`switch.py`** — the two extra switch descriptions
   (`vehicle_state_low_power_mode`, `vehicle_state_keep_accessory_power_on`)
   plus a `signing_required` field that gates them (in `async_setup_entry`) to
   vehicles that are command-signing capable (see #6). The commands are sent with the
   **public** `tesla-fleet-api` methods `set_low_power_mode(on)` and
   `set_keep_accessory_power_mode(on)` — do **not** reach into private
   internals (e.g. `api._command`) or hand-build protobuf. They are normal
   toggles (real state comes from `power_mode.py`), not assumed-state. These two
   are built as a fork-only subclass `TeslaFleetPowerModeSwitchEntity` that,
   after a successful command, calls `coordinator.mark_power_mode(key, value)`
   so the optimistic toggle is persisted and a cached/offline read can't revert
   it (see #4/#5).
2. **`manifest.json`** — adds a `version` field (required for custom
   components) and floors `tesla-fleet-api` to **>= 1.7.2** (the power-mode
   methods need it; older HA releases pin lower, e.g. 2026.7.2 pins 1.4.7). The
   floor never downgrades a newer core pin — see `_floor_tesla_fleet_api` in
   `apply_patches.py`.
3. **`strings.json` / `translations/en.json`** — entries for the two extra
   switches (`icons.json` is synced verbatim; the switches use default icons).
4. **`coordinator.py`** — requests the `vehicle_data_combo` endpoint and merges
   the decoded low-power / keep-accessory-power state (from `power_mode.py`)
   into the coordinator data, so the two switches show **real** state. Also adds
   `mark_power_mode(key, value)`, which the switch calls after a command to
   persist the optimistic value into both `self.data` (so the offline
   early-return, which returns `self.data` verbatim, keeps it) and the tracker.
5. **`power_mode.py`** — fork-only module (no core equivalent). Decodes the
   base64 `vehicle_data` protobuf: `charge_state` field 191 = low power,
   field 194 = keep accessory power (both undocumented in Tesla's proto, so
   decoded from raw wire format). `PowerModeTracker` only updates from a capture
   with a newer timestamp (a cached/asleep read carries a stale one) and holds
   an optimistic value from `set_optimistic` until a capture at/after the
   command confirms it — so a just-issued toggle isn't reverted by a stale/
   cached read while the car sleeps. The upstream sync leaves it untouched.
6. **`__init__.py`** — widens the `signing` check to
   `command_signing in ("required", "allowed")` (core only checks `"required"`),
   so the switches also appear on `"allowed"` vehicles like the 2025 Model 3
   (issue #19) — both values mean the vehicle supports the Vehicle Command
   Protocol. Plus a single `LOGGER.debug` line after the check that logs the raw
   per-vehicle value, so a user whose power-mode switches are still missing can
   diagnose why (issue #17). Both re-applied by `patch_init` in
   `apply_patches.py`.
7. **`README.md` / `hacs.json`** — repo packaging, not part of core.

When touching anything else, prefer syncing the file verbatim from HA core
rather than editing by hand.

## Syncing from upstream

Upstream source lives at
`home-assistant/core` → `homeassistant/components/tesla_fleet/`.

- **Sync from the HA-core RELEASE tag matching your installed HA, not `dev`.**
  `dev` references core APIs newer HA doesn't have yet (e.g.
  `device_tracker.EntityStateAttribute`), which breaks entities on released HA.
  The sync default and `apply_patches.py` target `2026.7.2`; bump both when you
  upgrade HA.
- `apply_patches.py` floors `tesla-fleet-api` to **>= 1.7.2** (power-mode
  methods), so the manifest works even when the synced core release pins lower.
- Re-apply the customizations listed above after pulling upstream files.
- A GitHub Action under `.github/workflows/` automates this sync; keep its
  patch definitions in step with the customizations above.

## Conventions

- Python style follows HA core (ruff/pylint clean; `SLF001` private-access
  lint should not be needed once the public API methods are used).
- The domain **must** remain `tesla_fleet` — changing it breaks the override.
- Power-mode state is read from the `vehicle_data_combo` protobuf snapshot
  (`power_mode.py`); the switches are normal toggles showing real state, which
  updates within a poll (~30–50s Fleet-API lag) when changed from the Tesla app
  or an automation.
