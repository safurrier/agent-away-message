---
id: agent-away-message-local-model-tutorial
title: Tested local-model setups
description: Optional local OpenAI-compatible server guidance.
index:
  - id: qwen-35-4b-q4
    title: Qwen 3.5 4B Q4
  - id: qwen-36-27b-q8-mtp
    title: Qwen 3.6 27B Q8 MTP
---

# Tested local-model setups

This optional tutorial records commands tested in this project's local dogfood with llama.cpp. It is not required for the command backend. These commands are version-sensitive: use a llama.cpp build that supports the shown flags, and check `llama-server --help` before substituting a build or model revision.

## Qwen 3.5 4B Q4

The tested server used the `qwen3.5-4b-q4` alias on port 8012:

```bash
llama-server \
  --hf-repo unsloth/Qwen3.5-4B-GGUF:Q4_K_M \
  --alias qwen3.5-4b-q4 \
  --host 127.0.0.1 \
  --port 8012 \
  --ctx-size 32768 \
  --gpu-layers auto \
  --jinja \
  --reasoning off \
  --metrics
```

Use `http://127.0.0.1:8012/v1` and `qwen3.5-4b-q4` in both local backend tables.

## Qwen 3.6 27B Q8 MTP

The tested MTP server used the `qwen3.6-27b-q8-mtp` alias on port 8080:

```bash
llama-server \
  -hf unsloth/Qwen3.6-27B-MTP-GGUF:Q8_0 \
  --alias qwen3.6-27b-q8-mtp \
  --host 127.0.0.1 \
  --port 8080 \
  --spec-type draft-mtp \
  -ngl 999 \
  -fa on \
  -c 65536 \
  --metrics
```

Use `http://127.0.0.1:8080/v1` and `qwen3.6-27b-q8-mtp` in both local backend tables. This larger configuration needs enough host and GPU memory for its model, context, and draft-MTP setup.
