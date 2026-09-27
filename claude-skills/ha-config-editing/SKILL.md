---
name: ha-config-editing
description: Make targeted, validated Home Assistant YAML configuration changes in /config from inside Amira. Use for configuration.yaml, package includes, integrations, template sensors, helpers, YAML automations, and configuration reload or validation work.
---

# Home Assistant configuration editing (Amira)

Follow `ha-safe-operations` for access. UI-managed items live in `.storage` and change through the API, not by file edits.

## Workflow

1. Read the exact live file under `/config` and its include structure. Identify the file HA actually loads for the setting before editing.
2. Save the current content (e.g. `cp file /config/.amira-backup/<file>.<timestamp>`) and note its hash. Make the smallest targeted edit; preserve comments, ordering and unrelated configuration.
3. Resolve every referenced entity ID, service, integration name and file path against this HA. Never synthesize identifiers.
4. Validate with the real check: `POST http://supervisor/core/api/config/core/check_config` (or `ha core check`). If validation fails, restore the saved content instead of layering another speculative fix on top.
5. Apply only the required reload (domain reload service) or restart, then read the affected runtime config or entity state to confirm it took effect.
6. Re-read the live file and report its new hash.

## Include-aware editing

- Follow `!include`, package and split-domain references before editing.
- Keep one source of truth: change the file HA loads, not a parallel example or copied fragment.
- Treat package boundaries as real configuration scopes; do not move unrelated keys into `configuration.yaml` for convenience.

## Completion evidence

Report the file changed, validation result, reload/restart result, observed runtime effect and final hash.
