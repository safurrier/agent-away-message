"""Click command surface for safe local operation and native hook bridges."""

from __future__ import annotations

import fcntl
import json
import os
import shlex
import socket
import time
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path
from typing import TextIO

import click

from agent_away_message.adapters import (
    AdapterError,
    codex_candid_source,
    codex_context_source,
    codex_record,
    pi_candid_source,
    pi_context_source,
    pi_record,
)
from agent_away_message.config import (
    BackendSettings,
    ConfigError,
    Settings,
    default_settings,
    load_settings,
)
from agent_away_message.context import (
    MAX_CONTEXT_PACKET_BYTES,
    ContextQueue,
    deliver_context,
    process_one,
    receive_context,
)
from agent_away_message.daemon import (
    CountChangeDebouncer,
    PreviewGenerationError,
    load_active_sessions,
    refresh,
    safe_generation_error,
    valid_fallback,
)
from agent_away_message.generation import (
    CommandClient,
    CompletionClient,
    GenerationError,
    ModelRequestError,
    OpenAICompatibleClient,
    load_history,
    quarantine_invalid_activity,
    quarantine_invalid_history,
    reduce_and_check_candid_activity,
    remove_candid_status,
    save_candid_status,
)
from agent_away_message.history_simulation import simulate_history
from agent_away_message.identity import IdentityError, digest_session_id
from agent_away_message.installation import (
    InstallationError,
    atomic_write,
    codex_configured,
    executable_available,
    merge_codex_hooks,
    pi_configured,
    pi_extension_text,
    resolved_executable,
)
from agent_away_message.models import (
    TURN_EVENTS,
    ActiveSession,
    ContextMode,
    Harness,
    LifecycleEvent,
    LifecycleRecord,
    Presence,
    PublicationMode,
    turn_token_for,
)
from agent_away_message.privacy import PrivacyError, validate_public_hint
from agent_away_message.publisher import (
    DiscordPublisher,
    MemoryPublisher,
    Publisher,
    PypresenceClient,
    discover_discord_accounts,
)
from agent_away_message.session_inspector import SessionInspectionError, inspect_session
from agent_away_message.store import EventStore, EventStoreError


class OwnedContextSocket:
    """Socket plus a kernel-released exclusive lock for its pathname."""

    def __init__(self, server: socket.socket, path: Path, lock_descriptor: int) -> None:
        self._server = server
        self.path = path
        self._lock_descriptor = lock_descriptor
        self._closed = False

    def recv(self, size: int) -> bytes:
        return self._server.recv(size)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        # Keep the lock through unlink: a successor cannot bind between cleanup
        # and release, and an unclean death releases the kernel lock automatically.
        try:
            self._server.close()
            self.path.unlink(missing_ok=True)
        finally:
            fcntl.flock(self._lock_descriptor, fcntl.LOCK_UN)
            os.close(self._lock_descriptor)


class AppContext:
    """Shared immutable CLI state."""

    def __init__(
        self, settings: Settings, as_json: bool, config_path: Path | None
    ) -> None:
        self.settings = settings
        self.as_json = as_json
        self.config_path = config_path


@dataclass(slots=True)
class _DaemonRuntime:
    """Own one foreground daemon's resources and iteration ordering."""

    app: AppContext
    stage_one_client: CompletionClient
    stage_two_client: CompletionClient
    publisher: Publisher
    context_socket: OwnedContextSocket | None
    count_debouncer: CountChangeDebouncer | None
    queue: ContextQueue
    generation_error: str | None = None
    force_generic: bool = False

    def step(self) -> tuple[bool, Presence | None]:
        """Return whether an iteration was admitted and its optional presence."""
        self.generation_error = None
        if self.context_socket is not None:
            try:
                self._process_context()
            except ModelRequestError as error:
                if self.app.settings.publication_mode is PublicationMode.PREVIEW:
                    raise
                return True, self._publish_stage_one_fallback(error)
        current = datetime.now(UTC)
        sessions = load_active_sessions(self.app.settings, current)
        refresh_settings = self.app.settings
        if self.force_generic:
            refresh_settings = replace(
                self.app.settings, context_mode=ContextMode.GENERIC
            )
            sessions = [
                ActiveSession(
                    item.harness,
                    item.session_id,
                    item.expires_at,
                    None,
                    item.current_turn_token,
                    None,
                )
                for item in sessions
            ]
            self.force_generic = False
        if self.count_debouncer is not None:
            admitted = self.count_debouncer.admit(sessions, current)
            if admitted is None:
                return False, None
            sessions = admitted
        presence = refresh(
            refresh_settings,
            self.stage_two_client,
            self.publisher,
            current,
            sessions=sessions,
            on_generation_error=self._record_generation_error,
        )
        return True, presence

    def close(self) -> None:
        try:
            self.publisher.close()
        finally:
            if self.context_socket is not None:
                self.context_socket.close()

    def next_sleep(self, interval: int) -> float:
        if self.count_debouncer is None:
            return float(interval)
        remaining = self.count_debouncer.seconds_until_due(datetime.now(UTC))
        return float(interval) if remaining is None else min(interval, remaining)

    def _record_generation_error(self, error: GenerationError) -> None:
        self.generation_error = safe_generation_error(error)

    def _publish_stage_one_fallback(self, error: ModelRequestError) -> Presence | None:
        """Retain recent public prose without invoking stage two after source egress fails."""
        self._record_generation_error(error)
        current = datetime.now(UTC)
        sessions = load_active_sessions(self.app.settings, current)
        history = load_history(
            self.app.settings.history_path, self.app.settings.history_limit
        )
        generated = valid_fallback(
            history, current, self.app.settings.retain_last_seconds
        )
        if not sessions or generated is None:
            self.publisher.publish(None)
            return None
        count = len(sessions)
        noun = "agent" if count == 1 else "agents"
        presence = Presence(
            details=f"{count} coding {noun} active",
            state=generated.state,
        )
        self.publisher.publish(presence)
        return presence

    def _process_context(self) -> None:
        assert self.context_socket is not None
        _drain_context_socket(self.context_socket, self.queue)
        process_one(
            self.queue,
            partial(reduce_and_check_candid_activity, self.stage_one_client),
            lambda session_id, token, activity, phase: save_candid_status(
                self.app.settings.candid_path,
                session_id,
                token,
                activity,
                self.app.settings.activity_window_limit,
                phase=phase,
            ),
            self._reject_context,
        )

    def _reject_context(self, session_id: str, token: str, phase: str) -> None:
        if phase != "settled":
            return
        self.queue.suppress_provisional(session_id, token)
        remove_candid_status(
            self.app.settings.candid_path,
            session_id,
            token,
            self.app.settings.activity_window_limit,
        )
        self.force_generic = True


def _emit_daemon_error(app: AppContext, publisher: Publisher, error: Exception) -> None:
    try:
        publisher.publish(None)
    except (ConnectionError, OSError):
        pass
    _emit(
        app,
        {
            "active": None,
            "details": None,
            "state": None,
            "generation_error": safe_generation_error(error),
        },
    )


def _emit(context: AppContext, payload: dict[str, object]) -> None:
    if context.as_json:
        click.echo(json.dumps(payload, sort_keys=True))
    else:
        click.echo(" ".join(f"{key}={value}" for key, value in payload.items()))


def _settings(
    config_path: Path | None,
    state_dir: Path | None,
    context_mode: str | None,
    publication_mode: str | None,
    model_url: str | None,
    model_name: str | None,
    model_timeout: float | None,
) -> tuple[Settings, Path | None]:
    return (
        load_settings(
            config_path,
            state_dir=state_dir,
            context_mode=context_mode,
            publication_mode=publication_mode,
            model_url=model_url,
            model_name=model_name,
            model_timeout=model_timeout,
        ),
        config_path,
    )


@click.group()
@click.option(
    "--config", "config_path", type=click.Path(path_type=Path, dir_okay=False)
)
@click.option("--state-dir", type=click.Path(path_type=Path, file_okay=False))
@click.option(
    "--context-mode",
    type=click.Choice(["generic", "candid", "locked", "contextual"]),
    default=None,
)
@click.option(
    "--publication-mode", type=click.Choice(["preview", "discord"]), default=None
)
@click.option("--model-url", default=None)
@click.option("--model-name", default=None)
@click.option("--model-timeout", type=click.FloatRange(min=0.1), default=None)
@click.option(
    "--json", "as_json", is_flag=True, help="Write machine-readable successful results."
)
@click.pass_context
def cli(
    context: click.Context,
    config_path: Path | None,
    state_dir: Path | None,
    context_mode: str | None,
    publication_mode: str | None,
    model_url: str | None,
    model_name: str | None,
    model_timeout: float | None,
    as_json: bool,
) -> None:
    """Create privacy-reduced activity statuses from exact Pi and Codex lifecycle events."""
    try:
        settings, resolved_config_path = _settings(
            config_path,
            state_dir,
            context_mode,
            publication_mode,
            model_url,
            model_name,
            model_timeout,
        )
    except ConfigError as error:
        # Native adapters must append exact lifecycle facts even after config drift.
        if context.invoked_subcommand in {"codex-hook", "pi-hook"}:
            fallback = default_settings()
            settings = Settings(
                state_dir=state_dir or fallback.state_dir,
                context_mode=ContextMode.CANDID
                if context_mode == "candid"
                else ContextMode.GENERIC,
                publication_mode=PublicationMode(publication_mode or "preview"),
            )
            context.obj = AppContext(settings, as_json, None)
            return
        raise click.ClickException(str(error)) from error
    context.obj = AppContext(settings, as_json, resolved_config_path)


@cli.command("discord-accounts")
@click.option(
    "--discord-client-id",
    required=True,
    help="Discord application client ID used for local IPC handshakes.",
)
@click.pass_obj
def discord_accounts(app: AppContext, discord_client_id: str) -> None:
    """List Discord accounts reachable through local desktop IPC."""
    try:
        accounts = discover_discord_accounts(discord_client_id)
    except (ConnectionError, OSError) as error:
        raise click.ClickException(str(error)) from error
    payload = [
        {
            "pipe": account.pipe,
            "user_id": account.user_id,
            "username": account.username,
            "display_name": account.display_name,
        }
        for account in accounts
    ]
    if app.as_json:
        click.echo(json.dumps({"accounts": payload}, sort_keys=True))
        return
    if not payload:
        click.echo("No reachable Discord accounts.")
        return
    click.echo("PIPE\tUSER ID\tUSERNAME\tDISPLAY NAME")
    for account in payload:
        click.echo(
            f"{account['pipe']}\t{account['user_id']}\t{account['username']}\t"
            f"{account['display_name'] or ''}"
        )


@cli.command()
@click.option(
    "--once", is_flag=True, help="Refresh once instead of running continuously."
)
@click.option("--interval", type=click.IntRange(1, 3600), default=30, show_default=True)
@click.option(
    "--discord-client-id", help="Discord application client ID for discord mode."
)
@click.option(
    "--discord-user-id",
    help="Publish only through the local Discord account with this stable user ID.",
)
@click.pass_obj
def daemon(
    app: AppContext,
    once: bool,
    interval: int,
    discord_client_id: str | None,
    discord_user_id: str | None,
) -> None:
    """Run the foreground service loop; preview is the default publication mode."""
    if not once:
        try:
            quarantined = quarantine_invalid_history(
                app.settings.history_path, app.settings.history_limit
            )
        except GenerationError as error:
            raise click.ClickException(str(error)) from error
        if quarantined is not None:
            _emit(app, {"history_quarantined": True})
        if app.settings.context_mode is ContextMode.CANDID:
            try:
                activity_quarantined = quarantine_invalid_activity(
                    app.settings.candid_path, app.settings.activity_window_limit
                )
            except GenerationError as error:
                raise click.ClickException(str(error)) from error
            if activity_quarantined is not None:
                _emit(app, {"activity_quarantined": True})
    if app.settings.publication_mode.value == "discord":
        if not discord_client_id:
            raise click.ClickException(
                "--discord-client-id is required in discord mode"
            )
        publisher = DiscordPublisher(
            PypresenceClient(discord_client_id, discord_user_id)
        )
    else:
        publisher = MemoryPublisher()
    try:
        stage_one_client, stage_two_client = _model_clients(app.settings)
    except GenerationError as error:
        raise click.ClickException(str(error)) from error
    _emit(app, _routing_disclosure(app.settings))
    context_socket = (
        _open_context_socket(app.settings)
        if app.settings.context_mode is ContextMode.CANDID
        else None
    )
    runtime = _DaemonRuntime(
        app=app,
        stage_one_client=stage_one_client,
        stage_two_client=stage_two_client,
        publisher=publisher,
        context_socket=context_socket,
        count_debouncer=CountChangeDebouncer()
        if app.settings.publication_mode is PublicationMode.DISCORD and not once
        else None,
        queue=ContextQueue(),
    )
    try:
        while True:
            try:
                admitted, presence = runtime.step()
            except (
                PreviewGenerationError,
                GenerationError,
                EventStoreError,
                ConnectionError,
                OSError,
            ) as error:
                if once:
                    raise click.ClickException(safe_generation_error(error)) from error
                _emit_daemon_error(app, publisher, error)
                time.sleep(interval)
                continue
            if not admitted:
                time.sleep(runtime.next_sleep(interval))
                continue
            payload: dict[str, object] = {
                "active": presence is not None,
                "details": presence.details if presence else None,
                "state": presence.state if presence else None,
            }
            if runtime.generation_error is not None:
                payload["generation_error"] = runtime.generation_error
            _emit(app, payload)
            if once:
                break
            time.sleep(interval)
    except KeyboardInterrupt:
        return
    finally:
        runtime.close()


@cli.command()
@click.pass_obj
def status(app: AppContext) -> None:
    """Show current exact liveness reduction without calling a model or Discord."""
    try:
        sessions = EventStore(app.settings.events_path).active_sessions()
    except EventStoreError as error:
        raise click.ClickException("local event state is invalid") from error
    _emit(
        app,
        {
            "active_agents": len(sessions),
            "harnesses": sorted({session.harness.value for session in sessions}),
        },
    )


@cli.command()
@click.pass_obj
def preview(app: AppContext) -> None:
    """Generate a one-shot local preview; model failures are surfaced."""
    publisher = MemoryPublisher()
    try:
        _stage_one, stage_two = _model_clients(app.settings)
        presence = refresh(
            app.settings,
            stage_two,
            publisher,
        )
    except (PreviewGenerationError, GenerationError, EventStoreError) as error:
        raise click.ClickException(safe_generation_error(error)) from error
    _emit(
        app,
        {
            "active": presence is not None,
            "details": presence.details if presence else None,
            "state": presence.state if presence else None,
        },
    )


@cli.command()
@click.option(
    "--harness", type=click.Choice([item.value for item in Harness]), required=True
)
@click.option(
    "--session-id", required=True, help="Opaque stable session or process identifier."
)
@click.option(
    "--event",
    "event_name",
    type=click.Choice([LifecycleEvent.STARTED.value, LifecycleEvent.STOPPED.value]),
    required=True,
)
@click.option(
    "--activity", help="Already-public activity used only by candid status generation."
)
@click.pass_obj
def ingest(
    app: AppContext,
    harness: str,
    session_id: str,
    event_name: str,
    activity: str | None,
) -> None:
    """Ingest an exact lifecycle event and optional already-public candid activity."""
    try:
        hint = validate_public_hint(activity) if activity else None
        now = datetime.now(UTC)
        record = LifecycleRecord(
            schema_version=1,
            harness=Harness(harness),
            session_id=digest_session_id(session_id, app.settings.identity_key_path),
            event=LifecycleEvent(event_name),
            occurred_at=now,
            expires_at=now + timedelta(seconds=app.settings.ttl_seconds),
            provenance="cli",
            public_activity_hint=hint,
        )
        EventStore(app.settings.events_path).append(record)
    except (EventStoreError, IdentityError, PrivacyError) as error:
        raise click.ClickException(
            "event was rejected by the privacy contract"
        ) from error
    _emit(app, {"accepted": True, "event": event_name})


def _read_hook(stream: TextIO) -> str:
    content = stream.read()
    if not content.strip():
        raise click.ClickException("hook stdin is empty")
    return content


def _open_context_socket(settings: Settings) -> OwnedContextSocket:
    """Serialize recovery and binding with a kernel-released local file lock."""
    settings.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    settings.state_dir.chmod(0o700)
    path = settings.context_socket_path
    lock_path = path.with_suffix(path.suffix + ".lock")
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    lock_path.chmod(0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as error:
        os.close(descriptor)
        raise OSError("context socket is already owned by an active daemon") from error
    try:
        if path.exists():
            probe = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
            try:
                probe.connect(str(path))
                probe.send(b"")
            except OSError:
                path.unlink(missing_ok=True)
            else:
                raise OSError("context socket is already owned by an active daemon")
            finally:
                probe.close()
        server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        try:
            server.bind(str(path))
        except OSError:
            server.close()
            raise
        path.chmod(0o600)
        server.setblocking(False)
        return OwnedContextSocket(server, path, descriptor)
    except OSError:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)
        raise


def _drain_context_socket(server: OwnedContextSocket, queue: ContextQueue) -> None:
    while True:
        try:
            packet = server.recv(MAX_CONTEXT_PACKET_BYTES + 1)
        except BlockingIOError:
            return
        job = receive_context(packet)
        if job is not None:
            queue.submit(job)


def _model_client(backend: BackendSettings) -> CompletionClient:
    if backend.kind == "local":
        return OpenAICompatibleClient(
            backend.url,
            backend.model,
            timeout_seconds=backend.timeout_seconds,
            chat_template_kwargs=backend.chat_template_kwargs,
            enable_thinking=backend.enable_thinking,
        )
    return CommandClient(
        executable=backend.executable or "",
        args=backend.args,
        timeout_seconds=backend.timeout_seconds,
        pass_environment=backend.pass_environment,
    )


def _model_clients(settings: Settings) -> tuple[CompletionClient, CompletionClient]:
    if settings.stage_one.remote and not settings.allow_remote_context:
        raise GenerationError(
            "external stage one requires privacy.allow_remote_context=true"
        )
    return _model_client(settings.stage_one), _model_client(settings.stage_two)


def _routing_disclosure(settings: Settings) -> dict[str, object]:
    return {
        "stage_one_backend": settings.stage_one.kind,
        "stage_two_backend": settings.stage_two.kind,
        "raw_context_shared_with_external_backend": (
            settings.context_mode is ContextMode.CANDID
            and settings.stage_one.remote
            and settings.allow_remote_context
        ),
    }


def _codex_fallback_id() -> str:
    """Prefer the stable tmux pane, then a bounded parent-process identity."""
    return os.environ.get("TMUX_PANE") or f"ppid-{os.getppid()}"


def _codex_hook_command(
    settings: Settings, executable: str, config_path: Path | None
) -> str:
    parts = [executable]
    if config_path is not None:
        parts.extend(["--config", str(config_path)])
    parts.extend(["--state-dir", str(settings.state_dir)])
    if settings.context_mode is ContextMode.CANDID:
        parts.extend(["--context-mode", settings.context_mode.value])
    parts.append("codex-hook")
    return shlex.join(parts)


@cli.command("codex-hook")
@click.pass_obj
def codex_hook(app: AppContext) -> None:
    """Read a native Codex hook JSON envelope from stdin; errors fail soft."""
    try:
        payload = _read_hook(click.get_text_stream("stdin"))
        record = codex_record(
            payload,
            app.settings.ttl_seconds,
            identity_key=app.settings.identity_key_path,
            fallback_session_id=_codex_fallback_id(),
        )
        store = EventStore(app.settings.events_path)
        store.append(record)
        if app.settings.context_mode is ContextMode.CANDID:
            # Input produces advisory provisional activity; Stop settles that same turn.
            if record.event in TURN_EVENTS:
                source = codex_context_source(payload)
                turn_token = turn_token_for(
                    record.harness, record.session_id, record.event, record.occurred_at
                )
                phase = "provisional"
            else:
                source = codex_candid_source(payload)
                turn_token = store.completion_turn_token(record)
                phase = "settled"
            if source is not None and turn_token is not None:
                deliver_context(
                    app.settings.context_socket_path,
                    record.session_id,
                    turn_token,
                    source,
                    phase,
                )
    except (
        AdapterError,
        EventStoreError,
        GenerationError,
        OSError,
        click.ClickException,
    ):
        return


@cli.command("pi-hook")
@click.pass_obj
def pi_hook(app: AppContext) -> None:
    """Read the Pi extension's minimal JSON envelope from stdin; errors fail soft."""
    try:
        payload = _read_hook(click.get_text_stream("stdin"))
        record = pi_record(
            payload,
            app.settings.ttl_seconds,
            identity_key=app.settings.identity_key_path,
        )
        store = EventStore(app.settings.events_path)
        store.append(record)
        if app.settings.context_mode is ContextMode.CANDID:
            if record.event in TURN_EVENTS:
                source = pi_context_source(payload)
                turn_token = turn_token_for(
                    record.harness, record.session_id, record.event, record.occurred_at
                )
                phase = "provisional"
            else:
                source = pi_candid_source(payload)
                turn_token = store.completion_turn_token(record)
                phase = "settled"
            if source is not None and turn_token is not None:
                deliver_context(
                    app.settings.context_socket_path,
                    record.session_id,
                    turn_token,
                    source,
                    phase,
                )
    except (AdapterError, EventStoreError, OSError, click.ClickException):
        return


@cli.command()
@click.option("--codex-config", "--config", type=click.Path(path_type=Path))
@click.option("--pi-extension", type=click.Path(path_type=Path))
@click.option(
    "--dry-run",
    is_flag=True,
    help="Preview whether additive integration changes are needed without writing.",
)
@click.pass_obj
def setup(
    app: AppContext,
    codex_config: Path | None,
    pi_extension: Path | None,
    dry_run: bool,
) -> None:
    """Install Codex hooks and/or the Pi extension additively and idempotently."""
    if codex_config is None and pi_extension is None:
        raise click.ClickException("provide --codex-config and/or --pi-extension")
    changed: dict[str, bool] = {}
    try:
        executable = resolved_executable()
        if codex_config is not None:
            existing: dict[str, object] = {}
            if codex_config.exists():
                loaded = json.loads(codex_config.read_text(encoding="utf-8"))
                if not isinstance(loaded, dict):
                    raise InstallationError("Codex config must be a JSON object")
                existing = loaded
            updated, codex_changed = merge_codex_hooks(
                existing, _codex_hook_command(app.settings, executable, app.config_path)
            )
            changed["codex"] = codex_changed
            if codex_changed and not dry_run:
                atomic_write(codex_config, json.dumps(updated, indent=2) + "\n")
        if pi_extension is not None:
            source = pi_extension_text(
                executable,
                app.settings.state_dir,
                app.settings.context_mode,
                app.config_path,
            )
            pi_changed = (
                not pi_extension.exists()
                or pi_extension.read_text(encoding="utf-8") != source
            )
            changed["pi"] = pi_changed
            if pi_changed and not dry_run:
                atomic_write(pi_extension, source)
    except (OSError, json.JSONDecodeError, InstallationError) as error:
        raise click.ClickException(
            "setup could not safely update integrations"
        ) from error
    _emit(app, {"changed": any(changed.values()), "dry_run": dry_run, **changed})


@cli.command()
@click.option("--codex-config", type=click.Path(path_type=Path))
@click.option("--pi-extension", type=click.Path(path_type=Path))
@click.pass_obj
def doctor(
    app: AppContext, codex_config: Path | None, pi_extension: Path | None
) -> None:
    """Report local integration readiness without contacting Discord or a model."""
    _emit(
        app,
        {
            "state_dir": str(app.settings.state_dir),
            "events_file": app.settings.events_path.exists(),
            "default_context_mode": app.settings.context_mode.value,
            "default_publication_mode": app.settings.publication_mode.value,
            **_routing_disclosure(app.settings),
            "executable": executable_available(),
            "pi_extension": pi_configured(pi_extension, app.settings.context_mode)
            if pi_extension is not None
            else False,
            "codex_hook": codex_configured(codex_config, app.settings.context_mode)
            if codex_config is not None
            else False,
        },
    )


@cli.group()
def fixture() -> None:
    """Inspect privacy-safe synthetic fixtures; no raw prompt history is accepted."""


@fixture.command("inspect")
@click.argument("path", type=click.Path(exists=True, path_type=Path))
@click.pass_obj
def fixture_inspect(app: AppContext, path: Path) -> None:
    """Validate a JSONL fixture and report only aggregate counts."""
    store = EventStore(path)
    try:
        records = store.inspect_fixture()
        active = store.active_sessions()
    except EventStoreError as error:
        raise click.ClickException("fixture violates the privacy contract") from error
    _emit(app, {"valid": True, "records": records, "active_agents": len(active)})


@cli.command("session-inspect")
@click.option(
    "--harness", type=click.Choice([item.value for item in Harness]), required=True
)
@click.argument("path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.pass_obj
def session_inspect(app: AppContext, harness: str, path: Path) -> None:
    """Inspect one explicitly supplied session path and print structural counts only."""
    try:
        counts = inspect_session(path, Harness(harness))
    except SessionInspectionError as error:
        raise click.ClickException(
            "session did not match the structural contract"
        ) from error
    _emit(app, {"records": sum(counts.values()), "types": counts})


def _parse_since(value: str) -> timedelta:
    if not value.endswith("h") or not value[:-1].isdigit():
        raise click.BadParameter("use an hour duration such as 24h")
    hours = int(value[:-1])
    if not 1 <= hours <= 168:
        raise click.BadParameter("duration must be between 1h and 168h")
    return timedelta(hours=hours)


@cli.command("simulate-history")
@click.option("--since", "since_value", default="24h", show_default=True)
@click.option(
    "--codex-root",
    type=click.Path(path_type=Path, file_okay=False),
    default=Path.home() / ".codex" / "sessions",
    show_default=True,
)
@click.option(
    "--pi-root",
    type=click.Path(path_type=Path, file_okay=False),
    default=Path.home() / ".pi" / "agent" / "sessions",
    show_default=True,
)
@click.option(
    "--max-examples", type=click.IntRange(0, 32), default=12, show_default=True
)
@click.pass_obj
def simulate_history_command(
    app: AppContext,
    since_value: str,
    codex_root: Path,
    pi_root: Path,
    max_examples: int,
) -> None:
    """Approximate recent presence from local history without publishing it."""
    now = datetime.now(UTC)
    if (
        app.settings.context_mode is ContextMode.CANDID
        and app.settings.stage_one.remote
    ):
        raise click.ClickException(
            "candid simulate-history requires a local stage one backend"
        )
    try:
        stage_one, stage_two = _model_clients(app.settings)
        result = simulate_history(
            stage_one,
            stage_two,
            mode=app.settings.context_mode,
            codex_root=codex_root,
            pi_root=pi_root,
            since=now - _parse_since(since_value),
            now=now,
            ttl_seconds=app.settings.ttl_seconds,
            max_examples=max_examples,
        )
    except GenerationError as error:
        raise click.ClickException("history replay generation failed") from error
    if app.as_json:
        click.echo(json.dumps(result, sort_keys=True))
    else:
        click.echo(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    cli()
