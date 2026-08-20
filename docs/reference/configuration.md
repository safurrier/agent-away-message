---
id: agent-away-message-configuration
title: Configuration reference
description: Version 2 configuration fields, generation backends, and consent rules.
---

# Configuration reference

agent-away-message reads `config.toml` from the platform configuration directory unless `--config` selects another file:

- Linux: `$XDG_CONFIG_HOME/agent-away-message/config.toml`, or `~/.config/agent-away-message/config.toml`
- macOS: `~/Library/Application Support/agent-away-message/config.toml`

An explicitly selected file must exist. Ordinary commands reject unknown fields and configuration versions other than `2`. Native hook entry points intentionally fall back to safe defaults so a bad optional configuration file can't suppress lifecycle evidence.

## Top-level fields

| Field | Required | Meaning |
| --- | --- | --- |
| `schema_version` | Yes | Must be `2`. |
| `context_mode` | No | `generic` by default, or `candid`. |
| `stage_one` | No | Reduces candid source to a proposed public activity. Defaults to a local backend. |
| `stage_two` | No | Writes the final status from public inputs. Defaults to a local backend. |
| `privacy.allow_remote_context` | No | Must be `true` before candid source can reach a command-backed stage one. Defaults to `false`. |

Publication mode is a CLI option rather than a file field. It defaults to `preview`.

## Local backend

A local backend must use credential-free loopback HTTP or HTTPS. Accepted hosts are `127.0.0.1`, `localhost`, and `::1`.

```toml
[stage_one]
backend = "local"
[stage_one.local]
url = "http://127.0.0.1:8012/v1"
model = "local-model"
timeout_seconds = 10
enable_thinking = false
```

| Field | Default | Constraint |
| --- | --- | --- |
| `url` | `http://127.0.0.1:8012/v1` | Credential-free loopback URL with no query or fragment. |
| `model` | `local-model` | Model name sent to the compatible API. |
| `timeout_seconds` | `10` | Positive number. |
| `enable_thinking` | Backend default | Optional Boolean passed to compatible clients. |
| `chat_template_kwargs` | None | Optional table passed to compatible clients. |

The same fields apply under `[stage_two.local]`.

## Command backend

A command backend runs an operator-controlled executable. Core starts it directly without a shell, uses a neutral temporary working directory, and passes an empty environment unless `pass_environment` names variables to copy.

```toml
[stage_two]
backend = "command"
[stage_two.command]
executable = "/absolute/path/to/completion-adapter"
args = []
timeout_seconds = 90
pass_environment = []
```

| Field | Default | Constraint |
| --- | --- | --- |
| `executable` | None | Required absolute path. |
| `args` | `[]` | Literal strings passed after the executable. |
| `timeout_seconds` | `10` | Positive number covering request and response transfer. |
| `pass_environment` | `[]` | Environment variable names. Core copies values only for named variables. |

Core sends one UTF-8 JSON object on standard input:

```json
{"schema_version":1,"prompt":"..."}
```

The executable must return one bounded UTF-8 JSON object on standard output:

```json
{"schema_version":1,"completion":"..."}
```

Core doesn't put the prompt in process arguments, environment values, persistent files, or diagnostics. It discards child standard error, enforces output bounds and one shared deadline, and cleans up the complete process group. It never falls back to another backend.

These controls don't constrain the executable itself. A command adapter can log, write, or transmit anything it receives.

## Candid consent

A command backend is conservatively treated as external. When candid mode uses a command-backed stage one, configuration must include:

```toml
context_mode = "candid"

[privacy]
allow_remote_context = true
```

Without that consent, commands that initialize generation fail before sending source. `doctor` can still report the resolved external route without contacting it. Stage two needs no matching consent because it receives only aggregate facts, admitted public activity, and recent accepted public statuses.

`simulate-history` is stricter: candid replay rejects a command-backed stage one before reading source history.

## CLI overrides

Global options can override the selected file for one invocation:

```bash
agent-away-message \
  --config "$PWD/config.toml" \
  --context-mode generic \
  --publication-mode preview \
  --model-url http://127.0.0.1:8012/v1 \
  --model-name local-model \
  --model-timeout 10 \
  preview
```

Model overrides apply only to stages configured with a local backend.

Run `agent-away-message --help` for the current option surface. Run `agent-away-message --config PATH --json doctor` to inspect the resolved backend kinds and disclosure state without contacting a model or Discord.
