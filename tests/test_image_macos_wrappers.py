from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

import pytest

try:
    import fcntl
except ImportError:  # pragma: no cover - macOS and Linux both provide fcntl
    fcntl = None


ROOT = Path(__file__).resolve().parents[1]
MACOS_ROOT = ROOT / "packaging" / "image" / "platform" / "macos"


@pytest.fixture
def held_service_port_lock() -> None:
    """Serialize the fixed 127.0.0.1:3130 held-service fixture."""

    if fcntl is None:  # pragma: no cover
        pytest.skip("held-service fixture requires POSIX flock support")
    lock_path = Path(tempfile.gettempdir()) / "image-pptgen-held-command-test.lock"
    with lock_path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _fixture(
    tmp_path: Path, *, install_root: Path | None = None, manager_exit: int = 0
) -> tuple[Path, Path]:
    install_root = install_root or (tmp_path / "Library" / "Application Support" / "image-pptgen")
    current_app = install_root / "current" / "app"
    venv_bin = install_root / "current-venv" / "bin"
    current_app.mkdir(parents=True)
    venv_bin.mkdir(parents=True)
    (current_app / "runtime_manager.py").write_text("# manager\n", encoding="utf-8")
    (current_app / "image-launcher.py").write_text("# launcher\n", encoding="utf-8")
    calls = tmp_path / "calls.log"
    held = install_root / "current" / "macos" / "image-pptgen-held-command.sh"
    held.parent.mkdir(parents=True)
    held.write_text(
        "#!/bin/sh\n"
        f"printf 'held:%s\\n' \"$*\" >> {calls!s}\n",
        encoding="utf-8",
    )
    held.chmod(0o755)
    python = venv_bin / "python"
    python.write_text(
        "#!/bin/sh\n"
        f"printf 'python:%s|root=%s|py=%s\\n' \"$*\" \"$IMAGE_PPTGEN_DATA_ROOT\" \"$IMAGE_PPTGEN_PYTHON\" >> {calls!s}\n"
        f"exit {manager_exit}\n",
        encoding="utf-8",
    )
    python.chmod(0o755)
    cli = venv_bin / "image-pptgen"
    cli.write_text(
        "#!/bin/sh\n"
        f"printf 'cli:%s\\n' \"$*\" >> {calls!s}\n",
        encoding="utf-8",
    )
    cli.chmod(0o755)
    return install_root, calls


def _run(
    wrapper: str,
    install_root: Path,
    args: list[str],
    *,
    use_override: bool = True,
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    if use_override:
        environment["IMAGE_PPTGEN_INSTALL_ROOT"] = str(install_root)
    else:
        environment["HOME"] = str(install_root.parents[1])
    return subprocess.run(
        ["bash", str(MACOS_ROOT / wrapper), *args],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )


def test_cli_wrapper_routes_synchronous_default_command_through_held_runtime(
    tmp_path: Path,
) -> None:
    install_root, calls = _fixture(tmp_path)

    completed = _run(
        "image-pptgen-wrapper.sh",
        install_root,
        ["material", "submit", "--title", "中国历史", "--json"],
    )

    assert completed.returncode == 0, completed.stderr
    lines = calls.read_text(encoding="utf-8").splitlines()
    assert lines == ["held:material submit --title 中国历史 --json"]


@pytest.mark.parametrize(
    "arguments",
    [
        ["doctor", "--json"],
        ["split", "propose", "--deck-id", "3", "--json"],
        ["status", "--run-id", "7", "--follow", "--jsonl"],
        ["result", "--run-id", "7", "--json"],
        ["generate-and-follow", "--deck-id", "3", "--jsonl"],
    ],
)
def test_cli_wrapper_uses_held_runtime_for_complete_synchronous_surfaces(
    tmp_path: Path, arguments: list[str]
) -> None:
    install_root, calls = _fixture(tmp_path)

    completed = _run("image-pptgen-wrapper.sh", install_root, arguments)

    assert completed.returncode == 0, completed.stderr
    assert calls.read_text(encoding="utf-8").splitlines() == ["held:" + " ".join(arguments)]


def test_cli_wrapper_keeps_raw_generate_on_managed_runtime_path(tmp_path: Path) -> None:
    install_root, calls = _fixture(tmp_path)

    completed = _run("image-pptgen-wrapper.sh", install_root, ["generate", "--json"])

    assert completed.returncode == 0, completed.stderr
    lines = calls.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert "runtime_manager.py ensure-ready --json" in lines[0]
    assert lines[1] == "cli:generate --json"


def test_cli_wrapper_explicit_external_base_url_stays_direct(tmp_path: Path) -> None:
    install_root, calls = _fixture(tmp_path)

    completed = _run(
        "image-pptgen-wrapper.sh",
        install_root,
        ["doctor", "--base-url", "http://127.0.0.1:43130", "--json"],
    )

    assert completed.returncode == 0, completed.stderr
    assert calls.read_text(encoding="utf-8").splitlines() == [
        "cli:doctor --base-url http://127.0.0.1:43130 --json"
    ]


def test_cli_wrapper_help_is_side_effect_free(tmp_path: Path) -> None:
    install_root, calls = _fixture(tmp_path)

    completed = _run("image-pptgen-wrapper.sh", install_root, ["--help"])

    assert completed.returncode == 0, completed.stderr
    assert calls.read_text(encoding="utf-8").splitlines() == ["cli:--help"]


def test_wrappers_default_to_the_codex_user_root(tmp_path: Path) -> None:
    home = tmp_path / "home"
    install_root, calls = _fixture(
        tmp_path, install_root=home / ".codex" / "image-pptgen"
    )

    completed = _run(
        "image-pptgen-wrapper.sh",
        install_root,
        ["--help"],
        use_override=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert calls.read_text(encoding="utf-8").splitlines() == ["cli:--help"]


def test_cli_wrapper_stops_when_manager_fails(tmp_path: Path) -> None:
    install_root, calls = _fixture(tmp_path, manager_exit=7)

    completed = _run("image-pptgen-wrapper.sh", install_root, ["generate", "--json"])

    assert completed.returncode == 7
    assert len(calls.read_text(encoding="utf-8").splitlines()) == 1


def test_server_wrapper_uses_active_venv_python_and_launcher(tmp_path: Path) -> None:
    install_root, calls = _fixture(tmp_path)

    completed = _run("image-pptgen-server-wrapper.sh", install_root, ["--host", "127.0.0.1"])

    assert completed.returncode == 0, completed.stderr
    line = calls.read_text(encoding="utf-8").strip()
    assert "image-launcher.py --host 127.0.0.1" in line
    assert f"root={install_root}" in line


def _held_fixture(tmp_path: Path) -> tuple[Path, Path]:
    """Create an installed macOS release that can prove held-service behavior."""

    install_root = tmp_path / "home" / ".codex" / "image-pptgen"
    current_app = install_root / "current" / "app"
    venv_bin = install_root / "current-venv" / "bin"
    current_app.mkdir(parents=True)
    venv_bin.mkdir(parents=True)
    calls = tmp_path / "held-calls.log"
    runtime_python = venv_bin / "python"
    runtime_python.symlink_to(Path(sys.executable))

    release = {
        "build_id": "build-123",
        "version": "0.0.0-test",
        "source_commit": "commit-123",
        "skill_sha256": "skill-123",
        "runtime_content_sha256": "runtime-123",
    }
    (current_app / "release-identity.json").write_text(
        json.dumps(release), encoding="utf-8"
    )
    (current_app / "runtime_manager.py").write_text(
        "from __future__ import annotations\n"
        "import json\n"
        "import os\n"
        "import sys\n"
        "from pathlib import Path\n"
        "calls = Path(os.environ['IMAGE_PPTGEN_TEST_CALLS'])\n"
        "with calls.open('a', encoding='utf-8') as handle:\n"
        "    handle.write('manager:' + ' '.join(sys.argv[1:]) + '\\n')\n"
        "print(os.environ.get('IMAGE_PPTGEN_TEST_MANAGER_RECEIPT', "
        "'{\\\"ok\\\":true,\\\"stopped\\\":false}'))\n",
        encoding="utf-8",
    )
    (current_app / "image-launcher.py").write_text(
        "from __future__ import annotations\n"
        "import json\n"
        "import os\n"
        "from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer\n"
        "from pathlib import Path\n"
        "calls = Path(os.environ['IMAGE_PPTGEN_TEST_CALLS'])\n"
        "with calls.open('a', encoding='utf-8') as handle:\n"
        "    handle.write(f'launcher:{os.getpid()}\\n')\n"
        "release = json.loads((Path(__file__).parent / 'release-identity.json').read_text(encoding='utf-8'))\n"
        "identity = {**release, 'base_url': 'http://127.0.0.1:3130', "
        "'product': 'image-pptgen', 'service': 'image-pptgen-server', "
        "'surface': 'public_image_3_0', 'instance_id': 'instance-123', "
        "'data_root': 'image-pptgen/state/data', "
        "'artifacts_root': 'image-pptgen/state/data/artifacts'}\n"
        "class Handler(BaseHTTPRequestHandler):\n"
        "    def do_GET(self):\n"
        "        if self.path != '/api/runtime-identity':\n"
        "            self.send_error(404)\n"
        "            return\n"
        "        payload = json.dumps(identity).encode('utf-8')\n"
        "        self.send_response(200)\n"
        "        self.send_header('Content-Type', 'application/json')\n"
        "        self.send_header('Content-Length', str(len(payload)))\n"
        "        self.end_headers()\n"
        "        self.wfile.write(payload)\n"
        "    def log_message(self, *_):\n"
        "        pass\n"
        "ThreadingHTTPServer(('127.0.0.1', 3130), Handler).serve_forever()\n",
        encoding="utf-8",
    )
    cli = venv_bin / "image-pptgen"
    cli.write_text(
        f"#!{sys.executable}\n"
        "from __future__ import annotations\n"
        "import os\n"
        "import sys\n"
        "from pathlib import Path\n"
        "args = sys.argv[1:]\n"
        "calls = Path(os.environ['IMAGE_PPTGEN_TEST_CALLS'])\n"
        "with calls.open('a', encoding='utf-8') as handle:\n"
        "    handle.write('cli:' + ' '.join(args) + '\\n')\n"
        "if args and args[0] == 'generate':\n"
        "    print(os.environ.get('IMAGE_PPTGEN_TEST_GENERATION_JSON', "
        "'{\\\"batch_id\\\":1,\\\"run_ids\\\":[7]}'))\n"
        "elif args and args[0] == 'status':\n"
        "    print('{\\\"run_id\\\":7,\\\"source_facts\\\":{\\\"run_status\\\":\\\"completed\\\"}}')\n"
        "else:\n"
        "    print('{\\\"ok\\\":true}')\n",
        encoding="utf-8",
    )
    cli.chmod(0o755)
    return install_root, calls


def _run_held(
    install_root: Path,
    calls: Path,
    args: list[str],
    *,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    environment = os.environ | {
        "IMAGE_PPTGEN_INSTALL_ROOT": str(install_root),
        "IMAGE_PPTGEN_TEST_CALLS": str(calls),
        "NO_PROXY": "localhost,127.0.0.1,::1",
        "no_proxy": "localhost,127.0.0.1,::1",
    }
    if extra_env:
        environment.update(extra_env)
    return subprocess.run(
        ["bash", str(MACOS_ROOT / "image-pptgen-held-command.sh"), *args],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
        timeout=20,
    )


def _assert_server_stopped(calls: Path) -> None:
    launcher_line = next(
        line for line in calls.read_text(encoding="utf-8").splitlines() if line.startswith("launcher:")
    )
    pid = int(launcher_line.split(":", 1)[1])
    for _ in range(20):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.05)
    pytest.fail(f"held launcher {pid} survived helper cleanup")


def test_held_command_proves_identity_then_forwards_exact_split_argv_and_cleans_up(
    tmp_path: Path,
    held_service_port_lock: None,
) -> None:
    install_root, calls = _held_fixture(tmp_path)

    completed = _run_held(
        install_root, calls, ["split", "propose", "--deck-id", "3", "--json"]
    )

    assert completed.returncode == 0, completed.stderr
    lines = calls.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "manager:stop --json"
    assert any(line.startswith("launcher:") for line in lines)
    assert lines[-1] == "cli:split propose --deck-id 3 --json"
    assert not (install_root / "state" / "runtime-manager" / "held-command.lock").exists()
    _assert_server_stopped(calls)


def test_held_command_generates_once_then_follows_once_under_the_same_parent(
    tmp_path: Path,
    held_service_port_lock: None,
) -> None:
    install_root, calls = _held_fixture(tmp_path)

    completed = _run_held(
        install_root, calls, ["generate-and-follow", "--deck-id", "9", "--jsonl"]
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.splitlines() == [
        '{"batch_id":1,"run_ids":[7]}',
        '{"run_id":7,"source_facts":{"run_status":"completed"}}',
    ]
    lines = calls.read_text(encoding="utf-8").splitlines()
    assert lines.count("cli:generate --deck-id 9 --json") == 1
    assert lines.count("cli:status --run-id 7 --follow --jsonl") == 1
    _assert_server_stopped(calls)


def test_held_command_rejects_ambiguous_generation_without_following(
    tmp_path: Path,
    held_service_port_lock: None,
) -> None:
    install_root, calls = _held_fixture(tmp_path)

    completed = _run_held(
        install_root,
        calls,
        ["generate-and-follow", "--deck-id", "9", "--jsonl"],
        extra_env={"IMAGE_PPTGEN_TEST_GENERATION_JSON": '{"batch_id":1,"run_ids":[7,8]}'},
    )

    assert completed.returncode == 4
    lines = calls.read_text(encoding="utf-8").splitlines()
    assert lines.count("cli:generate --deck-id 9 --json") == 1
    assert not any(line.startswith("cli:status") for line in lines)
    _assert_server_stopped(calls)


def test_held_command_rejects_non_loopback_base_url_and_missing_jsonl(
    tmp_path: Path,
    held_service_port_lock: None,
) -> None:
    install_root, calls = _held_fixture(tmp_path)

    wrong_base_url = _run_held(
        install_root,
        calls,
        ["split", "propose", "--deck-id", "3", "--json"],
        extra_env={"IMAGE_PPTGEN_BASE_URL": "http://127.0.0.1:3131"},
    )
    assert wrong_base_url.returncode == 3
    assert not calls.exists()

    missing_jsonl = _run_held(
        install_root, calls, ["generate-and-follow", "--deck-id", "9"]
    )
    assert missing_jsonl.returncode == 4
    lines = calls.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "manager:stop --json"
    assert not any(line.startswith("cli:") for line in lines)
    _assert_server_stopped(calls)


def test_held_command_fails_closed_for_invalid_manager_receipt_and_busy_lock(
    tmp_path: Path,
    held_service_port_lock: None,
) -> None:
    install_root, calls = _held_fixture(tmp_path)

    invalid = _run_held(
        install_root,
        calls,
        ["split", "propose", "--deck-id", "3", "--json"],
        extra_env={"IMAGE_PPTGEN_TEST_MANAGER_RECEIPT": '{"ok":false,"stopped":false}'},
    )
    assert invalid.returncode == 3
    assert calls.read_text(encoding="utf-8").splitlines() == ["manager:stop --json"]
    assert not (install_root / "state" / "runtime-manager" / "held-command.lock").exists()

    calls.unlink()
    lock = install_root / "state" / "runtime-manager" / "held-command.lock"
    lock.mkdir(parents=True)
    busy = _run_held(install_root, calls, ["split", "propose", "--deck-id", "3", "--json"])
    assert busy.returncode == 3
    assert "held runtime is already in use" in busy.stderr
    assert not calls.exists()


def test_held_command_distinguishes_unwritable_state_from_a_busy_lock(
    tmp_path: Path,
    held_service_port_lock: None,
) -> None:
    install_root, calls = _held_fixture(tmp_path)
    state_root = install_root / "state" / "runtime-manager"
    state_root.parent.mkdir(parents=True)
    state_root.write_text("not-a-directory", encoding="utf-8")

    denied = _run_held(
        install_root, calls, ["split", "propose", "--deck-id", "3", "--json"]
    )

    assert denied.returncode == 3
    assert "runtime state is not writable" in denied.stderr
    assert "already in use" not in denied.stderr
    assert not calls.exists()
