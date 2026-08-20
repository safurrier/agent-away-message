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

These llama.cpp commands worked in local dogfood runs. They're optional and don't apply to command backends. llama.cpp flags change, so run `llama-server --help` before using another build or model revision.

The original runs didn't retain an exact llama.cpp revision or hardware record. Treat these as known-working examples, not benchmarks, and not portable hardware recommendations.

## Qwen 3.5 4B Q4

Local dogfood used the `qwen3.5-4b-q4` alias on port 8012:

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

Configure both local stages with:

```toml
url = "http://127.0.0.1:8012/v1"
model = "qwen3.5-4b-q4"
```

## Qwen 3.6 27B Q8 with multi-token prediction

Local dogfood used the `qwen3.6-27b-q8-mtp` alias on port 8080:

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

Configure both local stages with:

```toml
url = "http://127.0.0.1:8080/v1"
model = "qwen3.6-27b-q8-mtp"
```

This model, context size, and multi-token prediction setup needs substantially more host and accelerator memory than the 4B example. Reduce the context or choose a smaller quantization when the server can't load the model.

For every setup, keep the server bound to loopback. agent-away-message rejects non-loopback local backend URLs.
