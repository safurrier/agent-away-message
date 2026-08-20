---
id: agent-away-message-decision-ledger
title: agent-away-message decision ledger
description: Append-only record of durable public-core design decisions.
index: []
---

# Decision ledger

This append-only ledger records decisions that still shape the public core.
Later entries mark superseded mechanics; earlier entries remain as history.
External execution stays generic because retired internal infrastructure is no
longer part of the contract.

## 2026-08-01 — exact event records and bounded public output

- **Decision:** Store versioned exact lifecycle records and make generated text
  display-only advisory data.
- **Why:** A guessed phase could hide source evidence and become accidental
  lifecycle authority.

## 2026-08-01 — additive native integration setup

- **Decision:** Install native hook groups and the packaged Pi extension
  additively, with resolved executable and state path, while preserving siblings.
- **Why:** Hook bridges must fail soft and must not damage existing configuration.

## 2026-08-02 — keyed identities and daemon-owned activity work

- **Decision:** Use stable locally keyed digests for hook identities. Source is
  delivered best-effort to a daemon socket only after lifecycle append; bounded
  asynchronous work produces validated public activity.
- **Why:** Sanitized IDs can retain semantic content, and synchronous generation
  can suppress or delay lifecycle evidence.
- **Consequence:** Delivery, queue, and generation failures discard advisory work
  only. They cannot change lifecycle state; Pi uses an ordered asynchronous sink.

## 2026-08-02 — exact-turn activity provenance

- **Decision:** Version activity jobs and public candidates with an opaque token
  derived from the corresponding native input record.
- **Why:** Without exact provenance, display-only activity can describe a prior
  turn after delivery or summary failure.
- **Consequence:** Reduction carries the latest turn token across heartbeats,
  clears it at a new Pi session incarnation, and attaches activity only for an
  equal token. Terminal hooks can skip queued heartbeats during shutdown.

## 2026-08-02 — candid native completion context

- **Decision:** Candid mode uses bounded native completion context and accepts
  one public-safe factual sentence or abstention tied to the exact input turn.
- **Why:** Completion context can make an AIM-style message more interesting,
  but mandatory summary fields make ordinary work sound artificially dramatic.
- **Consequence:** Candid text cannot assert lifecycle, success, blocked state,
  or progress. Rejection and abstention preserve lifecycle-only behavior.

## 2026-08-02 — calm exact-count publication and history simulation

- **Decision:** Keep lifecycle reduction exact while coalescing only count-only
  presentation changes for 60–120 seconds, suppressing duplicate payloads and
  clearing presence during guaranteed teardown.
- **Why:** Short-lived child and reviewer bursts should not flicker every poll,
  while settled counts remain truthful.
- **Decision:** Keep `simulate-history` separate, opt-in, bounded, and
  read-only; it returns only aggregate timing and validated public candidates.
- **Why:** Retrospective quality inspection needs representative history, while
  the continuously running daemon keeps a no-transcript boundary.

## 2026-08-05 — stable Pi identity and constrained local generation

- **Decision:** Use `ctx.sessionManager.getSessionId()` as Pi hook identity,
  then apply the existing local keyed digest before persistence.
- **Why:** One visible Pi session can use different processes for start, input,
  callbacks, and shutdown; process IDs split that session into false agents.
- **Decision:** Request structured output from generation boundaries. Stage-one
  candid reduction treats malformed or privacy-rejected activity as abstention;
  transport failures remain distinct and reportable. Stage-two status output is
  validated once and fails visibly when malformed, unsafe, or duplicated.
- **Why:** Structured output and explicit stage-specific rejection semantics keep
  privacy strict while making operational failures diagnosable.

## 2026-08-05 — no canned status fallback and opt-in local evaluation

- **Decision:** Supersede deterministic status fallback. One malformed, unsafe,
  or duplicate stage-two reply fails visibly without a repair call; continuous
  presence may reuse only an already validated recent status within its bound,
  then clears.
- **Why:** A canned phrase makes presence appear healthy while hiding a failed
  prompt or recent-history contract.
- **Decision:** Keep an opt-in evaluation over manually sanitized synthetic
  workloads, with hard safety checks and diagnostic diversity only.
- **Why:** Test doubles prove safety machinery but not useful generator behavior;
  an operator-owned local service is unavailable in ordinary CI.

## 2026-08-05 — evaluation groups are scenarios, not statistical splits

- **Decision:** Use common, edge, and privacy scenario groups. Ambiguous cases
  may produce validated generic output or abstention; only unambiguous evidence
  and safety constraints are hard gates.
- **Why:** The suite has no training or frozen-holdout consumer; stronger claims
  would create false precision. Fixed evaluation seeding reduces sampling noise
  without claiming cross-server reproducibility.

## 2026-08-05 — recover live daemons from invalid public history

- **Decision:** At startup, a continuous daemon validates bounded public history.
  An incompatible cache is atomically renamed to a timestamped invalid file with
  mode `0600`, reports only a safe quarantine flag, and resumes empty. Preview
  and one-shot modes fail visibly without mutation.
- **Why:** A stale public-only cache must not cause an infinite failure loop;
  deletion loses evidence and weaker validation violates the privacy contract.
- **Decision:** Preview exposes only a safe generation error class, never source,
  paths, underlying errors, or rejected output.
- **Why:** The distinction is operationally useful without expanding disclosure.

## 2026-08-06 — rolling advisory activity window

- **Decision:** Supersede exact-current-turn eligibility with a bounded,
  versioned rolling activity window. Input records provisional safe activity;
  completion supersedes the same turn with settled activity; aggregation uses the
  latest eligible activity across exactly live sessions.
- **Why:** Exact matching discarded useful settled activity when the next turn
  began and starved aggregate status wording.
- **Consequence:** The cache holds only validated prose, opaque provenance,
  phase, observed/expiry times, and schema metadata. It never changes exact
  liveness or counts; stopped and expired sessions are excluded.

## 2026-08-06 — monotonic ingress and owned socket recovery

- **Decision:** Persist activity with `observed_at <= expires_at <= observed_at
  + 30 minutes`; settled activity is monotonic over provisional activity in both
  queue and cache. Invalid activity caches quarantine in continuous mode and
  remain visible in preview and one-shot mode.
- **Decision:** Hold a kernel-released exclusive `flock` across stale inspection,
  unlink, bind, and teardown; unlink the owned socket before releasing the lock.
- **Why:** Delayed input must not replace completion, and probing then unlinking
  can remove a concurrent owner's socket. A kernel lease releases after a crash.

## 2026-08-10 — superseded routed execution boundary

- **Decision:** Earlier per-stage routing distinguished source reduction from
  public display generation, requiring explicit authorization before source left
  the machine and never silently falling back.
- **Why:** The two stages have different disclosure consequences.
- **Superseded by:** The public generic command boundary below. Its explicit
  executable, arguments, environment allowlist, consent, and JSON protocol are
  now the supported external-execution contract.

## 2026-08-11 — candidate-only activity checking

- **Decision:** Keep deterministic validation limited to shape, bounded plain
  text, and structural identifier leakage. In candid mode, a candidate-only
  recognizability check may reject a reduced public activity without receiving
  raw source or session facts. Final status generation has no prose judge or
  repair loop.
- **Why:** Vocabulary lists and semantic heuristics make unsupported claims about
  inactivity, outcomes, complications, progress, and system state.
- **Consequence:** Rejected activity and arbitrary checker output are never
  persisted; the checker has no factual-support authority.

## 2026-08-12 — generic and candid activity statuses

- **Decision:** Expose `generic` and `candid` modes. Generic writes task-agnostic
  prose from aggregate lifecycle facts. Candid reduces bounded source to admitted
  public activity before writing prose.
- **Why:** Count-only input cannot honestly describe a task, while task-linked
  prose needs an explicit disclosure-reduction boundary.
- **Consequence:** Generated activity is display-only; lifecycle records remain
  authoritative. Privacy rejection degrades to generic prose without route changes.

## 2026-08-12 — selected dry AIM voice permits restrained figurative phrasing

- **Decision:** Use a dry, understated, slightly elliptical AIM voice. Recent
  public states are negative style context; humor is optional and restrained,
  activity-grounded figurative phrasing is permitted.
- **Why:** Replay favored concise lines with modest character. Blanket rejection
  forced repetitive progress prose and generic away templates.
- **Consequence:** Deterministic privacy validation and source boundaries remain
  unchanged; style selection grants no factual-support authority.

## 2026-08-12 — structural-only identifiers and superseded repairs

- **Decision:** Detect structurally distinctive identifiers (case patterns,
  separators, digits, acronyms, hashes, paths, URLs, email, and dotted forms),
  not ordinary lowercase words.
- **Historical mechanism:** An earlier pipeline used fixed application-owned
  repair text rather than checker-authored prose. The generic/candid single-call
  writer later removed stage-two repairs entirely.
- **Why:** Broad allowlists create hidden vocabulary authority and free-form
  repair explanations add prompt-injection surface; removing repairs keeps the
  current failure contract simpler.

## 2026-08-17 — activity wording and stable elapsed timer

- **Decision:** Render Rich Presence as `Watching agents at work` with exact
  `N coding agent(s) active` details. Reuse one start timestamp through status,
  count, and reconnect updates; reset only when presence clears or daemon restarts.
- **Why:** Earlier wording implied user absence. The timer describes a continuous
  published work window, not the age of each generated sentence.
- **Consequence:** Timer state is advisory and in-memory; it never alters
  lifecycle authority or persistence.

## 2026-08-18 — public generic command backend

- **Decision:** Supersede the retired routed adapter with a generic direct command
  protocol: absolute executable, literal arguments, explicit environment allowlist,
  stdin/stdout JSON, streaming output bounds, process-group cleanup, safe errors,
  and explicit candid consent.
- **Why:** Public core can enforce process isolation and bounded transport without
  coupling users to retired internal execution infrastructure.
- **Consequence:** Trusted adapter logging remains outside core's privacy boundary.

## 2026-08-18 — command request deadline covers both pipe directions

- **Decision:** Send request bytes and read response bytes in one nonblocking
  selector loop under one deadline; close stdin after transmission and retain the
  hard response cap and process-group cleanup.
- **Why:** A command that does not read stdin can fill its request pipe and block a
  caller before a read-only timeout begins. Commands that write before reading
  also require concurrent draining to avoid a pipe deadlock.
- **Consequence:** Minimal injected process doubles retain their `communicate`
  compatibility seam; real subprocesses receive deadline-bounded duplex I/O.

## 2026-08-18 — retire internal planning templates

- **Decision:** Retire deleted internal planning templates; root `SPEC.md` is the
  canonical public specification.
- **Why:** Durable contract truth belongs in project-owned documents, not generic
  scaffolding.
