from __future__ import annotations

import json
import os
import sys
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
POSIX_DISPATCHER = (
    ROOT / "skills" / "generate-image-presentation" / "scripts" / "image-pptgen-dispatch"
)

pytestmark = pytest.mark.skipif(
    sys.platform != "linux", reason="Linux generate-and-follow composition"
)


def _write_fake_cli(
    path: Path,
    calls: Path,
    *,
    generate_json: str = '{"batch_id":1,"run_ids":[7]}',
    generate_exit: int = 0,
    follow_json: str = '{"run_id":7,"source_facts":{"run_status":"completed"}}',
    follow_exit: int = 0,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"#!{sys.executable}\n"
        "from pathlib import Path\n"
        "import sys\n"
        "args = sys.argv[1:]\n"
        f"calls = Path({str(calls)!r})\n"
        "calls.parent.mkdir(parents=True, exist_ok=True)\n"
        "with calls.open('a', encoding='utf-8') as handle:\n"
        "    handle.write('cli:' + ' '.join(args) + '\\n')\n"
        "if args[:1] == ['generate']:\n"
        f"    sys.stdout.write({generate_json!r} + '\\n')\n"
        f"    raise SystemExit({generate_exit})\n"
        "if args[:1] == ['status']:\n"
        f"    sys.stdout.write({follow_json!r} + '\\n')\n"
        f"    raise SystemExit({follow_exit})\n"
        "if args[:1] == ['generate-and-follow']:\n"
        "    sys.stdout.write('RAW_CLI_RECEIVED_COMPOSITE\\n')\n"
        "    raise SystemExit(99)\n"
        "sys.stdout.write('unexpected:' + ' '.join(args) + '\\n')\n"
        "raise SystemExit(98)\n",
        encoding="utf-8",
    )
    path.chmod(0o755)
    python3 = path.parent / "python3"
    if not python3.exists():
        python3.symlink_to(sys.executable)


def _run(
    args: list[str],
    *,
    env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/sh", str(POSIX_DISPATCHER), *args],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=20,
    )


def _isolated_env(tmp_path: Path, *, cli: Path | None = None) -> dict[str, str]:
    environment = os.environ.copy()
    environment["HOME"] = str(tmp_path / "home")
    environment["XDG_DATA_HOME"] = str(tmp_path / "xdg-data")
    environment.pop("IMAGE_PPTGEN_CLI", None)
    if cli is not None:
        environment["IMAGE_PPTGEN_CLI"] = str(cli)
    return environment


def _protocol_error(completed: subprocess.CompletedProcess[str], message: str) -> None:
    assert completed.returncode == 4
    assert completed.stdout == ""
    stderr_lines = [line for line in completed.stderr.splitlines() if line.strip()]
    assert stderr_lines
    payload = json.loads(stderr_lines[-1])
    assert payload["error"] == "platform_error"
    assert payload["message"] == message


def test_linux_generate_and_follow_intercepts_override_before_raw_cli(
    tmp_path: Path,
) -> None:
    calls = tmp_path / "calls.log"
    cli = tmp_path / "bin" / "image-pptgen"
    _write_fake_cli(cli, calls)
    completed = _run(
        ["generate-and-follow", "--deck-id", "9", "--jsonl"],
        env=_isolated_env(tmp_path, cli=cli),
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.splitlines() == [
        '{"batch_id":1,"run_ids":[7]}',
        '{"run_id":7,"source_facts":{"run_status":"completed"}}',
    ]
    lines = calls.read_text(encoding="utf-8").splitlines()
    assert lines == [
        "cli:generate --deck-id 9 --json",
        "cli:status --run-id 7 --follow --jsonl",
    ]
    assert "RAW_CLI_RECEIVED_COMPOSITE" not in completed.stdout


def test_linux_direct_generate_still_execs_public_cli_once(tmp_path: Path) -> None:
    calls = tmp_path / "calls.log"
    cli = tmp_path / "bin" / "image-pptgen"
    _write_fake_cli(cli, calls)
    completed = _run(
        ["generate", "--deck-id", "9", "--json"],
        env=_isolated_env(tmp_path, cli=cli),
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.splitlines() == ['{"batch_id":1,"run_ids":[7]}']
    assert calls.read_text(encoding="utf-8").splitlines() == [
        "cli:generate --deck-id 9 --json"
    ]


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (["generate-and-follow", "--jsonl"], "generate-and-follow requires a positive integer deck id"),
        (
            ["generate-and-follow", "--deck-id", "9"],
            "generate-and-follow requires --jsonl exactly once",
        ),
        (
            ["generate-and-follow", "--deck-id", "9", "--jsonl", "--jsonl"],
            "generate-and-follow accepts --jsonl at most once",
        ),
        (
            ["generate-and-follow", "--deck-id", "9", "--deck-id", "8", "--jsonl"],
            "generate-and-follow accepts --deck-id exactly once",
        ),
        (
            ["generate-and-follow", "--deck-id", "0", "--jsonl"],
            "generate-and-follow requires a positive integer deck id",
        ),
        (
            ["generate-and-follow", "--deck-id", "9", "--jsonl", "--run-id", "7"],
            "generate-and-follow received an unsupported argument",
        ),
        (
            ["generate-and-follow", "--deck-id"],
            "generate-and-follow requires --deck-id",
        ),
    ],
)
def test_linux_generate_and_follow_rejects_missing_duplicate_and_unsupported_args(
    tmp_path: Path, arguments: list[str], message: str
) -> None:
    calls = tmp_path / "calls.log"
    cli = tmp_path / "bin" / "image-pptgen"
    _write_fake_cli(cli, calls)
    completed = _run(arguments, env=_isolated_env(tmp_path, cli=cli))

    _protocol_error(completed, message)
    assert not calls.exists()


@pytest.mark.parametrize(
    "generate_json",
    [
        "not-json",
        "",
        '{"batch_id":1}',
        '{"batch_id":1,"run_ids":[]}',
        '{"batch_id":1,"run_ids":[7,8]}',
        '{"batch_id":1,"run_ids":[0]}',
        '{"batch_id":1,"run_ids":[true]}',
        '{"batch_id":1,"run_ids":["7"]}',
        '{"batch_id":1,"run_ids":[7]}\n{"extra":true}',
        "[7]",
    ],
)
def test_linux_generate_and_follow_rejects_malformed_generation_without_following(
    tmp_path: Path, generate_json: str
) -> None:
    calls = tmp_path / "calls.log"
    cli = tmp_path / "bin" / "image-pptgen"
    _write_fake_cli(cli, calls, generate_json=generate_json)
    completed = _run(
        ["generate-and-follow", "--deck-id", "9", "--jsonl"],
        env=_isolated_env(tmp_path, cli=cli),
    )

    _protocol_error(
        completed, "generation response did not bind exactly one positive run id"
    )
    assert calls.read_text(encoding="utf-8").splitlines() == [
        "cli:generate --deck-id 9 --json"
    ]


def test_linux_nonzero_generate_does_not_follow_and_propagates_exit(
    tmp_path: Path,
) -> None:
    calls = tmp_path / "calls.log"
    cli = tmp_path / "bin" / "image-pptgen"
    _write_fake_cli(
        cli,
        calls,
        generate_json='{"error":"generation_failed"}',
        generate_exit=7,
    )
    completed = _run(
        ["generate-and-follow", "--deck-id", "9", "--jsonl"],
        env=_isolated_env(tmp_path, cli=cli),
    )

    assert completed.returncode == 7
    assert completed.stdout == ""
    assert '{"error":"generation_failed"}' in completed.stderr
    assert calls.read_text(encoding="utf-8").splitlines() == [
        "cli:generate --deck-id 9 --json"
    ]


def test_linux_nonzero_follow_propagates_exit_after_one_generate(
    tmp_path: Path,
) -> None:
    calls = tmp_path / "calls.log"
    cli = tmp_path / "bin" / "image-pptgen"
    _write_fake_cli(cli, calls, follow_exit=9)
    completed = _run(
        ["generate-and-follow", "--deck-id", "9", "--jsonl"],
        env=_isolated_env(tmp_path, cli=cli),
    )

    assert completed.returncode == 9
    assert completed.stdout.splitlines()[0] == '{"batch_id":1,"run_ids":[7]}'
    assert calls.read_text(encoding="utf-8").splitlines() == [
        "cli:generate --deck-id 9 --json",
        "cli:status --run-id 7 --follow --jsonl",
    ]


def test_linux_generate_and_follow_uses_home_local_bin_without_override(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    calls = tmp_path / "calls.log"
    cli = home / ".local" / "bin" / "image-pptgen"
    _write_fake_cli(cli, calls)
    completed = _run(
        ["generate-and-follow", "--deck-id", "3", "--jsonl"],
        env=_isolated_env(tmp_path),
    )

    assert completed.returncode == 0, completed.stderr
    assert calls.read_text(encoding="utf-8").splitlines() == [
        "cli:generate --deck-id 3 --json",
        "cli:status --run-id 7 --follow --jsonl",
    ]
