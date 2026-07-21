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
   vehicles that require command signing. The commands are sent with the
   **public** `tesla-fleet-api` methods `set_low_power_mode(on)` and
   `set_keep_accessory_power_mode(on)` — do **not** reach into private
   internals (e.g. `api._command`) or hand-build protobuf. They are normal
   toggles (real state comes from `power_mode.py`), not assumed-state.
2. **`manifest.json`** — adds a `version` field (required for custom
   components) and pins `tesla-fleet-api` to the same release HA core pins.
3. **`strings.json` / `translations/en.json` / `icons.json`** — entries for the
   two extra switches.
4. **`coordinator.py`** — requests the `vehicle_data_combo` endpoint and merges
   the decoded low-power / keep-accessory-power state (from `power_mode.py`)
   into the coordinator data, so the two switches show **real** state.
5. **`power_mode.py`** — fork-only module (no core equivalent). Decodes the
   base64 `vehicle_data` protobuf: `charge_state` field 191 = low power,
   field 194 = keep accessory power (both undocumented in Tesla's proto, so
   decoded from raw wire format). The upstream sync leaves it untouched.
6. **`README.md` / `hacs.json`** — repo packaging, not part of core.

When touching anything else, prefer syncing the file verbatim from HA core
rather than editing by hand.

## Syncing from upstream

Upstream source lives at
`home-assistant/core` → `homeassistant/components/tesla_fleet/`.

- Pin `tesla-fleet-api` in `manifest.json` to **the same version HA core pins**
  (check `homeassistant/components/tesla_fleet/manifest.json` on the matching
  core ref). The power-mode methods above require **`tesla-fleet-api>=1.7.2`**.
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
