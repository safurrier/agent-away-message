---
id: agent-away-message-docs
title: agent-away-message documentation
description: Index for the durable product documentation.
---

# Documentation

Start with the root [README](../README.md) to install, configure, and preview agent-away-message.

## Operate it

- [Tested local-model setups](tutorials/local-qwen.md) gives optional llama.cpp commands.
- [Publish to Discord](how-to/publish-discord.md) covers the explicit publication path.
- [Inspect local history safely](how-to/inspect-history.md) covers structural inspection and bounded replay.

## Look up a contract

- [Configuration](reference/configuration.md) lists supported TOML fields and backend rules.
- [Command reference](reference/cli.md) maps commands to side effects and disclosure boundaries.
- [Product and interface contract](../SPEC.md) owns normative behavior.

## Understand the design

- [Architecture](explanation/architecture.md) explains lifecycle authority, generation, storage, and failure paths.
- [Decision ledger](explanation/decision-ledger.md) records why durable boundaries exist and identifies older, superseded mechanics.
- [Prompt-style evaluation](evaluations/prompt-style.md) preserves sanitized evidence behind the selected status voice.

Contributor instructions and documentation ownership live in [`docs/AGENTS.md`](AGENTS.md).
