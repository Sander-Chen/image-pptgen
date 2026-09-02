from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import sys
import threading
import zipfile
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib import error as urlerror
from urllib import request as urlrequest

import pytest


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PACKAGE_ROOT / "src"
REPO_ROOT = PACKAGE_ROOT.parents[2]
sys.path.insert(0, str(SRC_ROOT))


@contextmanager
def image_platform_stub(
    *,
    run_statuses: list[dict[str, Any]] | None = None,
    run_detail: dict[str, Any] | None = None,
    configs: list[dict[str, Any]] | None = None,
    run_ids: list[int] | None = None,
    runtime_identity: dict[str, Any] | None = None,
    png_files: dict[str, bytes] | None = None,
    split_resource_unavailable: bool = False,
    split_executable_identity_unavailable: bool = False,
):
    requests: list[dict[str, Any]] = []
    status_index = 0

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format: str, *_args: object) -> None:
            return

        def _send(self, status: int, payload: object) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_bytes(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
            nonlocal status_index
            requests.append({"method": "GET", "path": self.path})
            files = png_files or {
                "/artifacts/run_91_System%20Empty/%E6%98%A5%E6%97%A5.png": b"\x89PNG\r\n\x1a\nfixture",
                "/artifacts/run-91/slide-02.png": b"\x89PNG\r\n\x1a\nfixture",
            }
            if self.path in files:
                self._send_bytes(200, files[self.path], "image/png")
                return
            if self.path == "/api/runtime-identity":
                identity = dict(runtime_identity or _valid_image_runtime_identity())
                if identity.get("base_url") == "__stub_base_url__":
                    identity["base_url"] = f"http://127.0.0.1:{self.server.server_port}"
                self._send(200, identity)
                return
            if self.path == "/api/configs":
                self._send(
                    200,
                    configs
                    or [
                        {
                            "id": 3,
                            "name": "Codex Native Image 3.0",
                            "type": "image",
                            "route": "image_3_0",
                            "director": {"model": "gpt-5.6-sol", "reasoning_effort": "low"},
                            "renderer": {"model": "gpt-5.6-luna", "reasoning_effort": "low"},
                            "palette": {"model": "gpt-5.6-sol", "reasoning_effort": "low"},
                        },
                        {
                            "id": 4,
                            "name": "Codex Native Image 3.0 Luna Low Director",
                            "type": "image",
                            "route": "image_3_0",
                            "director": {"model": "gpt-5.6-luna", "reasoning_effort": "low"},
                            "renderer": {"model": "gpt-5.6-luna", "reasoning_effort": "low"},
                            "palette": {"model": "gpt-5.6-luna", "reasoning_effort": "low"},
                        },
                    ],
                )
                return
            if self.path.startswith("/api/runs/91/status"):
                payloads = run_statuses or [
                    {
                        "run_id": 91,
                        "status": "completed",
                        "progress": {
                            "completed": 2,
                            "failed": 0,
                            "running": 0,
                            "pending": 0,
                            "total": 2,
                        },
                        "activity": {"events": [], "next_cursor": None},
                    }
                ]
                payload = payloads[min(status_index, len(payloads) - 1)]
                status_index += 1
                self._send(200, payload)
                return
            if self.path == "/api/runs/91":
                self._send(
                    200,
                    run_detail
                    or {
                        "id": 91,
                        "engine": "image",
                        "status": "completed",
                        "design_principle_raw": "{\"theme\":\"ready\"}",
                        "slides": [
                            {
                                "id": 701,
                                "position": 1,
                                "slide_title": "Cover",
                                "status": "completed",
                                "has_displayable_artifact": True,
                                "final_image_path": "/artifacts/run_91_System Empty/春日.png",
                            },
                            {
                                "id": 702,
                                "position": 2,
                                "slide_title": "Body",
                                "status": "completed",
                                "has_displayable_artifact": True,
                                "final_image_path": "/artifacts/run-91/slide-02.png",
                            },
                        ],
                    },
                )
                return
            self._send(404, {"error": "Not found"})

        def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length)
            payload = json.loads(raw.decode("utf-8")) if raw else None
            requests.append({"method": "POST", "path": self.path, "json": payload})
            if self.path == "/api/decks":
                self._send(201, {"id": 41})
                return
            if self.path == "/api/decks/41/split-drafts":
                if split_resource_unavailable:
                    self._send(
                        503,
                        {
                            "error": "resource_unavailable",
                            "message": "Auto Split resources are currently unavailable; please retry shortly",
                        },
                    )
                    return
                if split_executable_identity_unavailable:
                    self._send(
                        503,
                        {
                            "error": "executable_identity_unavailable",
                            "message": "Codex Desktop executable identity is unavailable; please retry after Codex Desktop is ready",
                        },
                    )
                    return
                self._send(
                    201,
                    {
                        "id": 71,
                        "deck_id": 41,
                        "status": "pending",
                        "model": "gpt-5.6-terra",
                        "attempt_count": 3,
                        "content_mode": "faithful",
                        "slides": [
                            {"title": "第一章", "content": "保留全部事实 42。"},
                            {"title": "第二章", "content": "保留全部事实 7。"},
                        ],
                    },
                )
                return
            if self.path == "/api/deck-split-drafts/71/revise":
                if payload == {"target_page_count": 99}:
                    self._send(
                        422,
                        {
                            "error": "target_page_count_unavailable",
                            "message": "target_page_count is unavailable",
                        },
                    )
                    return
                self._send(
                    200,
                    {
                        "id": 71,
                        "deck_id": 41,
                        "status": "pending",
                        "model": "gpt-5.6-terra",
                        "attempt_count": 4,
                        "content_mode": "faithful",
                        "slides": [
                            {"title": "重排后的第一章", "content": "保留全部事实 42。"},
                            {"title": "重排后的第二章", "content": "保留全部事实 7。"},
                        ],
                    },
                )
                return
            if self.path == "/api/deck-split-drafts/71/confirm":
                self._send(
                    200,
                    {
                        "deck_id": 41,
                        "draft_id": 71,
                        "status": "confirmed",
                        "slide_count": 3,
                        "slide_ids": [501, 502, 503],
                    },
                )
                return
            if self.path == "/api/generate":
                self._send(202, {"batch_id": 81, "run_ids": run_ids or [91]})
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


def run_image_cli(
    *args: str,
    base_url: str | None = None,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = {"PYTHONPATH": str(SRC_ROOT)}
    if extra_env:
        env.update(extra_env)
    command = [sys.executable, "-m", "pptgen_toolkit.image_cli"]
    if base_url is not None:
        command.extend(["--base-url", base_url])
    command.extend(args)
    return subprocess.run(
        command,
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def _valid_image_runtime_identity() -> dict[str, str]:
    return {
        "artifacts_root": "image-pptgen/state/data/artifacts",
        "base_url": "__stub_base_url__",
        "build_id": "image-build-001",
        "data_root": "image-pptgen/state/data",
        "instance_id": "image-instance-001",
        "product": "image-pptgen",
        "service": "image-pptgen-server",
        "skill_sha256": "a" * 64,
        "source_commit": "b" * 40,
        "surface": "public_image_3_0",
        "runtime_content_sha256": "c" * 64,
        "version": "0.1.0",
    }


def test_image_cli_has_fixed_command_surface_and_default_port() -> None:
    from pptgen_toolkit import image_cli

    assert image_cli.DEFAULT_BASE_URL == "http://127.0.0.1:3130"
    parser = image_cli._parser()
    assert parser.prog == "image-pptgen"
    result_help = run_image_cli("result", "--help").stdout
    assert "--static-preview-file" in result_help
    assert "intent" not in parser.format_help()
    assert "preference" not in parser.format_help()
    assert "config-id" not in parser.format_help()
    assert "requirement" not in parser.format_help()


def test_image_doctor_uses_public_runtime_identity() -> None:
    with image_platform_stub() as (base_url, requests):
        result = run_image_cli("doctor", "--json", base_url=base_url)

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "artifacts_root": "image-pptgen/state/data/artifacts",
        "base_url": base_url,
        "build_id": "image-build-001",
        "data_root": "image-pptgen/state/data",
        "instance_id": "image-instance-001",
        "ok": True,
        "product": "image-pptgen",
        "service": "image-pptgen-server",
        "skill_sha256": "a" * 64,
        "source_commit": "b" * 40,
        "surface": "public_image_3_0",
        "runtime_content_sha256": "c" * 64,
        "version": "0.1.0",
    }
    assert requests == [{"method": "GET", "path": "/api/runtime-identity"}]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("product", "PPTGen"),
        ("service", "image-pptgen-platform"),
        ("surface", "public_html"),
        ("build_id", ""),
        ("version", ""),
        ("source_commit", ""),
        ("skill_sha256", ""),
        ("runtime_content_sha256", ""),
    ],
)
def test_image_doctor_fails_closed_on_wrong_or_incomplete_runtime_identity(
    field: str, value: str
) -> None:
    identity = _valid_image_runtime_identity()
    identity[field] = value
    with image_platform_stub(runtime_identity=identity) as (base_url, requests):
        result = run_image_cli("doctor", "--json", base_url=base_url)

    assert result.returncode == 4
    assert json.loads(result.stderr)["error"] == "platform_error"
    assert requests == [{"method": "GET", "path": "/api/runtime-identity"}]


def test_image_material_rejects_image_before_any_platform_call(tmp_path: Path) -> None:
    image_path = tmp_path / "source.png"
    image_path.write_bytes(b"\x89PNG\r\n\x1a\nnot-text")
    with image_platform_stub() as (base_url, requests):
        result = run_image_cli(
            "material",
            "submit",
            "--title",
            "Image source",
            "--text-file",
            str(image_path),
            "--json",
            base_url=base_url,
        )

    assert result.returncode == 2
    assert json.loads(result.stderr)["error"] == "unsupported_image_input"
    assert requests == []


def test_image_split_propose_revision_and_confirm_keep_same_draft() -> None:
    with image_platform_stub() as (base_url, requests):
        proposed = run_image_cli(
            "split",
            "propose",
            "--deck-id",
            "41",
            "--json",
            base_url=base_url,
        )
        revised = run_image_cli(
            "split",
            "revise",
            "--draft-id",
            "71",
            "--instruction",
            "把第二页拆成两页，但保留全部数字和原文",
            "--json",
            base_url=base_url,
        )
        confirmed = run_image_cli(
            "split",
            "confirm",
            "--draft-id",
            "71",
            "--json",
            base_url=base_url,
        )

    assert proposed.returncode == revised.returncode == confirmed.returncode == 0
    proposal_payload = json.loads(proposed.stdout)
    revision_payload = json.loads(revised.stdout)
    assert proposal_payload["draft_id"] == revision_payload["draft_id"] == 71
    assert proposal_payload["model"] == revision_payload["model"] == "gpt-5.6-terra"
    assert proposal_payload["attempt_count"] == 3
    assert revision_payload["attempt_count"] == 4
    assert proposal_payload["page_count"] == revision_payload["page_count"] == 2
    assert "重排后的第一章" in revision_payload["markdown"]
    assert json.loads(confirmed.stdout)["slide_count"] == 3
    assert requests == [
        {
            "method": "POST",
            "path": "/api/decks/41/split-drafts",
            "json": {},
        },
        {
            "method": "POST",
            "path": "/api/deck-split-drafts/71/revise",
            "json": {"instruction": "把第二页拆成两页，但保留全部数字和原文"},
        },
        {
            "method": "POST",
            "path": "/api/deck-split-drafts/71/confirm",
            "json": None,
        },
    ]


def test_image_split_target_page_revision_sends_structured_payload() -> None:
    with image_platform_stub() as (base_url, requests):
        revised = run_image_cli(
            "split",
            "revise",
            "--draft-id",
            "71",
            "--target-page-count",
            "3",
            "--json",
            base_url=base_url,
        )

    assert revised.returncode == 0, revised.stderr
    assert json.loads(revised.stdout)["draft_id"] == 71
    assert requests == [
        {
            "method": "POST",
            "path": "/api/deck-split-drafts/71/revise",
            "json": {"target_page_count": 3},
        }
    ]


def test_image_split_revision_rejects_mutually_exclusive_options() -> None:
    result = run_image_cli(
        "split",
        "revise",
        "--draft-id",
        "71",
        "--instruction",
        "调整标题",
        "--target-page-count",
        "3",
        "--json",
    )

    assert result.returncode == 2
    assert "not allowed with argument" in result.stderr


def test_image_split_target_page_error_preserves_typed_error_code() -> None:
    with image_platform_stub() as (base_url, _requests):
        result = run_image_cli(
            "split",
            "revise",
            "--draft-id",
            "71",
            "--target-page-count",
            "99",
            "--json",
            base_url=base_url,
        )

    assert result.returncode == 4
    assert json.loads(result.stderr) == {
        "error": "target_page_count_unavailable",
        "message": "target_page_count is unavailable",
    }


def test_image_split_resource_admission_error_preserves_typed_error_code() -> None:
    with image_platform_stub(split_resource_unavailable=True) as (base_url, requests):
        result = run_image_cli(
            "split",
            "propose",
            "--deck-id",
            "41",
            "--json",
            base_url=base_url,
        )

    assert result.returncode == 4
    assert json.loads(result.stderr) == {
        "error": "resource_unavailable",
        "message": "Auto Split resources are currently unavailable; please retry shortly",
    }
    assert requests == [
        {
            "method": "POST",
            "path": "/api/decks/41/split-drafts",
            "json": {},
        }
    ]


def test_image_split_executable_identity_error_preserves_typed_error_code() -> None:
    with image_platform_stub(split_executable_identity_unavailable=True) as (base_url, requests):
        result = run_image_cli(
            "split",
            "propose",
            "--deck-id",
            "41",
            "--json",
            base_url=base_url,
        )

    assert result.returncode == 4
    assert json.loads(result.stderr) == {
        "error": "executable_identity_unavailable",
        "message": "Codex Desktop executable identity is unavailable; please retry after Codex Desktop is ready",
    }
    assert requests == [
        {
            "method": "POST",
            "path": "/api/decks/41/split-drafts",
            "json": {},
        }
    ]


def test_image_generate_resolves_luna_config_and_sends_exact_six_fields() -> None:
    with image_platform_stub() as (base_url, requests):
        result = run_image_cli(
            "generate",
            "--deck-id",
            "41",
            "--json",
            base_url=base_url,
        )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "batch_id": 81,
        "config_name": "Codex Native Image 3.0 Luna Low Director",
        "deck_id": 41,
        "run_ids": [91],
        "status": "generation_started",
    }
    assert requests == [
        {"method": "GET", "path": "/api/configs"},
        {
            "method": "POST",
            "path": "/api/generate",
            "json": {
                "deck_id": 41,
                "config_id": 4,
                "engine": "image",
                "strategy": "image_3_0",
                "requirement_ids": [],
                "color_ids": [],
            },
        },
    ]


def _valid_luna_config(*, config_id: int = 4) -> dict[str, Any]:
    identity = {"model": "gpt-5.6-luna", "reasoning_effort": "low"}
    return {
        "id": config_id,
        "name": "Codex Native Image 3.0 Luna Low Director",
        "type": "image",
        "route": "image_3_0",
        "director": dict(identity),
        "renderer": dict(identity),
        "palette": dict(identity),
    }


def _valid_terra_e2e_config(*, config_id: int = 5) -> dict[str, Any]:
    identity = {"model": "gpt-5.6-terra", "reasoning_effort": "low"}
    return {
        "id": config_id,
        "name": "Codex Native Image 3.0 Terra Low E2E",
        "type": "image",
        "route": "image_3_0",
        "director": dict(identity),
        "renderer": dict(identity),
        "palette": dict(identity),
    }


def test_image_generate_uses_explicit_terra_e2e_config_when_enabled() -> None:
    with image_platform_stub(configs=[_valid_luna_config(), _valid_terra_e2e_config()]) as (
        base_url,
        requests,
    ):
        result = run_image_cli(
            "generate",
            "--deck-id",
            "41",
            "--json",
            base_url=base_url,
            extra_env={"IMAGE_PPTGEN_E2E_TERRA_LOW": "1"},
        )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["config_name"] == "Codex Native Image 3.0 Terra Low E2E"
    assert requests == [
        {"method": "GET", "path": "/api/configs"},
        {
            "method": "POST",
            "path": "/api/generate",
            "json": {
                "deck_id": 41,
                "config_id": 5,
                "engine": "image",
                "strategy": "image_3_0",
                "requirement_ids": [],
                "color_ids": [],
            },
        },
    ]


@pytest.mark.parametrize(
    "mutation",
    [
        lambda config: config.update(type="html"),
        lambda config: config.update(route="image_direct"),
        lambda config: config["director"].update(model="gpt-5.6-sol"),
        lambda config: config["director"].update(reasoning_effort="high"),
        lambda config: config["renderer"].update(model="gpt-5.6-sol"),
        lambda config: config["palette"].update(reasoning_effort="high"),
    ],
)
def test_image_generate_fails_closed_on_same_name_but_wrong_luna_identity(mutation) -> None:
    config = _valid_luna_config()
    mutation(config)
    with image_platform_stub(configs=[config]) as (base_url, requests):
        result = run_image_cli(
            "generate",
            "--deck-id",
            "41",
            "--json",
            base_url=base_url,
        )

    assert result.returncode == 4
    assert json.loads(result.stderr)["error"] == "platform_error"
    assert requests == [{"method": "GET", "path": "/api/configs"}]


def test_image_generate_fails_closed_on_ambiguous_luna_configs() -> None:
    with image_platform_stub(
        configs=[_valid_luna_config(), _valid_luna_config(config_id=5)]
    ) as (base_url, requests):
        result = run_image_cli(
            "generate",
            "--deck-id",
            "41",
            "--json",
            base_url=base_url,
        )

    assert result.returncode == 4
    assert requests == [{"method": "GET", "path": "/api/configs"}]


def test_image_generate_rejects_multiple_run_ids() -> None:
    with image_platform_stub(run_ids=[91, 92]) as (base_url, requests):
        result = run_image_cli(
            "generate",
            "--deck-id",
            "41",
            "--json",
            base_url=base_url,
        )

    assert result.returncode == 4
    assert json.loads(result.stderr)["error"] == "platform_error"
    assert requests == [
        {"method": "GET", "path": "/api/configs"},
        {
            "method": "POST",
            "path": "/api/generate",
            "json": {
                "deck_id": 41,
                "config_id": 4,
                "engine": "image",
                "strategy": "image_3_0",
                "requirement_ids": [],
                "color_ids": [],
            },
        },
    ]


def test_image_status_follow_reuses_grounded_progress_and_result_urls(
    tmp_path: Path,
) -> None:
    statuses = [
        {
            "run_id": 91,
            "status": "running",
            "progress": {
                "completed": 1,
                "failed": 0,
                "running": 1,
                "pending": 0,
                "total": 2,
            },
            "activity": {
                "events": [
                    {
                        "kind": "business_activity",
                        "message": "正在生成页面",
                        "cursor": "91:1",
                        "milestone": False,
                    }
                ],
                "next_cursor": "91:1",
            },
        },
        {
            "run_id": 91,
            "status": "completed",
            "progress": {
                "completed": 2,
                "failed": 0,
                "running": 0,
                "pending": 0,
                "total": 2,
            },
            "activity": {"events": [], "next_cursor": "91:2"},
        },
    ]
    with image_platform_stub(run_statuses=statuses) as (base_url, _requests):
        followed = run_image_cli(
            "status",
            "--run-id",
            "91",
            "--follow",
            "--jsonl",
            base_url=base_url,
            extra_env={"IMAGE_PPTGEN_STATUS_INTERVAL_SECONDS": "0.01"},
        )
        result = run_image_cli(
            "result",
            "--run-id",
            "91",
            "--json",
            base_url=base_url,
            extra_env=_static_preview_env(tmp_path, "linux"),
        )

    assert followed.returncode == 0, followed.stderr
    lines = [json.loads(line) for line in followed.stdout.splitlines() if line.strip()]
    assert any(line.get("event") == "activity" for line in lines)
    assert lines[-1]["source_facts"] == {
        "completed_slides": 2,
        "design_ready": True,
        "failed_slides": 0,
        "pending_slides": 0,
        "run_status": "completed",
        "running_slides": 0,
        "total_slides": 2,
    }
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["status"] == "completed"
    assert all("html_path" not in slide for slide in output["slides"])
    viewer = tmp_path / "static-preview-bundles" / "run-91" / "index.html"
    archive = tmp_path / "static-preview-bundles" / "run-91.zip"
    assert output["preview_url"] == viewer.resolve().as_uri()
    assert output["download_url"] == archive.resolve().as_uri()


def test_image_result_writes_standalone_static_preview(tmp_path: Path) -> None:
    preview_path = tmp_path / "run-91-preview.html"
    with image_platform_stub() as (base_url, requests):
        result = run_image_cli(
            "result",
            "--run-id",
            "91",
            "--static-preview-file",
            str(preview_path),
            "--json",
            base_url=base_url,
            extra_env=_static_preview_env(tmp_path / "artifacts", "linux"),
        )

    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["static_preview_path"] == str(preview_path)
    assert output["static_preview_url"] == preview_path.as_uri()
    document = preview_path.read_text(encoding="utf-8")
    assert document.count("data:image/png;base64,") == 2
    assert "Cover" in document
    assert "Body" in document
    assert [request["path"] for request in requests] == [
        "/api/runs/91",
        "/artifacts/run_91_System%20Empty/%E6%98%A5%E6%97%A5.png",
        "/artifacts/run-91/slide-02.png",
        "/artifacts/run_91_System%20Empty/%E6%98%A5%E6%97%A5.png",
        "/artifacts/run-91/slide-02.png",
    ]
    artifacts_root = tmp_path / "artifacts"
    assert output["preview_url"] == (
        artifacts_root / "static-preview-bundles" / "run-91" / "index.html"
    ).resolve().as_uri()
    assert output["download_url"] == (
        artifacts_root / "static-preview-bundles" / "run-91.zip"
    ).resolve().as_uri()


def _static_preview_env(tmp_path: Path, preview_os: str = "darwin") -> dict[str, str]:
    return {
        "IMAGE_PPTGEN_STATIC_PREVIEW_OS": preview_os,
        "PPT_ARTIFACTS_DIR": str(tmp_path),
    }


def test_static_preview_enabled_follows_sys_platform_without_os_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pptgen_toolkit import image_cli

    monkeypatch.delenv("IMAGE_PPTGEN_STATIC_PREVIEW_OS", raising=False)
    for platform in ("darwin", "linux", "win32"):
        monkeypatch.setattr(image_cli.sys, "platform", platform)
        assert image_cli._static_preview_os() == platform
        assert image_cli._static_preview_enabled() is True
    monkeypatch.setattr(image_cli.sys, "platform", "freebsd")
    assert image_cli._static_preview_os() == "freebsd"
    assert image_cli._static_preview_enabled() is False


def test_native_platform_completed_result_uses_file_preview_without_os_override(
    tmp_path: Path,
) -> None:
    if sys.platform not in {"darwin", "linux", "win32"}:
        pytest.skip(f"unsupported native platform {sys.platform!r}")
    with image_platform_stub() as (base_url, _requests):
        result = run_image_cli(
            "result",
            "--run-id",
            "91",
            "--json",
            base_url=base_url,
            extra_env={"PPT_ARTIFACTS_DIR": str(tmp_path)},
        )

    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    viewer = tmp_path / "static-preview-bundles" / "run-91" / "index.html"
    archive = tmp_path / "static-preview-bundles" / "run-91.zip"
    assert output["preview_url"] == viewer.resolve().as_uri()
    assert output["download_url"] == archive.resolve().as_uri()
    assert output["preview_url"].startswith("file:")
    assert "127.0.0.1" not in output["preview_url"]


@pytest.mark.parametrize("preview_os", ["darwin", "linux", "win32"])
def test_completed_result_writes_current_run_offline_bundle_and_viewer_local_zip(
    tmp_path: Path,
    preview_os: str,
) -> None:
    raw_first = "/artifacts/run space/%E6%98%A5%E6%97%A5 %2f%25.png"
    raw_second = "/artifacts/run space/slide 02.png"
    encoded_first = "/artifacts/run%20space/%E6%98%A5%E6%97%A5%20%2F%25.png"
    encoded_second = "/artifacts/run%20space/slide%2002.png"
    png_files = {
        encoded_first: b"\x89PNG\r\n\x1a\nfirst",
        encoded_second: b"\x89PNG\r\n\x1a\nsecond",
    }
    run_detail = {
        "id": 91,
        "engine": "image",
        "status": "completed",
        "design_principle_raw": "{\"theme\":\"ready\"}",
        "slides": [
            {
                "id": 702,
                "position": 2,
                "slide_title": "正文",
                "status": "completed",
                "has_displayable_artifact": True,
                "final_image_path": raw_second,
            },
            {
                "id": 701,
                "position": 1,
                "slide_title": "封面",
                "status": "completed",
                "has_displayable_artifact": True,
                "final_image_path": raw_first,
            },
        ],
    }
    with image_platform_stub(run_detail=run_detail, png_files=png_files) as (base_url, requests):
        result = run_image_cli(
            "result",
            "--run-id",
            "91",
            "--json",
            base_url=base_url,
            extra_env=_static_preview_env(tmp_path, preview_os),
        )

    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    viewer = tmp_path / "static-preview-bundles" / "run-91" / "index.html"
    archive = tmp_path / "static-preview-bundles" / "run-91.zip"
    manifest_path = viewer.parent / "manifest.json"
    viewer_archive = viewer.parent / archive.name
    assert output["preview_url"] == viewer.resolve().as_uri()
    assert output["download_url"] == archive.resolve().as_uri()
    assert output["preview_url"].startswith("file:")
    assert output["download_url"].startswith("file:")
    assert "127.0.0.1" not in output["preview_url"]
    assert "127.0.0.1" not in output["download_url"]
    assert output["static_preview"] == {
        "kind": "macos-static-preview-bundle",
        "manifest_version": 1,
        "run_id": 91,
        "viewer_path": str(viewer.resolve()),
        "zip_path": str(archive.resolve()),
        "page_count": 2,
    }
    assert viewer.is_file()
    assert archive.is_file()
    assert viewer_archive.read_bytes() == archive.read_bytes()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert [page["position"] for page in manifest["pages"]] == [1, 2]
    assert manifest["zip"]["sha256"] == hashlib.sha256(archive.read_bytes()).hexdigest()
    assert manifest["zip"]["size"] == archive.stat().st_size
    viewer_html = viewer.read_text(encoding="utf-8")
    assert 'href="run-91.zip"' in viewer_html
    assert "requestFullscreen" in viewer_html
    assert 'aria-label="Previous slide"' in viewer_html
    assert 'aria-label="Next slide"' in viewer_html
    assert 'aria-label="Direct page selection"' in viewer_html
    assert 'aria-label="Fit to window"' in viewer_html
    assert "Ready to present" in viewer_html
    assert "Presentation story (" in viewer_html
    assert "Download presentation package" in viewer_html
    assert "presentation-preview-rail" in viewer_html
    assert "presentation-preview-thumbnail-title" in viewer_html
    assert "color-scheme: dark" not in viewer_html
    assert "127.0.0.1:3130" not in viewer_html
    with zipfile.ZipFile(archive) as bundle:
        assert archive.name not in bundle.namelist()
        assert bundle.read("pages/page-001.png") == png_files[encoded_first]
        assert bundle.read("pages/page-002.png") == png_files[encoded_second]
    assert [request["path"] for request in requests] == [
        "/api/runs/91",
        encoded_first,
        encoded_second,
    ]


def test_macos_result_preserves_legacy_static_preview_file_option(tmp_path: Path) -> None:
    legacy_preview = tmp_path / "legacy-preview.html"
    artifacts_root = tmp_path / "artifacts"
    with image_platform_stub() as (base_url, _requests):
        result = run_image_cli(
            "result",
            "--run-id",
            "91",
            "--static-preview-file",
            str(legacy_preview),
            "--json",
            base_url=base_url,
            extra_env=_static_preview_env(artifacts_root),
        )

    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["static_preview_path"] == str(legacy_preview)
    assert output["static_preview_url"] == legacy_preview.as_uri()
    assert legacy_preview.read_text(encoding="utf-8").count("data:image/png;base64,") == 2
    assert output["preview_url"] == (
        artifacts_root / "static-preview-bundles" / "run-91" / "index.html"
    ).resolve().as_uri()
    assert output["download_url"] == (
        artifacts_root / "static-preview-bundles" / "run-91.zip"
    ).resolve().as_uri()


@pytest.mark.parametrize("preview_os", ["linux", "win32"])
def test_non_darwin_completed_result_returns_file_preview_and_zip(
    tmp_path: Path, preview_os: str
) -> None:
    with image_platform_stub() as (base_url, _requests):
        result = run_image_cli(
            "result",
            "--run-id",
            "91",
            "--json",
            base_url=base_url,
            extra_env={
                "IMAGE_PPTGEN_STATIC_PREVIEW_OS": preview_os,
                "PPT_ARTIFACTS_DIR": str(tmp_path),
            },
        )

    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    viewer = tmp_path / "static-preview-bundles" / "run-91" / "index.html"
    archive = tmp_path / "static-preview-bundles" / "run-91.zip"
    assert output["preview_url"] == viewer.resolve().as_uri()
    assert output["download_url"] == archive.resolve().as_uri()
    assert output["preview_url"].startswith("file:")
    assert output["download_url"].startswith("file:")
    assert "127.0.0.1" not in output["preview_url"]
    assert "127.0.0.1" not in output["download_url"]
    assert output["static_preview"]["run_id"] == 91
    assert viewer.is_file()
    assert archive.is_file()


@pytest.mark.parametrize("preview_os", ["darwin", "linux", "win32"])
def test_completed_result_bundle_survives_service_unavailable(
    tmp_path: Path, preview_os: str
) -> None:
    with image_platform_stub() as (base_url, _requests):
        result = run_image_cli(
            "result",
            "--run-id",
            "91",
            "--json",
            base_url=base_url,
            extra_env=_static_preview_env(tmp_path, preview_os),
        )
        loopback_preview = f"{base_url}/history/run/91/preview"

    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    viewer = tmp_path / "static-preview-bundles" / "run-91" / "index.html"
    archive = tmp_path / "static-preview-bundles" / "run-91.zip"
    viewer_zip = viewer.parent / archive.name
    assert output["preview_url"] == viewer.resolve().as_uri()
    assert output["download_url"] == archive.resolve().as_uri()
    assert viewer.is_file()
    assert archive.is_file()
    assert viewer_zip.read_bytes() == archive.read_bytes()
    document = viewer.read_text(encoding="utf-8")
    assert "requestFullscreen" in document
    assert "127.0.0.1:3130" not in document
    with zipfile.ZipFile(archive) as bundle:
        assert "index.html" in bundle.namelist()
        assert archive.name not in bundle.namelist()
    with pytest.raises((urlerror.URLError, TimeoutError, OSError)):
        urlrequest.urlopen(loopback_preview, timeout=1)


@pytest.mark.parametrize("preview_os", ["darwin", "linux", "win32"])
def test_completed_result_repeat_read_replaces_bundle_without_zip_self_inclusion(
    tmp_path: Path,
    preview_os: str,
) -> None:
    with image_platform_stub() as (base_url, _requests):
        first = run_image_cli(
            "result",
            "--run-id",
            "91",
            "--json",
            base_url=base_url,
            extra_env=_static_preview_env(tmp_path, preview_os),
        )
        second = run_image_cli(
            "result",
            "--run-id",
            "91",
            "--json",
            base_url=base_url,
            extra_env=_static_preview_env(tmp_path, preview_os),
        )

    assert first.returncode == second.returncode == 0, second.stderr
    archive = tmp_path / "static-preview-bundles" / "run-91.zip"
    first_output = json.loads(first.stdout)
    second_output = json.loads(second.stdout)
    assert first_output["preview_url"] == second_output["preview_url"]
    assert first_output["download_url"] == second_output["download_url"]
    with zipfile.ZipFile(archive) as bundle:
        names = bundle.namelist()
    assert "run-91.zip" not in names
    assert names.count("index.html") == 1
    nested = [name for name in names if name.endswith(".zip")]
    assert nested == []


def _completed_one_slide(path: str, *, run_id: int = 91) -> dict[str, Any]:
    return {
        "id": run_id,
        "engine": "image",
        "status": "completed",
        "design_principle_raw": "{\"theme\":\"ready\"}",
        "slides": [
            {
                "id": 701,
                "position": 1,
                "slide_title": "Cover",
                "status": "completed",
                "has_displayable_artifact": True,
                "final_image_path": path,
            }
        ],
    }


@pytest.mark.parametrize("preview_os", ["darwin", "linux", "win32"])
def test_completed_result_does_not_write_bundle_for_partially_completed_run(
    tmp_path: Path,
    preview_os: str,
) -> None:
    run_detail = {
        "id": 91,
        "engine": "image",
        "status": "completed",
        "design_principle_raw": "{\"theme\":\"ready\"}",
        "slides": [
            {
                "id": 701,
                "position": 1,
                "slide_title": "Cover",
                "status": "completed",
                "has_displayable_artifact": True,
                "final_image_path": "/artifacts/run-91/slide-01.png",
            },
            {
                "id": 702,
                "position": 2,
                "slide_title": "Body",
                "status": "running",
                "has_displayable_artifact": False,
                "final_image_path": None,
            },
        ],
    }
    with image_platform_stub(
        run_detail=run_detail,
        png_files={"/artifacts/run-91/slide-01.png": b"\x89PNG\r\n\x1a\nfirst"},
    ) as (base_url, _requests):
        result = run_image_cli(
            "result",
            "--run-id",
            "91",
            "--json",
            base_url=base_url,
            extra_env=_static_preview_env(tmp_path, preview_os),
        )

    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["status"] == "partially_completed"
    assert output["preview_url"] == f"{base_url}/history/run/91/preview"
    assert "static_preview" not in output
    assert not (tmp_path / "static-preview-bundles").exists()


@pytest.mark.parametrize(
    "run_detail,png_files",
    [
        (_completed_one_slide("/artifacts/run-91/slide-01.png", run_id=92), {"/artifacts/run-91/slide-01.png": b"\x89PNG\r\n\x1a\nfirst"}),
        (_completed_one_slide("/tmp/not-an-artifact.png"), {}),
        (_completed_one_slide("/artifacts/../secret.png"), {}),
        (_completed_one_slide("/artifacts/run-91/slide%.png"), {}),
        (_completed_one_slide("/artifacts/run-91/slide-01.png"), {"/artifacts/run-91/slide-01.png": b"not-a-png"}),
        (_completed_one_slide("/artifacts/run-91/slide-01.png"), {"/artifacts/run-91/slide-01.png": b""}),
    ],
)
@pytest.mark.parametrize("preview_os", ["darwin", "linux", "win32"])
def test_completed_result_fails_closed_on_mixed_invalid_or_non_png_artifacts(
    tmp_path: Path,
    preview_os: str,
    run_detail: dict[str, Any],
    png_files: dict[str, bytes],
) -> None:
    with image_platform_stub(run_detail=run_detail, png_files=png_files) as (
        base_url,
        _requests,
    ):
        result = run_image_cli(
            "result",
            "--run-id",
            "91",
            "--json",
            base_url=base_url,
            extra_env=_static_preview_env(tmp_path, preview_os),
        )

    assert result.returncode == 4, result.stderr
    assert json.loads(result.stderr)["error"] == "platform_error"
    assert not (tmp_path / "static-preview-bundles").exists()


@pytest.mark.parametrize("preview_os", ["darwin", "linux", "win32"])
def test_in_progress_result_keeps_loopback_urls(tmp_path: Path, preview_os: str) -> None:
    run_detail = {
        "id": 91,
        "engine": "image",
        "status": "running",
        "design_principle_raw": "{\"theme\":\"ready\"}",
        "slides": [
            {
                "id": 701,
                "position": 1,
                "slide_title": "Cover",
                "status": "running",
                "has_displayable_artifact": False,
                "final_image_path": None,
            }
        ],
    }
    with image_platform_stub(run_detail=run_detail, png_files={}) as (base_url, _requests):
        result = run_image_cli(
            "result",
            "--run-id",
            "91",
            "--json",
            base_url=base_url,
            extra_env=_static_preview_env(tmp_path, preview_os),
        )

    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["status"] == "in_progress"
    assert output["preview_url"] == f"{base_url}/history/run/91/preview"
    assert output["download_url"] == f"{base_url}/api/runs/91/download"
    assert "static_preview" not in output
    assert not (tmp_path / "static-preview-bundles").exists()


def test_skill_and_cli_contract_describe_cross_platform_offline_bundle() -> None:
    project_root = PACKAGE_ROOT.parents[1]
    skill = (
        project_root / "skills" / "generate-image-presentation" / "SKILL.md"
    ).read_text(encoding="utf-8")
    contract = (
        project_root
        / "skills"
        / "generate-image-presentation"
        / "references"
        / "cli-contract.md"
    ).read_text(encoding="utf-8")
    workflow = skill[skill.index("## Complete every image-pptgen command") :]
    result_section = skill[skill.index("## Return the same-Run result") :]
    assert workflow.count("<dispatcher>") == 12
    assert "--static-preview-file" in workflow
    assert "<dispatcher> result --run-id <run_id> --json" in skill
    assert "file:" in skill
    assert "do not present a loopback Preview or download URL" in skill
    assert "prebuilt ZIP" in skill
    assert "On Linux and Windows, run:" not in result_section
    assert "<doctor.base_url>/history/run/<run_id>/preview" in result_section
    assert "For an in-progress Run" in result_section
    assert "A completed result reads only that" in contract
    assert "`file:` viewer and ZIP URLs" in contract
    assert "Linux and Windows retain their existing loopback" not in contract
    assert "Darwin, Linux, and Windows" in contract
    assert "`--static-preview-file` remains an R58-compatible optional command argument" in contract


def test_static_preview_bundle_module_is_importable_without_backend() -> None:
    from pptgen_toolkit.static_preview_bundle import write_static_preview_bundle

    source = (SRC_ROOT / "pptgen_toolkit" / "static_preview_bundle.py").read_text(
        encoding="utf-8"
    )
    assert callable(write_static_preview_bundle)
    assert "import backend" not in source
    assert "import db" not in source
    assert "import pipeline" not in source


def test_image_cli_does_not_import_backend_modules() -> None:
    source = (SRC_ROOT / "pptgen_toolkit" / "image_cli.py").read_text(encoding="utf-8")
    assert "import backend" not in source
    assert "import db" not in source
    assert "import pipeline" not in source
    assert "from .static_preview_bundle import" in source


def test_image_generate_rejects_html_intent_and_config_overrides() -> None:
    with image_platform_stub() as (base_url, requests):
        intent = run_image_cli(
            "generate",
            "--deck-id",
            "41",
            "--intent",
            "auto",
            "--json",
            base_url=base_url,
        )
        config = run_image_cli(
            "generate",
            "--deck-id",
            "41",
            "--config-id",
            "4",
            "--json",
            base_url=base_url,
        )

    assert intent.returncode == 2
    assert config.returncode == 2
    assert requests == []


def test_default_base_url_is_free_for_a_local_probe() -> None:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.close()
    from pptgen_toolkit.image_client import ImagePptgenClient

    client = ImagePptgenClient("http://127.0.0.1:3130")
    assert client.base_url == "http://127.0.0.1:3130"
    assert client.long_timeout == 900.0
