---
name: ha-safe-operations
description: Inspect, configure, validate and verify changes in this Home Assistant from inside Amira. Use for dashboards, HACS, integrations, add-ons/Apps, energy control, automations, entity mappings, and live Home Assistant troubleshooting.
---

# Home Assistant operations (Amira)

You run inside the Amira add-on on this Home Assistant, as root, with full access. The user's request in the Amira chat is the authorization: carry it out, then report what changed.

## Access

- Config files: `/config` (also `/share`, `/ssl`, `/media`, `/backup`, `/addons`, `/addon_configs`).
- REST: `http://supervisor/core/api/...`, WebSocket: `ws://supervisor/core/websocket`, Supervisor: `http://supervisor/...`, all with `Authorization: Bearer $SUPERVISOR_TOKEN`. The `ha` CLI, `websocat`, python3 `websocket-client`/`aiohttp` are installed.
- HAOS host itself (outside the add-on container): `hostsh '<command>'` runs as root on the host with host mounts, network and processes, e.g. `hostsh 'ls /'`, `hostsh 'journalctl -n 50'`.
- Do not edit `/config/.storage/*` files directly: HA holds them in memory and overwrites them. Change UI-managed entities, dashboards, helpers and registries through the WebSocket/REST API.

## Start from live state

1. Read the current config, entity states and registry entries involved before writing. Never guess entity IDs, services or attributes.
2. Read `home-assistant-best-practices` before editing dashboards, automations, scripts, scenes, helpers or configuration.
3. Keep secrets (tokens, passwords) out of files, chat replies and logs.

## Dashboard workflow

1. Check whether the requested HACS card/resource is already installed and current; do not reinstall it just because a link was supplied.
2. Read the card's documentation and resolve every referenced entity (state, unit, device class, availability).
3. For a new specialty view prefer a separate storage-mode dashboard with a hyphenated URL path and stable view path.
4. Write through `lovelace/config/save`, then re-read the live dashboard config and re-check every mapped entity.

## HACS, Apps and external code

Inspect the upstream repository and release/version first, and install the exact component the task needs.

## Reporting

Report the source inspected, the exact change, the observed verification result, and the resulting state (e.g. automation enabled/disabled, App running/stopped). Never claim a check succeeded without direct evidence.
