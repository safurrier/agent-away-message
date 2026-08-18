---
id: agent-away-message-architecture
title: agent-away-message architecture
description: Data flow and privacy boundaries for coding-agent statuses.
index: []
---

# Architecture

Exact Pi and Codex hooks append allowlisted lifecycle records keyed by a local digest. Lifecycle reduction alone determines which agents are online.

## Data flow

```text
exact lifecycle -> count + harness mix -> stage-two writer -> validation -> preview/Discord
bounded candid source -> stage-one reducer -> candidate-only privacy check -> public activity window
```

Generic mode sends only aggregate facts to stage two. Candid mode may reduce bounded source to one public activity; deterministic validation and a candidate-only privacy check must admit it before it enters the bounded rolling window. Reduction failure degrades display prose to generic, never lifecycle evidence.

## Trust boundaries

| Boundary | Data crossing |
| --- | --- |
| Native hooks to event store | allowlisted lifecycle fields only |
| Hooks to candid socket | bounded ephemeral source, candid mode only |
| Source to stage one | bounded source; external command requires explicit consent |
| Reducer to privacy check | proposed public activity only |
| Store to stage two | aggregate facts and admitted public activity |
| Stage two to Discord | validated status and exact count metadata |

Local OpenAI-compatible servers and command adapters are operator-controlled. Command adapters receive schema-versioned stdin only, run without a shell in a neutral directory, use an explicit environment allowlist, discard stderr, stream bounded stdout, and have no fallback. Core transport controls do not prevent a trusted adapter from logging or forwarding its stdin.

The daemon owns the candid datagram socket and bounded queue. Hooks append lifecycle evidence first and fail soft if delivery or generation is unavailable. The rolling public activity window never becomes lifecycle authority. `simulate-history` is an explicit read-only audit exception: it returns only aggregate/public artifacts and never publishes to Discord.
