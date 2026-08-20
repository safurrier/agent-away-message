---
id: agent-away-message-architecture
title: agent-away-message architecture
description: Data flow, lifecycle authority, storage, and privacy boundaries.
index: []
---

# Architecture

agent-away-message separates exact lifecycle evidence from generated display text. Native Pi and Codex hooks determine which sessions are active. Models can change the sentence shown beside that count, but they can't create, remove, or reinterpret an active session.

## Data flow

```text
Pi / Codex hooks
    |
    +--> allowlisted lifecycle record --> exact liveness reduction --> count + harness mix
    |
    `--> bounded candid source --> stage-one reducer --> candidate check --> activity window
                                                                   |
count + harness mix + admitted public activity --------------------+
    |
    `--> stage-two writer --> deterministic validation --> preview or Discord
```

Generic mode skips the candid branch. Stage two receives aggregate facts and recent accepted public statuses, but no task source.

Candid mode sends bounded current-turn context to stage one. Stage one proposes one broad public activity or abstains. Deterministic shape and identifier checks run before a candidate-only recognizability check. Only an admitted activity can enter the rolling public activity window or reach stage two.

## Lifecycle authority

Hooks append lifecycle evidence before attempting advisory candid delivery. A socket, queue, reducer, writer, or publisher failure therefore can't suppress the underlying event.

Each persisted lifecycle record contains an opaque keyed session digest and an allowlisted event, timestamp, provenance marker, and already-admitted public activity metadata. Hooks discard unknown fields. Active-session reduction uses only exact start, heartbeat, terminal, and expiry behavior from those records.

A missed terminal event can leave a session visible until its lifecycle record expires, for up to 30 minutes. This is an expiry rule over source evidence, not a model inference.

## Generation stages

The stages have different disclosure boundaries:

1. **Stage one reduces source.** It runs only for candid activity and receives bounded current-turn context.
2. **The candidate check receives only the proposed public activity.** It can't compare against raw source or act as factual-support authority.
3. **Stage two writes the status.** It receives aggregate lifecycle facts, recent accepted public statuses, and the unique admitted activities associated with live sessions.
4. **Deterministic validation checks the final public string.** It enforces shape, bounds, duplicate rejection, and structural identifier leakage.

Local backends must use credential-free loopback OpenAI-compatible endpoints. Core treats command backends as external. A candid command-backed stage one requires explicit consent because source leaves the core process. Stage two needs no matching consent because its inputs are already public.

## Failure behavior

Failure paths preserve the source/display split:

- A stage-one rejection or abstention can fall back to generic stage-two generation. The configured route doesn't change.
- A stage-one transport failure prevents the stage-two call for that refresh.
- A malformed, unsafe, or duplicate stage-two result fails without a repair call or canned replacement.
- One-shot preview exposes a safe generation error class and doesn't hide the failure.
- A continuous daemon may keep a recent, already-validated public status within its retention bound. After that bound expires, it clears presence rather than inventing text.

None of these paths change exact lifecycle records or counts.

## Storage

| Store | Contents | Role |
| --- | --- | --- |
| `events.jsonl` | Allowlisted lifecycle records keyed by opaque local digests | Exact liveness authority |
| `activity-window.json` | Admitted public activity, opaque provenance, phase, and bounded timestamps | Optional candid context for live sessions |
| `message-history.json` | Recent validated public statuses | Cadence reuse, negative style context, duplicate avoidance, and bounded transport fallback |
| `identity.key` | Local key material | Stable opaque session identity |
| `context.sock` | Ephemeral local datagram endpoint | Advisory candid source delivery to the daemon |

Aggregation excludes activity whose session is no longer live or whose activity has expired. The bounded cache may retain that entry until normal pruning. Settled activity supersedes provisional activity for the same turn. Neither the activity window nor message history can make a session active.

A continuous daemon quarantines an incompatible public-output cache by atomically renaming it and starting with empty validated history. Preview and one-shot modes report invalid state without mutating it.

## Trust boundaries

| Boundary | Data crossing |
| --- | --- |
| Native hooks to event store | Allowlisted lifecycle fields only |
| Hooks to candid socket | Bounded ephemeral source in candid mode only |
| Source to stage one | Bounded source. An external command requires explicit consent. |
| Reducer to candidate check | Proposed public activity only |
| Stores to stage two | Aggregate facts, recent accepted public statuses, and admitted public activities |
| Stage two to Discord | Validated status, active count, and presentation metadata |

Command adapters receive one versioned JSON request on standard input. Core runs the absolute executable without a shell, in a neutral temporary directory, with an explicit environment allowlist and bounded duplex input/output. It discards child standard error and cleans up the process group. These controls can't prevent an operator-controlled adapter from logging or forwarding its input.

## Historical inspection

`session-inspect` reads one explicitly supplied session file and reports structural counts only.

`simulate-history` is a separate, explicit audit path. It scans bounded local history, returns aggregate timing and validated public examples, and never publishes to Discord. Candid replay requires local stage one and rejects an external command reducer before reading source.
