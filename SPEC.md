# Agent activity status contract

## Normative language

The capitalized terms MUST, MUST NOT, SHOULD, and MAY define requirements in this document.

## Summary

The application MUST derive coding-agent presence from exact native lifecycle records. Generated prose is advisory display data. Preview MUST be the default. Publication MUST be explicit.

## Goals and exclusions

The core MUST reduce disclosure while showing activity. It MUST NOT persist prompts, source context, model replies, commands, paths, credentials, or errors. Generated prose MUST NOT become lifecycle authority.

## Generation modes

Generic mode MUST send stage two only aggregate count, harness mix, and bounded recent accepted public statuses.

Candid mode MUST reduce bounded source to a broad public activity and independently check only that candidate. Stage two MAY receive aggregate facts, bounded recent accepted public statuses, and admitted public activities from live sessions. It MUST NOT receive raw source.

A stage-one transport failure MUST prevent a stage-two call for that refresh. Candidate rejection or abstention MAY degrade to generic generation but MUST NOT change the configured routes.

## Generation backends

Configuration MUST use schema version 2. Each generation stage MAY select only:

- a credential-free loopback OpenAI-compatible `local` backend; or
- a generic `command` backend.

Local backends MUST be the default for both stages.

A command backend has these requirements:

- Its executable MUST be an absolute path.
- Its arguments MUST be literal strings.
- Core MUST spawn the executable directly without a shell.
- Core MUST start a new process session in a neutral temporary directory.
- The environment MUST be empty unless configuration explicitly allowlists variable names.
- Core MUST discard standard error.
- Request transfer and response transfer MUST share one timeout.
- Core MUST bound standard output while reading it, not after capture.
- Core MUST kill and reap the complete process group before cleanup on every terminal path.
- Errors MUST remain safe and bounded without disclosing the executable, arguments, environment names, child output, or prompt.

A command backend MUST receive exactly one UTF-8 JSON object on standard input:

```json
{"schema_version":1,"prompt":"..."}
```

It MUST return exactly one bounded UTF-8 JSON object on standard output:

```json
{"schema_version":1,"completion":"..."}
```

Core MUST NOT fall back to another backend. It MUST normalize invalid process strings, launch errors, timeout, nonzero exit, malformed protocol, and overflow without echoing inputs.

Core MUST treat every command backend as external. Candid command stage one MUST require `privacy.allow_remote_context = true`. Command stage two needs no matching consent because it receives public data only. Candid `simulate-history` MUST reject command stage one before extracting source.

## Interfaces and contracts

Core commands are `daemon`, `preview`, `status`, `ingest`, `codex-hook`, `pi-hook`, `setup`, `doctor`, `discord-accounts`, `session-inspect`, `simulate-history`, and `fixture inspect`.

Doctor and daemon SHOULD disclose backend kinds and whether candid raw context is shared externally. Hooks MUST fail soft and MUST NOT echo untrusted input. Setup MUST be additive.

`discord-accounts` MUST inspect only reachable local Discord IPC endpoints, MUST NOT publish presence, and MUST NOT persist discovered identities. Discord publication MAY target a stable Discord user ID. When configured, each connection and reconnection MUST select only an endpoint whose READY handshake reports that exact ID and MUST fail closed when it is absent. Without a configured user ID, publication MUST preserve first-available IPC behavior.

A persisted lifecycle record contains only schema version, harness, opaque keyed session digest, exact event/time/provenance, and already-admitted public activity metadata. It MUST NOT contain hook payloads, prompts, commands, paths, model text, source, or errors. Hooks MUST discard unknown fields.

Codex accepts `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, and `Stop`.

Pi accepts `session_start`, non-extension `input`, `agent_start`, `tool_call`, `agent_end`, and `session_shutdown`.

Exact start, heartbeat, terminal, and expiry reduction MUST determine liveness. Event source, generated activity, and generated status MUST NOT substitute for lifecycle evidence.

The command response cap MUST apply while core reads bytes, not after capture. Core MUST normalize invalid process strings, launch errors, timeout, nonzero exit, malformed protocol, and overflow to bounded errors without echoing inputs.

## Invariants

Lifecycle evidence alone determines liveness. Stage two never receives raw source. Command prompts never appear in process arguments, environment values, persistent files, or diagnostics. Continuous publication MAY use bounded, validated public history only within its retention limit. Discord account discovery never emits a presence update or persists local account identity.

## Acceptance

Implementations MUST preserve command protocol, prompt isolation, loopback endpoint validation, consent gating, source-to-stage-two separation, lifecycle-only authority, and safe replay behavior.

```bash
mise run setup
mise run check
printf '{"hook_event_name":"UserPromptSubmit","session_id":"demo_1"}' | \
  uv run agent-away-message codex-hook
uv run agent-away-message --json status
uv run agent-away-message --json setup --codex-config /tmp/hooks.json --dry-run
uv run agent-away-message --context-mode candid --json simulate-history --since 24h --max-examples 12
```

Native hooks are silent and never echo untrusted input. `status` reports counts and harness names only. A real subprocess that exceeds command output limits is killed and reaped as a process group before its workspace is removed.

`mise run check`, `mise run verify`, and `mise run dogfood` SHOULD pass.
