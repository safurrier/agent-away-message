---
id: agent-away-message-inspect-history
title: Inspect local history safely
description: Structural session inspection and bounded, read-only status replay.
---

# Inspect local history safely

agent-away-message has two explicit inspection paths. Both read local files without changing them, but they answer different questions.

## Count event types in one file

Use `session-inspect` when you want to verify that one known Pi or Codex history file matches the expected structure:

```bash
agent-away-message --json session-inspect \
  --harness pi \
  /path/to/session.jsonl
```

The command prints structural counts only. It doesn't print prompts, commands, model text, or paths found inside the file.

## Approximate recent presence

Use `simulate-history` to sample a bounded period of local Pi and Codex history and see how status generation would have behaved:

```bash
agent-away-message --config "$PWD/config.toml" \
  --context-mode generic \
  --json simulate-history \
  --since 24h \
  --max-examples 12
```

The allowed time window is 1 to 168 hours. `--max-examples` accepts 0 to 32. Override the default roots when needed:

```bash
agent-away-message --config "$PWD/config.toml" \
  --json simulate-history \
  --codex-root /path/to/codex/sessions \
  --pi-root /path/to/pi/sessions \
  --since 12h \
  --max-examples 8
```

The replay is approximate because historical files aren't the native live hook stream. Output contains aggregate timing and validated public examples only. The command doesn't publish to Discord or persist sampled source/model text.

## Candid replay restrictions

Candid replay may read bounded source from local history. It requires a local stage-one backend even when ordinary live candid mode has explicit consent for a command-backed reducer. The command rejects an external command stage one before source extraction.

Use generic replay when you only need count/harness behavior. Use candid replay only when task-linked output is necessary and you have reviewed the selected local history roots.
