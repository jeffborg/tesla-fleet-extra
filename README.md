# Tesla Fleet - Keep Accessory Power & Low Power Mode

This is a custom component that extends the built-in Home Assistant Tesla Fleet integration with two additional switches:

- **Keep accessory power on** — Keeps 12V accessories (like a fridge) powered when the vehicle is parked
- **Low power mode** — Enables Tesla's low power mode

## Requirements

- Home Assistant 2024.x or later
- Tesla Fleet integration already configured
- A Tesla vehicle with firmware supporting these features (required: Vehicle Command Protocol / signed commands)

## Installation via HACS

1. In HACS, go to **Integrations → ⋮ → Custom repositories**
2. Add this repository URL and select category **Integration**
3. Install **Tesla Fleet (with Keep Accessory Power & Low Power Mode)**
4. Restart Home Assistant

## Notes

- These commands use the **Vehicle Command Protocol** (signed protobuf). They will not work on older vehicles that don't support signed commands, so the switches only appear on vehicles that require command signing.
- The switches show **real** on/off state, read from the vehicle's `vehicle_data` protobuf snapshot. State reflects changes made anywhere — the Tesla app, an automation, or Home Assistant — within a poll (there's a ~30–50s lag before the Fleet API reflects a change).

## Releasing a new version

Releases are created automatically by GitHub Actions when a version tag is pushed. The tag must match the `version` field in `custom_components/tesla_fleet/manifest.json`.

1. Update the version in `custom_components/tesla_fleet/manifest.json`:
   ```json
   "version": "1.0.1"
   ```
2. Commit the change:
   ```bash
   git commit -am "Bump version to 1.0.1"
   ```
3. Tag and push:
   ```bash
   git tag v1.0.1
   git push origin master --tags
   ```

GitHub Actions will validate that the tag matches the manifest version and publish a GitHub Release with auto-generated release notes. HACS installs the integration from the repository source at the tag.

> **Note:** If the tag version and manifest version do not match, the release workflow will fail before creating any release.
