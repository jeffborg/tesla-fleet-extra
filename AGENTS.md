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
   plus the `assumed_state` handling they need. The commands are sent with the
   **public** `tesla-fleet-api` methods `set_low_power_mode(on)` and
   `set_keep_accessory_power_mode(on)` — do **not** reach into private
   internals (e.g. `api._command`) or hand-build protobuf.
2. **`manifest.json`** — adds a `version` field (required for custom
   components) and pins `tesla-fleet-api` to the same release HA core pins.
3. **`strings.json` / `translations/en.json` / `icons.json`** — entries for the
   two extra switches.
4. **`README.md` / `hacs.json`** — repo packaging, not part of core.

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
- Because Tesla's API does not report power-mode state, those two switches use
  **assumed state** (they remember the last commanded value).
