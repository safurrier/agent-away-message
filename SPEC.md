# Agent activity status contract

## Summary

The application MUST derive coding-agent presence from exact native lifecycle records. Generated prose is advisory display data. Preview is the default; publication is explicit.

## Goals / Non-Goals

The core MUST reduce disclosure while showing activity. It MUST NOT persist prompts, source context, model replies, commands, paths, credentials, or errors. It MUST NOT make generated prose lifecycle authority.

## Requirements

Generic mode MUST send only aggregate count and harness mix to stage two. Candid mode MUST reduce bounded source to a broad public activity, independently check only that candidate, and send stage two only aggregate facts plus admitted public activity. A stage-one transport failure MUST prevent a stage-two call. Rejection MAY degrade to generic generation but MUST NOT change configured routes.

Configuration MUST use schema version 2 and MAY select only credential-free loopback OpenAI-compatible `local` or generic `command` backends. Local/local is the default. A command executable MUST be absolute; arguments MUST be literal strings; it MUST be directly spawned without a shell. It receives `{"schema_version":1,"prompt":"..."}` only on stdin and MUST return bounded UTF-8 `{"schema_version":1,"completion":"..."}` only on stdout. Core MUST start a new process session in a neutral temporary directory, discard stderr, use an empty environment allowlist by default, enforce timeout, kill and reap the complete process group before cleanup, and expose only safe bounded errors. It MUST NOT fall back to another backend or disclose executable, arguments, environment names, or child output.

A command backend MUST be treated as external. Candid command stage one MUST require `privacy.allow_remote_context=true`; command stage two needs no such consent because it receives public data only. Candid `simulate-history` MUST reject a command stage one before source extraction.

## Interfaces & Contracts

Core commands are `daemon`, `preview`, `status`, `ingest`, `codex-hook`, `pi-hook`, `setup`, `doctor`, `simulate-history`, and `fixture inspect`. Doctor and daemon SHOULD disclose backend kinds and whether candid raw context is shared externally. Hooks MUST fail soft and never echo untrusted input. Setup MUST be additive.

A persisted lifecycle record contains only schema version, harness, opaque keyed session digest, exact event/time/provenance, and already-admitted public activity metadata. It MUST NOT contain hook payloads, prompts, commands, paths, model text, source, or errors. Codex accepts `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, and `Stop`; Pi accepts `session_start`, non-extension `input`, `agent_start`, `tool_call`, `agent_end`, and `session_shutdown`. Unknown fields are discarded. Exact start/end reduction determines liveness; event source and generated status do not substitute for one another.

The command protocol is one JSON object on stdin and one JSON object on stdout, both with `schema_version: 1`. The stdout cap applies while bytes are read, not after capture. Invalid process strings (including embedded NUL), launch errors, timeout, nonzero exit, malformed protocol, and overflow are normalized to bounded errors without echoing inputs.

## Invariants

Lifecycle evidence alone determines liveness. Stage two never receives raw source. Command prompts never appear in argv, environment, files, diagnostics, or persisted state. Continuous publication MAY use bounded validated public history only within its retention limit.

## Acceptance

Implementations MUST preserve command protocol, prompt isolation, local endpoint validation, consent gating, source-to-stage-two separation, and safe replay behavior.

```bash
mise run setup
mise run check
printf '{"hook_event_name":"UserPromptSubmit","session_id":"demo_1"}' | \
  uv run agent-away-message codex-hook
uv run agent-away-message --json status
uv run agent-away-message --json setup --codex-config /tmp/hooks.json --dry-run
uv run agent-away-message --context-mode candid --json simulate-history --since 24h --max-examples 12
```

Native hooks are silent and never echo untrusted input. `status` reports counts and harness names only. A real subprocess that exceeds command stdout limits is killed and reaped as a process group before its workspace is removed. `mise run check`, `mise run verify`, and `mise run dogfood` SHOULD pass.
