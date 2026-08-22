# agent-away-message

**When a user corrects you or provides a non-obvious repository gotcha, document
it in the closest `AGENTS.md` before continuing.**

Local, privacy-first AIM-style away messages for Pi and Codex. The command entry
point is `agent_away_message.cli:cli`; executable behavior is bounded by
`SPEC.md`.

## WHY

This tool lets people show that coding agents are away without exporting their
prompts, repositories, commands, or errors. Exact lifecycle facts are durable;
model-produced prose is advisory display input.

## WHAT

- `agent_away_message/`: Click CLI, event store, adapters, local model and RPC seams.
- `tests/`: deterministic unit, CLI, and synthetic E2E tests.
- `integrations/`: native Codex hook declaration.
- `agent_away_message/assets/`: packaged Pi extension installed by the CLI.

## HOW

**Setup**: `mise run setup`.
**Fast gate**: `mise run check`.
**Synthetic E2E**: `mise run verify`.
**Fixture inspection**: `mise run dogfood`.

## Gotchas

- **DO** persist only `LifecycleRecord` allowlisted fields. **NOT** save hook
  payloads, model responses, commands, paths, or errors. **BECAUSE** local files
  are part of the privacy boundary.
- **DO** use exact source events to decide liveness. **NOT** use generated
  summaries or inferred phases as lifecycle authority. **BECAUSE** display prose
  cannot replace source evidence.
- **DO** keep preview failures visible and apply the same time-bounded Discord
  history fallback to transport failures and validator-rejected stage-two replies.
  **NOT** synthesize a canned replacement state. **BECAUSE** a deterministic phrase
  can make an unhealthy model look successful; rejection should remain observable
  while never affecting lifecycle authority.
- **DO** preserve existing hook entries in `setup`. **NOT** overwrite a config
  object such as an existing hook. **BECAUSE** setup is additive and retry-safe.
- **DO** embed the selected mode in the Pi extension: candid sends non-extension
  `input` provisionally and bounded current-turn context at `agent_end` to settle
  it, while generic sends neither. **NOT** summarize extension-originated input.
  **BECAUSE** only a matching daemon exposes the local socket, and extension
  prompts are private implementation traffic.
- **DO** let candid status be one supported factual sentence or abstention in a
  bounded rolling advisory activity window. **NOT** tie eligibility to only the
  current turn or let it determine liveness. **BECAUSE** completion can arrive
  after the next input, while exact lifecycle records remain authoritative.
- **DO** keep `simulate-history` explicit, read-only, bounded, local-stage-one,
  and raw-free on output; configured remote stage two may receive only admitted
  public data. **NOT** reuse its JSONL readers in the live daemon or persist
  sampled source/model text. **BECAUSE** retrospective dogfooding is a user-invoked
  audit exception, not an expansion of the runtime privacy boundary.
- **DO** let a continuous daemon atomically quarantine an incompatible pre-release
  incompatible public-output history cache and restart from empty validated history. **NOT** weaken
  validation, silently delete the cache, or hide the safe quarantine signal.
  **BECAUSE** history is only a bounded public-output cache, while preview and
  one-shot modes should still expose invalid state for diagnosis.
- **DO** derive Pi lifecycle identity from a stable session boundary rather than
  `process.pid`. **NOT** assume all Pi callbacks run in one process. **BECAUSE**
  live dogfood showed `session_start`/final shutdown in an outer Pi process while
  `input`/agent callbacks and an earlier shutdown ran in a child Pi process,
  splitting one visible session into two active agents.
- **DO** preserve the dry, understated AIM voice and allow restrained, activity-grounded
  figurative phrasing. **NOT** blanket-reject personification or force every status into
  plain progress prose. **BECAUSE** replay dogfood selected lines such as technical work
  acquiring paperwork or forming a committee over safer but formulaic alternatives.
- **DO** target a configured Discord account by the stable user ID in each IPC READY
  handshake and rediscover it after reconnects. **NOT** persist account identities, fall
  back to another account, or treat a pipe number as account identity. **BECAUSE** Stable,
  Canary, and other Discord clients acquire pipe numbers from startup order.

## Related Context

| Path | What's there |
|---|---|
| `SPEC.md` | Requirements, event contract, and acceptance scenarios. |
| `docs/explanation/architecture.md` | System boundaries and data flow. |
| `docs/explanation/decision-ledger.md` | Durable implementation decisions. |

<!-- generated-by: context-engineering@2.2.0 | last-updated: 2026-08-01 -->
