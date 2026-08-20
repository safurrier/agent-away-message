---
id: agent-away-message-prompt-style-evaluation
title: Prompt style evaluation
description: Sanitized prompt findings for AOL Instant Messenger-style coding-agent statuses.
index:
  - id: selected-positive-direction
    title: Selected positive direction
  - id: known-failures
    title: Known failures
  - id: first-24-hour-replay
    title: First 24-hour replay
  - id: second-24-hour-replay
    title: Second 24-hour replay
  - id: accepted-history-diversity-pass
    title: Accepted-history diversity pass
  - id: product-correction
    title: Product correction
---

# Prompt style evaluation

This note contains synthetic or manually sanitized examples only. It excludes raw history, local paths, session metadata, repositories, people, customers, and copied private source.

The replay notes retain mode, sample counts, and outcomes. The original runs didn't retain exact model/backend versions, so these are prompt-selection observations rather than reproducible benchmarks.

## Selected positive direction

- `a few loose ends have formed a committee`
- `apparently, embeddings come with paperwork`
- `the adapter remains unconvinced by the install path`

These have the intended voice: brief, dry, slightly odd, and tied to the work. Treat them as direction, not templates. Don't reuse their wording or sentence patterns.

## Known failures

- Literal absence templates: `away for a bit`, `back shortly`, `while i'm away`.
- Generic progress copy: `making steady progress`, `keeping things moving`.
- Repeated gerund openings: `checking...`, `reviewing...`, `writing...` on every line.
- Count-only traffic or machinery formulas detached from the task.
- Mandatory two-clause quips, repeated anthropomorphism, random-noun surrealism, faux nostalgia, or polished brand copy.
- Strict prompts that cause high abstention or simply restate the admitted activity.

## First 24-hour replay

First replay: 12 samples. Eight kept candid activity, four fell back to generic, and none failed generation. Two style problems stood out:

- Candid statuses often repeated the activity and added a clause about agents.
- Generic statuses described harnesses, hum, bustle, or traffic instead of the work surface.

The next writer prompt described the work directly, removed user/agent/harness framing, transformed candid activity instead of echoing it, and kept generic output task-agnostic.

## Second 24-hour replay

The generic and candid replays each produced 12 of 12 statuses without generation errors. Candid admitted all 12 activities.

Generic statuses overused traffic and space metaphors. Candid statuses overused personification and `getting`, `being`, or `acquiring`. More anti-pattern rules had only created new templates.

## Accepted-history diversity pass

The writer prompt now uses minimal positive guidance. It receives up to 12 previously accepted public statuses only as negative style examples. The prompt asks it to avoid their central image, notable vocabulary, opening grammar, sentence skeleton, and cadence. Deterministic validation still rejects exact duplicates. There is no similarity score or extra judge call.

A later replay generated 12 of 12 generic statuses and 12 of 12 candid statuses without generation errors. Candid admitted 11 activities and used one explicit generic fallback. Generic statuses still used load-related words but repeated the traffic metaphor less often. Candid statuses dropped the repeated `getting`/`being`/`acquiring` frame, varied their syntax, and kept admitted activities recognizable. Restrained task-grounded personification remained part of the selected voice.

Live preview then showed that rapidly changing candid activity could repeat one accurate topic across several otherwise distinct statuses. When the same topic appears in at least three recent accepted statuses, the prompt asks for a truthful paraphrase or another available facet. Task grounding still wins. There is no vocabulary counter, rejection rule, judge call, or fallback.

## Product correction

The status describes current work. It doesn't claim that the user is absent, and it isn't a report about agents.

Generic mode remains task-agnostic. In candid mode, the prompt asks the writer to keep a broad admitted task recognizable after privacy reduction. Automated checks validate privacy and structure, not semantic faithfulness. Humans still select the final voice from representative replay output.
