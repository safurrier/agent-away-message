---
id: agent-away-message-prompt-style-evaluation
title: Prompt style evaluation
description: Sanitized prompt findings for AIM-style coding-agent statuses.
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

This note preserves only synthetic or manually sanitized examples. It contains no raw history, local paths, session metadata, repositories, people, customers, or copied private source.

## Selected positive direction

- `a few loose ends have formed a committee`
- `apparently, embeddings come with paperwork`
- `the adapter remains unconvinced by the install path`

These were selected for dry understatement, task-linked mild oddness, and recognizable AIM-status brevity. They are inspiration, not few-shot examples: prompts must not copy their words, cadence, ordering, syntax, or recurring personification device.

## Known failures

- Literal absence templates: `away for a bit`, `back shortly`, `while i'm away`.
- Generic progress copy: `making steady progress`, `keeping things moving`.
- Repeated gerund openings: `checking...`, `reviewing...`, `writing...` on every line.
- Count-only traffic/machinery formulas detached from the task.
- Mandatory two-clause quips, repeated anthropomorphism, random-noun surrealism, faux nostalgia, or polished brand copy.
- Strict prompts that cause high abstention or merely restate the admitted activity.

## First 24-hour replay

The first raw-free candid replay attempted 12 samples: 8 admitted candid activity, 4 reduced to generic, and 0 generation errors. It confirmed the pipeline operates, but exposed two abstract style failures:

- candid statuses often restated the activity and appended a clause about agents;
- generic statuses repeatedly described harnesses, agents, hum, bustle, or traffic instead of the work surface.

The revised writer prompt describes the work itself, omits user/agent/harness framing, transforms candid activity rather than echoing it, and keeps generic output task-agnostic.

## Second 24-hour replay

Both mode-faithful replays generated 12 of 12 statuses. Candid admitted all 12 activities, and neither mode reported generation errors. Generic output fell into a congestion/spatial-metaphor monoculture. Candid output fell into personification and repeated getting/being/acquiring constructions. Prescriptive anti-pattern guidance had merely moved repetition into new templates.

## Accepted-history diversity pass

The writer prompt now uses minimal positive guidance. Up to twelve previously accepted public statuses are passed only as negative style examples, with a direct instruction to avoid their central image, notable vocabulary, opening grammar, sentence skeleton, and cadence. Exact-duplicate rejection remains deterministic; no similarity score or extra judge call was added.

A subsequent raw-free replay generated 12 of 12 generic statuses and 12 of 12 candid statuses without generation errors. Candid admitted 11 activities and made one explicit generic fallback. Generic output retained unavoidable load vocabulary but substantially reduced its prior congestion-image monoculture. Candid removed the repeated getting/being/acquiring frame and varied its syntax while keeping admitted activities recognizable; restrained task-grounded personification remained part of the selected voice.

Live preview later showed that rapidly changing candid activity could repeat one accurate topic across several otherwise distinct statuses. The writer now treats a topical word or phrase appearing in at least three recent accepted statuses as saturated and prefers a truthful paraphrase or another facet when available. This remains advisory prompt guidance: task grounding wins, and no vocabulary counter, rejection heuristic, judge call, or fallback was added.

## Product correction

The status is an update about work, not a claim that the user is absent and not a report about agents. Generic mode is deliberately task-agnostic. Candid mode must preserve a broad admitted task after privacy reduction. Automated checks validate privacy and structure; final vibe selection remains human judgment over representative replay output.
