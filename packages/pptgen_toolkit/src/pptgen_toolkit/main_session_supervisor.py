"""Recover one exact Codex main session after its original process is lost."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping
from uuid import UUID

from .client import PptgenClient

try:
    import fcntl
except ImportError:  # pragma: no cover - exercised through the portability guard
    fcntl = None  # type: ignore[assignment]


ACTIVE_RUN_STATUSES = {"queued", "pending", "running"}
ACTIVE_SUPERVISOR_STATUSES = {
    "starting",
    "watching_main_session",
    "recovering_main_session",
}
ALLOWED_APPROVAL_POLICIES = {"untrusted", "on-request", "never"}
ALLOWED_SANDBOX_MODES = {"read-only", "workspace-write", "danger-full-access"}


class MainSessionSupervisorError(RuntimeError):
    """Raised when an exact-session supervisor cannot start safely."""


@dataclass(frozen=True)
class SessionExecutionProfile:
    approval_policy: str
    model: str
    reasoning_effort: str | None
    sandbox_mode: str


@dataclass(frozen=True)
class ProcessSnapshot:
    pid: int
    parent_pid: int
    comm: str
    argv: tuple[str, ...]


@dataclass(frozen=True)
class SupervisorConfig:
    approval_policy: str
    base_url: str
    main_pid: int
    main_process_start_ticks: str
    max_resume_attempts: int
    model: str | None
    poll_interval_seconds: float
    reasoning_effort: str | None
    run_id: int
    session_id: str
    sandbox_mode: str
    state_path: Path
    work_dir: Path


def _validate_session_id(value: str) -> str:
    try:
        parsed = UUID(value)
    except ValueError as exc:
        raise MainSessionSupervisorError("session_id must be an exact UUID") from exc
    if str(parsed) != value.lower():
        raise MainSessionSupervisorError("session_id must use canonical UUID form")
    return str(parsed)


def resolve_registration_session_id(
    explicit_session_id: str | None,
    *,
    environ: Mapping[str, str] = os.environ,
) -> str:
    """Resolve one exact Session without guessing from recent-session state."""
    value = explicit_session_id or environ.get("CODEX_THREAD_ID")
    if not value:
        raise MainSessionSupervisorError(
            "CODEX_THREAD_ID is unavailable; pass --session-id only for explicit diagnostics"
        )
    return _validate_session_id(value)


def _parse_process_start_ticks(raw_stat: str) -> str | None:
    """Parse field 22 without splitting spaces that Linux permits in comm."""
    marker = raw_stat.rfind(") ")
    if marker < 0:
        return None
    fields_after_comm = raw_stat[marker + 2 :].split()
    return fields_after_comm[19] if len(fields_after_comm) > 19 else None


def _parse_process_parent_pid(raw_stat: str) -> int | None:
    marker = raw_stat.rfind(") ")
    if marker < 0:
        return None
    fields_after_comm = raw_stat[marker + 2 :].split()
    if len(fields_after_comm) < 2:
        return None
    try:
        return int(fields_after_comm[1])
    except ValueError:
        return None


def read_process_snapshot(
    pid: int,
    *,
    proc_root: Path = Path("/proc"),
) -> ProcessSnapshot | None:
    if pid <= 0:
        return None
    process_dir = proc_root / str(pid)
    try:
        raw_stat = (process_dir / "stat").read_text(encoding="utf-8")
        parent_pid = _parse_process_parent_pid(raw_stat)
        comm = (process_dir / "comm").read_text(encoding="utf-8").strip()
        raw_cmdline = (process_dir / "cmdline").read_bytes()
    except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
        return None
    if parent_pid is None:
        return None
    argv = tuple(
        part.decode("utf-8", errors="replace")
        for part in raw_cmdline.split(b"\0")
        if part
    )
    return ProcessSnapshot(
        pid=pid,
        parent_pid=parent_pid,
        comm=comm,
        argv=argv,
    )


UNSAFE_CODEX_HOST_MARKERS = ("app-server", "proxy", "code-mode-host")


def _is_safe_interactive_codex(snapshot: ProcessSnapshot) -> bool:
    executable = Path(snapshot.argv[0]).name if snapshot.argv else ""
    if snapshot.comm.lower() != "codex" and executable.lower() != "codex":
        return False
    command = " ".join(snapshot.argv).lower()
    return not any(marker in command for marker in UNSAFE_CODEX_HOST_MARKERS)


def find_codex_main_pid(
    *,
    start_pid: int | None = None,
    process_reader: Callable[[int], ProcessSnapshot | None] = read_process_snapshot,
) -> int:
    """Return the nearest safe interactive Codex CLI ancestor."""
    current = start_pid if start_pid is not None else os.getppid()
    visited: set[int] = set()
    while current > 0 and current not in visited:
        visited.add(current)
        snapshot = process_reader(current)
        if snapshot is None:
            break
        if _is_safe_interactive_codex(snapshot):
            return snapshot.pid
        current = snapshot.parent_pid
    raise MainSessionSupervisorError(
        "no safe interactive Codex CLI ancestor; shared app-server/proxy hosts are not supervised"
    )


def process_start_ticks(pid: int) -> str | None:
    """Return Linux process start ticks so PID reuse cannot impersonate the main process."""
    if pid <= 0:
        return None
    try:
        raw_stat = (Path("/proc") / str(pid) / "stat").read_text(encoding="utf-8")
    except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
        return None
    return _parse_process_start_ticks(raw_stat)


def process_identity_matches(pid: int, expected_start_ticks: str) -> bool:
    return process_start_ticks(pid) == expected_start_ticks


def read_session_execution_profile(
    session_id: str,
    *,
    codex_home: Path | None = None,
) -> SessionExecutionProfile:
    """Read the latest execution profile recorded by one exact local Session."""
    session_id = _validate_session_id(session_id)
    home = codex_home or Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    matches = list((home / "sessions").rglob(f"*{session_id}*.jsonl"))
    if len(matches) != 1:
        raise MainSessionSupervisorError(
            f"expected one local JSONL for exact session_id, found {len(matches)}"
        )
    latest_payload: dict[str, Any] | None = None
    for line in matches[0].read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict) or event.get("type") != "turn_context":
            continue
        payload = event.get("payload")
        if not isinstance(payload, dict):
            continue
        latest_payload = payload
    if latest_payload is None:
        raise MainSessionSupervisorError("exact Session has no recorded execution profile")
    model = latest_payload.get("model")
    if not isinstance(model, str) or not model:
        raise MainSessionSupervisorError("exact Session has no recorded model profile")
    effort_value = latest_payload.get("effort")
    reasoning_effort = effort_value if isinstance(effort_value, str) and effort_value else None
    approval_policy = latest_payload.get("approval_policy")
    sandbox_policy = latest_payload.get("sandbox_policy")
    sandbox_mode = (
        sandbox_policy.get("type") if isinstance(sandbox_policy, dict) else None
    )
    if (
        approval_policy not in ALLOWED_APPROVAL_POLICIES
        or sandbox_mode not in ALLOWED_SANDBOX_MODES
    ):
        raise MainSessionSupervisorError(
            "exact Session has an unknown or missing execution policy"
        )
    return SessionExecutionProfile(
        approval_policy=approval_policy,
        model=model,
        reasoning_effort=reasoning_effort,
        sandbox_mode=sandbox_mode,
    )


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def _load_state(config: SupervisorConfig) -> dict[str, Any]:
    if config.state_path.exists():
        value = json.loads(config.state_path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise MainSessionSupervisorError("main-session state is invalid")
        if value.get("session_id") != config.session_id or value.get("run_id") != config.run_id:
            raise MainSessionSupervisorError("main-session state belongs to another Session or Run")
        return value
    return {
        "approval_policy": config.approval_policy,
        "base_url": config.base_url,
        "last_seq": None,
        "main_pid": config.main_pid,
        "main_process_start_ticks": config.main_process_start_ticks,
        "model": config.model,
        "reasoning_effort": config.reasoning_effort,
        "resume_attempts": 0,
        "resume_history": [],
        "run_id": config.run_id,
        "run_status": "unknown",
        "session_id": config.session_id,
        "sandbox_mode": config.sandbox_mode,
        "status": "starting",
    }


def _record_run_status(state: dict[str, Any], run_status: dict[str, Any]) -> None:
    state["run_status"] = str(run_status.get("status") or "unknown")
    activity = run_status.get("activity")
    if isinstance(activity, dict):
        next_cursor = activity.get("next_cursor")
        if isinstance(next_cursor, str) and next_cursor:
            state["last_seq"] = next_cursor


def build_resume_command(
    config: SupervisorConfig,
    *,
    last_seq: str | None,
) -> tuple[list[str], str]:
    """Build one exact-ID continuation; never choose a recent or replacement Session."""
    session_id = _validate_session_id(config.session_id)
    follow_command = (
        f"pptgen --base-url {shlex.quote(config.base_url)} status "
        f"--run-id {config.run_id} --follow --jsonl"
    )
    if last_seq:
        follow_command += f" --after-activity-cursor {shlex.quote(last_seq)}"
    result_command = (
        f"pptgen --base-url {shlex.quote(config.base_url)} result "
        f"--run-id {config.run_id} --json"
    )
    prompt = (
        f"继续当前已经存在的 PPTGen Run {config.run_id}，不要创建新的 Deck 或 Run，"
        f"不得调用 generate。请只运行以下续读命令并保持到真实终态：{follow_command}。"
        f"终态后只运行以下结果命令：{result_command}，"
        "再按原 generate-presentation 工作流展示同一个 Run 的 Presentation Preview。"
    )
    command = [
        "codex",
        "-s",
        config.sandbox_mode,
        "-a",
        config.approval_policy,
        "exec",
        "resume",
        "--json",
        "--skip-git-repo-check",
    ]
    if config.model:
        command.extend(["--model", config.model])
    if config.reasoning_effort:
        command.extend(
            ["--config", f"model_reasoning_effort={json.dumps(config.reasoning_effort)}"]
        )
    command.extend([session_id, prompt])
    return command, prompt


def _usage_from_events(events: list[dict[str, Any]]) -> dict[str, int] | None:
    completed = [event for event in events if event.get("type") == "turn.completed"]
    if not completed or not isinstance(completed[-1].get("usage"), dict):
        return None
    return {
        key: int(value)
        for key, value in completed[-1]["usage"].items()
        if isinstance(value, int)
    }


def run_exact_resume(
    command: list[str],
    output_dir: Path,
    attempt: int,
) -> dict[str, Any]:
    """Run and persist one exact-ID resume attempt without owning the PPT Run."""
    output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(output_dir, 0o700)
    raw_path = output_dir / f"attempt-{attempt}.jsonl"
    stderr_path = output_dir / f"attempt-{attempt}.stderr.txt"
    with raw_path.open("w", encoding="utf-8") as stdout, stderr_path.open(
        "w", encoding="utf-8"
    ) as stderr:
        os.chmod(raw_path, 0o600)
        os.chmod(stderr_path, 0o600)
        completed = subprocess.run(
            command,
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            text=True,
        )
    events: list[dict[str, Any]] = []
    for line in raw_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            events.append(value)
    observed_session_id = next(
        (
            str(event["thread_id"])
            for event in events
            if event.get("type") == "thread.started"
            and isinstance(event.get("thread_id"), str)
        ),
        None,
    )
    return {
        "attempt": attempt,
        "exit_code": completed.returncode,
        "observed_session_id": observed_session_id,
        "raw_jsonl_path": str(raw_path),
        "stderr_path": str(stderr_path),
        "usage": _usage_from_events(events),
    }


def supervise_session(
    config: SupervisorConfig,
    *,
    client: PptgenClient | Any | None = None,
    process_identity_matches: Callable[[int, str], bool] = process_identity_matches,
    resume_runner: Callable[[list[str], Path, int], dict[str, Any]] = run_exact_resume,
    sleep: Callable[[float], None] = time.sleep,
    max_wait_cycles: int | None = None,
) -> dict[str, Any]:
    """Watch one main process and resume its exact Session at most twice."""
    if fcntl is None:
        raise MainSessionSupervisorError("exact main-session supervision requires Linux")
    if config.max_resume_attempts != 2:
        raise MainSessionSupervisorError("max_resume_attempts must remain exactly 2")
    _validate_session_id(config.session_id)
    state = _load_state(config)
    platform = client or PptgenClient(config.base_url)
    lock_path = config.state_path.with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock:
        os.chmod(lock_path, 0o600)
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise MainSessionSupervisorError(
                "an exact-session supervisor already owns this Session and Run"
            ) from exc
        wait_cycles = 0
        while True:
            run_status = platform.get_run_status(
                run_id=config.run_id,
                activity_after=state.get("last_seq"),
            )
            _record_run_status(state, run_status)
            if state["run_status"] not in ACTIVE_RUN_STATUSES:
                state["status"] = "run_terminal"
                _atomic_write_json(config.state_path, state)
                return state
            if process_identity_matches(
                config.main_pid,
                config.main_process_start_ticks,
            ):
                state["status"] = "watching_main_session"
                _atomic_write_json(config.state_path, state)
                wait_cycles += 1
                if max_wait_cycles is not None and wait_cycles >= max_wait_cycles:
                    return state
                sleep(config.poll_interval_seconds)
                continue
            break

        output_dir = config.state_path.parent / f"{config.session_id}-{config.run_id}"
        while int(state.get("resume_attempts") or 0) < config.max_resume_attempts:
            attempt = int(state.get("resume_attempts") or 0) + 1
            command, _ = build_resume_command(config, last_seq=state.get("last_seq"))
            state["status"] = "recovering_main_session"
            state["resume_attempts"] = attempt
            _atomic_write_json(config.state_path, state)
            outcome = resume_runner(command, output_dir, attempt)
            if not isinstance(outcome, dict):
                raise MainSessionSupervisorError("resume runner returned invalid evidence")
            state.setdefault("resume_history", []).append(outcome)
            run_status = platform.get_run_status(
                run_id=config.run_id,
                activity_after=state.get("last_seq"),
            )
            _record_run_status(state, run_status)
            exact_session = outcome.get("observed_session_id") == config.session_id
            successful_turn = outcome.get("exit_code") == 0
            if (
                exact_session
                and successful_turn
                and state["run_status"] not in ACTIVE_RUN_STATUSES
            ):
                state["status"] = "completed"
                _atomic_write_json(config.state_path, state)
                return state
            _atomic_write_json(config.state_path, state)
            if attempt < config.max_resume_attempts:
                sleep(2.0 if attempt == 1 else 10.0)

        state["status"] = "main_resume_failed"
        _atomic_write_json(config.state_path, state)
        return state


def _state_root() -> Path:
    configured = os.environ.get("XDG_STATE_HOME")
    base = Path(configured) if configured else Path.home() / ".local" / "state"
    return base / "pptgen" / "main-session"


def start_supervisor(
    *,
    base_url: str,
    main_pid: int,
    run_id: int,
    session_id: str,
) -> dict[str, Any]:
    """Start one detached monitor and return its durable identity."""
    session_id = _validate_session_id(session_id)
    if run_id <= 0:
        raise MainSessionSupervisorError("run_id must be positive")
    start_ticks = process_start_ticks(main_pid)
    if start_ticks is None:
        raise MainSessionSupervisorError("main_pid is not a live readable process")
    if shutil.which("codex") is None:
        raise MainSessionSupervisorError("codex CLI is not available")
    profile = read_session_execution_profile(session_id)
    root = _state_root()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(root, 0o700)
    state_path = root / f"{session_id}-{run_id}.json"
    if state_path.exists():
        prior = json.loads(state_path.read_text(encoding="utf-8"))
        prior_pid = prior.get("supervisor_pid") if isinstance(prior, dict) else None
        if (
            isinstance(prior_pid, int)
            and process_start_ticks(prior_pid) == prior.get("supervisor_process_start_ticks")
            and prior.get("status") in ACTIVE_SUPERVISOR_STATUSES
        ):
            return {**prior, "reused": True}
    stdout_path = root / f"{session_id}-{run_id}.supervisor.stdout.txt"
    stderr_path = root / f"{session_id}-{run_id}.supervisor.stderr.txt"
    startup_read_fd, startup_write_fd = os.pipe()
    command = [
        sys.executable,
        "-m",
        "pptgen_toolkit.main_session_supervisor",
        "run",
        "--base-url",
        base_url,
        "--main-pid",
        str(main_pid),
        "--main-process-start-ticks",
        start_ticks,
        "--approval-policy",
        profile.approval_policy,
        "--model",
        profile.model,
        "--run-id",
        str(run_id),
        "--session-id",
        session_id,
        "--sandbox-mode",
        profile.sandbox_mode,
        "--state-path",
        str(state_path),
        "--startup-fd",
        str(startup_read_fd),
    ]
    if profile.reasoning_effort:
        command.extend(["--reasoning-effort", profile.reasoning_effort])
    state = {
        "approval_policy": profile.approval_policy,
        "base_url": base_url,
        "last_seq": None,
        "main_pid": main_pid,
        "main_process_start_ticks": start_ticks,
        "model": profile.model,
        "reasoning_effort": profile.reasoning_effort,
        "resume_attempts": 0,
        "resume_history": [],
        "run_id": run_id,
        "run_status": "unknown",
        "session_id": session_id,
        "sandbox_mode": profile.sandbox_mode,
        "status": "starting",
        "supervisor_pid": None,
        "supervisor_process_start_ticks": None,
    }
    _atomic_write_json(state_path, state)
    try:
        with stdout_path.open("a", encoding="utf-8") as stdout, stderr_path.open(
            "a", encoding="utf-8"
        ) as stderr:
            os.chmod(stdout_path, 0o600)
            os.chmod(stderr_path, 0o600)
            process = subprocess.Popen(
                command,
                close_fds=True,
                pass_fds=(startup_read_fd,),
                start_new_session=True,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                text=True,
            )
    except BaseException:
        os.close(startup_read_fd)
        os.close(startup_write_fd)
        raise
    os.close(startup_read_fd)
    state["supervisor_pid"] = process.pid
    state["supervisor_process_start_ticks"] = process_start_ticks(process.pid)
    _atomic_write_json(state_path, state)
    try:
        os.write(startup_write_fd, b"1")
    finally:
        os.close(startup_write_fd)
    return {**state, "state_path": str(state_path), "reused": False}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pptgen-main-session-supervisor")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run")
    run.add_argument(
        "--approval-policy",
        choices=sorted(ALLOWED_APPROVAL_POLICIES),
        required=True,
    )
    run.add_argument("--base-url", required=True)
    run.add_argument("--main-pid", type=int, required=True)
    run.add_argument("--main-process-start-ticks", required=True)
    run.add_argument("--model")
    run.add_argument("--run-id", type=int, required=True)
    run.add_argument("--reasoning-effort")
    run.add_argument("--session-id", required=True)
    run.add_argument(
        "--sandbox-mode",
        choices=sorted(ALLOWED_SANDBOX_MODES),
        required=True,
    )
    run.add_argument("--state-path", type=Path, required=True)
    run.add_argument("--startup-fd", type=int, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        startup_signal = os.read(args.startup_fd, 1)
    finally:
        os.close(args.startup_fd)
    if startup_signal != b"1":
        raise MainSessionSupervisorError("supervisor registration handshake failed")
    config = SupervisorConfig(
        approval_policy=args.approval_policy,
        base_url=args.base_url,
        main_pid=args.main_pid,
        main_process_start_ticks=args.main_process_start_ticks,
        max_resume_attempts=2,
        model=args.model,
        poll_interval_seconds=2.0,
        reasoning_effort=args.reasoning_effort,
        run_id=args.run_id,
        session_id=args.session_id,
        sandbox_mode=args.sandbox_mode,
        state_path=args.state_path,
        work_dir=Path.cwd(),
    )
    result = supervise_session(config)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
    return 0 if result.get("status") in {"completed", "run_terminal"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
