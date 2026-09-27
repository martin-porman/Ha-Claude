# Ha-Claude — Amira with native Claude Code

> [!WARNING]
> **Read this before installing.**
>
> - **This edition is not suitable for people who use macOS or Windows.**
> - **This version is for people who take responsibility for their own commands and accept the risks.**
>   It gives an AI agent unrestricted root access to your Home Assistant host: no permission prompts,
>   full hardware access, the Docker API, every add-on's data, and an unauthenticated root terminal on port 7681.
>   A wrong command can break or wipe your Home Assistant installation.
> - No support, no warranty. If you are not comfortable with that, use the official add-ons instead.

Self-maintained fork of the Amira Home Assistant add-on. Amira is the chat UI inside Home Assistant; here its
`claude_code` provider runs the real Claude Code CLI with its own tools, skills and settings, and the add-on
has full access to the Home Assistant host.

Based on [Bobsilvio/ha-claude](https://github.com/Bobsilvio/ha-claude) 4.8.1. Upstream is not tracked; this repo is the source of truth.

## Layout

| Path | Contents | Goes to |
|---|---|---|
| `claude-backend/` | The add-on (slug `claude-backend`) | `/addons/claude-backend` on the HA host |
| `claude-skills/` | Claude Code skills used by Amira's Claude | `/data/claude/skills` inside the add-on |
| `amira-skills/` | Amira card skills (Mushroom, Swiss Army Knife, HTML/JS card) | `/config/amira/skills` |
| `amira-config/mcp_config.json` | Amira MCP server config | `/config/amira/mcp_config.json` |
| `claude-config/settings.json` | Claude Code user settings (theme, bypass prompt accepted) | `/data/claude/settings.json` inside the add-on |

## What differs from upstream 4.8.1

- **Native Claude Code** (`providers/claude_code.py`): with `CLAUDE_CODE_NATIVE_TOOLS=true` the CLI keeps its
  built-in tools (Bash, Read, Write, Edit, …), settings, MCP and skills and runs with
  `--dangerously-skip-permissions`, working in `/config`. Set it to `false` for the original tool-simulator mode.
- **Permissions** (`config.yaml`): `hassio_api` with role `admin`, `auth_api`, `full_access`, `docker_api`,
  all 17 `privileged` capabilities, and read/write maps for `config`, `share`, `ssl`, `media`, `backup`,
  `addons`, `all_addon_configs`. Protection mode must be switched off after install for `full_access`/`docker_api`.
- **Environment**: `IS_SANDBOX=1`, `CLAUDE_CODE_WORK_DIR=/config`, `CLAUDE_CODE_TIMEOUT=900`.
- **Root Claude terminal**: s6 service `claude-terminal` runs `ttyd` + `tmux` with Claude Code on port 7681
  (unauthenticated, full root).
- **`hostsh '<command>'`** (`rootfs/usr/local/bin/hostsh`): runs a command as root on the HAOS host through a
  short-lived privileged helper container (Docker API + `nsenter -t 1`).
- **Image tools**: `git`, `openssh-client`, `sqlite`, `websocat`, `ttyd`, `tmux`, and the `ha` CLI.

## Install / update on the HA host

The add-on is installed as a **local** add-on (store slug `local_claude-backend`), built on the HA box from this folder.

1. Copy `claude-backend/` to `/addons/claude-backend` on the HA host (e.g. through the SSH add-on).
2. To release a change, bump `version:` in `claude-backend/config.yaml`, then:
   ```sh
   ha store reload
   ha apps update local_claude-backend   # first install: ha apps install local_claude-backend
   ```
3. First install only: turn protection mode off (Settings → Add-ons → Amira → Protection mode), or with the
   add-on's own admin token: `POST http://supervisor/addons/local_claude-backend/security {"protected": false}`.
4. Copy the skills and config into place (paths in the table above).
5. Set the add-on option `claude_code_oauth_token` to a token from `claude setup-token` (Claude Pro/Max).
6. If Home Assistant still shows the old version as an available update, reload the Supervisor integration
   (Settings → Devices & services → Supervisor → Reload).

## Licenses

- Add-on code: PolyForm Noncommercial 1.0.0, see `LICENSE`.
  Required Notice: Copyright Bobsilvio (https://github.com/Bobsilvio/ha-claude)
- `claude-skills/home-assistant-best-practices`: MIT, see its `LICENSE`.
