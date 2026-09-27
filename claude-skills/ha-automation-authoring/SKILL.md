---
name: ha-automation-authoring
description: Inspect, create, modify, or debug Home Assistant automations and scripts from inside Amira. Use for triggers, conditions, actions, entity mappings, automation YAML, script edits, and automation validation.
---

# Home Assistant automation authoring (Amira)

Follow `ha-safe-operations` for access and `home-assistant-best-practices` for HA constructs. Work from the exact automation or script source in this HA, never from a name or entity inferred from memory.

## Workflow

1. Classify the request as a new automation, a targeted change to an existing one, or diagnosis. A request to add/create stays a creation task unless the user names an existing target.
2. Resolve each entity, service, helper and device capability live. Check state, units, availability and valid service fields before composing YAML.
3. Read the complete current configuration of existing targets (`GET /api/config/automation/config/<id>` or the YAML file). For a change, state the precise trigger, condition and action delta.
4. Keep conditions before actions. Values produced by an action cannot drive an earlier condition; put response-dependent branching after the action with `choose` or `if`.
5. Use native HA YAML structures, explicit `entity_id` targets, and stable aliases/IDs. Keep actions deterministic and avoid duplicate equivalent calls.
6. Save through the real path (`POST /api/config/automation/config/<id>` or the YAML file plus `automation.reload`), then re-read the result and check its enabled state separately from its configuration.

## Diagnostics

- Start with traces (WebSocket `trace/list`, `trace/get`), logs, current state and recent history for the affected trigger.
- Separate a trigger failure, a condition failure, a service/action failure and entity unavailability in the report.
- Reuse state and config already read in the same task; do not re-read without a changed premise.

## Completion evidence

Report the target ID, resolved dependencies, exact behavioural change, validation result and final enabled state. A dashboard check is not automation verification.
