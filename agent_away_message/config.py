"""Versioned application configuration and safe local defaults."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse

from platformdirs import user_config_path, user_state_path

from agent_away_message.models import ContextMode, PublicationMode

_CONFIG_VERSION = 2


class ConfigError(ValueError):
    """An application config is malformed or asks for an unsafe route."""


@dataclass(frozen=True, slots=True)
class BackendSettings:
    """One generation stage's configured backend; credentials never live here."""

    kind: str = "local"
    url: str = "http://127.0.0.1:8012/v1"
    model: str = "local-model"
    timeout_seconds: float = 10.0
    chat_template_kwargs: dict[str, object] | None = None
    enable_thinking: bool | None = None
    executable: str | None = None
    args: tuple[str, ...] = ()
    pass_environment: tuple[str, ...] = ()

    @property
    def remote(self) -> bool:
        """Commands are opaque and therefore conservatively external."""
        return self.kind == "command"


@dataclass(frozen=True, slots=True)
class Settings:
    state_dir: Path
    context_mode: ContextMode = ContextMode.GENERIC
    publication_mode: PublicationMode = PublicationMode.PREVIEW
    ttl_seconds: int = 1_800
    retain_last_seconds: int = 300
    generation_cadence_min_seconds: int = 480
    generation_cadence_max_seconds: int = 1_200
    history_limit: int = 12
    activity_window_limit: int = 32
    stage_one: BackendSettings = BackendSettings()
    stage_two: BackendSettings = BackendSettings()
    allow_remote_context: bool = False

    @property
    def model_url(self) -> str:
        return self.stage_two.url

    @property
    def model_name(self) -> str:
        return self.stage_two.model

    @property
    def model_api_key(self) -> str:
        return "local"

    @property
    def model_timeout_seconds(self) -> float:
        return self.stage_two.timeout_seconds

    @property
    def events_path(self) -> Path:
        return self.state_dir / "events.jsonl"

    @property
    def history_path(self) -> Path:
        return self.state_dir / "message-history.json"

    @property
    def identity_key_path(self) -> Path:
        return self.state_dir / "identity.key"

    @property
    def context_socket_path(self) -> Path:
        return self.state_dir / "context.sock"

    @property
    def candid_path(self) -> Path:
        return self.state_dir / "activity-window.json"


def default_config_path() -> Path:
    return user_config_path("agent-away-message") / "config.toml"


def default_settings() -> Settings:
    return Settings(state_dir=user_state_path("agent-away-message"))


def validate_local_url(value: object) -> str:
    if not isinstance(value, str):
        raise ConfigError("local model URL must be a string")
    try:
        parsed = urlparse(value)
        invalid = (
            parsed.scheme not in {"http", "https"}
            or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        )
    except ValueError as error:
        raise ConfigError(
            "local model URL must be credential-free loopback HTTP"
        ) from error
    if invalid:
        raise ConfigError("local model URL must be credential-free loopback HTTP")
    return value


def _table(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{name} must be a TOML table")
    return cast(dict[str, Any], value)


def _positive(value: object, label: str, default: float) -> float:
    value = default if value is None else value
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
        raise ConfigError(f"{label} must be positive")
    return float(value)


def _strings(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ConfigError(f"{label} must be a list of strings")
    strings = tuple(cast(str, item) for item in value)
    if any("\x00" in item for item in strings):
        raise ConfigError(f"{label} contains an invalid process string")
    return strings


def _backend(value: object, name: str, defaults: BackendSettings) -> BackendSettings:
    table = _table(value, name)
    if set(table) - {"backend", "local", "command"}:
        raise ConfigError(f"{name} has unsupported fields")
    kind = table.get("backend", "local")
    if kind not in {"local", "command"}:
        raise ConfigError(f"{name}.backend must be local or command")
    if kind == "local":
        local = _table(table.get("local", {}), f"{name}.local")
        allowed = {
            "url",
            "model",
            "timeout_seconds",
            "chat_template_kwargs",
            "enable_thinking",
        }
        if set(local) - allowed:
            raise ConfigError(f"{name}.local has unsupported fields")
        kwargs = local.get("chat_template_kwargs")
        if kwargs is not None and not isinstance(kwargs, dict):
            raise ConfigError(f"{name}.local.chat_template_kwargs must be a table")
        if "enable_thinking" in local and not isinstance(
            local["enable_thinking"], bool
        ):
            raise ConfigError(f"{name}.local.enable_thinking must be boolean")
        url, model = local.get("url", defaults.url), local.get("model", defaults.model)
        if not isinstance(url, str) or not isinstance(model, str):
            raise ConfigError(f"{name}.local url and model must be strings")
        try:
            validate_local_url(url)
        except ConfigError as error:
            raise ConfigError(f"{name}.local.url is invalid") from error
        return BackendSettings(
            url=url,
            model=model,
            timeout_seconds=_positive(
                local.get("timeout_seconds"),
                f"{name}.local.timeout_seconds",
                defaults.timeout_seconds,
            ),
            chat_template_kwargs=kwargs,
            enable_thinking=local.get("enable_thinking"),
        )
    command = _table(table.get("command", {}), f"{name}.command")
    if set(command) - {"executable", "args", "timeout_seconds", "pass_environment"}:
        raise ConfigError(f"{name}.command has unsupported fields")
    executable = command.get("executable")
    if (
        not isinstance(executable, str)
        or not executable
        or "\x00" in executable
        or not Path(executable).is_absolute()
    ):
        raise ConfigError(f"{name}.command.executable must be an absolute path")
    args = _strings(command.get("args", []), f"{name}.command.args")
    env = _strings(
        command.get("pass_environment", []), f"{name}.command.pass_environment"
    )
    if any(not item or "=" in item for item in env):
        raise ConfigError(f"{name}.command.pass_environment has invalid names")
    return BackendSettings(
        kind="command",
        executable=executable,
        args=args,
        pass_environment=env,
        timeout_seconds=_positive(
            command.get("timeout_seconds"),
            f"{name}.command.timeout_seconds",
            defaults.timeout_seconds,
        ),
    )


def load_settings(
    config_path: Path | None = None,
    *,
    state_dir: Path | None = None,
    context_mode: str | None = None,
    publication_mode: str | None = None,
    model_url: str | None = None,
    model_name: str | None = None,
    model_timeout: float | None = None,
) -> Settings:
    settings = default_settings()
    path = config_path if config_path is not None else default_config_path()
    if config_path is not None and not path.is_file():
        raise ConfigError("application config does not exist")
    if path.exists():
        try:
            raw = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
            raise ConfigError("application config is invalid") from error
        if not isinstance(raw, dict) or raw.get("schema_version") != _CONFIG_VERSION:
            raise ConfigError(
                "application config schema_version must be 2; migrate configuration"
            )
        if set(raw) - {
            "schema_version",
            "context_mode",
            "privacy",
            "stage_one",
            "stage_two",
        }:
            raise ConfigError("application config has unsupported fields")
        privacy = _table(raw.get("privacy", {}), "privacy")
        if set(privacy) - {"allow_remote_context"} or not isinstance(
            privacy.get("allow_remote_context", False), bool
        ):
            raise ConfigError("privacy.allow_remote_context must be boolean")
        mode = raw.get("context_mode", settings.context_mode.value)
        if mode == "locked":
            mode = "generic"
        try:
            parsed_mode = ContextMode(mode)
        except ValueError as error:
            raise ConfigError("context_mode must be generic or candid") from error
        settings = replace(
            settings,
            context_mode=parsed_mode,
            stage_one=_backend(
                raw.get("stage_one", {}), "stage_one", settings.stage_one
            ),
            stage_two=_backend(
                raw.get("stage_two", {}), "stage_two", settings.stage_two
            ),
            allow_remote_context=privacy.get("allow_remote_context", False),
        )
    if state_dir is not None:
        settings = replace(settings, state_dir=state_dir)
    if context_mode is not None:
        if context_mode == "locked":
            context_mode = "generic"
        try:
            settings = replace(settings, context_mode=ContextMode(context_mode))
        except ValueError as error:
            raise ConfigError("context mode must be generic or candid") from error
    if publication_mode is not None:
        settings = replace(settings, publication_mode=PublicationMode(publication_mode))
    if any(item is not None for item in (model_url, model_name, model_timeout)):

        def override(backend: BackendSettings) -> BackendSettings:
            if backend.kind != "local":
                return backend
            url = model_url if model_url is not None else backend.url
            validate_local_url(url)
            return replace(
                backend,
                url=url,
                model=model_name if model_name is not None else backend.model,
                timeout_seconds=model_timeout
                if model_timeout is not None
                else backend.timeout_seconds,
            )

        settings = replace(
            settings,
            stage_one=override(settings.stage_one),
            stage_two=override(settings.stage_two),
        )
    return settings
