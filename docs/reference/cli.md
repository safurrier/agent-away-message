---
id: agent-away-message-cli-reference
title: Command reference
description: Command purpose, inputs, output, and side effects.
---

# Command reference

Global options such as `--config`, `--state-dir`, `--context-mode`, `--publication-mode`, and `--json` appear before the command:

```bash
agent-away-message --config "$PWD/config.toml" --json status
```

Use `agent-away-message COMMAND --help` for current arguments and bounds.

| Command | Purpose | Model or Discord call | Writes |
| --- | --- | --- | --- |
| `fixture inspect PATH` | Validate a synthetic lifecycle JSON Lines fixture and return aggregate counts. | Neither | Nothing |
| `doctor` | Report safe configuration and supplied integration readiness. | Neither | Nothing |
| `status` | Reduce persisted lifecycle records to current count and harness mix. | Neither | Nothing |
| `discord-accounts` | List accounts reachable through local Discord IPC for an application ID. | Local Discord IPC handshake only; no presence update | Nothing |
| `setup` | Add Codex hooks or install the packaged Pi extension. | Neither | Integration files unless `--dry-run` |
| `ingest` | Append one exact lifecycle event and an optional already-public activity hint. | Neither | Lifecycle state, including the validated public hint when supplied |
| `codex-hook` | Read one native Codex hook envelope from standard input. | No direct model or Discord call | Allowlisted lifecycle state |
| `pi-hook` | Read one minimal Pi extension envelope from standard input. | No direct model or Discord call | Allowlisted lifecycle state |
| `preview` | Generate one local status preview from current reduced lifecycle state. | Stage two only, never Discord | Bounded validated public history |
| `daemon` | Run foreground refresh and candid work loops. Preview publication is the default. | Configured model stages. Discord only in explicit Discord mode. | Public caches, candid socket state, and optional Discord presence |
| `session-inspect` | Inspect one explicitly supplied Pi or Codex session file and print structural event counts. | Neither | Nothing |
| `simulate-history` | Approximate recent presence from local Pi and Codex histories. | Configured stages, never Discord | Nothing |

## Safe first checks

These commands don't contact a model or Discord:

```bash
agent-away-message --json fixture inspect tests/fixtures/dogfood-events.jsonl
agent-away-message --config "$PWD/config.toml" --json doctor
agent-away-message --config "$PWD/config.toml" --json status
```

`doctor` reports the resolved backend kinds and whether the configuration would share candid source with an external command backend. It checks only the integration paths you provide.

## Select a local Discord account

When multiple Discord desktop clients or accounts are running, inspect their local READY identities before starting the daemon:

```bash
agent-away-message --json discord-accounts \
  --discord-client-id YOUR_APPLICATION_ID
```

The command closes every probe, does not publish a presence, and does not persist the returned identities. Pass the chosen stable `user_id` to `daemon --discord-user-id`. A configured daemon fails closed when that account is unavailable and discovers it again after reconnects; it never falls back to another account. Omitting the option preserves first-available behavior.

## Setup and dry run

`setup` is additive and idempotent. Preview changes before writing:

```bash
agent-away-message --config "$PWD/config.toml" --json setup \
  --codex-config ~/.codex/hooks.json \
  --pi-extension ~/.pi/agent/extensions/agent-away-message.ts \
  --dry-run
```

Remove `--dry-run` to write the missing integration entries. Existing sibling hooks remain intact.

## Native hooks

`codex-hook` and `pi-hook` are integration entry points rather than interactive commands. They read one JSON envelope from standard input, discard unknown fields, fail soft, and never echo untrusted input. The adapters bound the candid fields they select. The complete input envelope has no byte cap.

## One-file structural inspection

`session-inspect` reads only the path you explicitly pass and returns event-type counts:

```bash
agent-away-message --json session-inspect --harness pi /path/to/session.jsonl
```

It doesn't print prompts, commands, paths found inside the file, or model text. Use it to verify that a local history file matches the expected structural contract.

## Historical simulation

`simulate-history` is an explicit, read-only audit path. It scans the selected Pi and Codex roots for a bounded time window, reconstructs approximate lifecycle timing, and returns aggregate timing plus validated public examples. It doesn't publish to Discord or persist sampled source/model text.

See [Inspect local history safely](../how-to/inspect-history.md) before running it against real history.
