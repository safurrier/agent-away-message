# agent-away-message

<p align="center">
  <img src="docs/assets/branding/discord-application-icon.png" width="256" alt="A cursor-headed coding agent working at a retro computer beside a mountain lake">
</p>

AOL Instant Messenger-style Discord status updates for unattended Pi and Codex coding agents. Exact native lifecycle events determine presence. Generated prose is advisory. Preview is the default and never contacts Discord.

## What it looks like

![Discord Rich Presence showing coding agents at work](docs/assets/discord-rich-presence.png)

The active-agent count comes from lifecycle events. Privacy reduction happens before the model writes the sentence.

## How it works

[![Animated architecture diagram showing Pi and Codex lifecycle hooks splitting into an exact count path and a privacy-reduced status path before meeting in Discord](docs/assets/architecture/agent-away-message-flow.gif)](docs/assets/architecture/agent-away-message-flow.mp4)

The animation is a simplified overview. The lifecycle path owns the exact count. The separate candid path reduces bounded task context before the status writer produces public prose.

[Open the H.264 animation](docs/assets/architecture/agent-away-message-flow.mp4) · [View the static diagram](docs/assets/architecture/agent-away-message-flow.png) · [Edit the Excalidraw source](docs/assets/architecture/agent-away-message-flow.excalidraw)

## Quick start

agent-away-message supports macOS and Linux. You need Python 3.12 or newer, [uv](https://docs.astral.sh/uv/), and Git. The project installs from a checkout. There is no PyPI release.

```bash
git clone https://github.com/safurrier/agent-away-message.git agent-away-message
cd agent-away-message
UV_TOOL_BIN_DIR="$PWD/.bin" uv tool install --from . agent-away-message
export PATH="$PWD/.bin:$PATH"
agent-away-message --help
```

This puts the command launcher in the checkout-local `.bin` directory. `setup` can then embed that exact executable path in native integrations.

Before configuring a model, inspect the synthetic fixture:

```bash
agent-away-message --json fixture inspect tests/fixtures/dogfood-events.jsonl
# {"active_agents": 2, "records": 2, "valid": true}
```

This is a deterministic, model-free check of lifecycle reduction and the persisted record contract.

## Choose what the model can see

| Mode | Generation input |
| --- | --- |
| `generic` | The writer receives active-agent count, harness mix, and bounded recent accepted public statuses. This is the default. |
| `candid` | A first stage reduces bounded current-turn context to broad public activity. The writer receives aggregate facts, recent accepted public statuses, and admitted activities from live sessions. |

Neither mode lets generated text determine whether an agent is active.

## Configure generation

Start a loopback OpenAI-compatible server. The [local llama.cpp examples](docs/tutorials/local-qwen.md) are optional. Any compatible server on `127.0.0.1`, `localhost`, or `::1` can provide both stages.

Create `config.toml` in the checkout:

```toml
schema_version = 2
context_mode = "generic"

[stage_one]
backend = "local"
[stage_one.local]
url = "http://127.0.0.1:8012/v1"
model = "local-model"

[stage_two]
backend = "local"
[stage_two.local]
url = "http://127.0.0.1:8012/v1"
model = "local-model"
```

Check the route and integration paths without contacting the model or Discord:

```bash
agent-away-message --config "$PWD/config.toml" --json doctor
```

A trusted command adapter can replace either local stage. See the [configuration reference](docs/reference/configuration.md) for the JSON protocol, process isolation, environment allowlist, and candid consent requirement.

## Connect Pi or Codex

Install either integration independently. `setup` is additive and preserves existing hook entries.

### Pi

```bash
agent-away-message --config "$PWD/config.toml" setup \
  --pi-extension ~/.pi/agent/extensions/agent-away-message.ts
agent-away-message --config "$PWD/config.toml" --json doctor \
  --pi-extension ~/.pi/agent/extensions/agent-away-message.ts
```

### Codex

```bash
agent-away-message --config "$PWD/config.toml" setup \
  --codex-config ~/.codex/hooks.json
agent-away-message --config "$PWD/config.toml" --json doctor \
  --codex-config ~/.codex/hooks.json
```

Start or interact with a Pi or Codex session, then check the exact lifecycle state without calling a model:

```bash
agent-away-message --config "$PWD/config.toml" --json status
```

When `active_agents` is nonzero, generate a local preview:

```bash
agent-away-message --config "$PWD/config.toml" preview
```

## Publish to Discord

Create a Discord application, upload the [provided application icon](docs/assets/branding/discord-application-icon.png), and copy its Application ID. Keep the Discord desktop client running on the same machine, then start the foreground daemon:

```bash
agent-away-message --config "$PWD/config.toml" \
  --publication-mode discord daemon --discord-client-id YOUR_APPLICATION_ID
```

The daemon must remain running. See [Publish to Discord](docs/how-to/publish-discord.md) for the full setup and failure checks.

## Privacy boundary

| Destination | Data it receives |
| --- | --- |
| Local event store | Allowlisted lifecycle fields, keyed session identity, and already-admitted public activity metadata |
| Generic writer | Count, harness mix, and recent accepted public statuses |
| Candid reducer | Bounded current-turn context |
| Discord | Validated status, active-agent count, and Rich Presence presentation metadata |

Core keeps command prompts out of arguments, environment values, persistent files, and diagnostics. A trusted model server or command adapter can still log, write, or transmit its input. Core can't constrain that operator-controlled process.

## Troubleshooting

- No active lifecycle records means preview has nothing to publish.
- A missed shutdown can remain active until its lifecycle record expires, for up to 30 minutes.
- `doctor` checks configuration, routing, executable availability, and the integration paths you supply. It doesn't probe the model or Discord.
- A loopback connection failure means the compatible server isn't running at the configured URL.
- `command response invalid` means an adapter returned the wrong JSON shape. `command request failed` covers launch, timeout, and nonzero exit without exposing child output.
- Discord failures usually mean the desktop client isn't running, the Application ID is wrong, or local Discord inter-process communication is unavailable.

## Develop locally

```bash
mise run setup     # install development dependencies
mise run check     # formatting, lint, types, and deterministic tests
mise run verify    # deterministic end-to-end smoke tests
mise run dogfood   # inspect the synthetic privacy-safe fixture
```

## Read the design

- [Documentation index](docs/README.md)
- [Product and interface contract](SPEC.md)
- [Architecture and privacy boundaries](docs/explanation/architecture.md)
- [Decision ledger](docs/explanation/decision-ledger.md)
- [Prompt-style evaluation](docs/evaluations/prompt-style.md)
