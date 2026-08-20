---
id: agent-away-message-publish-discord
title: Publish to Discord
description: Configure and run the explicit Discord Rich Presence publication path.
---

# Publish to Discord

Discord publication is opt-in. Complete the source install, model configuration, and Pi or Codex setup in the root [README](../../README.md) before enabling it.

## Create the Discord application

1. Open the [Discord Developer Portal](https://discord.com/developers/applications) and create an application.
2. Upload the [provided application icon](../assets/branding/discord-application-icon.png).
3. Copy the Application ID. agent-away-message uses it as the Rich Presence client ID.
4. Start the Discord desktop client on the same machine. Rich Presence uses local Discord inter-process communication rather than a bot token.

## Check local state first

Confirm routing and integration readiness without contacting a model or Discord:

```bash
agent-away-message --config "$PWD/config.toml" --json doctor
agent-away-message --config "$PWD/config.toml" --json status
```

Interact with a connected Pi or Codex session if `active_agents` is zero. Then generate one local preview:

```bash
agent-away-message --config "$PWD/config.toml" preview
```

Don't enable Discord publication until the preview contains an acceptable public status.

## Start the foreground daemon

```bash
agent-away-message --config "$PWD/config.toml" \
  --publication-mode discord daemon \
  --discord-client-id YOUR_APPLICATION_ID
```

Keep this process running. The daemon refreshes validated prose, publishes the current active-agent count, and keeps one elapsed-time start through status and count changes. It clears Rich Presence during an orderly shutdown.

The published activity uses these fixed presentation fields:

- activity: `Watching agents at work`
- details: `1 coding agent active` or `N coding agents active`
- state: the validated generated status
- elapsed time: the start of the current continuous publication window

Generated prose never determines the count.

## Diagnose failures

| Symptom | Check |
| --- | --- |
| No Rich Presence appears | Confirm the Discord desktop client is running on the same machine. |
| Connection or pipe error | Confirm local Discord inter-process communication is available and no sandbox blocks it. |
| Application not found | Recopy the Application ID from the Developer Portal. Don't use a bot token or public key. |
| No active agents | Run `status`, then interact with a connected Pi or Codex session. |
| Preview generation fails | Fix the model or command backend before debugging Discord. |
| Presence lingers after a crash | Restart and stop the daemon cleanly, or wait for Discord to expire the stale local presence. |

Stopping the foreground process intentionally ends publication. Run it under your preferred service manager only after the command works interactively. This repository doesn't install or own a background service.
