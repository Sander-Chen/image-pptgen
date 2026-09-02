from __future__ import annotations

import json
import socket
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PACKAGE_ROOT / "src"
REPO_ROOT = PACKAGE_ROOT.parents[1]


@contextmanager
def platform_stub(
    *,
    split_delay: float = 0,
    run_statuses: list[dict[str, object]] | None = None,
    run_detail: dict[str, object] | None = None,
    runtime_identity: dict[str, object] | None = None,
):
    requests: list[dict[str, object]] = []
    status_index = 0

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format: str, *_args: object) -> None:
            return

        def _send(self, status: int, payload: object) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
            nonlocal status_index
            requests.append({"method": "GET", "path": self.path})
            if self.path == "/api/runtime-identity":
                self._send(
                    200,
                    runtime_identity
                    or {
                        "base_url": f"http://127.0.0.1:{self.server.server_port}",
                        "build_id": "build-test-001",
                        "data_root": "/isolated/state",
                        "instance_id": "instance-test-001",
                        "product": "PPTGen",
                        "release_root": "/isolated/releases/test",
                        "service": "pptgen-platform",
                        "skill_sha256": "a" * 64,
                        "source_commit": "b" * 40,
                        "version": "0.1.1",
                    },
                )
                return
            if self.path == "/api/decks?status=active":
                self._send(200, [{"id": 999, "title": "private-list-body"}])
                return
            if self.path == "/api/configs":
                self._send(
                    200,
                    [
                        {"id": 8, "type": "image", "is_default": 1},
                        {"id": 9, "type": "html", "is_default": 1},
                    ],
                )
                return
            if self.path.startswith("/api/runs/91/status") and run_statuses:
                payload = run_statuses[min(status_index, len(run_statuses) - 1)]
                status_index += 1
                self._send(200, payload)
                return
            if self.path == "/api/runs/91":
                self._send(
                    200,
                    run_detail
                    or {"id": 91, "design_principle_raw": "{\"theme\":\"ready\"}"},
                )
                return
            self._send(404, {"error": "Not found"})

        def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length)
            payload = json.loads(raw) if raw else None
            requests.append({"method": "POST", "path": self.path, "json": payload})
            if self.path == "/api/decks":
                self._send(201, {"id": 41})
                return
            if self.path == "/api/decks/41/split-drafts":
                if split_delay:
                    time.sleep(split_delay)
                mode = payload["mode"]
                self._send(
                    201,
                    {
                        "id": 71 if mode == "deterministic" else 72,
                        "deck_id": 41,
                        "status": "pending",
                        "mode": "deterministic" if mode == "deterministic" else "llm_auto",
                        "content_mode": None if mode == "deterministic" else "faithful",
                        "slides": [
                            {"title": "机会", "content": "第一页内容", "split_mode": mode},
                            {"title": "行动", "content": "第二页内容", "split_mode": mode},
                        ],
                    },
                )
                return
            if self.path == "/api/deck-split-drafts/71/revise":
                self._send(
                    200,
                    {
                        "id": 71,
                        "deck_id": 41,
                        "status": "pending",
                        "mode": "deterministic",
                        "slides": [
                            {
                                "title": "先讲结论",
                                "content": "把行动建议提前。",
                                "split_mode": "deterministic",
                            },
                            {
                                "title": "再讲依据",
                                "content": "保留全部市场证据。",
                                "split_mode": "deterministic",
                            },
                        ],
                    },
                )
                return
            if self.path == "/api/deck-split-drafts/72/retry":
                self._send(
                    200,
                    {
                        "id": 72,
                        "deck_id": 41,
                        "status": "pending",
                        "mode": "llm_auto",
                        "attempt_count": 2,
                        "slides": [
                            {"title": "机会", "content": "第一页内容", "split_mode": "llm_auto"},
                            {"title": "行动", "content": "第二页内容", "split_mode": "llm_auto"},
                        ],
                    },
                )
                return
            if self.path == "/api/deck-split-drafts/71/confirm":
                self._send(
                    200,
                    {
                        "slide_ids": [501, 502],
                        "slides": [
                            {"id": 501, "deck_id": 41, "position": 1},
                            {"id": 502, "deck_id": 41, "position": 2},
                        ],
                    },
                )
                return
            if self.path == "/api/requirements":
                self._send(201, {"id": 61})
                return
            if self.path == "/api/generate":
                self._send(
                    201,
                    {
                        "batch_id": 81,
                        "run_ids": [91],
                        "slides_per_run": 2,
                        "total_runs": 1,
                    },
                )
                return
            self._send(404, {"error": "Not found"})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def run_cli(*args: str, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    env = {"PYTHONPATH": str(SRC_ROOT)}
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, "-m", "pptgen_toolkit.cli", *args],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def unused_local_url() -> str:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return f"http://127.0.0.1:{port}"


def test_doctor_reads_runtime_identity_without_exposing_deck_list() -> None:
    with platform_stub() as (base_url, requests):
        result = run_cli("--base-url", base_url, "doctor", "--json")

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "base_url": base_url,
        "build_id": "build-test-001",
        "data_root": "/isolated/state",
        "instance_id": "instance-test-001",
        "ok": True,
        "product": "PPTGen",
        "release_root": "/isolated/releases/test",
        "service": "pptgen-platform",
        "skill_sha256": "a" * 64,
        "source_commit": "b" * 40,
        "version": "0.1.1",
    }
    assert "private-list-body" not in result.stdout
    assert requests == [{"method": "GET", "path": "/api/runtime-identity"}]


def test_doctor_rejects_wrong_runtime_build_identity() -> None:
    with platform_stub() as (base_url, requests):
        result = run_cli(
            "--base-url",
            base_url,
            "doctor",
            "--json",
            extra_env={"PPTGEN_EXPECTED_BUILD_ID": "expected-build"},
        )

    assert result.returncode == 4
    assert json.loads(result.stderr)["error"] == "platform_error"
    assert "build identity mismatch" in json.loads(result.stderr)["message"]
    assert requests == [{"method": "GET", "path": "/api/runtime-identity"}]


def test_doctor_rejects_wrong_runtime_instance_identity() -> None:
    with platform_stub() as (base_url, requests):
        result = run_cli(
            "--base-url",
            base_url,
            "doctor",
            "--json",
            extra_env={"PPTGEN_EXPECTED_INSTANCE_ID": "expected-instance"},
        )

    assert result.returncode == 4
    assert json.loads(result.stderr)["error"] == "platform_error"
    assert "instance identity mismatch" in json.loads(result.stderr)["message"]
    assert requests == [{"method": "GET", "path": "/api/runtime-identity"}]


@pytest.mark.parametrize(
    ("field", "wrong_value"),
    [
        ("base_url", "http://127.0.0.1:1"),
        ("data_root", "/wrong/state"),
        ("product", "NotPPTGen"),
        ("release_root", "/wrong/release"),
        ("service", "wrong-service"),
        ("skill_sha256", "c" * 64),
        ("source_commit", "d" * 40),
        ("version", "9.9.9"),
    ],
)
def test_doctor_rejects_any_wrong_trusted_runtime_identity_field(
    field: str,
    wrong_value: str,
) -> None:
    listener_identity = {
        "base_url": "pending",
        "build_id": "build-test-001",
        "data_root": "/isolated/state",
        "instance_id": "instance-test-001",
        "product": "PPTGen",
        "release_root": "/isolated/releases/test",
        "service": "pptgen-platform",
        "skill_sha256": "a" * 64,
        "source_commit": "b" * 40,
        "version": "0.1.1",
    }
    with platform_stub(runtime_identity=listener_identity) as (base_url, requests):
        listener_identity["base_url"] = base_url
        expected = dict(listener_identity)
        listener_identity[field] = wrong_value
        result = run_cli(
            "--base-url",
            base_url,
            "doctor",
            "--json",
            extra_env={
                "PPTGEN_EXPECTED_IDENTITY_JSON": json.dumps(expected, sort_keys=True)
            },
        )

    assert result.returncode == 4
    assert json.loads(result.stderr)["error"] == "platform_error"
    message = json.loads(result.stderr)["message"].lower()
    assert f"{field.replace('_', ' ')} identity mismatch" in message
    assert requests == [{"method": "GET", "path": "/api/runtime-identity"}]


def test_material_submit_posts_utf8_text_and_returns_existing_deck_id(tmp_path: Path) -> None:
    material = tmp_path / "材料.md"
    material.write_text("# 发布计划\n\n## 第一页\n你好，世界。", encoding="utf-8")

    with platform_stub() as (base_url, requests):
        result = run_cli(
            "--base-url",
            base_url,
            "material",
            "submit",
            "--title",
            "老板演示",
            "--text-file",
            str(material),
            "--json",
        )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "deck_id": 41,
        "status": "material_accepted",
        "title": "老板演示",
    }
    assert requests == [
        {
            "method": "POST",
            "path": "/api/decks",
            "json": {
                "content": "# 发布计划\n\n## 第一页\n你好，世界。",
                "title": "老板演示",
            },
        }
    ]


def test_material_submit_rejects_binary_png_before_http(tmp_path: Path) -> None:
    image = tmp_path / "公众号截图.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\nnot-a-real-image")

    with platform_stub() as (base_url, requests):
        result = run_cli(
            "--base-url",
            base_url,
            "material",
            "submit",
            "--title",
            "截图材料",
            "--text-file",
            str(image),
            "--json",
        )

    assert result.returncode == 2
    assert json.loads(result.stderr) == {
        "error": "unsupported_image_input",
        "message": "Image and OCR input are not supported; provide a text or Markdown file",
    }
    assert requests == []


def test_material_submit_rejects_text_svg_before_http(tmp_path: Path) -> None:
    image = tmp_path / "diagram.svg"
    image.write_text("<svg><text>looks readable</text></svg>", encoding="utf-8")

    with platform_stub() as (base_url, requests):
        result = run_cli(
            "--base-url",
            base_url,
            "material",
            "submit",
            "--title",
            "SVG material",
            "--text-file",
            str(image),
            "--json",
        )

    assert result.returncode == 2
    assert json.loads(result.stderr)["error"] == "unsupported_image_input"
    assert requests == []


def test_material_submit_rejects_png_content_renamed_as_text_before_http(
    tmp_path: Path,
) -> None:
    image = tmp_path / "公众号截图.txt"
    image.write_bytes(b"\x89PNG\r\n\x1a\nnot-a-real-image")

    with platform_stub() as (base_url, requests):
        result = run_cli(
            "--base-url",
            base_url,
            "material",
            "submit",
            "--title",
            "伪装截图",
            "--text-file",
            str(image),
            "--json",
        )

    assert result.returncode == 2
    assert json.loads(result.stderr)["error"] == "unsupported_image_input"
    assert requests == []


def test_material_submit_rejects_svg_content_renamed_as_markdown_before_http(
    tmp_path: Path,
) -> None:
    image = tmp_path / "diagram.md"
    image.write_text(
        "\ufeff  <?xml version=\"1.0\"?>\n<svg><text>looks readable</text></svg>",
        encoding="utf-8",
    )

    with platform_stub() as (base_url, requests):
        result = run_cli(
            "--base-url",
            base_url,
            "material",
            "submit",
            "--title",
            "伪装 SVG",
            "--text-file",
            str(image),
            "--json",
        )

    assert result.returncode == 2
    assert json.loads(result.stderr)["error"] == "unsupported_image_input"
    assert requests == []


def test_material_submit_keeps_ordinary_xml_text_supported(tmp_path: Path) -> None:
    material = tmp_path / "outline.txt"
    material.write_text(
        '<?xml version="1.0"?>\n<presentation><title>计划</title></presentation>',
        encoding="utf-8",
    )

    with platform_stub() as (base_url, requests):
        result = run_cli(
            "--base-url",
            base_url,
            "material",
            "submit",
            "--title",
            "XML 文本",
            "--text-file",
            str(material),
            "--json",
        )

    assert result.returncode == 0, result.stderr
    assert requests[0]["path"] == "/api/decks"


def test_connection_failure_is_deterministic_json_error() -> None:
    result = run_cli("--base-url", unused_local_url(), "doctor", "--json")

    assert result.returncode == 3
    payload = json.loads(result.stderr)
    assert payload["error"] == "platform_unavailable"
    assert payload["message"].startswith("Cannot reach PPTGen Platform at http://127.0.0.1:")


def test_package_does_not_import_backend_modules() -> None:
    package_dir = SRC_ROOT / "pptgen_toolkit"
    forbidden = ("backend", "db", "pipeline", "server", "sqlite3", "shelve")
    sources = "\n".join(path.read_text(encoding="utf-8") for path in package_dir.glob("*.py"))

    for module in forbidden:
        assert f"import {module}" not in sources
        assert f"from {module}" not in sources


def test_platform_api_failure_is_deterministic_json_error() -> None:
    with platform_stub() as (base_url, requests):
        result = run_cli(
            "--base-url",
            base_url,
            "split",
            "confirm",
            "--draft-id",
            "999",
            "--json",
        )

    assert result.returncode == 4
    assert json.loads(result.stderr) == {
        "error": "platform_error",
        "message": "PPTGen Platform returned HTTP 404 for /api/deck-split-drafts/999/confirm",
    }
    assert requests == [
        {
            "method": "POST",
            "path": "/api/deck-split-drafts/999/confirm",
            "json": None,
        }
    ]


def test_frozen_cli_contract_documents_exact_commands_routes_and_exits() -> None:
    contract = REPO_ROOT / "skills" / "generate-presentation" / "references" / "cli-contract.md"
    text = contract.read_text(encoding="utf-8")

    assert "Contract version: `0.1.0`" in text
    for command in (
        "`pptgen doctor --json`",
        "`pptgen material submit --title <title> --text-file <path> --json`",
        "`pptgen split propose --deck-id <id> --mode deterministic|llm --json`",
        "`pptgen split revise --draft-id <id> --instruction <text> --json`",
        "`pptgen split confirm --draft-id <id> --json`",
        "`pptgen generate --deck-id <id> --intent auto|preference [--preference <text>] --json`",
        "`pptgen status --run-id <id> --follow --jsonl`",
        "`pptgen result --run-id <id> --json`",
    ):
        assert command in text
    for code in ("`0` success", "`2` local input", "`3` unavailable", "`4` Platform/API"):
        assert code in text
    for route in (
        "`GET /api/decks?status=active`",
        "`POST /api/decks`",
        "`POST /api/decks/<id>/split-drafts`",
        "`POST /api/deck-split-drafts/<id>/revise`",
        "`POST /api/deck-split-drafts/<id>/confirm`",
        "`POST /api/requirements`",
        "`POST /api/generate`",
        "`GET /api/runs/<id>/status`",
        "`GET /api/runs/<id>`",
    ):
        assert route in text


def test_skill_metadata_describes_the_complete_conversation() -> None:
    metadata = (
        REPO_ROOT / "skills" / "generate-presentation" / "agents" / "openai.yaml"
    ).read_text(encoding="utf-8")

    assert 'display_name: "Generate Presentation"' in metadata
    assert (
        'short_description: "Create and follow PPTGen presentations end to end"'
        in metadata
    )
    assert "$generate-presentation" in metadata
    for phrase in (
        "review and confirm the page split",
        "choose Auto or a design preference",
        "follow generation progress",
        "open the final Presentation Preview",
    ):
        assert phrase in metadata


def test_skill_freezes_the_approved_business_branches() -> None:
    skill = (REPO_ROOT / "skills" / "generate-presentation" / "SKILL.md").read_text(
        encoding="utf-8"
    )

    for phrase in (
        "Ask whether the user wants fully automatic design or has a concrete design",
        "A concrete preference wins mixed",
        "任务进度",
        "当前活动",
        "partially_completed",
        "start a new generation task",
        "Reject PNG, JPEG, SVG and every other image input immediately",
        "references/cli-contract.md",
    ):
        assert phrase in skill
    assert skill.count(
        "Ask whether the user wants fully automatic design or has a concrete design"
    ) == 1


def test_skill_makes_generate_follow_result_one_unbroken_turn() -> None:
    skill = " ".join(
        (REPO_ROOT / "skills" / "generate-presentation" / "SKILL.md")
        .read_text(encoding="utf-8")
        .split()
    )

    for phrase in (
        "Do not end the turn after `pptgen generate` returns",
        "Immediately run exactly one",
        "For every `pptgen` command in this workflow",
        "Continue the same invocation until process exit",
        "Preserve the complete tool result",
        "resume that same command with `write_stdin` using the same `session_id`",
        "resume that outer call with `wait` using the same `cell_id`",
        "Never treat a `cell_id` as a command `session_id`",
        "Do not start a second follow",
        "Only after the follow process exits",
        "run `pptgen result` exactly once",
        "`<doctor.base_url>/history/run/<run_id>/preview`",
        "automatically open",
        "same retained `run_id`",
        "Run Detail remains available only from its existing diagnostic entry",
        "real browser opener",
        "truthful clickable fallback",
        "An installed `xdg-open` binary alone does not prove",
        "headless, SSH, or Docker without GUI/browser integration",
    ):
        assert phrase in skill


def test_skill_auto_saves_directly_pasted_text_without_user_file_work() -> None:
    skill = " ".join(
        (REPO_ROOT / "skills" / "generate-presentation" / "SKILL.md")
        .read_text(encoding="utf-8")
        .split()
    )

    for phrase in (
        "pasted presentation body",
        "save it yourself",
        "task-local",
        "collision-safe",
        "UTF-8",
        "byte-for-byte",
        "Do not summarize, normalize or reformat",
        "Do not ask the user to save",
        "verify that the saved bytes match",
        "material submit",
    ):
        assert phrase in skill


def test_skill_drive_publication_is_full_success_and_opt_in_only() -> None:
    skill_dir = REPO_ROOT / "skills" / "generate-presentation"
    skill = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    publication = (skill_dir / "references" / "google-drive-publication.md").read_text(
        encoding="utf-8"
    )
    normalized = " ".join(publication.split())

    assert "references/google-drive-publication.md" in skill
    for phrase in (
        "only after `result.status` is exactly `completed`",
        "explicitly answers yes",
        "one task-specific folder",
        "Upload every PNG exactly once",
        "matching names, count and byte sizes",
        "do not call any Google Drive tool",
        "partially_completed",
    ):
        assert phrase in normalized


def test_split_propose_deterministic_returns_complete_page_markdown() -> None:
    with platform_stub() as (base_url, requests):
        result = run_cli(
            "--base-url",
            base_url,
            "split",
            "propose",
            "--deck-id",
            "41",
            "--mode",
            "deterministic",
            "--json",
        )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "deck_id": 41,
        "draft_id": 71,
        "markdown": "## 第 1 页：机会\n\n第一页内容\n\n## 第 2 页：行动\n\n第二页内容",
        "mode": "deterministic",
        "page_count": 2,
        "status": "pending",
    }
    assert requests == [
        {
            "method": "POST",
            "path": "/api/decks/41/split-drafts",
            "json": {"mode": "deterministic"},
        }
    ]


def test_split_propose_llm_uses_standalone_autosplit_setting() -> None:
    with platform_stub() as (base_url, requests):
        result = run_cli(
            "--base-url",
            base_url,
            "split",
            "propose",
            "--deck-id",
            "41",
            "--mode",
            "llm",
            "--json",
        )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["draft_id"] == 72
    assert payload["content_mode"] == "faithful"
    assert requests == [
        {
            "method": "POST",
            "path": "/api/decks/41/split-drafts",
            "json": {"mode": "llm"},
        },
    ]


def test_split_retry_client_sends_only_failed_draft_id() -> None:
    sys.path.insert(0, str(SRC_ROOT))
    from pptgen_toolkit.client import PptgenClient

    with platform_stub() as (base_url, requests):
        result = PptgenClient(base_url).retry_split_draft(draft_id=72)

    assert result["id"] == 72
    assert result["attempt_count"] == 2
    assert requests == [
        {
            "method": "POST",
            "path": "/api/deck-split-drafts/72/retry",
            "json": {},
        }
    ]


def test_llm_split_uses_long_operation_timeout() -> None:
    sys.path.insert(0, str(SRC_ROOT))
    from pptgen_toolkit.client import PptgenClient

    with platform_stub(split_delay=0.15) as (base_url, _requests):
        client = PptgenClient(base_url, timeout=0.05, long_timeout=1.0)
        draft = client.create_split_draft(deck_id=41, mode="llm")

    assert draft["id"] == 72


def test_split_revise_sends_natural_language_and_returns_complete_markdown() -> None:
    with platform_stub() as (base_url, requests):
        result = run_cli(
            "--base-url",
            base_url,
            "split",
            "revise",
            "--draft-id",
            "71",
            "--instruction",
            "把行动建议放到第一页，但保留市场证据",
            "--json",
        )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "deck_id": 41,
        "draft_id": 71,
        "markdown": (
            "## 第 1 页：先讲结论\n\n把行动建议提前。\n\n"
            "## 第 2 页：再讲依据\n\n保留全部市场证据。"
        ),
        "mode": "deterministic",
        "page_count": 2,
        "status": "pending",
    }
    assert requests == [
        {
            "method": "POST",
            "path": "/api/deck-split-drafts/71/revise",
            "json": {"instruction": "把行动建议放到第一页，但保留市场证据"},
        }
    ]


def test_split_confirm_uses_existing_transaction_and_returns_written_ids() -> None:
    with platform_stub() as (base_url, requests):
        result = run_cli(
            "--base-url",
            base_url,
            "split",
            "confirm",
            "--draft-id",
            "71",
            "--json",
        )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "deck_id": 41,
        "draft_id": 71,
        "slide_count": 2,
        "slide_ids": [501, 502],
        "status": "confirmed",
    }
    assert requests == [
        {
            "method": "POST",
            "path": "/api/deck-split-drafts/71/confirm",
            "json": None,
        }
    ]


def test_generate_auto_uses_existing_auto_path_without_creating_preference() -> None:
    with platform_stub() as (base_url, requests):
        result = run_cli(
            "--base-url",
            base_url,
            "generate",
            "--deck-id",
            "41",
            "--intent",
            "auto",
            "--json",
        )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "batch_id": 81,
        "deck_id": 41,
        "intent": "auto",
        "run_ids": [91],
        "status": "generation_started",
    }
    assert requests == [
        {"method": "GET", "path": "/api/configs"},
        {
            "method": "POST",
            "path": "/api/generate",
            "json": {"config_id": 9, "deck_id": 41, "mode": "auto"},
        },
    ]


def test_generate_preference_creates_exact_requirement_and_omits_auto_mode() -> None:
    preference = "全自动，但要暗黑风；强调高级感"
    with platform_stub() as (base_url, requests):
        result = run_cli(
            "--base-url",
            base_url,
            "generate",
            "--deck-id",
            "41",
            "--intent",
            "preference",
            "--preference",
            preference,
            "--json",
        )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "batch_id": 81,
        "deck_id": 41,
        "intent": "preference",
        "run_ids": [91],
        "status": "generation_started",
    }
    assert requests == [
        {"method": "GET", "path": "/api/configs"},
        {
            "method": "POST",
            "path": "/api/requirements",
            "json": {"content": preference, "title": "PPTGen Skill preference"},
        },
        {
            "method": "POST",
            "path": "/api/generate",
            "json": {
                "color_ids": [],
                "config_id": 9,
                "deck_id": 41,
                "requirement_ids": [61],
            },
        },
    ]


def test_generate_preference_requires_nonempty_preference_before_http() -> None:
    with platform_stub() as (base_url, requests):
        result = run_cli(
            "--base-url",
            base_url,
            "generate",
            "--deck-id",
            "41",
            "--intent",
            "preference",
            "--json",
        )

    assert result.returncode == 2
    assert json.loads(result.stderr) == {
        "error": "preference_required",
        "message": "Preference intent requires --preference",
    }
    assert requests == []


def test_status_follow_emits_grounded_updates_and_honest_heartbeat() -> None:
    statuses = [
        {
            "run_id": 91,
            "status": "running",
            "started_at": "2026-07-13 14:00:00",
            "completed_at": None,
            "error_message": None,
            "progress": {
                "completed": 0,
                "failed": 0,
                "running": 2,
                "pending": 0,
                "total": 2,
            },
        },
        {
            "run_id": 91,
            "status": "running",
            "started_at": "2026-07-13 14:00:00",
            "completed_at": None,
            "error_message": None,
            "progress": {
                "completed": 0,
                "failed": 0,
                "running": 2,
                "pending": 0,
                "total": 2,
            },
        },
        {
            "run_id": 91,
            "status": "running",
            "started_at": "2026-07-13 14:00:00",
            "completed_at": None,
            "error_message": None,
            "progress": {
                "completed": 1,
                "failed": 0,
                "running": 1,
                "pending": 0,
                "total": 2,
            },
        },
        {
            "run_id": 91,
            "status": "completed",
            "started_at": "2026-07-13 14:00:00",
            "completed_at": "2026-07-13 14:01:00",
            "error_message": None,
            "progress": {
                "completed": 2,
                "failed": 0,
                "running": 0,
                "pending": 0,
                "total": 2,
            },
        },
    ]
    with platform_stub(run_statuses=statuses) as (base_url, requests):
        result = run_cli(
            "--base-url",
            base_url,
            "status",
            "--run-id",
            "91",
            "--follow",
            "--jsonl",
            extra_env={"PPTGEN_STATUS_INTERVAL_SECONDS": "0.01"},
        )

    assert result.returncode == 0, result.stderr
    events = [json.loads(line) for line in result.stdout.splitlines()]
    assert [event["event"] for event in events] == [
        "update",
        "heartbeat",
        "update",
        "update",
    ]
    assert events[0]["current_activity"] == "设计方案已生成；正在生成页面，已完成 0/2 页"
    assert events[1]["source_facts"] == events[0]["source_facts"]
    assert events[2]["current_activity"] == "设计方案已生成；正在生成页面，已完成 1/2 页"
    assert events[-1]["current_activity"] == "全部 2 页已生成完成"
    assert events[-1]["task_progress"] == [
        {"step": "设计方案", "status": "completed"},
        {"step": "页面生成", "status": "completed"},
        {"step": "完成", "status": "completed"},
    ]
    assert all(event["run_id"] == 91 for event in events)
    assert all(event["follow_elapsed_seconds"] >= 0 for event in events)
    assert events[1]["kind"] == "heartbeat"
    assert events[1]["milestone"] is False
    assert events[1]["current_activity"] == "任务仍在运行，尚无新的业务里程碑。"
    assert requests == [
        item
        for _ in statuses
        for item in (
            {"method": "GET", "path": "/api/runs/91/status"},
            {"method": "GET", "path": "/api/runs/91"},
        )
    ]


def test_status_follow_emits_only_safe_activity_fields_and_advances_cursor() -> None:
    statuses = [
        {
            "run_id": 91,
            "status": "running",
            "progress": {"completed": 0, "failed": 0, "running": 1, "pending": 0, "total": 1},
            "activity": {
                "events": [
                    {
                        "cursor": "7:1",
                        "kind": "business_activity",
                        "message": "正在准备整体设计方案。",
                        "milestone": False,
                        "observed_at": "2026-07-15T12:00:01Z",
                        "source": {"stage_id": "deck-design-director", "private": "do-not-copy"},
                        "raw_command": "cat /home/user/private.md",
                    },
                    {
                        "cursor": "7:2",
                        "kind": "agent_message",
                        "message": "设计检查完成。",
                        "milestone": False,
                        "raw_payload": {"secret": "do-not-copy"},
                    },
                    {
                        "cursor": "7:3",
                        "kind": "business_activity",
                        "message": "cat /home/user/private.md with token=do-not-copy",
                        "milestone": False,
                    },
                ],
                "next_cursor": "7:3",
                "run_id": 91,
            },
        },
        {
            "run_id": 91,
            "status": "completed",
            "progress": {"completed": 1, "failed": 0, "running": 0, "pending": 0, "total": 1},
            "activity": {"events": [], "next_cursor": "7:3", "run_id": 91},
        },
    ]
    with platform_stub(run_statuses=statuses) as (base_url, requests):
        result = run_cli(
            "--base-url",
            base_url,
            "status",
            "--run-id",
            "91",
            "--follow",
            "--jsonl",
            extra_env={"PPTGEN_STATUS_INTERVAL_SECONDS": "0.01"},
        )

    assert result.returncode == 0, result.stderr
    events = [json.loads(line) for line in result.stdout.splitlines()]
    assert [event["event"] for event in events] == [
        "activity",
        "agent_message",
        "update",
        "update",
    ]
    assert events[0] == {
        "cursor": "7:1",
        "event": "activity",
        "kind": "business_activity",
        "message": "正在准备整体设计方案。",
        "milestone": False,
        "observed_at": "2026-07-15T12:00:01Z",
        "run_id": 91,
    }
    rendered = result.stdout
    assert "raw_command" not in rendered
    assert "raw_payload" not in rendered
    assert "do-not-copy" not in rendered
    assert "/home/user" not in rendered
    assert requests == [
        {"method": "GET", "path": "/api/runs/91/status"},
        {"method": "GET", "path": "/api/runs/91"},
        {"method": "GET", "path": "/api/runs/91/status?activity_after=7%3A3"},
        {"method": "GET", "path": "/api/runs/91"},
    ]


def test_status_follow_can_resume_from_a_persisted_activity_cursor() -> None:
    statuses = [
        {
            "run_id": 91,
            "status": "completed",
            "progress": {"completed": 1, "failed": 0, "running": 0, "pending": 0, "total": 1},
            "activity": {"events": [], "next_cursor": "7:9", "run_id": 91},
        }
    ]
    with platform_stub(run_statuses=statuses) as (base_url, requests):
        result = run_cli(
            "--base-url",
            base_url,
            "status",
            "--run-id",
            "91",
            "--follow",
            "--jsonl",
            "--after-activity-cursor",
            "7:9",
        )

    assert result.returncode == 0, result.stderr
    assert requests == [
        {"method": "GET", "path": "/api/runs/91/status?activity_after=7%3A9"},
        {"method": "GET", "path": "/api/runs/91"},
    ]


def test_status_follow_preserves_partial_failed_and_interrupted_terminals() -> None:
    cases = [
        {
            "status": "completed_with_failures",
            "progress": {
                "completed": 1,
                "failed": 1,
                "running": 0,
                "pending": 0,
                "total": 2,
            },
            "current_activity": "页面生成已结束：成功 1 页，失败 1 页",
            "task_progress": [
                {"step": "设计方案", "status": "completed"},
                {"step": "页面生成", "status": "failed"},
                {"step": "完成", "status": "failed"},
            ],
        },
        {
            "status": "failed",
            "progress": {
                "completed": 0,
                "failed": 2,
                "running": 0,
                "pending": 0,
                "total": 2,
            },
            "current_activity": "页面生成已结束：成功 0 页，失败 2 页",
            "task_progress": [
                {"step": "设计方案", "status": "completed"},
                {"step": "页面生成", "status": "failed"},
                {"step": "完成", "status": "failed"},
            ],
        },
        {
            "status": "interrupted",
            "progress": {
                "completed": 0,
                "failed": 0,
                "running": 0,
                "pending": 2,
                "total": 2,
            },
            "current_activity": "任务已结束，状态为 interrupted",
            "task_progress": [
                {"step": "设计方案", "status": "completed"},
                {"step": "页面生成", "status": "pending"},
                {"step": "完成", "status": "failed"},
            ],
        },
    ]

    for case in cases:
        terminal = {
            "run_id": 91,
            "status": case["status"],
            "started_at": "2026-07-13 14:00:00",
            "completed_at": "2026-07-13 14:01:00",
            "error_message": None,
            "progress": case["progress"],
        }
        with platform_stub(run_statuses=[terminal]) as (base_url, requests):
            result = run_cli(
                "--base-url",
                base_url,
                "status",
                "--run-id",
                "91",
                "--follow",
                "--jsonl",
                extra_env={"PPTGEN_STATUS_INTERVAL_SECONDS": "0.01"},
            )

        assert result.returncode == 0, result.stderr
        events = [json.loads(line) for line in result.stdout.splitlines()]
        assert len(events) == 1
        event = events[0]
        assert event["event"] == "update"
        assert event["current_activity"] == case["current_activity"]
        assert event["task_progress"] == case["task_progress"]
        assert event["source_facts"] == {
            "completed_slides": case["progress"]["completed"],
            "design_ready": True,
            "failed_slides": case["progress"]["failed"],
            "pending_slides": case["progress"]["pending"],
            "run_status": case["status"],
            "running_slides": case["progress"]["running"],
            "total_slides": case["progress"]["total"],
        }
        assert requests == [
            {"method": "GET", "path": "/api/runs/91/status"},
            {"method": "GET", "path": "/api/runs/91"},
        ]


def _completed_run_detail() -> dict[str, object]:
    return {
        "id": 91,
        "batch_id": 81,
        "deck_id": 41,
        "config_id": 9,
        "config_name": "Codex HTML",
        "requirement_title": "AutoSkill System Requirement",
        "engine": "html",
        "strategy": "codex_html",
        "status": "completed",
        "created_at": "2026-07-13 14:00:00",
        "started_at": "2026-07-13 14:00:01",
        "completed_at": "2026-07-13 14:01:00",
        "output_dir": "/isolated/run-91",
        "error_message": None,
        "design_principle_raw": "RAW DESIGN DIRECTOR EVIDENCE",
        "slides": [
            {
                "id": 501,
                "position": 1,
                "slide_title": "封面",
                "status": "completed",
                "has_displayable_artifact": True,
                "html_path": "/isolated/run-91/01.html",
                "screenshot_path": "/isolated/run-91/01.png",
                "final_image_path": None,
                "error_message": None,
            },
            {
                "id": 502,
                "position": 2,
                "slide_title": "内容",
                "status": "completed",
                "has_displayable_artifact": True,
                "html_path": "/isolated/run-91/02.html",
                "screenshot_path": "/isolated/run-91/02.png",
                "final_image_path": None,
                "error_message": None,
            },
        ],
    }


def _partially_completed_image_run_detail() -> dict[str, object]:
    return {
        "id": 91,
        "batch_id": 82,
        "deck_id": 42,
        "config_id": 10,
        "config_name": "Codex Native Image 3.0",
        "requirement_title": "System managed",
        "engine": "image",
        "strategy": "image_3_0",
        "status": "completed_with_failures",
        "created_at": "2026-07-30 14:00:00",
        "started_at": "2026-07-30 14:00:01",
        "completed_at": "2026-07-30 14:01:00",
        "output_dir": "/isolated/run-91",
        "error_message": "palette_extraction_failed",
        "design_principle_raw": None,
        "slides": [
            {
                "id": 601,
                "position": 1,
                "slide_title": "封面",
                "status": "failed",
                "has_displayable_artifact": False,
                "html_path": None,
                "screenshot_path": None,
                "final_image_path": None,
                "error_message": "blocked_by_palette_extraction_failed",
            },
            {
                "id": 602,
                "position": 2,
                "slide_title": "内容种子",
                "status": "completed",
                "has_displayable_artifact": True,
                "html_path": None,
                "screenshot_path": None,
                "final_image_path": "/isolated/run-91/02.png",
                "error_message": None,
            },
            {
                "id": 603,
                "position": 3,
                "slide_title": "其余内容",
                "status": "skipped",
                "has_displayable_artifact": False,
                "html_path": None,
                "screenshot_path": None,
                "final_image_path": None,
                "error_message": "blocked_by_palette_extraction_failed",
            },
        ],
    }


def test_result_projects_raw_design_run_detail_html_png_and_cover() -> None:
    detail = _completed_run_detail()
    with platform_stub(run_detail=detail) as (base_url, requests):
        result = run_cli(
            "--base-url",
            base_url,
            "result",
            "--run-id",
            "91",
            "--json",
        )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "completed"
    assert payload["platform_status"] == "completed"
    assert payload["design_director_raw"] == "RAW DESIGN DIRECTOR EVIDENCE"
    assert payload["slide_count"] == 2
    assert payload["successful_slide_count"] == 2
    assert payload["failed_slide_count"] == 0
    assert payload["cover_png"] == "/isolated/run-91/01.png"
    assert payload["run_detail"] == {
        "batch_id": 81,
        "completed_at": "2026-07-13 14:01:00",
        "config_id": 9,
        "config_name": "Codex HTML",
        "created_at": "2026-07-13 14:00:00",
        "deck_id": 41,
        "engine": "html",
        "error_message": None,
        "output_dir": "/isolated/run-91",
        "requirement_title": "AutoSkill System Requirement",
        "started_at": "2026-07-13 14:00:01",
        "strategy": "codex_html",
    }
    assert payload["slides"] == [
        {
            "error_message": None,
            "html_path": "/isolated/run-91/01.html",
            "png_path": "/isolated/run-91/01.png",
            "position": 1,
            "run_slide_id": 501,
            "status": "completed",
            "title": "封面",
        },
        {
            "error_message": None,
            "html_path": "/isolated/run-91/02.html",
            "png_path": "/isolated/run-91/02.png",
            "position": 2,
            "run_slide_id": 502,
            "status": "completed",
            "title": "内容",
        },
    ]
    assert requests == [{"method": "GET", "path": "/api/runs/91"}]


def test_result_projects_final_image_only_page_and_partial_image_terminal() -> None:
    detail = _partially_completed_image_run_detail()
    with platform_stub(run_detail=detail) as (base_url, requests):
        result = run_cli(
            "--base-url",
            base_url,
            "result",
            "--run-id",
            "91",
            "--json",
        )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["platform_status"] == "completed_with_failures"
    assert payload["status"] == "partially_completed"
    assert payload["successful_slide_count"] == 1
    assert payload["failed_slide_count"] == 2
    assert payload["cover_png"] == "/isolated/run-91/02.png"
    assert payload["slides"][1] == {
        "error_message": None,
        "html_path": None,
        "png_path": "/isolated/run-91/02.png",
        "position": 2,
        "run_slide_id": 602,
        "status": "completed",
        "title": "内容种子",
    }
    assert requests == [{"method": "GET", "path": "/api/runs/91"}]


def test_result_completes_image_run_without_html_design_artifact() -> None:
    detail = _partially_completed_image_run_detail()
    detail["status"] = "completed"
    detail["error_message"] = None
    slides = detail["slides"]
    assert isinstance(slides, list)
    for index, slide in enumerate(slides, start=1):
        assert isinstance(slide, dict)
        slide.update(
            {
                "status": "completed",
                "has_displayable_artifact": True,
                "html_path": None,
                "screenshot_path": None,
                "final_image_path": f"/isolated/run-91/{index:02d}.png",
                "error_message": None,
            }
        )

    with platform_stub(run_detail=detail) as (base_url, _requests):
        result = run_cli(
            "--base-url",
            base_url,
            "result",
            "--run-id",
            "91",
            "--json",
        )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["platform_status"] == "completed"
    assert payload["design_director_raw"] is None
    assert payload["status"] == "completed"
    assert payload["successful_slide_count"] == 3
    assert payload["failed_slide_count"] == 0


def test_result_still_requires_design_artifact_for_completed_html_run() -> None:
    detail = _completed_run_detail()
    detail["design_principle_raw"] = None

    with platform_stub(run_detail=detail) as (base_url, _requests):
        result = run_cli(
            "--base-url",
            base_url,
            "result",
            "--run-id",
            "91",
            "--json",
        )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["platform_status"] == "completed"
    assert payload["status"] == "partially_completed"
    assert payload["successful_slide_count"] == 2
    assert payload["failed_slide_count"] == 0


def test_result_rejects_screenshot_only_image_page_as_missing_final_artifact() -> None:
    detail = _partially_completed_image_run_detail()
    detail["status"] = "completed"
    detail["error_message"] = None
    slides = detail["slides"]
    assert isinstance(slides, list)
    for index, slide in enumerate(slides, start=1):
        assert isinstance(slide, dict)
        slide.update(
            {
                "status": "completed",
                "has_displayable_artifact": True,
                "html_path": None,
                "screenshot_path": None,
                "final_image_path": f"/isolated/run-91/{index:02d}.png",
                "error_message": None,
            }
        )
    first_slide = slides[0]
    assert isinstance(first_slide, dict)
    first_slide["screenshot_path"] = "/isolated/run-91/01-screenshot.png"
    first_slide["final_image_path"] = None

    with platform_stub(run_detail=detail) as (base_url, _requests):
        result = run_cli(
            "--base-url",
            base_url,
            "result",
            "--run-id",
            "91",
            "--json",
        )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["slides"][0]["status"] == "missing_artifact"
    assert payload["slides"][0]["png_path"] is None
    assert payload["slides"][0]["error_message"] == "Expected final image artifact is missing"
    assert payload["status"] == "partially_completed"
    assert payload["successful_slide_count"] == 2
    assert payload["failed_slide_count"] == 1


def test_result_still_requires_html_and_png_for_completed_html_page() -> None:
    detail = _completed_run_detail()
    slides = detail["slides"]
    assert isinstance(slides, list)
    assert isinstance(slides[1], dict)
    slides[1]["html_path"] = None

    with platform_stub(run_detail=detail) as (base_url, _requests):
        result = run_cli(
            "--base-url",
            base_url,
            "result",
            "--run-id",
            "91",
            "--json",
        )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "partially_completed"
    assert payload["successful_slide_count"] == 1
    assert payload["failed_slide_count"] == 1
    assert payload["slides"][1]["status"] == "missing_artifact"
    assert payload["slides"][1]["png_path"] == "/isolated/run-91/02.png"
    assert payload["slides"][1]["error_message"] == "Expected HTML/PNG artifact is missing"


def test_result_maps_completed_with_failures_to_partial_and_keeps_successes() -> None:
    detail = _completed_run_detail()
    detail["status"] = "completed_with_failures"
    slides = detail["slides"]
    assert isinstance(slides, list)
    slides[1] = {
        "id": 502,
        "position": 2,
        "slide_title": "内容",
        "status": "failed",
        "has_displayable_artifact": False,
        "html_path": None,
        "screenshot_path": None,
        "final_image_path": None,
        "error_message": "render failed",
    }

    with platform_stub(run_detail=detail) as (base_url, _requests):
        result = run_cli(
            "--base-url",
            base_url,
            "result",
            "--run-id",
            "91",
            "--json",
        )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "partially_completed"
    assert payload["successful_slide_count"] == 1
    assert payload["failed_slide_count"] == 1
    assert payload["cover_png"] == "/isolated/run-91/01.png"
    assert payload["slides"][0]["status"] == "completed"
    assert payload["slides"][1]["status"] == "failed"
    assert payload["slides"][1]["error_message"] == "render failed"


def test_result_never_calls_missing_artifact_complete() -> None:
    detail = _completed_run_detail()
    slides = detail["slides"]
    assert isinstance(slides, list)
    assert isinstance(slides[1], dict)
    slides[1]["screenshot_path"] = None
    slides[1]["has_displayable_artifact"] = False

    with platform_stub(run_detail=detail) as (base_url, _requests):
        result = run_cli(
            "--base-url",
            base_url,
            "result",
            "--run-id",
            "91",
            "--json",
        )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["platform_status"] == "completed"
    assert payload["status"] == "partially_completed"
    assert payload["successful_slide_count"] == 1
    assert payload["failed_slide_count"] == 1
