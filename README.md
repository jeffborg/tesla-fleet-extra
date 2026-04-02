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
