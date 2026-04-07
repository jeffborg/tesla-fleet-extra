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

- These commands use the **Vehicle Command Protocol** (signed protobuf). They will not work on older vehicles that don't support signed commands.
- Because the Tesla API does not report the state of these settings, Home Assistant uses **assumed state** — it remembers the last command you sent. After a HA restart the state shows as unknown until toggled once.

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
   git push origin main --tags
   ```

GitHub Actions will validate that the tag matches the manifest version, package the integration into a zip, and publish a GitHub Release with auto-generated release notes.

> **Note:** If the tag version and manifest version do not match, the release workflow will fail before creating any release.
