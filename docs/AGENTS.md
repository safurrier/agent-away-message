# Documentation routing

`SPEC.md` owns requirements. `docs/explanation/architecture.md` owns the data
flow and privacy boundary. `docs/explanation/decision-ledger.md` records durable
public-core tradeoffs. `docs/evaluations/prompt-style.md` owns sanitized style
evidence. `docs/tutorials/local-qwen.md` owns optional tested local-model server
guidance.

## Gotchas

- **DO** update the specification and architecture when changing a persisted or
  public contract. **NOT** leave the decision only in a plan log. **BECAUSE**
  these docs are the durable source for future work.
- **DO** append a ledger entry for a boundary decision. **NOT** rewrite a past
  decision to hide its history. **BECAUSE** the ledger is a decision trail.
