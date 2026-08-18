"""Additive installation and truthful local integration checks."""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import tempfile
from importlib.resources import files
from pathlib import Path
from typing import Final

from agent_away_message.models import ContextMode

CODEX_EVENTS: Final = ("UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop")
CODEX_COMMAND: Final = "agent-away-message codex-hook"
PI_MARKER: Final = "agent-away-message:managed-extension"
_PI_EXECUTABLE: Final = re.compile(
    r"^const BRIDGE_EXECUTABLE = (?P<value>\"(?:\\.|[^\"\\])*\");$", re.MULTILINE
)
_PI_CONTEXT_MODE: Final = re.compile(
    r'^const CONTEXT_MODE: [^=]+ = (?P<value>"(?:\\.|[^"\\])*");$', re.MULTILINE
)


class InstallationError(ValueError):
    """Raised when an existing configuration cannot be safely extended."""


def merge_codex_hooks(
    existing: dict[str, object], command: str = CODEX_COMMAND
) -> tuple[dict[str, object], bool]:
    """Add one command to every supported native hook without replacing peers."""
    updated = dict(existing)
    hooks_value = updated.get("hooks", {})
    if not isinstance(hooks_value, dict):
        raise InstallationError("Codex hooks must be a JSON object")
    hooks = dict(hooks_value)
    changed = False
    for event in CODEX_EVENTS:
        groups_value = hooks.get(event, [])
        if not isinstance(groups_value, list):
            raise InstallationError(f"Codex {event} hooks must be a list")
        groups, found, group_changed = _configure_groups(groups_value, command)
        changed = changed or group_changed
        if not found:
            groups.append({"hooks": [{"type": "command", "command": command}]})
            changed = True
        hooks[event] = groups
    updated["hooks"] = hooks
    return updated, changed


def codex_configured(path: Path, context_mode: ContextMode | None = None) -> bool:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return False
        hooks = payload.get("hooks")
        return isinstance(hooks, dict) and all(
            isinstance(hooks.get(event), list)
            and _has_live_command(hooks[event], context_mode)
            for event in CODEX_EVENTS
        )
    except (OSError, json.JSONDecodeError):
        return False


def pi_extension_text(
    executable: str = "agent-away-message",
    state_dir: Path | None = None,
    context_mode: ContextMode = ContextMode.GENERIC,
    config_path: Path | None = None,
) -> str:
    """Read the packaged Pi extension asset."""
    source = (
        files("agent_away_message.assets").joinpath("pi-away-message.ts").read_text()
    )
    common_args = ["--state-dir", str(state_dir)] if state_dir is not None else []
    if config_path is not None:
        common_args = ["--config", str(config_path), *common_args]
    return (
        source.replace("__AAM_EXECUTABLE__", json.dumps(executable))
        .replace("__AAM_COMMON_ARGS__", json.dumps(common_args))
        .replace("__AAM_CONTEXT_MODE__", json.dumps(context_mode.value))
    )


def pi_configured(path: Path, context_mode: ContextMode | None = None) -> bool:
    try:
        source = path.read_text(encoding="utf-8")
        match = _PI_EXECUTABLE.search(source)
        mode_match = _PI_CONTEXT_MODE.search(source)
        return (
            PI_MARKER in source
            and match is not None
            and _executable_exists(json.loads(match.group("value")))
            and (
                context_mode is None
                or (
                    mode_match is not None
                    and json.loads(mode_match.group("value")) == context_mode.value
                )
            )
        )
    except (json.JSONDecodeError, OSError):
        return False


def atomic_write(path: Path, content: str) -> None:
    """Write a user config without exposing a partially-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}-"
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def executable_available() -> bool:
    return shutil.which("agent-away-message") is not None


def resolved_executable() -> str:
    """Return the stable executable path embedded into installed adapters."""
    executable = shutil.which("agent-away-message")
    if executable is None:
        raise InstallationError("agent-away-message executable is not available")
    return str(Path(executable).resolve())


def _has_live_command(
    groups: list[object], context_mode: ContextMode | None = None
) -> bool:
    for group in groups:
        if not isinstance(group, dict):
            continue
        commands = group.get("hooks")
        if not isinstance(commands, list):
            continue
        for hook in commands:
            if isinstance(hook, dict):
                command = hook.get("command")
                if (
                    _is_away_command(command)
                    and _command_executable_exists(command)
                    and _command_matches_mode(command, context_mode)
                ):
                    return True
    return False


def _command_matches_mode(value: object, context_mode: ContextMode | None) -> bool:
    if context_mode is None:
        return True
    if not isinstance(value, str):
        return False
    try:
        parts = shlex.split(value)
    except ValueError:
        return False
    try:
        index = parts.index("--context-mode")
    except ValueError:
        return context_mode is ContextMode.GENERIC
    try:
        installed_mode = ContextMode(parts[index + 1])
    except (ValueError, IndexError):
        return False
    return installed_mode is context_mode


def _configure_groups(
    groups: list[object], command: str
) -> tuple[list[object], bool, bool]:
    updated_groups: list[object] = []
    found = False
    changed = False
    for group in groups:
        if not isinstance(group, dict):
            updated_groups.append(group)
            continue
        commands = group.get("hooks")
        if not isinstance(commands, list):
            updated_groups.append(group)
            continue
        updated_commands: list[object] = []
        for hook in commands:
            if not isinstance(hook, dict) or not _is_away_command(hook.get("command")):
                updated_commands.append(hook)
                continue
            found = True
            replacement = dict(hook)
            if replacement.get("command") != command:
                replacement["command"] = command
                changed = True
            updated_commands.append(replacement)
        updated_group = dict(group)
        updated_group["hooks"] = updated_commands
        updated_groups.append(updated_group)
    return updated_groups, found, changed


def _is_away_command(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parts = shlex.split(value)
    except ValueError:
        return False
    return (
        len(parts) >= 2
        and Path(parts[0]).name == "agent-away-message"
        and parts[-1] == "codex-hook"
    )


def _command_executable_exists(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parts = shlex.split(value)
    except ValueError:
        return False
    return bool(parts) and _executable_exists(parts[0])


def _executable_exists(executable: object) -> bool:
    if not isinstance(executable, str) or not executable:
        return False
    if Path(executable).is_absolute():
        return Path(executable).is_file() and os.access(executable, os.X_OK)
    return shutil.which(executable) is not None
