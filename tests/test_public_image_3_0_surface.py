from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap


ROOT = Path(__file__).resolve().parents[1]


def _run_public_case(
    tmp_path: Path,
    case_source: str,
    *,
    extra_env: dict[str, str] | None = None,
) -> dict:
    database_path = tmp_path / "public.db"
    artifacts_path = tmp_path / "artifacts"
    encoded_case = base64.b64encode(textwrap.dedent(case_source).encode("utf-8")).decode("ascii")
    bootstrap = f"""
import base64
import json
from pathlib import Path

import public_server
import db as dbmod
from backend.services import model_profiles

app = public_server.app
app.config.update(TESTING=True)
client = app.test_client()
exec(base64.b64decode({encoded_case!r}).decode("utf-8"))
"""
    env = os.environ.copy()
    env.update(
        {
            "PYTHONPATH": str(ROOT),
            "PPT_DB_PATH": str(database_path),
            "PPT_ARTIFACTS_DIR": str(artifacts_path),
            "PPTGEN_PUBLIC_DATA_DIR": str(tmp_path),
        }
    )
    env.update(extra_env or {})
    completed = subprocess.run(
        [sys.executable, "-c", bootstrap],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    result_line = next(
        (line for line in reversed(completed.stdout.splitlines()) if line.startswith("PUBLIC_RESULT:")),
        None,
    )
    assert result_line, completed.stdout
    return json.loads(result_line.removeprefix("PUBLIC_RESULT:"))


def _run_server_static_case(tmp_path: Path, case_source: str) -> dict:
    """Run the underlying static server with isolated public artifact roots."""
    database_path = tmp_path / "server.db"
    artifacts_path = tmp_path / "artifacts"
    encoded_case = base64.b64encode(textwrap.dedent(case_source).encode("utf-8")).decode("ascii")
    bootstrap = f"""
import base64
import json

import server

app = server.app
app.config.update(TESTING=True)
client = app.test_client()
exec(base64.b64decode({encoded_case!r}).decode("utf-8"))
"""
    env = os.environ.copy()
    env.update(
        {
            "PYTHONPATH": str(ROOT),
            "PPT_DB_PATH": str(database_path),
            "PPT_ARTIFACTS_DIR": str(artifacts_path),
        }
    )
    completed = subprocess.run(
        [sys.executable, "-c", bootstrap],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    result_line = next(
        (line for line in reversed(completed.stdout.splitlines()) if line.startswith("STATIC_RESULT:")),
        None,
    )
    assert result_line, completed.stdout
    return json.loads(result_line.removeprefix("STATIC_RESULT:"))


def test_terra_e2e_route_is_explicit_and_reaches_pre_image_context(tmp_path: Path) -> None:
    result = _run_public_case(
        tmp_path,
        """
        from backend.services import pipeline_context, public_image_surface

        configs = public_image_surface.public_configs()
        terra = next(item for item in configs if item["name"] == model_profiles.NATIVE_IMAGE_3_0_TERRA_E2E_CONFIG_NAME)
        split = public_image_surface._public_split_execution()

        deck_id = dbmod.create_deck("Terra E2E", "完整材料")
        dbmod.create_slide(deck_id, 1, "Cover", "Cover", split_mode="manual")
        dbmod.create_slide(deck_id, 2, "Seed", "Seed", split_mode="manual")
        launched = []
        public_server.server.launch_batch_runs = lambda run_ids, db_path, max_concurrent_runs: launched.append(list(run_ids))
        response = client.post("/api/generate", json={
            "deck_id": deck_id,
            "config_id": terra["id"],
            "engine": "image",
            "strategy": "image_3_0",
            "requirement_ids": [],
            "color_ids": [],
        })
        payload = response.get_json()
        context = pipeline_context.load_run_context(payload["run_ids"][0])
        print("PUBLIC_RESULT:" + json.dumps({
            "status": response.status_code,
            "public_config_names": [item["name"] for item in configs],
            "terra": terra,
            "split": {"model": split["model"], "thinking": split["thinking"]},
            "models": {
                "director": context.image_designer_config["model"],
                "palette": context.image_palette_extractor_config["model"],
                "launcher": context.image_generator_config["model"],
            },
            "launched": launched,
        }))
        """,
        extra_env={"IMAGE_PPTGEN_E2E_TERRA_LOW": "1"},
    )

    assert result["status"] == 202
    assert result["public_config_names"][-1] == "Codex Native Image 3.0 Terra Low E2E"
    identity = {"model": "gpt-5.6-terra", "reasoning_effort": "low"}
    assert result["terra"]["director"] == identity
    assert result["terra"]["renderer"] == identity
    assert result["terra"]["palette"] == identity
    # The explicit Terra E2E seam remains pinned for this existing acceptance
    # route; production with the seam disabled uses Luna-first recovery.
    assert result["split"] == {"model": "gpt-5.6-terra", "thinking": "low"}
    assert result["models"] == {
        "director": "gpt-5.6-terra",
        "palette": "gpt-5.6-terra",
        "launcher": "gpt-5.6-terra",
    }
    assert len(result["launched"]) == 1


def test_public_image_split_identity_and_command_private_artifacts_are_not_served(
    tmp_path: Path,
) -> None:
    result = _run_server_static_case(
        tmp_path,
        """
        import asyncio
        import os
        import sys
        from pathlib import Path
        from backend.services import codex_exec

        artifact_root = Path(os.environ["PPT_ARTIFACTS_DIR"])
        split_root = artifact_root / "split-drafts" / "r34-static-route"
        codex_exec.resolve_codex_executable = lambda: sys.executable
        codex_exec.build_codex_command = lambda **_kwargs: [
            sys.executable,
            "-c",
            "import json,sys; sys.stdin.read(); print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':'private static proof'}}))",
        ]
        execution = asyncio.run(
            codex_exec.run_codex_exec_json(
                stage_id="public-split-static-route",
                role="auto_spill",
                prompt="private artifact proof",
                work_dir=split_root / "scratch",
                artifact_dir=split_root,
                model="gpt-5.6-luna",
                reasoning_effort="low",
                timeout_seconds=5,
            )
        )
        private_dir = split_root / ".codex-private"
        private_names = ("codex.executable-identity.json", "codex.command.json")
        private_statuses = {}
        encoded_statuses = {}
        root_statuses = {}
        for name in private_names:
            private_relative = private_dir.joinpath(name).relative_to(artifact_root).as_posix()
            root_relative = split_root.joinpath(name).relative_to(artifact_root).as_posix()
            private_statuses[name] = client.get("/artifacts/" + private_relative).status_code
            encoded_statuses[name] = client.get(
                "/artifacts/split-drafts/r34-static-route/%2Ecodex-private/" + name
            ).status_code
            root_statuses[name] = client.get("/artifacts/" + root_relative).status_code
        print("STATIC_RESULT:" + json.dumps({
            "exit_code": execution.exit_code,
            "receipt_relative": execution.executable_identity_path.relative_to(artifact_root).as_posix(),
            "command_relative": execution.command_path.relative_to(artifact_root).as_posix(),
            "private_statuses": private_statuses,
            "encoded_statuses": encoded_statuses,
            "root_statuses": root_statuses,
            "root_files_exist": {
                name: split_root.joinpath(name).exists() for name in private_names
            },
        }))
        """,
    )

    assert result["exit_code"] == 0
    assert result["receipt_relative"] == (
        "split-drafts/r34-static-route/.codex-private/codex.executable-identity.json"
    )
    assert result["command_relative"] == (
        "split-drafts/r34-static-route/.codex-private/codex.command.json"
    )
    assert result["private_statuses"] == {
        "codex.executable-identity.json": 404,
        "codex.command.json": 404,
    }
    assert result["encoded_statuses"] == result["private_statuses"]
    assert result["root_statuses"] == {
        "codex.executable-identity.json": 404,
        "codex.command.json": 404,
    }
    assert result["root_files_exist"] == {
        "codex.executable-identity.json": False,
        "codex.command.json": False,
    }


def test_public_image_doctor_exposes_complete_safe_source_runtime_identity(tmp_path: Path) -> None:
    result = _run_public_case(
        tmp_path,
        """
        response = client.get(
            "/api/runtime-identity?product=caller-override&secret=do-not-return"
        )
        print("PUBLIC_RESULT:" + json.dumps({
            "status": response.status_code,
            "identity": response.get_json(),
        }))
        """,
    )

    assert result["status"] == 200
    assert result["identity"] == {
        "artifacts_root": "image-pptgen/state/data/artifacts",
        "base_url": "http://127.0.0.1:3130",
        "build_id": "source-dev-public-image-3-0",
        "data_root": "image-pptgen/state/data",
        "instance_id": "source-dev-public-image-3-0",
        "product": "image-pptgen",
        "service": "image-pptgen-server",
        "skill_sha256": "source-dev-public-image-3-0",
        "source_commit": "source-dev-public-image-3-0",
        "surface": "public_image_3_0",
        "runtime_content_sha256": "source-dev-public-image-3-0",
        "version": "source-dev",
    }


def _public_image_3_0_pipeline_case(*, failure_mode: str = "none") -> str:
    """Exercise the public Generate boundary through the real four-page route.

    The provider seams are replaced inside the disposable subprocess.  The
    public endpoint still creates the batch/run/run-slides; the pipeline is
    then invoked with that run id, which keeps this characterization focused
    on the public call chain rather than duplicating generation setup.
    """

    return textwrap.dedent(
        """
        import hashlib
        import json
        from pathlib import Path

        import pipeline
        from backend.services import codex_native_image, public_image_surface
        from tests.test_image_3_0_cover_repair import (
            PALETTE_XML,
            SEED_PNG_BYTES,
            SEED_STYLE_DNA,
            _native_result,
        )

        failure_mode = __FAILURE_MODE__
        deck_id = dbmod.create_deck(
            "Public Image 3.0 Journey",
            "# Cover\\n# Position 2 Seed\\n# Position 3\\n# Position 4",
        )
        slide_ids = {
            "cover": dbmod.create_slide(
                deck_id, 1, "Cover", "Cover content", split_mode="manual"
            ),
            "seed": dbmod.create_slide(
                deck_id, 2, "Position 2 Seed", "Seed content", split_mode="manual"
            ),
            "later_3": dbmod.create_slide(
                deck_id, 3, "Position 3", "Later content 3", split_mode="manual"
            ),
            "later_4": dbmod.create_slide(
                deck_id, 4, "Position 4", "Later content 4", split_mode="manual"
            ),
        }
        configs = public_image_surface.public_configs()
        config = next(
            item for item in configs if item["name"] == "Codex Native Image 3.0"
        )

        launch_ledger = []
        events = []
        director_calls = []
        renderer_calls = []
        palette_calls = []

        def capture_launcher(run_ids, db_path, max_concurrent_runs):
            launch_ledger.append(
                {
                    "run_ids": list(run_ids),
                    "db_path": db_path,
                    "max_concurrent_runs": max_concurrent_runs,
                }
            )

        public_server.server.launch_batch_runs = capture_launcher

        def forbidden_provider(*_args, **_kwargs):
            raise AssertionError("public characterization reached an external provider")

        pipeline.call_llm_with_metadata = forbidden_provider
        pipeline.call_gemini_native = forbidden_provider
        pipeline.generate_image_generator = forbidden_provider

        run_slide_positions = {}

        def fake_director(
            agent_config,
            prompt,
            *,
            run_id,
            run_slide_id,
            stage_id,
            timeout_seconds,
            reference_image_paths=None,
        ):
            position = run_slide_positions[run_slide_id]
            call = {
                "position": position,
                "stage_id": stage_id,
                "prompt": prompt,
                "reference_image_paths": list(reference_image_paths or []),
            }
            director_calls.append(call)
            events.append(["director", position])
            if position == 1 or stage_id == "cover-prompt-generation":
                return "## Image prompt\\nMinimal cover composition.", [
                    {"attempt": 1, "status": "result_received"}
                ]
            return (
                "<Visual>"
                f"<Deck_Style_DNA>{SEED_STYLE_DNA}</Deck_Style_DNA>"
                f"<Title>Position {position}</Title>"
                "<Quality_Checklist>remove during cleanup</Quality_Checklist>"
                "</Visual>",
                [{"attempt": 1, "status": "result_received"}],
            )

        def fake_renderer(
            agent_config,
            prompt,
            output_path,
            *,
            run_id,
            run_slide_id,
            stage_id,
            output_dir,
            timeout_seconds,
            reference_image_paths=None,
            metadata=None,
        ):
            position = run_slide_positions[run_slide_id]
            call = {
                "position": position,
                "stage_id": stage_id,
                "prompt": prompt,
                "output_path": str(output_path),
                "reference_image_paths": list(reference_image_paths or []),
                "metadata": dict(metadata or {}),
            }
            renderer_calls.append(call)
            events.append(["renderer", position])
            if failure_mode == "slide" and position == 3:
                raise RuntimeError("single-slide-renderer-failure")
            marker = "seed" if position == 2 else f"position-{position}"
            return _native_result(
                prompt=prompt,
                output_path=Path(output_path),
                run_id=run_id,
                run_slide_id=run_slide_id,
                stage_id=stage_id,
                marker=marker,
            )

        def fake_palette(
            agent_config,
            *,
            run_id,
            run_slide_id,
            seed_png_path,
            timeout_seconds,
        ):
            palette_calls.append(
                {
                    "agent_config": dict(agent_config),
                    "run_id": run_id,
                    "run_slide_id": run_slide_id,
                    "image_path": str(seed_png_path),
                    "timeout_seconds": timeout_seconds,
                }
            )
            events.append(["palette", 2])
            if failure_mode == "palette":
                raise RuntimeError("seed-palette-extraction-failed")
            return PALETTE_XML

        pipeline.run_native_image_three_zero_director = fake_director
        pipeline.run_native_image_three_zero_palette = fake_palette
        codex_native_image.generate_codex_native_image = fake_renderer

        response = client.post(
            "/api/generate",
            json={
                "deck_id": deck_id,
                "config_id": config["id"],
                "engine": "image",
                "strategy": "image_3_0",
                "requirement_ids": [],
                "color_ids": [],
            },
        )
        payload = response.get_json()
        run_id = payload["run_ids"][0]
        run_slide_positions = {
            slide["id"]: int(slide["position"])
            for slide in dbmod.list_run_slides(run_id)
        }
        pipeline.ARTIFACTS_DIR = str(public_server.server.ARTIFACTS_DIR)
        pipeline.run_pipeline_from_db(run_id, str(dbmod.DB_PATH))

        run = dbmod.get_run(run_id)
        slides = dbmod.list_run_slides(run_id)
        run_artifacts = json.loads(run["stage_artifacts"] or "{}")
        artifacts_by_position = {
            int(slide["position"]): json.loads(slide["stage_artifacts"] or "{}")
            for slide in slides
        }
        detail_response = client.get(f"/api/runs/{run_id}")
        detail = detail_response.get_json()
        db = dbmod.get_db()
        try:
            color_titles = [
                row["title"]
                for row in db.execute("SELECT title FROM colors ORDER BY id").fetchall()
            ]
        finally:
            db.close()

        seed_hash = hashlib.sha256(SEED_PNG_BYTES).hexdigest()
        palette_hash = hashlib.sha256(PALETTE_XML.encode("utf-8")).hexdigest()
        cover_director = next(
            call for call in director_calls if call["position"] == 1
        ) if any(call["position"] == 1 for call in director_calls) else None
        cover_renderer = next(
            call for call in renderer_calls if call["position"] == 1
        ) if any(call["position"] == 1 for call in renderer_calls) else None
        print(
            "PUBLIC_RESULT:"
            + json.dumps(
                {
                    "generate_status": response.status_code,
                    "generate_payload": payload,
                    "launch_ledger": launch_ledger,
                    "run_id": run_id,
                    "run_status": run["status"],
                    "slide_statuses": [slide["status"] for slide in slides],
                    "slide_errors": [slide["error_message"] for slide in slides],
                    "events": events,
                    "director_positions": [call["position"] for call in director_calls],
                    "renderer_positions": [call["position"] for call in renderer_calls],
                    "palette_call_count": len(palette_calls),
                    "cover_director_prompt": cover_director["prompt"] if cover_director else None,
                    "cover_renderer_prompt": cover_renderer["prompt"] if cover_renderer else None,
                    "cover_director_refs": (
                        cover_director["reference_image_paths"]
                        if cover_director
                        else None
                    ),
                    "cover_renderer_refs": (
                        cover_renderer["reference_image_paths"]
                        if cover_renderer
                        else None
                    ),
                    "lineage": run_artifacts.get("seed_palette_lineage"),
                    "detail_status": detail_response.status_code,
                    "detail_lineage": detail.get("stage_artifacts", {}).get("seed_palette_lineage"),
                    "reference_keys": {
                        position: sorted(
                            artifact.get("request_chain", {}).get("references", {}).keys()
                        )
                        for position, artifact in artifacts_by_position.items()
                    },
                    "seed_reference_evidence": {
                        position: artifact.get("request_chain", {})
                        .get("references", {})
                        .get("seed_png")
                        for position, artifact in artifacts_by_position.items()
                    },
                    "artifact_keys": {
                        position: sorted(artifact.keys())
                        for position, artifact in artifacts_by_position.items()
                    },
                    "seed_dependencies": {
                        int(slide["position"]): json.loads(slide["seed_dependency"] or "null")
                        for slide in slides
                    },
                    "xml_presence": {
                        int(slide["position"]): {
                            "raw": bool(slide["xml_raw"]),
                            "clean": bool(slide["xml_clean"]),
                        }
                        for slide in slides
                    },
                    "style_dna": {
                        position: SEED_STYLE_DNA in str(slide["xml_raw"] or "")
                        and SEED_STYLE_DNA in str(slide["xml_clean"] or "")
                        for position, slide in ((int(item["position"]), item) for item in slides)
                    },
                    "image_paths_exist": {
                        int(slide["position"]): bool(
                            slide["final_image_path"]
                            and Path(slide["final_image_path"]).is_file()
                        )
                        for slide in slides
                    },
                    "seed_image_bytes_match": bool(
                        slides[1]["final_image_path"]
                        and Path(slides[1]["final_image_path"]).read_bytes() == SEED_PNG_BYTES
                    ),
                    "color_titles": color_titles,
                    "lineage_hashes": [seed_hash, palette_hash],
                    "detail_keys": sorted(detail),
                },
                ensure_ascii=False,
            )
        )
        """.replace("__FAILURE_MODE__", repr(failure_mode))
    )


def test_public_generate_runs_four_page_image_3_0_pipeline_with_seed_palette_lineage(tmp_path):
    result = _run_public_case(tmp_path, _public_image_3_0_pipeline_case())

    assert result["generate_status"] == 202
    assert len(result["launch_ledger"]) == 1
    assert result["run_status"] == "completed"
    assert result["slide_statuses"] == ["completed"] * 4
    assert result["palette_call_count"] == 1
    events = [tuple(event) for event in result["events"]]
    assert events[0] == ("director", 2)
    seed_renderer = events.index(("renderer", 2))
    palette = events.index(("palette", 2))
    assert seed_renderer < palette
    for position in (1, 3, 4):
        assert palette < events.index(("director", position))
    assert set(result["director_positions"]) == {1, 2, 3, 4}
    assert set(result["renderer_positions"]) == {1, 2, 3, 4}

    lineage = result["lineage"]
    assert lineage["deck_position"] == 2
    assert lineage["extraction_stage"] == "seed_palette_extraction"
    assert lineage["seed_png_sha256"] == result["lineage_hashes"][0]
    assert lineage["palette_sha256"] == result["lineage_hashes"][1]
    assert lineage["colors"] == [
        "#111111",
        "#FFFFFF",
        "#002B36",
        "#FDF6E3",
        "#C94132",
        "#D4B258",
        "#21667B",
        "#E89999",
        "#E67E39",
        "#6B4E3E",
    ]
    assert result["detail_status"] == 200
    assert result["detail_lineage"] == lineage

    assert "seed_png" not in result["reference_keys"]["1"]
    for position in (3, 4):
        assert "seed_png" in result["reference_keys"][str(position)]
        assert result["seed_reference_evidence"][str(position)]["source_slide_id"] == 2
        assert result["seed_reference_evidence"][str(position)]["reference_type"] == "seed_png"
        assert result["seed_dependencies"][str(position)] == {
            "seed_slide_id": 2,
            "seed_slide_position": 2,
        }
        assert result["style_dna"][str(position)] is True
        assert result["xml_presence"][str(position)] == {"raw": True, "clean": True}
    assert result["style_dna"]["2"] is True
    assert result["xml_presence"]["2"] == {"raw": True, "clean": True}
    assert result["image_paths_exist"] == {"1": True, "2": True, "3": True, "4": True}
    assert result["seed_image_bytes_match"] is True
    assert result["color_titles"] == ["System Empty Color"]

    for prompt in (result["cover_director_prompt"], result["cover_renderer_prompt"]):
        assert prompt is not None
        assert result["lineage_hashes"][0] in prompt
        assert result["lineage_hashes"][1] in prompt
    assert result["cover_director_refs"] == []
    assert result["cover_renderer_refs"] == []


def test_public_generate_exposes_honest_palette_failure_after_seed(tmp_path):
    result = _run_public_case(
        tmp_path, _public_image_3_0_pipeline_case(failure_mode="palette")
    )

    assert result["generate_status"] == 202
    assert result["run_status"] == "completed_with_failures"
    assert result["slide_statuses"] == ["failed", "completed", "failed", "failed"]
    assert result["palette_call_count"] == 1
    assert [tuple(event) for event in result["events"]] == [
        ("director", 2),
        ("renderer", 2),
        ("palette", 2),
    ]
    assert result["image_paths_exist"] == {"1": False, "2": True, "3": False, "4": False}
    assert result["seed_image_bytes_match"] is True
    assert all(
        "seed_palette_extraction_failed" in str(error)
        for position, error in enumerate(result["slide_errors"], start=1)
        if position != 2
    )
    assert result["lineage"] is None
    assert result["detail_status"] == 200
    assert result["detail_lineage"] is None
    assert result["color_titles"] == ["System Empty Color"]


def test_public_generate_exposes_honest_single_slide_failure_after_palette(tmp_path):
    result = _run_public_case(
        tmp_path, _public_image_3_0_pipeline_case(failure_mode="slide")
    )

    assert result["generate_status"] == 202
    assert result["run_status"] == "completed_with_failures"
    assert result["slide_statuses"] == ["completed", "completed", "failed", "completed"]
    assert result["palette_call_count"] == 1
    events = [tuple(event) for event in result["events"]]
    assert events[0] == ("director", 2)
    assert events.index(("renderer", 2)) < events.index(("palette", 2))
    assert events.index(("palette", 2)) < events.index(("renderer", 3))
    assert result["image_paths_exist"] == {"1": True, "2": True, "3": False, "4": True}
    assert result["seed_image_bytes_match"] is True
    assert "native_image_generation_failed" in str(result["slide_errors"][2])
    assert result["lineage"] is not None
    assert result["detail_status"] == 200
    assert result["detail_lineage"] == result["lineage"]
    assert result["color_titles"] == ["System Empty Color"]


def test_public_image_skill_split_order_flows_through_generation_detail_and_zip(tmp_path):
    result = _run_public_case(
        tmp_path,
        """
        import hashlib
        import io
        import zipfile

        import pipeline
        from backend.services import codex_native_image, deck_split_drafts, public_image_surface
        from tests.test_image_3_0_cover_repair import (
            PALETTE_XML,
            SEED_STYLE_DNA,
            _native_result,
        )

        source = (
            "# Image Skill Order Journey\\n\\n"
            "## Alpha\\n\\nAlpha source body with number 42.\\n\\n"
            "## Beta\\n\\nBeta source body with number 7.\\n\\n"
            "## Gamma\\n\\nGamma source body with number 9."
        )
        content_slides = [
            {"title": "Alpha", "content": "Alpha source body with number 42."},
            {"title": "Beta", "content": "Beta source body with number 7."},
            {"title": "Gamma", "content": "Gamma source body with number 9."},
        ]
        deck_id = dbmod.create_deck("Image Skill Order Journey", source)
        deck_split_drafts.generate_llm_split = lambda *_args, **_kwargs: content_slides
        proposal = client.post(f"/api/decks/{deck_id}/split-drafts", json={})
        proposal_body = proposal.get_json()
        confirmation = client.post(
            f"/api/deck-split-drafts/{proposal_body['id']}/confirm", json={}
        )
        confirmed_slides = dbmod.list_slides(deck_id)

        configs = public_image_surface.public_configs()
        config = next(
            item
            for item in configs
            if item["name"] == "Codex Native Image 3.0 Luna Low Director"
        )
        assert config["director"] == {"model": "gpt-5.6-luna", "reasoning_effort": "low"}
        launch_ledger = []

        def capture_launcher(run_ids, db_path, max_concurrent_runs):
            launch_ledger.append({
                "run_ids": list(run_ids),
                "db_path": db_path,
                "max_concurrent_runs": max_concurrent_runs,
            })

        public_server.server.launch_batch_runs = capture_launcher

        def forbidden_provider(*_args, **_kwargs):
            raise AssertionError("public ordering characterization reached an external provider")

        pipeline.call_llm_with_metadata = forbidden_provider
        pipeline.call_gemini_native = forbidden_provider
        pipeline.generate_image_generator = forbidden_provider

        run_slide_positions = {}
        director_positions = []
        renderer_positions = []
        palette_positions = []

        def fake_director(
            agent_config,
            prompt,
            *,
            run_id,
            run_slide_id,
            stage_id,
            timeout_seconds,
            reference_image_paths=None,
        ):
            position = run_slide_positions[run_slide_id]
            director_positions.append(position)
            if position == 1:
                return "## Image prompt\\nMinimal cover composition.", [
                    {"attempt": 1, "status": "result_received"}
                ]
            return (
                "<Visual>"
                f"<Deck_Style_DNA>{SEED_STYLE_DNA}</Deck_Style_DNA>"
                f"<Title>Position {position}</Title>"
                "<Quality_Checklist>remove during cleanup</Quality_Checklist>"
                "</Visual>",
                [{"attempt": 1, "status": "result_received"}],
            )

        def fake_renderer(
            agent_config,
            prompt,
            output_path,
            *,
            run_id,
            run_slide_id,
            stage_id,
            output_dir,
            timeout_seconds,
            reference_image_paths=None,
            metadata=None,
        ):
            position = run_slide_positions[run_slide_id]
            renderer_positions.append(position)
            marker = "seed" if position == 2 else f"position-{position}"
            return _native_result(
                prompt=prompt,
                output_path=Path(output_path),
                run_id=run_id,
                run_slide_id=run_slide_id,
                stage_id=stage_id,
                marker=marker,
            )

        def fake_palette(
            agent_config,
            *,
            run_id,
            run_slide_id,
            seed_png_path,
            timeout_seconds,
        ):
            palette_positions.append(run_slide_positions[run_slide_id])
            return PALETTE_XML

        pipeline.run_native_image_three_zero_director = fake_director
        pipeline.run_native_image_three_zero_palette = fake_palette
        codex_native_image.generate_codex_native_image = fake_renderer

        generation = client.post(
            "/api/generate",
            json={
                "deck_id": deck_id,
                "config_id": config["id"],
                "engine": "image",
                "strategy": "image_3_0",
                "requirement_ids": [],
                "color_ids": [],
            },
        )
        run_id = generation.get_json()["run_ids"][0]
        run_slide_positions = {
            int(slide["id"]): int(slide["position"])
            for slide in dbmod.list_run_slides(run_id)
        }
        pipeline.ARTIFACTS_DIR = str(public_server.server.ARTIFACTS_DIR)
        pipeline.run_pipeline_from_db(run_id, str(dbmod.DB_PATH))
        db = dbmod.get_db()
        db.execute(
            "UPDATE batches SET status = 'completed' WHERE id = ?",
            (int(dbmod.get_run(run_id)["batch_id"]),),
        )
        db.commit()
        db.close()

        run = dbmod.get_run(run_id)
        run_slides = dbmod.list_run_slides(run_id)
        detail_response = client.get(f"/api/runs/{run_id}")
        detail = detail_response.get_json()

        def inspect_zip(response):
            if response.status_code != 200:
                return [f"STATUS:{response.status_code}"], response.get_json() or {}, {}
            with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
                names = sorted(archive.namelist())
                manifest = json.loads(archive.read("manifest.json"))
                payload_hashes = {
                    name: hashlib.sha256(archive.read(name)).hexdigest()
                    for name in names
                    if name != "manifest.json"
                }
            return names, manifest, payload_hashes

        run_download = client.get(f"/api/runs/{run_id}/download")
        batch_id = int(run["batch_id"])
        batch_download = client.get(f"/api/batches/{batch_id}/download")
        run_names, run_manifest, run_hashes = inspect_zip(run_download)
        batch_names, batch_manifest, batch_hashes = inspect_zip(batch_download)
        print("PUBLIC_RESULT:" + json.dumps({
            "proposal_status": proposal.status_code,
            "proposal_page_count": proposal_body["page_count"],
            "confirmation_status": confirmation.status_code,
            "confirmation": confirmation.get_json(),
            "deck_positions": [int(slide["position"]) for slide in confirmed_slides],
            "deck_types": [slide["split_mode"] for slide in confirmed_slides],
            "generate_status": generation.status_code,
            "launch_ledger": launch_ledger,
            "run_status": run["status"],
            "run_slide_positions": [int(slide["position"]) for slide in run_slides],
            "run_slide_types": [slide["slide_type"] for slide in run_slides],
            "director_positions": director_positions,
            "renderer_positions": renderer_positions,
            "palette_positions": palette_positions,
            "detail_status": detail_response.status_code,
            "detail_positions": [int(slide["position"]) for slide in detail["slides"]],
            "detail_titles": [slide["slide_title"] for slide in detail["slides"]],
            "detail_paths": [slide["final_image_path"] for slide in detail["slides"]],
            "run_names": run_names,
            "run_download_status": run_download.status_code,
            "run_included": run_manifest.get("included", []),
            "run_hashes": run_hashes,
            "batch_names": batch_names,
            "batch_download_status": batch_download.status_code,
            "batch_included": batch_manifest.get("included", []),
            "batch_hashes": batch_hashes,
        }, ensure_ascii=False))
        """,
    )

    expected_positions = [1, 2, 3, 4]
    expected_run_paths = [f"slides/slide-{position:02d}.png" for position in expected_positions]
    expected_batch_paths = [
        f"runs/run-1/slides/slide-{position:02d}.png" for position in expected_positions
    ]
    assert result["proposal_status"] == 201
    assert result["proposal_page_count"] == 3
    assert result["confirmation_status"] == 200
    assert result["confirmation"]["slide_count"] == 4
    assert result["deck_positions"] == expected_positions
    assert result["deck_types"] == ["image_skill_cover", "llm_auto", "llm_auto", "llm_auto"]
    assert result["generate_status"] == 202
    assert result["launch_ledger"] and result["launch_ledger"][0]["run_ids"] == [1]
    assert result["run_status"] == "completed"
    assert result["run_slide_positions"] == expected_positions
    assert result["run_slide_types"] == ["cover", "content", "content", "content"]
    assert set(result["director_positions"]) == set(expected_positions)
    assert set(result["renderer_positions"]) == set(expected_positions)
    assert result["palette_positions"] == [2]
    assert result["detail_status"] == 200
    assert result["detail_positions"] == expected_positions
    assert result["detail_titles"] == [
        "Image Skill Order Journey",
        "Alpha",
        "Beta",
        "Gamma",
    ]
    assert all(path and path.startswith("/artifacts/") for path in result["detail_paths"])
    assert result["run_download_status"] == 200
    assert result["batch_download_status"] == 200
    assert result["run_names"] == ["manifest.json", *expected_run_paths]
    assert result["batch_names"] == ["manifest.json", *expected_batch_paths]
    assert [item["position"] for item in result["run_included"]] == expected_positions
    assert [item["zip_path"] for item in result["run_included"]] == expected_run_paths
    assert [item["position"] for item in result["batch_included"]] == expected_positions
    assert [item["zip_path"] for item in result["batch_included"]] == expected_batch_paths
    assert [item["sha256"] for item in result["run_included"]] == [
        result["run_hashes"][path] for path in expected_run_paths
    ]
    assert [item["sha256"] for item in result["batch_included"]] == [
        result["batch_hashes"][path] for path in expected_batch_paths
    ]


def test_public_surface_uses_isolated_roots_and_returns_only_safe_native_configs(tmp_path):
    result = _run_public_case(
        tmp_path,
        """
        list_response = client.get("/api/configs")
        configs = list_response.get_json()
        detail_responses = [client.get(f"/api/configs/{config['id']}") for config in configs]
        print("PUBLIC_RESULT:" + json.dumps({
            "list_status": list_response.status_code,
            "configs": configs,
            "detail_statuses": [response.status_code for response in detail_responses],
            "details": [response.get_json() for response in detail_responses],
            "db_path": str(dbmod.DB_PATH),
            "artifact_path": str(public_server.server.ARTIFACTS_DIR),
        }))
        """,
    )

    assert result["list_status"] == 200
    assert result["db_path"] == str(tmp_path / "public.db")
    assert result["artifact_path"] == str(tmp_path / "artifacts")
    assert {config["name"] for config in result["configs"]} == {
        "Codex Native Image 3.0",
        "Codex Native Image 3.0 Luna Low Director",
    }
    assert result["detail_statuses"] == [200, 200]
    expected_keys = {
        "id",
        "name",
        "type",
        "route",
        "timeout_minutes",
        "max_concurrent_runs",
        "director",
        "renderer",
        "palette",
    }
    for config in [*result["configs"], *result["details"]]:
        assert set(config) == expected_keys
        assert config["type"] == "image"
        assert config["route"] == "image_3_0"
        assert config["max_concurrent_runs"] == 6
        assert config["renderer"] == {"model": "gpt-5.6-luna", "reasoning_effort": "low"}
        assert config["palette"] == config["director"]
        assert "api_key" not in json.dumps(config)
        assert "endpoint" not in json.dumps(config)
        assert "profile_id" not in json.dumps(config)

    by_name = {config["name"]: config for config in result["configs"]}
    assert by_name["Codex Native Image 3.0"]["director"] == {
        "model": "gpt-5.6-sol",
        "reasoning_effort": "low",
    }
    assert by_name["Codex Native Image 3.0 Luna Low Director"]["director"] == {
        "model": "gpt-5.6-luna",
        "reasoning_effort": "low",
    }


def test_public_audit_jsonl_keeps_only_safe_canonical_session_digest():
    from backend.services import public_image_surface

    digest = "a" * 64
    safe = public_image_surface._sanitize(
        {
            "jsonl": {
                "raw": {
                    "sha256": digest,
                    "path": "/private/raw.jsonl",
                    "unknown": "must-drop",
                },
                "observed": {
                    "sha256": digest,
                    "source_path": "/home/private/observed.jsonl",
                    "raw_content": "must-drop",
                },
                "canonical_session": {
                    "bytes": 42,
                    "sha256": digest,
                    "source_path": "/home/private/session.jsonl",
                    "archive_path": "/private/archive.jsonl",
                    "session_id": "must-drop",
                    "raw_content": "must-drop",
                    "nested": {"bytes": 999, "path": "/private/nested"},
                },
                "unknown": "must-drop",
            },
            "canonical_session": {
                "bytes": 999,
                "sha256": digest,
                "path": "/private/top-level.jsonl",
            },
        }
    )

    assert safe == {
        "jsonl": {
            "raw": {"sha256": digest},
            "observed": {"sha256": digest},
            "canonical_session": {"bytes": 42, "sha256": digest},
        }
    }


def test_public_audit_detail_route_preserves_run_owned_semantics_without_private_material(tmp_path):
    result = _run_public_case(
        tmp_path,
        """
        from backend.services import codex_audit, public_image_surface

        digest = "b" * 64
        public_image_surface._run_id_is_public = lambda run_id: run_id == 1
        dbmod.get_run = lambda run_id: {"id": run_id}
        codex_audit.get_native_codex_audit_detail = lambda **kwargs: {
            "run_id": kwargs["run_id"],
            "invocation_id": kwargs["invocation_id"],
            "lineage": {
                "run_id": kwargs["run_id"],
                "run_slide_id": 3,
                "stage_id": "image-generation",
                "attempt": 1,
                "invocation_id": kwargs["invocation_id"],
                "session": {
                    "bytes": 42,
                    "sha256": digest,
                    "path": "/private/session.jsonl",
                },
                "call": {"id": "image-call-1", "arguments_sha256": digest},
            },
            "prompt": "Business prompt/input remains readable",
            "assistant_output": "Assistant image blueprint remains readable",
            "tool_calls": [
                {
                    "event_sequence": 2,
                    "kind": "command_execution",
                    "name": None,
                    "call_id": None,
                    "payload": {
                        "id": "tool-1",
                        "type": "command_execution",
                        "command": "/private/should-not-leak",
                    },
                }
            ],
            "imagegen_calls": [
                {
                    "event_sequence": 3,
                    "kind": "image_generation_end",
                    "name": "imagegen",
                    "call_id": "image-call-1",
                    "payload": {
                        "type": "image_generation_end",
                        "status": "completed",
                        "call_id": "image-call-1",
                        "revised_prompt": "Image business output",
                        "path": "/private/output.png",
                    },
                }
            ],
            "errors": {
                "invocation_error": None,
                "metadata_error": None,
                "event_errors": [],
            },
            "jsonl": {
                "raw": {
                    "sha256": digest,
                    "path": "/private/raw.jsonl",
                    "unknown": "must-drop",
                },
                "observed": {
                    "sha256": digest,
                    "source_path": "/home/private/observed.jsonl",
                    "raw_content": "must-drop",
                },
                "canonical_session": {
                    "bytes": 42,
                    "sha256": digest,
                    "source_path": "/home/private/session.jsonl",
                    "archive_path": "/private/archive.jsonl",
                    "session_id": "must-drop",
                    "raw_content": "must-drop",
                    "nested": {"bytes": 999, "path": "/private/nested"},
                },
                "unknown": "must-drop",
            },
            "canonical_session": {
                "bytes": 999,
                "sha256": digest,
                "path": "/private/top-level.jsonl",
            },
            "metadata": {
                "requested_model": "gpt-5.6-sol",
                "actual_model": "gpt-5.6-sol",
                "fallback_used": False,
                "api_key": "must-drop",
                "business_image": {
                    "path": "/private/output.png",
                    "sha256": digest,
                },
            },
        }
        response = client.get("/api/runs/1/codex-audit/invocations/2")
        print("PUBLIC_RESULT:" + json.dumps({
            "status": response.status_code,
            "payload": response.get_json(),
        }))
        """,
    )

    assert result["status"] == 200
    payload = result["payload"]
    assert payload["run_id"] == 1
    assert payload["invocation_id"] == 2
    assert payload["prompt"] == "Business prompt/input remains readable"
    assert payload["assistant_output"] == "Assistant image blueprint remains readable"
    assert payload["tool_calls"] == [
        {
            "event_sequence": 2,
            "kind": "command_execution",
            "name": None,
            "call_id": None,
            "payload": {"id": "tool-1", "type": "command_execution"},
        }
    ]
    assert payload["imagegen_calls"] == [
        {
            "event_sequence": 3,
            "kind": "image_generation_end",
            "name": "imagegen",
            "call_id": "image-call-1",
            "payload": {
                "type": "image_generation_end",
                "status": "completed",
                "call_id": "image-call-1",
                "revised_prompt": "Image business output",
            },
        }
    ]
    assert payload["lineage"] == {
        "run_id": 1,
        "run_slide_id": 3,
        "stage_id": "image-generation",
        "attempt": 1,
        "invocation_id": 2,
        "session": {"bytes": 42, "sha256": "b" * 64},
        "call": {"id": "image-call-1", "arguments_sha256": "b" * 64},
    }
    assert payload["jsonl"] == {
        "raw": {"sha256": "b" * 64},
        "observed": {"sha256": "b" * 64},
        "canonical_session": {"bytes": 42, "sha256": "b" * 64},
    }
    serialized = json.dumps(payload)
    for private_value in (
        "/private/",
        "must-drop",
        "api_key",
        "should-not-leak",
    ):
        assert private_value not in serialized


def test_public_surface_fails_the_config_pair_closed_when_one_binding_drifts(tmp_path):
    result = _run_public_case(
        tmp_path,
        """
        sol = dbmod.get_config_by_name(model_profiles.NATIVE_IMAGE_3_0_CONFIG_NAME)
        luna = dbmod.get_config_by_name(model_profiles.NATIVE_IMAGE_3_0_LUNA_DIRECTOR_CONFIG_NAME)
        bindings = json.loads(sol["route_model_bindings"])
        db = dbmod.get_db()
        db.execute(
            "UPDATE model_profiles SET model = 'drifted-model' WHERE id = ?",
            (bindings["image_designer"]["profile_id"],),
        )
        db.commit()
        db.close()
        listed = client.get("/api/configs")
        sol_detail = client.get(f"/api/configs/{sol['id']}")
        luna_detail = client.get(f"/api/configs/{luna['id']}")
        print("PUBLIC_RESULT:" + json.dumps({
            "listed": listed.get_json(),
            "details": [sol_detail.status_code, luna_detail.status_code],
        }))
        """,
    )

    assert result == {"listed": [], "details": [404, 404]}


def test_public_surface_fails_closed_for_removed_and_admin_apis(tmp_path):
    result = _run_public_case(
        tmp_path,
        """
        before = len(dbmod.list_configs())
        cases = [
            ("get", "/api/requirements", None),
            ("get", "/api/colors", None),
            ("get", "/api/prompts", None),
            ("get", "/api/model-profiles", None),
            ("get", "/api/system-settings", None),
            ("get", "/api/evaluations", None),
            ("get", "/api/runfail/stats", None),
            ("get", "/api/codex-sessions/not-public/summary", None),
            ("get", "/api/auto-split-settings", None),
            ("post", "/api/decks/1/split-drafts", {}),
            ("post", "/api/configs", {"name": "must-not-exist"}),
            ("post", "/api/generation-actions", {"action": "retry"}),
        ]
        responses = []
        for method, path, payload in cases:
            response = getattr(client, method)(path, json=payload)
            responses.append({"path": path, "status": response.status_code, "body": response.get_json()})
        print("PUBLIC_RESULT:" + json.dumps({
            "responses": responses,
            "config_count_unchanged": len(dbmod.list_configs()) == before,
        }))
        """,
    )

    assert result["config_count_unchanged"] is True
    assert all(item["status"] == 404 for item in result["responses"])
    assert {json.dumps(item["body"], sort_keys=True) for item in result["responses"]} == {
        '{"error": "Not found"}'
    }


def test_public_image_split_propose_and_revision_are_fixed_faithful_and_safe(tmp_path):
    result = _run_public_case(
        tmp_path,
        """
        from backend.services import deck_split_drafts

        source = (
            "# Source title\\n\\n"
            "## 第一章\\n\\n"
            "第一章保留普通词语 Alpha，并保留编号 42。\\n\\n"
            "## 第二章\\n\\n"
            "第二章保留普通词语 Beta，并保留编号 7。"
        )
        deck_id = dbmod.create_deck("Public split", source)
        config_calls = []
        revision_calls = []

        def faithful_split(deck_content, config, prompt):
            config_calls.append(dict(config))
            assert deck_content == source
            return [
                {"title": "第一章", "content": "第一章保留普通词语 Alpha，并保留编号 42。"},
                {"title": "第二章", "content": "第二章保留普通词语 Beta，并保留编号 7。"},
            ]

        def faithful_revision(deck_content, current_slides, instruction, config, *, content_mode):
            revision_calls.append({"config": dict(config), "content_mode": content_mode, "instruction": instruction})
            assert deck_content == source
            assert current_slides[0]["content"].endswith("编号 42。")
            return [
                {"title": "第一章（调整）", "content": "第一章保留普通词语 Alpha，并保留编号 42。"},
                {"title": "第二章（调整）", "content": "第二章保留普通词语 Beta，并保留编号 7。"},
            ]

        deck_split_drafts.generate_llm_split = faithful_split
        deck_split_drafts.generate_split_revision = faithful_revision

        proposal = client.post(f"/api/decks/{deck_id}/split-drafts", json={})
        proposal_body = proposal.get_json()
        revision = client.post(
            f"/api/deck-split-drafts/{proposal_body['id']}/revise",
            json={"instruction": "把标题写得更清楚"},
        )
        db = dbmod.get_db()
        try:
            draft_count = db.execute("SELECT COUNT(*) AS n FROM deck_split_drafts").fetchone()["n"]
        finally:
            db.close()
        print("PUBLIC_RESULT:" + json.dumps({
            "proposal_status": proposal.status_code,
            "proposal": proposal_body,
            "revision_status": revision.status_code,
            "revision": revision.get_json(),
            "config_calls": config_calls,
            "revision_calls": revision_calls,
            "draft_count": draft_count,
        }, ensure_ascii=False))
        """,
    )

    assert result["proposal_status"] == 201
    assert result["revision_status"] == 200
    safe_keys = {
        "id",
        "deck_id",
        "status",
        "model",
        "attempt_count",
        "content_mode",
        "page_count",
        "slides",
    }
    assert set(result["proposal"]) == safe_keys
    assert set(result["revision"]) == safe_keys
    assert result["proposal"]["content_mode"] == result["revision"]["content_mode"] == "faithful"
    assert result["proposal"]["model"] == result["revision"]["model"] == "gpt-5.6-luna"
    assert result["proposal"]["attempt_count"] == 1
    assert result["revision"]["attempt_count"] == 2
    assert result["proposal"]["page_count"] == result["revision"]["page_count"] == 2
    assert all(set(slide) == {"title", "content"} for slide in result["proposal"]["slides"])
    assert all(set(slide) == {"title", "content"} for slide in result["revision"]["slides"])
    assert result["revision"]["id"] == result["proposal"]["id"]
    assert result["revision"]["deck_id"] == result["proposal"]["deck_id"]
    assert result["draft_count"] == 1
    assert {call["model"] for call in result["config_calls"]} == {"gpt-5.6-luna"}
    assert {call["thinking"] for call in result["config_calls"]} == {"low"}
    assert {call["content_mode"] for call in result["config_calls"]} == {"faithful"}
    assert {call["model"] for call in (item["config"] for item in result["revision_calls"])} == {"gpt-5.6-luna"}
    assert {item["content_mode"] for item in result["revision_calls"]} == {"faithful"}


def test_public_image_target_page_revision_is_same_draft_and_model_free(tmp_path):
    result = _run_public_case(
        tmp_path,
        """
        from backend.services import deck_split_drafts

        source = (
            "# Source title\\n\\n"
            "## 第一章\\n\\n"
            "第一章短内容 Alpha。\\n\\n"
            "## 第二章\\n\\n"
            "第二章较长内容 Beta one two three four。\\n\\n"
            "## 第三章\\n\\n"
            "第三章内容 Gamma。\\n\\n"
            "## 第四章\\n\\n"
            "第四章内容 Delta。"
        )
        model_calls = []
        deck_id = dbmod.create_deck("Public target page", source)

        def faithful_split(deck_content, config, prompt):
            del config, prompt
            model_calls.append("propose")
            assert deck_content == source
            return [
                {"title": "第一章", "content": "第一章短内容 Alpha。"},
                {"title": "第二章", "content": "第二章较长内容 Beta one two three four。"},
                {"title": "第三章", "content": "第三章内容 Gamma。"},
                {"title": "第四章", "content": "第四章内容 Delta。"},
            ]

        deck_split_drafts.generate_llm_split = faithful_split
        deck_split_drafts.generate_split_revision = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("target page revision must not invoke a model")
        )
        proposal = client.post(f"/api/decks/{deck_id}/split-drafts", json={})
        draft = proposal.get_json()
        db = dbmod.get_db()
        before = db.execute(
            "SELECT id, slides_json, attempt_count FROM deck_split_drafts WHERE id = ?",
            (draft["id"],),
        ).fetchone()
        db.close()

        revised = client.post(
            f"/api/deck-split-drafts/{draft['id']}/revise",
            json={"target_page_count": 2},
        )
        revised_body = revised.get_json()
        db = dbmod.get_db()
        after = db.execute(
            "SELECT id, slides_json, attempt_count FROM deck_split_drafts WHERE id = ?",
            (draft["id"],),
        ).fetchone()
        db.close()

        cas_proposal = client.post(f"/api/decks/{deck_id}/split-drafts", json={})
        cas_draft = cas_proposal.get_json()
        db = dbmod.get_db()
        cas_before = db.execute(
            "SELECT id, slides_json, attempt_count FROM deck_split_drafts WHERE id = ?",
            (cas_draft["id"],),
        ).fetchone()
        db.close()
        original_target_builder = deck_split_drafts._target_page_count_slides
        concurrent_slides_json = json.dumps(
            [{"title": "Concurrent", "content": "Concurrent update"}],
            ensure_ascii=False,
        )

        def concurrent_target_builder(deck_content, target_page_count, **kwargs):
            db = dbmod.get_db()
            db.execute(
                "UPDATE deck_split_drafts SET slides_json = ? WHERE id = ?",
                (concurrent_slides_json, cas_draft["id"]),
            )
            db.commit()
            db.close()
            return original_target_builder(deck_content, target_page_count, **kwargs)

        deck_split_drafts._target_page_count_slides = concurrent_target_builder
        cas_response = client.post(
            f"/api/deck-split-drafts/{cas_draft['id']}/revise",
            json={"target_page_count": 2},
        )
        deck_split_drafts._target_page_count_slides = original_target_builder
        db = dbmod.get_db()
        cas_after = db.execute(
            "SELECT id, slides_json, attempt_count FROM deck_split_drafts WHERE id = ?",
            (cas_draft["id"],),
        ).fetchone()
        db.close()

        unavailable = client.post(
            f"/api/deck-split-drafts/{draft['id']}/revise",
            json={"target_page_count": 99},
        )
        db = dbmod.get_db()
        after_unavailable = db.execute(
            "SELECT id, slides_json, attempt_count FROM deck_split_drafts WHERE id = ?",
            (draft["id"],),
        ).fetchone()
        db.close()
        both = client.post(
            f"/api/deck-split-drafts/{draft['id']}/revise",
            json={"instruction": "调整标题", "target_page_count": 2},
        )
        print("PUBLIC_RESULT:" + json.dumps({
            "proposal_status": proposal.status_code,
            "revision_status": revised.status_code,
            "revision": revised_body,
            "unavailable_status": unavailable.status_code,
            "unavailable": unavailable.get_json(),
            "both_status": both.status_code,
            "model_calls": model_calls,
            "before": dict(before),
            "after": dict(after),
            "after_unavailable": dict(after_unavailable),
            "cas_status": cas_response.status_code,
            "cas_before": dict(cas_before),
            "cas_after": dict(cas_after),
            "concurrent_slides_json": concurrent_slides_json,
        }, ensure_ascii=False))
        """,
    )

    assert result["proposal_status"] == 201
    assert result["revision_status"] == 200
    assert result["revision"]["id"] == result["before"]["id"]
    assert result["revision"]["page_count"] == 2
    assert result["revision"]["attempt_count"] == result["before"]["attempt_count"] == 1
    assert result["after"]["id"] == result["before"]["id"]
    assert result["after"]["attempt_count"] == result["before"]["attempt_count"]
    assert result["unavailable_status"] == 422
    assert result["unavailable"]["error"] == "target_page_count_unavailable"
    assert result["after_unavailable"] == result["after"]
    assert result["both_status"] == 404
    assert result["cas_status"] == 409
    assert result["cas_after"]["slides_json"] == result["concurrent_slides_json"]
    assert result["cas_after"]["attempt_count"] == result["cas_before"]["attempt_count"]
    assert result["model_calls"] == ["propose", "propose"]


def test_public_image_explicit_h1_target_page_revision_is_model_free_and_exact(tmp_path):
    result = _run_public_case(
        tmp_path,
        """
        from pathlib import Path
        from backend.services import deck_split_drafts
        from splitter import split_by_explicit_h1

        source = Path("tests/fixtures/explicit-h1-thirteen-pages.md").read_text(
            encoding="utf-8"
        )
        expected_titles = [f"第{index}页 显式一级标题" for index in range(1, 14)]
        model_calls = []
        deck_id = dbmod.create_deck("Public explicit H1 pages", source)

        def faithful_split(deck_content, config, prompt):
            del config, prompt
            model_calls.append("propose")
            assert deck_content == source
            sections = split_by_explicit_h1(deck_content) or []
            return [
                {"title": section["title"], "content": section["content"]}
                for section in sections
            ]

        deck_split_drafts.generate_llm_split = faithful_split
        deck_split_drafts.generate_split_revision = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("target page revision must not invoke a model")
        )
        proposal = client.post(f"/api/decks/{deck_id}/split-drafts", json={})
        draft = proposal.get_json()
        db = dbmod.get_db()
        before = db.execute(
            "SELECT id, status, slides_json, attempt_count FROM deck_split_drafts WHERE id = ?",
            (draft["id"],),
        ).fetchone()
        db.close()

        revised = client.post(
            f"/api/deck-split-drafts/{draft['id']}/revise",
            json={"target_page_count": 13},
        )
        revised_body = revised.get_json()
        db = dbmod.get_db()
        after = db.execute(
            "SELECT id, status, slides_json, attempt_count FROM deck_split_drafts WHERE id = ?",
            (draft["id"],),
        ).fetchone()
        db.close()

        unavailable = client.post(
            f"/api/deck-split-drafts/{draft['id']}/revise",
            json={"target_page_count": 14},
        )
        db = dbmod.get_db()
        after_unavailable = db.execute(
            "SELECT id, status, slides_json, attempt_count FROM deck_split_drafts WHERE id = ?",
            (draft["id"],),
        ).fetchone()
        db.close()
        print("PUBLIC_RESULT:" + json.dumps({
            "proposal_status": proposal.status_code,
            "revision_status": revised.status_code,
            "revision": revised_body,
            "unavailable_status": unavailable.status_code,
            "unavailable": unavailable.get_json(),
            "model_calls": model_calls,
            "before": dict(before),
            "after": dict(after),
            "after_unavailable": dict(after_unavailable),
            "expected_titles": expected_titles,
        }, ensure_ascii=False))
        """,
    )

    assert result["proposal_status"] == 201
    assert result["revision_status"] == 200
    assert result["revision"]["id"] == result["before"]["id"]
    assert result["revision"]["deck_id"]
    assert result["revision"]["status"] == "pending"
    assert result["revision"]["page_count"] == 13
    assert result["revision"]["attempt_count"] == result["before"]["attempt_count"] == 1
    assert result["after"]["id"] == result["before"]["id"]
    assert result["after"]["status"] == "pending"
    assert result["after"]["attempt_count"] == result["before"]["attempt_count"]
    assert [slide["title"] for slide in result["revision"]["slides"]] == result["expected_titles"]
    for index, slide in enumerate(result["revision"]["slides"], start=1):
        assert f"ALPHA-{index}" in slide["content"]
        assert f"编号 {100 + index}。" in slide["content"]
    assert "## 第1页嵌套二级" in result["revision"]["slides"][0]["content"]
    assert "### 嵌套三级甲" in result["revision"]["slides"][0]["content"]
    assert "# 伪一级标题 围栏内" in result["revision"]["slides"][4]["content"]
    assert "# 伪一级标题 注释内" in result["revision"]["slides"][4]["content"]
    assert result["unavailable_status"] == 422
    assert result["unavailable"]["error"] == "target_page_count_unavailable"
    assert result["after_unavailable"] == result["after"]
    assert result["model_calls"] == ["propose"]


def test_public_image_plain_text_target_page_revision_expands_without_model(tmp_path):
    result = _run_public_case(
        tmp_path,
        """
        from backend.services import deck_split_drafts, public_image_surface

        source = "\\n\\n".join(
            (
                f"第{index}段保留原始事实 Alpha-{index}，包含编号 {1000 + index}。"
                "这是一段没有 Markdown 标题的普通文本。"
            )
            for index in range(1, 7)
        )
        model_calls = []
        deck_id = dbmod.create_deck("Public plain text pages", source)

        def faithful_split(deck_content, config, prompt):
            del config, prompt
            model_calls.append("propose")
            assert deck_content == source
            units = deck_split_drafts._ordered_source_units(
                deck_content, prefer_explicit_h1=True
            )
            return [
                {"title": unit["title"], "content": unit["content"]}
                for unit in units
            ]

        deck_split_drafts.generate_llm_split = faithful_split
        deck_split_drafts.generate_split_revision = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("target page revision must not invoke a model")
        )
        proposal = client.post(f"/api/decks/{deck_id}/split-drafts", json={})
        draft = proposal.get_json()
        db = dbmod.get_db()
        before = db.execute(
            "SELECT id, status, slides_json, attempt_count FROM deck_split_drafts WHERE id = ?",
            (draft["id"],),
        ).fetchone()
        db.close()

        revised = client.post(
            f"/api/deck-split-drafts/{draft['id']}/revise",
            json={"target_page_count": 5},
        )
        revised_body = revised.get_json()
        db = dbmod.get_db()
        after = db.execute(
            "SELECT id, status, slides_json, attempt_count FROM deck_split_drafts WHERE id = ?",
            (draft["id"],),
        ).fetchone()
        db.close()

        unavailable = client.post(
            f"/api/deck-split-drafts/{draft['id']}/revise",
            json={"target_page_count": 99},
        )
        db = dbmod.get_db()
        after_unavailable = db.execute(
            "SELECT id, status, slides_json, attempt_count FROM deck_split_drafts WHERE id = ?",
            (draft["id"],),
        ).fetchone()
        db.close()
        print("PUBLIC_RESULT:" + json.dumps({
            "proposal_status": proposal.status_code,
            "proposal_page_count": draft["page_count"],
            "revision_status": revised.status_code,
            "revision": revised_body,
            "unavailable_status": unavailable.status_code,
            "unavailable": unavailable.get_json(),
            "model_calls": model_calls,
            "before": dict(before),
            "after": dict(after),
            "after_unavailable": dict(after_unavailable),
            "source_tokens": public_image_surface._public_split_body_tokens(source),
            "revised_tokens": public_image_surface._public_split_body_tokens(
                "\\n".join(slide["content"] for slide in revised_body.get("slides") or [])
            ),
        }, ensure_ascii=False))
        """,
    )

    assert result["proposal_status"] == 201
    assert result["proposal_page_count"] < 5
    assert result["revision_status"] == 200
    assert result["revision"]["id"] == result["before"]["id"]
    assert result["revision"]["status"] == "pending"
    assert result["revision"]["page_count"] == 5
    assert result["revision"]["attempt_count"] == result["before"]["attempt_count"] == 1
    assert result["after"]["id"] == result["before"]["id"]
    assert result["after"]["status"] == "pending"
    assert result["after"]["attempt_count"] == result["before"]["attempt_count"]
    assert result["source_tokens"] == result["revised_tokens"]
    assert result["unavailable_status"] == 422
    assert result["unavailable"]["error"] == "target_page_count_unavailable"
    assert result["after_unavailable"] == result["after"]
    assert result["model_calls"] == ["propose"]


def test_public_image_split_uses_only_bounded_luna_luna_terra_transport_recovery(
    tmp_path,
):
    result = _run_public_case(
        tmp_path,
        """
        from backend.services import deck_split_drafts

        good = [
            {"title": "第一章", "content": "Alpha source sentence with number 42."},
            {"title": "第二章", "content": "Beta source sentence with number 7."},
        ]
        scenarios = {
            "first_success": ["ok"],
            "second_luna_success": ["transport", "ok"],
            "terra_success": ["transport", "transport", "ok"],
            "three_transport_failures": ["transport", "transport", "transport"],
            "ordinary_provider_failure": ["provider"],
            "timeout_failure": ["timeout"],
            "parse_failure": ["parse"],
            "integrity_failure": ["integrity"],
            "configuration_failure": ["configuration"],
            "content_failure": ["content"],
        }
        observed = {}
        for name, outcomes in scenarios.items():
            source = "Alpha source sentence with number 42.\\nBeta source sentence with number 7."
            deck_id = dbmod.create_deck(name, source)
            calls = []
            cursor = {"value": 0}

            def fake_split(deck_content, config, prompt):
                del deck_content, prompt
                cursor["value"] += 1
                calls.append(config["model"])
                outcome = outcomes[cursor["value"] - 1]
                if outcome == "transport":
                    raise deck_split_drafts.SplitExecutionFailure(
                        "provider_rejected",
                        "transport-only test failure",
                        transport_only=True,
                    )
                if outcome == "provider":
                    raise deck_split_drafts.SplitExecutionFailure(
                        "provider_rejected", "ordinary provider rejection"
                    )
                if outcome == "timeout":
                    raise TimeoutError("provider timed out")
                if outcome == "parse":
                    raise json.JSONDecodeError("invalid response", "", 0)
                if outcome == "integrity":
                    raise deck_split_drafts.SplitDraftError(
                        "Auto Split integrity check failed"
                    )
                if outcome == "configuration":
                    raise deck_split_drafts.SplitExecutionFailure(
                        "configuration", "profile drift"
                    )
                if outcome == "content":
                    raise deck_split_drafts.SplitDraftError(
                        "Auto Split returned invalid content"
                    )
                return good

            deck_split_drafts.generate_llm_split = fake_split
            response = client.post(f"/api/decks/{deck_id}/split-drafts", json={})
            payload = response.get_json()
            db = dbmod.get_db()
            row = db.execute(
                "SELECT model, attempt_count, status FROM deck_split_drafts WHERE deck_id = ?",
                (deck_id,),
            ).fetchone()
            db.close()
            observed[name] = {
                "status": response.status_code,
                "body": payload,
                "calls": calls,
                "row": dict(row),
            }
        print("PUBLIC_RESULT:" + json.dumps(observed, ensure_ascii=False))
        """,
    )

    assert result["first_success"]["status"] == 201
    assert result["first_success"]["calls"] == ["gpt-5.6-luna"]
    assert result["first_success"]["body"]["model"] == "gpt-5.6-luna"
    assert result["first_success"]["body"]["attempt_count"] == 1

    assert result["second_luna_success"]["status"] == 201
    assert result["second_luna_success"]["calls"] == [
        "gpt-5.6-luna",
        "gpt-5.6-luna",
    ]
    assert result["second_luna_success"]["body"]["model"] == "gpt-5.6-luna"
    assert result["second_luna_success"]["body"]["attempt_count"] == 2

    assert result["terra_success"]["status"] == 201
    assert result["terra_success"]["calls"] == [
        "gpt-5.6-luna",
        "gpt-5.6-luna",
        "gpt-5.6-terra",
    ]
    assert result["terra_success"]["body"]["model"] == "gpt-5.6-terra"
    assert result["terra_success"]["body"]["attempt_count"] == 3

    failed = result["three_transport_failures"]
    assert failed["status"] == 502
    assert failed["calls"] == ["gpt-5.6-luna", "gpt-5.6-luna", "gpt-5.6-terra"]
    assert failed["body"]["draft"]["status"] == "failed"
    assert failed["body"]["draft"]["model"] == "gpt-5.6-terra"
    assert failed["body"]["draft"]["attempt_count"] == 3

    ordinary = result["ordinary_provider_failure"]
    assert ordinary["status"] == 502
    assert ordinary["calls"] == ["gpt-5.6-luna"]
    assert ordinary["body"]["draft"]["model"] == "gpt-5.6-luna"
    assert ordinary["body"]["draft"]["attempt_count"] == 1

    for name in (
        "timeout_failure",
        "parse_failure",
        "integrity_failure",
        "configuration_failure",
        "content_failure",
    ):
        failed_once = result[name]
        assert 400 <= failed_once["status"] < 600
        assert failed_once["calls"] == ["gpt-5.6-luna"]
        assert failed_once["body"]["draft"]["status"] == "failed"
        assert failed_once["body"]["draft"]["model"] == "gpt-5.6-luna"
        assert failed_once["body"]["draft"]["attempt_count"] == 1


def test_public_image_split_terminalizes_timeout_and_resource_admission_once(tmp_path):
    result = _run_public_case(
        tmp_path,
        """
        from types import SimpleNamespace
        from backend.services import deck_split_drafts
        from backend.services.codex_platform_gate import CodexGateCapacityTimeout

        observed = {}
        for case in ("timeout", "resource"):
            deck_id = dbmod.create_deck(
                case,
                "# Source\\n\\n## First\\n\\nFirst body.\\n\\n## Second\\n\\nSecond body.",
            )
            calls = []

            async def fake_runner(**kwargs):
                calls.append({
                    "model": kwargs["model"],
                    "timeout_seconds": kwargs["timeout_seconds"],
                    "admission_timeout_seconds": kwargs["admission_timeout_seconds"],
                })
                if case == "timeout":
                    return SimpleNamespace(exit_code=124, timed_out=True, final_text="")
                raise CodexGateCapacityTimeout("host capacity detail must remain private")

            deck_split_drafts.run_codex_exec_json = fake_runner
            response = client.post(f"/api/decks/{deck_id}/split-drafts", json={})
            db = dbmod.get_db()
            row = db.execute(
                "SELECT status, attempt_count, last_error_code, error_message "
                "FROM deck_split_drafts WHERE deck_id = ?",
                (deck_id,),
            ).fetchone()
            db.close()
            observed[case] = {
                "status": response.status_code,
                "body": response.get_json(),
                "calls": calls,
                "row": dict(row),
            }
        print("PUBLIC_RESULT:" + json.dumps(observed, ensure_ascii=False))
        """,
    )

    timeout = result["timeout"]
    assert timeout["status"] == 504
    assert timeout["calls"] == [
        {
            "model": "gpt-5.6-luna",
            "timeout_seconds": 840,
            "admission_timeout_seconds": 30,
        }
    ]
    assert timeout["body"]["draft"]["status"] == "failed"
    assert timeout["body"]["draft"]["attempt_count"] == 1
    assert timeout["row"] == {
        "status": "failed",
        "attempt_count": 1,
        "last_error_code": "timeout",
        "error_message": "Auto Split timed out",
    }

    resource = result["resource"]
    assert resource["status"] == 503, resource
    assert resource["calls"] == timeout["calls"]
    assert resource["body"]["error"] == "resource_unavailable"
    assert resource["body"]["draft"]["status"] == "failed"
    assert resource["body"]["draft"]["attempt_count"] == 1
    assert "retry" in resource["body"]["message"].lower()
    assert "host capacity detail" not in resource["body"]["message"]
    assert resource["row"] == {
        "status": "failed",
        "attempt_count": 1,
        "last_error_code": "resource_unavailable",
        "error_message": resource["body"]["message"],
    }


def test_public_image_executable_identity_failure_is_typed_and_persisted_for_create_and_revise(
    tmp_path,
):
    result = _run_public_case(
        tmp_path,
        """
        from backend.services import deck_split_drafts
        from backend.services.codex_executable import CodexExecutableUnavailable

        source = (
            "# Source title\\n\\n"
            "## 第一章\\n\\n"
            "第一章保留普通词语 Alpha，并保留编号 42。\\n\\n"
            "## 第二章\\n\\n"
            "第二章保留普通词语 Beta，并保留编号 7。"
        )
        failure = CodexExecutableUnavailable(
            "windows_cache_candidate_missing",
            path=Path(r"C:\\Users\\agent\\AppData\\Local\\OpenAI\\Codex\\bin"),
        )
        create_deck_id = dbmod.create_deck("identity create", source)

        def unavailable_split(*_args, **_kwargs):
            raise failure

        deck_split_drafts.generate_llm_split = unavailable_split
        create_response = client.post(f"/api/decks/{create_deck_id}/split-drafts", json={})
        db = dbmod.get_db()
        create_row = db.execute(
            "SELECT status, attempt_count, last_error_code, error_message "
            "FROM deck_split_drafts WHERE deck_id = ?",
            (create_deck_id,),
        ).fetchone()
        db.close()

        revise_deck_id = dbmod.create_deck("identity revise", source)
        good = [
            {"title": "第一章", "content": "第一章保留普通词语 Alpha，并保留编号 42。"},
            {"title": "第二章", "content": "第二章保留普通词语 Beta，并保留编号 7。"},
        ]
        deck_split_drafts.generate_llm_split = lambda *_args, **_kwargs: good
        draft = client.post(f"/api/decks/{revise_deck_id}/split-drafts", json={}).get_json()
        deck_split_drafts.generate_split_revision = lambda *_args, **_kwargs: (_ for _ in ()).throw(failure)
        revise_response = client.post(
            f"/api/deck-split-drafts/{draft['id']}/revise",
            json={"instruction": "调整标题但保留事实"},
        )
        db = dbmod.get_db()
        revise_row = db.execute(
            "SELECT status, attempt_count, last_error_code, error_message "
            "FROM deck_split_drafts WHERE id = ?",
            (draft["id"],),
        ).fetchone()
        db.close()
        print("PUBLIC_RESULT:" + json.dumps({
            "create": {"status": create_response.status_code, "body": create_response.get_json(), "row": dict(create_row)},
            "revise": {"status": revise_response.status_code, "body": revise_response.get_json(), "row": dict(revise_row)},
        }, ensure_ascii=False))
        """,
    )

    expected_message = (
        "Codex Desktop executable identity is unavailable; please retry after Codex Desktop is ready"
    )
    assert result["create"] == {
        "status": 503,
        "body": {"error": "executable_identity_unavailable", "message": expected_message},
        "row": {
            "status": "failed",
            "attempt_count": 1,
            "last_error_code": "executable_identity_unavailable",
            "error_message": expected_message,
        },
    }
    assert result["revise"] == {
        "status": 503,
        "body": {"error": "executable_identity_unavailable", "message": expected_message},
        "row": {
            "status": "pending",
            "attempt_count": 2,
            "last_error_code": "executable_identity_unavailable",
            "error_message": expected_message,
        },
    }
    assert "Users" not in json.dumps(result)
    assert "hash" not in json.dumps(result).lower()


def test_public_image_split_uses_captured_r18_raw_transport_terminal_only(tmp_path):
    result = _run_public_case(
        tmp_path,
        """
        import os
        from types import SimpleNamespace
        from backend.services import deck_split_drafts

        # Exact event shape from the Mac R18 Luna Low failure capture.  The
        # test exercises the raw JSONL classifier through the public proposal
        # path rather than manufacturing SplitExecutionFailure.transport_only.
        raw_events = [
            {"type": "thread.started", "thread_id": "01a01123-f3b9-7382-9263-292622ea4e5d"},
            {"type": "turn.started"},
            {"type": "error", "message": "Reconnecting... 2/5 (stream disconnected before completion: No route to host (os error 65))"},
            {"type": "error", "message": "Reconnecting... 3/5 (stream disconnected before completion: No route to host (os error 65))"},
            {"type": "error", "message": "Reconnecting... 4/5 (stream disconnected before completion: No route to host (os error 65))"},
            {"type": "error", "message": "Reconnecting... 5/5 (stream disconnected before completion: No route to host (os error 65))"},
            {"type": "item.completed", "item": {"id": "item_0", "type": "error", "message": "Falling back from WebSockets to HTTPS transport. stream disconnected before completion: No route to host (os error 65)"}},
            {"type": "error", "message": "Reconnecting... 1/5 (stream disconnected before completion: error sending request for url (https://chatgpt.com/backend-api/codex/responses))"},
            {"type": "error", "message": "Reconnecting... 2/5 (stream disconnected before completion: error sending request for url (https://chatgpt.com/backend-api/codex/responses))"},
            {"type": "error", "message": "Reconnecting... 3/5 (stream disconnected before completion: error sending request for url (https://chatgpt.com/backend-api/codex/responses))"},
            {"type": "error", "message": "Reconnecting... 4/5 (stream disconnected before completion: error sending request for url (https://chatgpt.com/backend-api/codex/responses))"},
            {"type": "error", "message": "Reconnecting... 5/5 (stream disconnected before completion: error sending request for url (https://chatgpt.com/backend-api/codex/responses))"},
            {"type": "error", "message": "stream disconnected before completion: error sending request for url (https://chatgpt.com/backend-api/codex/responses)"},
            {"type": "turn.failed", "error": {"message": "stream disconnected before completion: error sending request for url (https://chatgpt.com/backend-api/codex/responses)"}},
        ]
        raw_transport = "".join(json.dumps(event) + "\\n" for event in raw_events)
        successful_boundary_plan = "```json\\n[{\\\"title\\\":\\\"第一页\\\",\\\"section_ids\\\":[1]},{\\\"title\\\":\\\"第二页\\\",\\\"section_ids\\\":[2]}]\\n```"
        source = "# Source\\n\\n## 第一章\\n\\nAlpha source sentence with number 42.\\n\\n## 第二章\\n\\nBeta source sentence with number 7."

        def run_case(name, raw_failure):
            deck_id = dbmod.create_deck(name, source)
            models = []

            async def fake_runner(**kwargs):
                models.append(kwargs["model"])
                if len(models) < 3:
                    raw_path = Path(os.environ["PPT_ARTIFACTS_DIR"]) / f"{name}-{len(models)}.raw.jsonl"
                    raw_path.write_text(raw_failure, encoding="utf-8")
                    return SimpleNamespace(exit_code=1, timed_out=False, raw_jsonl_path=raw_path)
                return SimpleNamespace(exit_code=0, timed_out=False, raw_jsonl_path=None)

            deck_split_drafts.run_codex_exec_json = fake_runner
            deck_split_drafts.materialize_codex_result_final_text = lambda _result: successful_boundary_plan
            response = client.post(f"/api/decks/{deck_id}/split-drafts", json={})
            return {"status": response.status_code, "body": response.get_json(), "models": models}

        recovered = run_case("captured transport", raw_transport)

        # A provider rejection that merely resembles the transport terminal
        # must not leave the first Luna invocation.
        rejected = run_case(
            "rejected transport-like",
            raw_transport.replace("No route to host", "model rejected the request"),
        )
        print("PUBLIC_RESULT:" + json.dumps({"recovered": recovered, "rejected": rejected}, ensure_ascii=False))
        """,
    )

    recovered = result["recovered"]
    assert recovered["status"] == 201
    assert recovered["models"] == ["gpt-5.6-luna", "gpt-5.6-luna", "gpt-5.6-terra"]
    assert recovered["body"]["model"] == "gpt-5.6-terra"
    assert recovered["body"]["attempt_count"] == 3

    rejected = result["rejected"]
    assert rejected["status"] == 502
    assert rejected["models"] == ["gpt-5.6-luna"]
    assert rejected["body"]["draft"]["model"] == "gpt-5.6-luna"
    assert rejected["body"]["draft"]["attempt_count"] == 1


def test_public_image_terra_e2e_split_seam_remains_single_terra_attempt(tmp_path):
    result = _run_public_case(
        tmp_path,
        """
        from backend.services import deck_split_drafts

        source = "# Source\\n\\n## 第一章\\n\\nAlpha source sentence with number 42.\\n\\n## 第二章\\n\\nBeta source sentence with number 7."
        calls = []

        def fake_split(deck_content, config, prompt):
            del deck_content, prompt
            calls.append(config["model"])
            return [
                {"title": "第一章", "content": "Alpha source sentence with number 42."},
                {"title": "第二章", "content": "Beta source sentence with number 7."},
            ]

        deck_split_drafts.generate_llm_split = fake_split
        deck_id = dbmod.create_deck("Terra seam", source)
        response = client.post(f"/api/decks/{deck_id}/split-drafts", json={})
        print("PUBLIC_RESULT:" + json.dumps({
            "status": response.status_code,
            "body": response.get_json(),
            "calls": calls,
        }, ensure_ascii=False))
        """,
        extra_env={"IMAGE_PPTGEN_E2E_TERRA_LOW": "1"},
    )

    assert result["status"] == 201
    assert result["calls"] == ["gpt-5.6-terra"]
    assert result["body"]["model"] == "gpt-5.6-terra"
    assert result["body"]["attempt_count"] == 1


def test_public_image_terra_revision_does_not_reenter_luna_or_sol(tmp_path):
    result = _run_public_case(
        tmp_path,
        """
        from backend.services import deck_split_drafts

        source = "Alpha source sentence with number 42.\\nBeta source sentence with number 7."
        good = [
            {"title": "第一章", "content": "Alpha source sentence with number 42."},
            {"title": "第二章", "content": "Beta source sentence with number 7."},
        ]
        proposal_calls = []
        proposal_cursor = {"value": 0}

        def proposal(deck_content, config, prompt):
            del deck_content, prompt
            proposal_cursor["value"] += 1
            proposal_calls.append(config["model"])
            if proposal_cursor["value"] < 3:
                raise deck_split_drafts.SplitExecutionFailure(
                    "provider_rejected", "transport-only", transport_only=True
                )
            return good

        revision_calls = []

        def revision(deck_content, current_slides, instruction, config, *, content_mode):
            del deck_content, current_slides, instruction, content_mode
            revision_calls.append(config["model"])
            return good

        deck_split_drafts.generate_llm_split = proposal
        deck_split_drafts.generate_split_revision = revision
        deck_id = dbmod.create_deck("Terra revision", source)
        draft_response = client.post(f"/api/decks/{deck_id}/split-drafts", json={})
        draft = draft_response.get_json()
        revised_response = client.post(
            f"/api/deck-split-drafts/{draft['id']}/revise",
            json={"instruction": "只调整分页"},
        )
        revised = revised_response.get_json()
        confirm_response = client.post(
            f"/api/deck-split-drafts/{draft['id']}/confirm", json={}
        )
        print("PUBLIC_RESULT:" + json.dumps({
            "proposal_status": draft_response.status_code,
            "proposal_calls": proposal_calls,
            "draft": draft,
            "revision_status": revised_response.status_code,
            "revision_calls": revision_calls,
            "revised": revised,
            "confirm_status": confirm_response.status_code,
        }, ensure_ascii=False))
        """,
    )

    assert result["proposal_status"] == 201
    assert result["proposal_calls"] == [
        "gpt-5.6-luna",
        "gpt-5.6-luna",
        "gpt-5.6-terra",
    ]
    assert result["draft"]["model"] == "gpt-5.6-terra"
    assert result["draft"]["attempt_count"] == 3
    assert result["revision_status"] == 200
    assert result["revision_calls"] == ["gpt-5.6-terra"]
    assert result["revised"]["model"] == "gpt-5.6-terra"
    assert result["revised"]["attempt_count"] == 4
    assert result["confirm_status"] == 200


def test_public_image_split_rejects_overrides_without_mutation(tmp_path):
    result = _run_public_case(
        tmp_path,
        """
        deck_id = dbmod.create_deck("Public split", "source body")
        db = dbmod.get_db()
        try:
            before = db.execute("SELECT COUNT(*) AS n FROM deck_split_drafts").fetchone()["n"]
        finally:
            db.close()
        payloads = [
            {"mode": "llm"},
            {"mode": "deterministic"},
            {"model": "gpt-5.6-sol"},
            {"profile_id": 99},
            {"thinking_effort": "high"},
            {"content_mode": "editorial"},
            {"retry": True},
            {"delete": True},
            {"unexpected": True},
        ]
        responses = []
        for payload in payloads:
            response = client.post(f"/api/decks/{deck_id}/split-drafts", json=payload)
            responses.append({"status": response.status_code, "body": response.get_json()})
        db = dbmod.get_db()
        try:
            after = db.execute("SELECT COUNT(*) AS n FROM deck_split_drafts").fetchone()["n"]
        finally:
            db.close()
        print("PUBLIC_RESULT:" + json.dumps({"responses": responses, "before": before, "after": after}))
        """,
    )

    assert result["before"] == result["after"] == 0
    assert all(item == {"status": 404, "body": {"error": "Not found"}} for item in result["responses"])


def test_public_image_split_fails_on_every_source_parity_break_and_sol_profile(tmp_path):
    result = _run_public_case(
        tmp_path,
        """
        from backend.services import deck_split_drafts, model_profiles

        source = "Alpha source sentence with number 42.\\nBeta source sentence with number 7."
        good = [
            {"title": "Page one", "content": "Alpha source sentence with number 42."},
            {"title": "Page two", "content": "Beta source sentence with number 7."},
        ]
        invalid = {
            "dropped": [
                {"title": "Page one", "content": "Alpha source sentence with number 42."},
            ],
            "reordered": list(reversed(good)),
            "paraphrased": [
                {"title": "Page one", "content": "Alpha rewritten sentence with number 42."},
                {"title": "Page two", "content": "Beta source sentence with number 7."},
            ],
            "number_changed": [
                {"title": "Page one", "content": "Alpha source sentence with number 43."},
                {"title": "Page two", "content": "Beta source sentence with number 7."},
            ],
            "inserted": [
                {"title": "Page one", "content": "Alpha source sentence with inserted text 99 and number 42."},
                {"title": "Page two", "content": "Beta source sentence with number 7."},
            ],
        }
        proposal_statuses = {}
        revision_statuses = {}
        revision_unchanged = {}
        for name, candidate in invalid.items():
            deck_id = dbmod.create_deck(f"Public split {name}", source)
            deck_split_drafts.generate_llm_split = lambda *_args, **_kwargs: candidate
            proposal = client.post(f"/api/decks/{deck_id}/split-drafts", json={})
            proposal_statuses[name] = proposal.status_code

            revision_deck_id = dbmod.create_deck(f"Public revision {name}", source)
            deck_split_drafts.generate_llm_split = lambda *_args, **_kwargs: good
            draft = client.post(f"/api/decks/{revision_deck_id}/split-drafts", json={}).get_json()
            db = dbmod.get_db()
            before_revision = db.execute(
                "SELECT slides_json FROM deck_split_drafts WHERE id = ?", (draft["id"],)
            ).fetchone()["slides_json"]
            db.close()
            deck_split_drafts.generate_split_revision = lambda *_args, _candidate=candidate, **_kwargs: _candidate
            revision = client.post(
                f"/api/deck-split-drafts/{draft['id']}/revise",
                json={"instruction": "keep every source sentence"},
            )
            revision_statuses[name] = revision.status_code
            db = dbmod.get_db()
            after_revision = db.execute(
                "SELECT slides_json FROM deck_split_drafts WHERE id = ?", (draft["id"],)
            ).fetchone()["slides_json"]
            db.close()
            revision_unchanged[name] = before_revision == after_revision

        db = dbmod.get_db()
        luna = db.execute(
            "SELECT * FROM model_profiles WHERE name = ?", ("AutoSplit · GPT-5.6 Luna",)
        ).fetchone()
        db.execute("UPDATE model_profiles SET model = ? WHERE id = ?", ("gpt-5.6-sol", luna["id"]))
        db.commit()
        db.close()
        sol_deck = dbmod.create_deck("Public split sol", source)
        sol_response = client.post(f"/api/decks/{sol_deck}/split-drafts", json={})
        print("PUBLIC_RESULT:" + json.dumps({
            "proposal_statuses": proposal_statuses,
            "revision_statuses": revision_statuses,
            "revision_unchanged": revision_unchanged,
            "sol_status": sol_response.status_code,
            "sol_body": sol_response.get_json(),
        }))
        """,
    )

    assert result["proposal_statuses"] == {
        "dropped": 422,
        "reordered": 422,
        "paraphrased": 422,
        "number_changed": 422,
        "inserted": 422,
    }
    assert result["revision_statuses"] == result["proposal_statuses"]
    assert all(result["revision_unchanged"].values())
    assert result["sol_status"] == 400
    assert result["sol_body"] == {"error": "Public Image split profile is not configured"}


def test_public_image_confirm_calls_server_owned_image_confirmation(tmp_path):
    result = _run_public_case(
        tmp_path,
        """
        from backend.services import deck_split_drafts

        deck_id = dbmod.create_deck("Public split", "Alpha body\\nBeta body")
        deck_split_drafts.generate_llm_split = lambda *_args, **_kwargs: [
            {"title": "Alpha", "content": "Alpha body"},
            {"title": "Beta", "content": "Beta body"},
        ]
        draft = client.post(f"/api/decks/{deck_id}/split-drafts", json={}).get_json()
        calls = []

        def confirm_image_skill_split_draft(draft_id):
            calls.append(draft_id)
            return {
                "slide_ids": [11, 12, 13],
                "slides": [{"deck_id": deck_id}],
            }

        def forbidden_generic(*_args, **_kwargs):
            raise AssertionError("generic confirmation must not be used")

        deck_split_drafts.confirm_image_skill_split_draft = confirm_image_skill_split_draft
        deck_split_drafts.confirm_split_draft = forbidden_generic
        response = client.post(
            f"/api/deck-split-drafts/{draft['id']}/confirm", json={}
        )
        print("PUBLIC_RESULT:" + json.dumps({
            "status": response.status_code,
            "body": response.get_json(),
            "calls": calls,
        }))
        """,
    )

    assert result == {
        "status": 200,
        "body": {
            "deck_id": 1,
            "draft_id": 1,
            "slide_count": 3,
            "slide_ids": [11, 12, 13],
            "status": "confirmed",
        },
        "calls": [1],
    }


def test_public_surface_exhaustively_fails_closed_for_every_unlisted_api_pair(tmp_path):
    result = _run_public_case(
        tmp_path,
        """
        import re
        from backend.services import public_image_surface

        automatic = {"HEAD", "OPTIONS"}
        registered = {
            (method, rule.rule)
            for rule in app.url_map.iter_rules()
            if rule.rule.startswith("/api/")
            for method in rule.methods - automatic
        }
        allowed = public_image_surface._ALLOWED_API_RULES
        expected_allowed = {
            ("GET", "/api/runtime-identity"),
            ("GET", "/api/decks"),
            ("POST", "/api/decks"),
            ("GET", "/api/decks/<int:deck_id>"),
            ("PUT", "/api/decks/<int:deck_id>"),
            ("DELETE", "/api/decks/<int:deck_id>"),
            ("POST", "/api/decks/<int:deck_id>/archive"),
            ("POST", "/api/decks/<int:deck_id>/restore"),
            ("POST", "/api/decks/<int:deck_id>/force-delete"),
            ("PUT", "/api/decks/<int:deck_id>/folders"),
            ("GET", "/api/decks/<int:deck_id>/slides"),
            ("POST", "/api/decks/<int:deck_id>/slides"),
            ("POST", "/api/decks/<int:deck_id>/split"),
            ("POST", "/api/decks/<int:deck_id>/split-drafts"),
            ("POST", "/api/deck-split-drafts/<int:draft_id>/revise"),
            ("POST", "/api/deck-split-drafts/<int:draft_id>/confirm"),
            ("GET", "/api/slides/<int:slide_id>"),
            ("PUT", "/api/slides/<int:slide_id>"),
            ("DELETE", "/api/slides/<int:slide_id>"),
            ("GET", "/api/folders"),
            ("POST", "/api/folders"),
            ("PUT", "/api/folders/<int:folder_id>"),
            ("POST", "/api/bulk-actions"),
            ("GET", "/api/configs"),
            ("GET", "/api/configs/<int:config_id>"),
            ("POST", "/api/generate"),
            ("GET", "/api/batches"),
            ("GET", "/api/batches/active"),
            ("GET", "/api/batches/<int:batch_id>"),
            ("GET", "/api/batches/<int:batch_id>/download"),
            ("GET", "/api/runs"),
            ("GET", "/api/runs/<int:run_id>"),
            ("GET", "/api/runs/<int:run_id>/status"),
            ("GET", "/api/runs/<int:run_id>/download"),
            ("GET", "/api/run-slides/<int:run_slide_id>/evidence-download"),
            ("GET", "/api/runs/<int:run_id>/codex-audit/invocations/<int:invocation_id>"),
            ("GET", "/api/runs/<int:run_id>/codex-audit/invocations/<int:invocation_id>/events"),
        }
        assert allowed == expected_allowed
        assert not (allowed - registered), sorted(allowed - registered)

        def concrete(rule):
            value = re.sub(r"<int:[^>]+>", "999999", rule)
            return re.sub(r"<[^>]+>", "not-public", value)

        probes = []
        for method, rule in sorted(registered - allowed):
            response = client.open(concrete(rule), method=method, json={})
            probes.append((method, rule, response.status_code, response.get_json(silent=True)))
        for method in ("PATCH", "HEAD", "OPTIONS"):
            response = client.open("/api/configs", method=method, json={})
            probes.append((method, "/api/configs", response.status_code, response.get_json(silent=True)))
        unknown = client.get("/api/not-a-public-capability")
        probes.append(("GET", "/api/not-a-public-capability", unknown.status_code, unknown.get_json()))
        print("PUBLIC_RESULT:" + json.dumps({
            "registered_count": len(registered),
            "allowed_count": len(allowed),
            "probed_count": len(probes),
            "bad": [
                probe
                for probe in probes
                if probe[2] != 404
                or (probe[0] != "HEAD" and probe[3] != {"error": "Not found"})
            ],
        }))
        """,
    )

    assert result["registered_count"] >= 100
    assert result["allowed_count"] >= 30
    assert result["probed_count"] >= 70
    assert result["bad"] == []


def test_public_server_rejects_runtime_roots_outside_public_data_dir(tmp_path):
    public_data = tmp_path / "public-data"
    protected_db = tmp_path / "protected" / "ppt.db"
    env = os.environ.copy()
    env.update(
        {
            "PYTHONPATH": str(ROOT),
            "PPTGEN_PUBLIC_DATA_DIR": str(public_data),
            "PPT_DB_PATH": str(protected_db),
            "PPT_ARTIFACTS_DIR": str(public_data / "artifacts"),
        }
    )
    completed = subprocess.run(
        [sys.executable, "-c", "import public_server"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode != 0
    assert "PPT_DB_PATH must be inside PPTGEN_PUBLIC_DATA_DIR" in completed.stderr
    assert not protected_db.exists()


def test_public_server_rejects_historical_export_root_outside_public_data_dir(tmp_path):
    public_data = tmp_path / "public-data"
    escaped_historical_root = tmp_path / "outside" / "historical-data"
    env = os.environ.copy()
    env.update(
        {
            "PYTHONPATH": str(ROOT),
            "PPTGEN_PUBLIC_DATA_DIR": str(public_data),
            "PPT_DB_PATH": str(public_data / "ppt.db"),
            "PPT_ARTIFACTS_DIR": str(public_data / "artifacts"),
            "PPTGEN_HISTORICAL_DATA_DIR": str(escaped_historical_root),
        }
    )
    completed = subprocess.run(
        [sys.executable, "-c", "import public_server"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode != 0
    assert "PPTGEN_HISTORICAL_DATA_DIR must be inside PPTGEN_PUBLIC_DATA_DIR" in completed.stderr
    assert not escaped_historical_root.exists()


def test_public_server_rejects_a_caller_selected_protected_data_root(tmp_path):
    protected_data = tmp_path / "protected-data"
    protected_db = protected_data / "ppt.db"
    env = os.environ.copy()
    env.update(
        {
            "PYTHONPATH": str(ROOT),
            "PPTGEN_PROTECTED_DATA_ROOTS": str(protected_data),
            "PPTGEN_PUBLIC_DATA_DIR": str(protected_data),
            "PPT_DB_PATH": str(protected_db),
            "PPT_ARTIFACTS_DIR": str(protected_data / "artifacts"),
        }
    )
    completed = subprocess.run(
        [sys.executable, "-c", "import public_server"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode != 0
    assert "Public data roots must not overlap a protected data root" in completed.stderr
    assert not protected_db.exists()


def test_public_surface_limits_folders_and_bulk_actions_to_decks(tmp_path):
    result = _run_public_case(
        tmp_path,
        """
        from backend.services import folders

        deck_id = dbmod.create_deck("Deck", "# Cover")
        private_parent = folders.create_folder("color", "Private")
        blocked = [
            client.get("/api/folders?scope=requirement"),
            client.get("/api/folders"),
            client.post("/api/folders", json={"scope": "color", "name": "Hidden"}),
            client.post(
                "/api/folders",
                json={"scope": "deck", "name": "Cross Scope", "parent_id": private_parent["id"]},
            ),
            client.post("/api/bulk-actions", json={"entity_type": "prompt", "action": "archive", "ids": [1]}),
        ]
        folder_response = client.post("/api/folders", json={"scope": "deck", "name": "Public"})
        folder = folder_response.get_json()
        cross_scope_update = client.put(
            f"/api/folders/{folder['id']}",
            json={"parent_id": private_parent["id"]},
        )
        cross_scope_assignment = client.put(
            f"/api/decks/{deck_id}/folders",
            json={"folder_ids": [private_parent["id"]]},
        )
        assign_response = client.put(f"/api/decks/{deck_id}/folders", json={"folder_ids": [folder["id"]]})
        bulk_response = client.post(
            "/api/bulk-actions",
            json={"entity_type": "deck", "action": "archive", "ids": [deck_id]},
        )
        print("PUBLIC_RESULT:" + json.dumps({
            "blocked": [response.status_code for response in blocked],
            "folder_status": folder_response.status_code,
            "folder": folder,
            "cross_scope": [cross_scope_update.status_code, cross_scope_assignment.status_code],
            "assign_status": assign_response.status_code,
            "bulk_status": bulk_response.status_code,
        }))
        """,
    )

    assert result["blocked"] == [404, 404, 404, 404, 404]
    assert result["folder_status"] == 201
    assert result["folder"]["scope"] == "deck"
    assert result["cross_scope"] == [404, 404]
    assert result["assign_status"] == 200
    assert result["bulk_status"] == 200


def test_public_force_delete_exports_stay_isolated_and_paths_are_not_public(tmp_path):
    result = _run_public_case(
        tmp_path,
        """
        from pathlib import Path

        public_data = Path(public_server.PUBLIC_DATA_DIR)
        checkout_historical_root = Path(public_server.BASE_DIR) / "historical_data"
        before_root_files = sorted(str(path) for path in checkout_historical_root.glob("**/*") if path.is_file())

        def make_deck(title):
            deck_id = dbmod.create_deck(title, "# Cover\\n# Seed")
            dbmod.create_slide(deck_id, 1, "Cover", "Cover content")
            dbmod.create_slide(deck_id, 2, "Seed", "Seed content")
            return deck_id

        direct_id = make_deck("Direct force delete")
        direct_response = client.post(f"/api/decks/{direct_id}/force-delete")

        bulk_id = make_deck("Bulk force delete")
        bulk_response = client.post(
            "/api/bulk-actions",
            json={"entity_type": "deck", "action": "force_delete", "ids": [bulk_id]},
        )

        historical_root = public_data / "historical-data"
        export_files = sorted(str(path) for path in historical_root.glob("deck_*.json"))
        after_root_files = sorted(str(path) for path in checkout_historical_root.glob("**/*") if path.is_file())
        print("PUBLIC_RESULT:" + json.dumps({
            "statuses": [direct_response.status_code, bulk_response.status_code],
            "direct": direct_response.get_json(),
            "bulk": bulk_response.get_json(),
            "export_files": export_files,
            "export_contents": [Path(path).read_text(encoding="utf-8") for path in export_files],
            "before_root_files": before_root_files,
            "after_root_files": after_root_files,
            "public_data": str(public_data),
        }))
        """,
    )

    assert result["statuses"] == [200, 200]
    assert len(result["export_files"]) == 2
    assert all(
        Path(path).is_relative_to(Path(result["public_data"]) / "historical-data")
        for path in result["export_files"]
    )
    assert result["before_root_files"] == result["after_root_files"]
    assert "historical_export_path" not in json.dumps(result["direct"])
    assert "historical_export_path" not in json.dumps(result["bulk"])
    assert all('"slides"' in content for content in result["export_contents"])


def test_public_surface_filters_run_batch_and_artifact_ownership_and_redacts_detail(tmp_path):
    result = _run_public_case(
        tmp_path,
        """
        artifacts = Path(public_server.server.ARTIFACTS_DIR)
        artifacts.mkdir(parents=True, exist_ok=True)
        deck_id = dbmod.create_deck("Deck", "# Cover\\n# Seed")
        slide_id = dbmod.create_slide(deck_id, 1, "Cover", "Content")
        requirement_id = dbmod.create_requirement("__public_empty_requirement__", "PRIVATE_REQUIREMENT")
        color_id = dbmod.create_color("__public_empty_color__", "PRIVATE_COLOR")
        public_config = dbmod.get_config_by_name(model_profiles.NATIVE_IMAGE_3_0_CONFIG_NAME)
        hidden_config = dbmod.get_config_by_name(model_profiles.NATIVE_IMAGE_DIRECT_CONFIG_NAME)

        public_batch = dbmod.create_batch(deck_id, public_config["id"], [requirement_id], [color_id], total_runs=1)
        public_run = dbmod.create_run(
            deck_id,
            requirement_id,
            color_id,
            public_config["id"],
            batch_id=public_batch,
            engine="image",
            strategy="image_3_0",
            route_metadata={"private_path": "/tmp/PRIVATE_ROUTE"},
            stage_artifacts={"seed_palette_lineage": {"palette_sha256": "safe-palette"}},
            model_call_metadata={"api_key": "PRIVATE_KEY"},
        )
        public_slide = dbmod.create_run_slide(public_run, slide_id, 1, "cover")
        public_png = artifacts / "run-public" / "slide.png"
        public_png.parent.mkdir(parents=True, exist_ok=True)
        public_png.write_bytes(b"public-png")
        dbmod.update_run_slide(public_slide, final_image_path=str(public_png), status="completed")

        hidden_batch = dbmod.create_batch(deck_id, hidden_config["id"], [requirement_id], [color_id], total_runs=1)
        hidden_run = dbmod.create_run(
            deck_id,
            requirement_id,
            color_id,
            hidden_config["id"],
            batch_id=hidden_batch,
            engine="image",
            strategy="image_direct",
        )
        hidden_slide = dbmod.create_run_slide(hidden_run, slide_id, 1, "cover")
        hidden_png = artifacts / "run-hidden" / "slide.png"
        hidden_png.parent.mkdir(parents=True, exist_ok=True)
        hidden_png.write_bytes(b"hidden-png")
        dbmod.update_run_slide(hidden_slide, final_image_path=str(hidden_png), status="completed")

        db = dbmod.get_db()
        db.execute("UPDATE runs SET status = 'completed'")
        db.execute("UPDATE batches SET status = 'completed'")
        db.commit()
        db.close()

        runs_response = client.get("/api/runs")
        batches_response = client.get("/api/batches")
        detail_response = client.get(f"/api/runs/{public_run}")
        hidden_responses = [
            client.get(f"/api/runs/{hidden_run}"),
            client.get(f"/api/runs/{hidden_run}/status"),
            client.get(f"/api/batches/{hidden_batch}"),
            client.get(f"/api/runs/{hidden_run}/download"),
            client.get(f"/api/run-slides/{hidden_slide}/evidence-download"),
        ]
        public_artifact = client.get("/artifacts/run-public/slide.png")
        hidden_artifact = client.get("/artifacts/run-hidden/slide.png")
        print("PUBLIC_RESULT:" + json.dumps({
            "runs_status": runs_response.status_code,
            "run_ids": [item["id"] for item in runs_response.get_json()],
            "batches_status": batches_response.status_code,
            "batch_ids": [item["id"] for item in batches_response.get_json()],
            "detail_status": detail_response.status_code,
            "detail": detail_response.get_json(),
            "hidden_statuses": [response.status_code for response in hidden_responses],
            "public_artifact": [public_artifact.status_code, public_artifact.data.decode("utf-8")],
            "hidden_artifact_status": hidden_artifact.status_code,
        }))
        """,
    )

    assert result["runs_status"] == 200
    assert result["run_ids"] and len(result["run_ids"]) == 1
    assert result["batches_status"] == 200
    assert result["batch_ids"] and len(result["batch_ids"]) == 1
    assert result["detail_status"] == 200
    detail_text = json.dumps(result["detail"], ensure_ascii=False)
    for secret in (
        "PRIVATE_REQUIREMENT",
        "PRIVATE_COLOR",
        "PRIVATE_ROUTE",
        "PRIVATE_KEY",
        "requirement_content",
        "color_content",
        "model_call_metadata",
        "designer_prompt_version",
        "html_prompt_version",
    ):
        assert secret not in detail_text
    assert result["detail"]["stage_artifacts"]["seed_palette_lineage"]["palette_sha256"] == "safe-palette"
    assert result["hidden_statuses"] == [404, 404, 404, 404, 404]
    assert result["public_artifact"] == [200, "public-png"]
    assert result["hidden_artifact_status"] == 404


def test_public_surface_redacts_secrets_embedded_in_error_messages(tmp_path):
    result = _run_public_case(
        tmp_path,
        """
        deck_id = dbmod.create_deck("Deck", "# Cover")
        slide_id = dbmod.create_slide(deck_id, 1, "Cover", "Content")
        requirement_id = dbmod.create_requirement("__public_empty_requirement__", "")
        color_id = dbmod.create_color("__public_empty_color__", "")
        config = dbmod.get_config_by_name(model_profiles.NATIVE_IMAGE_3_0_CONFIG_NAME)
        batch_id = dbmod.create_batch(deck_id, config["id"], [requirement_id], [color_id], total_runs=1)
        run_id = dbmod.create_run(
            deck_id, requirement_id, color_id, config["id"], batch_id=batch_id,
            engine="image", strategy="image_3_0",
        )
        run_slide_id = dbmod.create_run_slide(run_id, slide_id, 1, "cover")
        private_error = (
            "authorization=Bearer VERY_PRIVATE_TOKEN "
            "thread_id=PRIVATE_THREAD session_id=PRIVATE_SESSION "
            "failed at /tmp/private/raw.json"
        )
        db = dbmod.get_db()
        db.execute("UPDATE runs SET error_message = ? WHERE id = ?", (private_error, run_id))
        db.execute("UPDATE run_slides SET error_message = ? WHERE id = ?", (private_error, run_slide_id))
        db.execute("UPDATE batches SET error_message = ? WHERE id = ?", (private_error, batch_id))
        db.commit()
        db.close()

        responses = [
            client.get("/api/runs"),
            client.get(f"/api/runs/{run_id}"),
            client.get(f"/api/runs/{run_id}/status"),
            client.get("/api/batches"),
            client.get(f"/api/batches/{batch_id}"),
        ]
        print("PUBLIC_RESULT:" + json.dumps({
            "statuses": [response.status_code for response in responses],
            "payload": [response.get_json() for response in responses],
        }))
        """,
    )

    assert result["statuses"] == [200, 200, 200, 200, 200]
    rendered = json.dumps(result["payload"], ensure_ascii=False)
    for secret in (
        "VERY_PRIVATE_TOKEN",
        "PRIVATE_THREAD",
        "PRIVATE_SESSION",
        "/tmp/private/raw.json",
    ):
        assert secret not in rendered
    assert "error_message" in rendered


def test_public_history_reads_never_reconcile_or_pump_foreign_work(tmp_path):
    result = _run_public_case(
        tmp_path,
        """
        deck_id = dbmod.create_deck("Deck", "# Cover\\n# Seed")
        requirement_id = dbmod.create_requirement("__public_empty_requirement__", "")
        color_id = dbmod.create_color("__public_empty_color__", "")
        public_config = dbmod.get_config_by_name(model_profiles.NATIVE_IMAGE_3_0_CONFIG_NAME)
        hidden_config = dbmod.get_config_by_name(model_profiles.NATIVE_IMAGE_DIRECT_CONFIG_NAME)

        public_batch = dbmod.create_batch(deck_id, public_config["id"], [requirement_id], [color_id], total_runs=1)
        public_run = dbmod.create_run(
            deck_id, requirement_id, color_id, public_config["id"], batch_id=public_batch,
            engine="image", strategy="image_3_0",
        )
        hidden_batch = dbmod.create_batch(deck_id, hidden_config["id"], [requirement_id], [color_id], total_runs=1)
        dbmod.create_run(
            deck_id, requirement_id, color_id, hidden_config["id"], batch_id=hidden_batch,
            engine="image", strategy="image_direct",
        )
        db = dbmod.get_db()
        db.execute("UPDATE batches SET status = 'running'")
        db.execute("UPDATE runs SET status = 'running'")
        db.commit()
        db.close()

        def forbidden(*args, **kwargs):
            raise AssertionError("public history invoked a global side effect")

        public_server.server.run_history.reconcile_default_timeout = forbidden
        public_server.server.run_history.list_batches = forbidden
        public_server.server.run_history.get_active_batch = forbidden
        public_server.server.run_history.get_batch_detail = forbidden
        public_server.server.pump_batch_queue = forbidden
        public_server.server.evaluations.enrich_run_with_deck_snapshot = forbidden

        responses = [
            client.get("/api/runs"),
            client.get(f"/api/runs/{public_run}"),
            client.get(f"/api/runs/{public_run}/status"),
            client.get("/api/batches"),
            client.get("/api/batches/active"),
            client.get(f"/api/batches/{public_batch}"),
        ]
        print("PUBLIC_RESULT:" + json.dumps({
            "statuses": [response.status_code for response in responses],
            "run_ids": [row["id"] for row in responses[0].get_json()],
            "batch_ids": [row["id"] for row in responses[3].get_json()],
            "active_id": responses[4].get_json()["id"],
        }))
        """,
    )

    assert result == {
        "statuses": [200, 200, 200, 200, 200, 200],
        "run_ids": [1],
        "batch_ids": [1],
        "active_id": 1,
    }


def test_public_surface_rejects_mixed_batches_llm_split_and_non_png_artifacts(tmp_path):
    result = _run_public_case(
        tmp_path,
        """
        artifacts = Path(public_server.server.ARTIFACTS_DIR)
        deck_id = dbmod.create_deck("Deck", "# Cover\\n# Seed")
        slide_id = dbmod.create_slide(deck_id, 1, "Cover", "Content")
        requirement_id = dbmod.create_requirement("__public_empty_requirement__", "")
        color_id = dbmod.create_color("__public_empty_color__", "")
        public_config = dbmod.get_config_by_name(model_profiles.NATIVE_IMAGE_3_0_CONFIG_NAME)
        hidden_config = dbmod.get_config_by_name(model_profiles.NATIVE_IMAGE_DIRECT_CONFIG_NAME)
        mixed_batch = dbmod.create_batch(deck_id, public_config["id"], [requirement_id], [color_id], total_runs=2)
        public_run = dbmod.create_run(
            deck_id, requirement_id, color_id, public_config["id"], batch_id=mixed_batch,
            engine="image", strategy="image_3_0",
        )
        dbmod.create_run(
            deck_id, requirement_id, color_id, hidden_config["id"], batch_id=mixed_batch,
            engine="image", strategy="image_direct",
        )
        run_slide = dbmod.create_run_slide(public_run, slide_id, 1, "cover")
        png = artifacts / "owned" / "slide.png"
        html = artifacts / "owned" / "slide.html"
        png.parent.mkdir(parents=True, exist_ok=True)
        png.write_bytes(b"png")
        html.write_text("PRIVATE_HTML", encoding="utf-8")
        dbmod.update_run_slide(
            run_slide,
            final_image_path=str(png),
            html_path=str(html),
            status="completed",
        )

        split_calls = []
        def forbidden_split(*args, **kwargs):
            split_calls.append(True)
            raise AssertionError("LLM split must not run")
        public_server.server.split_deck = forbidden_split

        mixed = client.get(f"/api/batches/{mixed_batch}")
        mixed_download = client.get(f"/api/batches/{mixed_batch}/download")
        blocked_split = client.post(
            f"/api/decks/{deck_id}/split",
            json={"config_id": 999, "replace": True},
        )
        html_response = client.get("/artifacts/owned/slide.html")
        png_response = client.get("/artifacts/owned/slide.png")
        print("PUBLIC_RESULT:" + json.dumps({
            "mixed": mixed.status_code,
            "mixed_download": mixed_download.status_code,
            "split": blocked_split.status_code,
            "split_calls": len(split_calls),
            "html": html_response.status_code,
            "png": [png_response.status_code, png_response.data.decode("utf-8")],
        }))
        """,
    )

    assert result == {
        "mixed": 404,
        "mixed_download": 404,
        "split": 404,
        "split_calls": 0,
        "html": 404,
        "png": [200, "png"],
    }


def test_public_downloads_are_png_only_and_generic_slide_evidence_fails_closed(tmp_path):
    result = _run_public_case(
        tmp_path,
        """
        import io
        import zipfile

        artifacts = Path(public_server.server.ARTIFACTS_DIR)
        deck_id = dbmod.create_deck("Deck", "# Cover\\n# Seed")
        slide_id = dbmod.create_slide(deck_id, 1, "Cover", "Content")
        requirement_id = dbmod.create_requirement("__public_empty_requirement__", "PRIVATE_REQ")
        color_id = dbmod.create_color("__public_empty_color__", "PRIVATE_COLOR")
        config = dbmod.get_config_by_name(model_profiles.NATIVE_IMAGE_3_0_CONFIG_NAME)
        batch_id = dbmod.create_batch(deck_id, config["id"], [requirement_id], [color_id], total_runs=1)
        run_id = dbmod.create_run(
            deck_id, requirement_id, color_id, config["id"], batch_id=batch_id,
            engine="image", strategy="image_3_0",
        )
        run_slide_id = dbmod.create_run_slide(run_id, slide_id, 1, "cover")
        png = artifacts / "run" / "slide.png"
        png.parent.mkdir(parents=True, exist_ok=True)
        png.write_bytes(b"public-image")
        dbmod.update_run_slide(run_slide_id, final_image_path=str(png), status="completed")
        dbmod.create_artifact_version(
            target_run_slide_id=run_slide_id,
            artifact_run_slide_id=run_slide_id,
            status="completed",
            final_image_path=str(png),
            evidence_snapshot={
                "prompt": "PRIVATE_PROMPT",
                "config": {"api_key": "PRIVATE_KEY"},
                "response": {"raw": "PRIVATE_RESPONSE"},
            },
            make_active=True,
        )
        db = dbmod.get_db()
        db.execute("UPDATE runs SET status = 'completed' WHERE id = ?", (run_id,))
        db.execute("UPDATE batches SET status = 'completed' WHERE id = ?", (batch_id,))
        db.commit()
        db.close()

        run_response = client.get(f"/api/runs/{run_id}/download")
        batch_response = client.get(f"/api/batches/{batch_id}/download")
        slide_response = client.get(f"/api/run-slides/{run_slide_id}/evidence-download")

        def inspect_zip(response):
            with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
                names = sorted(archive.namelist())
                payload = b"\\n".join(archive.read(name) for name in names).decode("utf-8")
            return names, payload

        run_names, run_payload = inspect_zip(run_response)
        batch_names, batch_payload = inspect_zip(batch_response)
        print("PUBLIC_RESULT:" + json.dumps({
            "statuses": [run_response.status_code, batch_response.status_code, slide_response.status_code],
            "run_names": run_names,
            "batch_names": batch_names,
            "leaks": [
                secret
                for secret in ["PRIVATE_REQ", "PRIVATE_COLOR", "PRIVATE_PROMPT", "PRIVATE_KEY", "PRIVATE_RESPONSE"]
                if secret in run_payload or secret in batch_payload
            ],
        }))
        """,
    )

    assert result["statuses"] == [200, 200, 404]
    assert result["leaks"] == []
    assert result["run_names"] == ["manifest.json", "slides/slide-01.png"]
    assert result["batch_names"] == ["manifest.json", "runs/run-1/slides/slide-01.png"]


def test_public_evidence_zip_recovers_only_audit_bound_native_public_snapshot(tmp_path):
    result = _run_public_case(
        tmp_path,
        """
        import hashlib
        import io
        import zipfile

        from backend.services import codex_audit

        artifacts = Path(public_server.server.ARTIFACTS_DIR)
        public_config = dbmod.get_config_by_name(model_profiles.NATIVE_IMAGE_3_0_CONFIG_NAME)
        hidden_config = dbmod.get_config_by_name(model_profiles.NATIVE_IMAGE_DIRECT_CONFIG_NAME)
        requirement_id = dbmod.create_requirement("__public_empty_requirement__", "")
        color_id = dbmod.create_color("__public_empty_color__", "")

        def make_run(config, *, strategy, label):
            deck_id = dbmod.create_deck(label, "# Cover")
            slide_id = dbmod.create_slide(deck_id, 1, "Cover", "Content")
            batch_id = dbmod.create_batch(
                deck_id, config["id"], [requirement_id], [color_id], total_runs=1,
            )
            run_id = dbmod.create_run(
                deck_id, requirement_id, color_id, config["id"], batch_id=batch_id,
                engine="image", strategy=strategy,
            )
            run_slide_id = dbmod.create_run_slide(run_id, slide_id, 1, "cover")
            png = artifacts / label / "slide.png"
            png.parent.mkdir(parents=True, exist_ok=True)
            png.write_bytes(f"PNG::{label}".encode("utf-8"))
            dbmod.update_run_slide(run_slide_id, final_image_path=str(png), status="completed")
            db = dbmod.get_db()
            try:
                db.execute("UPDATE runs SET status = 'completed' WHERE id = ?", (run_id,))
                db.execute("UPDATE batches SET status = 'completed' WHERE id = ?", (batch_id,))
                db.commit()
            finally:
                db.close()
            return run_id, run_slide_id, png

        def set_native_public_only_snapshot(run_slide_id, png, *, terminal_state="result_received"):
            projection = {
                "requested_model": "gpt-5.6-luna",
                "actual_model": "gpt-5.6-luna",
                "requested_reasoning_effort": "low",
                "actual_reasoning_effort": "low",
                "cli_version": "test-cli",
                "binary_sha256": "test-binary",
                "attempt": 1,
                "terminal_state": terminal_state,
                "retry": False,
                "timeout": False,
                "skip": False,
                "fallback_used": False,
                "failure_code": None,
                "business_image": {
                    "png_valid": True,
                    "width": 16,
                    "height": 9,
                    "bytes": len(png.read_bytes()),
                    "sha256": hashlib.sha256(png.read_bytes()).hexdigest(),
                },
            }
            active = dbmod.get_active_artifact_version(run_slide_id)
            assert active is not None
            stage_artifacts = {"image": {"native_public": projection}}
            snapshot = {"slide_stage_artifacts": stage_artifacts}
            db = dbmod.get_db()
            try:
                db.execute(
                    "UPDATE artifact_versions SET evidence_snapshot = ? WHERE id = ?",
                    (json.dumps(snapshot), active["id"]),
                )
                db.execute(
                    "UPDATE run_slides SET stage_artifacts = ? WHERE id = ?",
                    (json.dumps(stage_artifacts), run_slide_id),
                )
                db.commit()
            finally:
                db.close()
            return projection

        def add_typed_native_audit(run_id, run_slide_id, projection, *, private_marker):
            metadata = {
                "native_evidence_discriminator": "codex_native_image_private_v1",
                **projection,
                "business_image": {
                    **projection["business_image"],
                    "path": f"/PRIVATE_AUDIT_PATH/{private_marker}/slide.png",
                },
                "canonical_session": {
                    "archive_path": f"/PRIVATE_AUDIT_PATH/{private_marker}/session.jsonl",
                },
            }
            return dbmod.create_codex_invocation(
                run_id=run_id,
                run_slide_id=run_slide_id,
                stage_id="image-generation",
                role="image_generator",
                status="result_received",
                metadata=metadata,
            )

        legal_run, legal_slide, legal_png = make_run(
            public_config, strategy="image_3_0", label="legal",
        )
        legal_projection = set_native_public_only_snapshot(legal_slide, legal_png)
        legal_invocation = add_typed_native_audit(
            legal_run, legal_slide, legal_projection, private_marker="LEGAL",
        )

        # Future writes must persist the same safe marker at artifact-version
        # creation time, rather than requiring a later public download repair.
        future_deck = dbmod.create_deck("future", "# Cover")
        future_slide_id = dbmod.create_slide(future_deck, 1, "Cover", "Content")
        future_batch = dbmod.create_batch(
            future_deck, public_config["id"], [requirement_id], [color_id], total_runs=1,
        )
        future_run = dbmod.create_run(
            future_deck, requirement_id, color_id, public_config["id"], batch_id=future_batch,
            engine="image", strategy="image_3_0",
        )
        future_slide = dbmod.create_run_slide(future_run, future_slide_id, 1, "cover")
        future_png = artifacts / "future" / "slide.png"
        future_png.parent.mkdir(parents=True, exist_ok=True)
        future_png.write_bytes(legal_png.read_bytes())
        future_stage_artifacts = {"image": {"native_public": legal_projection}}
        dbmod.update_run_slide(
            future_slide, stage_artifacts=json.dumps(future_stage_artifacts), status="running",
        )
        add_typed_native_audit(
            future_run, future_slide, legal_projection, private_marker="FUTURE",
        )
        dbmod.update_run_slide(
            future_slide,
            final_image_path=str(future_png),
            stage_artifacts=json.dumps(future_stage_artifacts),
            status="completed",
        )
        future_marker = codex_audit.find_nested_native_private_evidence(
            dbmod.get_active_artifact_version(future_slide)["evidence_snapshot"]
        )

        mismatch_run, mismatch_slide, mismatch_png = make_run(
            public_config, strategy="image_3_0", label="mismatch",
        )
        mismatch_projection = set_native_public_only_snapshot(
            mismatch_slide, mismatch_png, terminal_state="mismatch",
        )
        audit_projection = dict(mismatch_projection)
        audit_projection["terminal_state"] = "result_received"
        add_typed_native_audit(
            mismatch_run, mismatch_slide, audit_projection, private_marker="MISMATCH",
        )

        hidden_run, hidden_slide, hidden_png = make_run(
            hidden_config, strategy="image_direct", label="hidden",
        )
        hidden_projection = set_native_public_only_snapshot(hidden_slide, hidden_png)
        add_typed_native_audit(
            hidden_run, hidden_slide, hidden_projection, private_marker="HIDDEN",
        )
        dbmod.create_artifact_version(
            target_run_slide_id=legal_slide,
            artifact_run_slide_id=hidden_slide,
            status="completed",
            final_image_path=str(hidden_png),
            evidence_snapshot={"slide_stage_artifacts": {"image": {"native_public": hidden_projection}}},
            make_active=True,
        )
        hidden_source_response = client.get(f"/api/run-slides/{legal_slide}/evidence-download")

        # Restore the legal source after proving that an active hidden source
        # cannot be made public merely by presenting a typed audit record.
        dbmod.create_artifact_version(
            target_run_slide_id=legal_slide,
            artifact_run_slide_id=legal_slide,
            status="completed",
            final_image_path=str(legal_png),
            evidence_snapshot={"slide_stage_artifacts": {"image": {"native_public": legal_projection}}},
            make_active=True,
        )
        legacy_before = dbmod.get_active_artifact_version(legal_slide)["evidence_snapshot"]
        def forbidden_legacy_repair(*_args, **_kwargs):
            raise AssertionError("legacy evidence GET must not persist a snapshot repair")
        dbmod.update_artifact_version_evidence_snapshot = forbidden_legacy_repair
        legal_response = client.get(f"/api/run-slides/{legal_slide}/evidence-download")
        repeat_response = client.get(f"/api/run-slides/{legal_slide}/evidence-download")
        mismatch_response = client.get(f"/api/run-slides/{mismatch_slide}/evidence-download")

        names = []
        payload = ""
        if legal_response.status_code == 200:
            with zipfile.ZipFile(io.BytesIO(legal_response.data)) as archive:
                names = sorted(archive.namelist())
                payload = b"\\n".join(archive.read(name) for name in names).decode("utf-8")
        legacy_after = dbmod.get_active_artifact_version(legal_slide)["evidence_snapshot"]
        marker = codex_audit.find_nested_native_private_evidence(legacy_after)
        print("PUBLIC_RESULT:" + json.dumps({
            "statuses": [legal_response.status_code, repeat_response.status_code, mismatch_response.status_code, hidden_source_response.status_code],
            "names": names,
            "payload": payload,
            "legal_invocation": legal_invocation,
            "marker": marker,
            "future_marker": future_marker,
            "legacy_before": legacy_before,
            "legacy_after": legacy_after,
        }))
        """,
    )

    assert result["statuses"] == [200, 200, 404, 404]
    assert result["names"] == [
        "active_artifact/slide.png",
        "manifest.json",
        "native_audit/projection.json",
    ]
    assert result["marker"] is None
    assert result["legacy_after"] == result["legacy_before"]
    marker_text = json.dumps(result["marker"], sort_keys=True)
    assert "PRIVATE_AUDIT_PATH" not in marker_text
    assert "PRIVATE_AUDIT_PATH" not in result["payload"]
    assert result["future_marker"] is not None
    assert "PRIVATE_AUDIT_PATH" not in json.dumps(result["future_marker"], sort_keys=True)


def test_public_surface_rejects_active_artifacts_sourced_from_hidden_run_slides(tmp_path):
    result = _run_public_case(
        tmp_path,
        """
        import io
        import zipfile

        artifacts = Path(public_server.server.ARTIFACTS_DIR)
        deck_id = dbmod.create_deck("Deck", "# Cover")
        slide_id = dbmod.create_slide(deck_id, 1, "Cover", "Content")
        requirement_id = dbmod.create_requirement("__public_empty_requirement__", "")
        color_id = dbmod.create_color("__public_empty_color__", "")
        public_config = dbmod.get_config_by_name(model_profiles.NATIVE_IMAGE_3_0_CONFIG_NAME)
        hidden_config = dbmod.get_config_by_name(model_profiles.NATIVE_IMAGE_DIRECT_CONFIG_NAME)
        public_batch = dbmod.create_batch(
            deck_id, public_config["id"], [requirement_id], [color_id], total_runs=1,
        )
        public_run = dbmod.create_run(
            deck_id, requirement_id, color_id, public_config["id"], batch_id=public_batch,
            engine="image", strategy="image_3_0",
        )
        public_slide = dbmod.create_run_slide(public_run, slide_id, 1, "cover")
        hidden_run = dbmod.create_run(
            deck_id, requirement_id, color_id, hidden_config["id"],
            engine="image", strategy="image_direct",
        )
        hidden_slide = dbmod.create_run_slide(hidden_run, slide_id, 1, "cover")

        public_png = artifacts / "public-target" / "slide.png"
        hidden_png = artifacts / "hidden-source" / "slide.png"
        public_png.parent.mkdir(parents=True, exist_ok=True)
        hidden_png.parent.mkdir(parents=True, exist_ok=True)
        public_png.write_bytes(b"public-target")
        hidden_png.write_bytes(b"PRIVATE_HIDDEN_SOURCE")
        dbmod.update_run_slide(public_slide, final_image_path=str(public_png), status="completed")
        dbmod.update_run_slide(hidden_slide, final_image_path=str(hidden_png), status="completed")
        dbmod.create_artifact_version(
            target_run_slide_id=public_slide,
            artifact_run_slide_id=hidden_slide,
            status="completed",
            final_image_path=str(hidden_png),
            evidence_snapshot={
                "native_evidence_discriminator": "codex_native_image_private_v1",
            },
            make_active=True,
        )
        db = dbmod.get_db()
        db.execute("UPDATE runs SET status = 'completed' WHERE id IN (?, ?)", (public_run, hidden_run))
        db.execute("UPDATE batches SET status = 'completed' WHERE id = ?", (public_batch,))
        db.commit()
        db.close()

        detail_response = client.get(f"/api/runs/{public_run}")
        evidence_response = client.get(f"/api/run-slides/{public_slide}/evidence-download")
        artifact_response = client.get("/artifacts/hidden-source/slide.png")
        run_download = client.get(f"/api/runs/{public_run}/download")
        batch_download = client.get(f"/api/batches/{public_batch}/download")

        def inspect_zip(response):
            with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
                names = sorted(archive.namelist())
                payload = b"\\n".join(archive.read(name) for name in names)
            return names, "PRIVATE_HIDDEN_SOURCE" in payload.decode("utf-8")

        run_names, run_leak = inspect_zip(run_download)
        batch_names, batch_leak = inspect_zip(batch_download)
        print("PUBLIC_RESULT:" + json.dumps({
            "detail_status": detail_response.status_code,
            "detail": detail_response.get_json(),
            "evidence_status": evidence_response.status_code,
            "artifact_status": artifact_response.status_code,
            "download_statuses": [run_download.status_code, batch_download.status_code],
            "run_names": run_names,
            "batch_names": batch_names,
            "leaks": [run_leak, batch_leak],
        }))
        """,
    )

    assert result["detail_status"] == 200
    assert result["detail"]["slides"][0]["final_image_path"] is None
    assert result["evidence_status"] == 404
    assert result["artifact_status"] == 404
    assert result["download_statuses"] == [200, 200]
    assert result["run_names"] == ["manifest.json"]
    assert result["batch_names"] == ["manifest.json"]
    assert result["leaks"] == [False, False]


def test_public_surface_ownership_oracle_uses_distinct_target_and_source_ids(tmp_path):
    result = _run_public_case(
        tmp_path,
        """
        import hashlib
        import io
        import zipfile

        artifacts = Path(public_server.server.ARTIFACTS_DIR)
        artifacts.mkdir(parents=True, exist_ok=True)

        # The old fixture's sequential 1/2 IDs could make a wrong ownership
        # comparison look correct.  Offset each table deliberately, then
        # assert the IDs are pairwise distinct before exercising the routes.
        filler_deck = dbmod.create_deck("Filler Deck", "# Filler")
        filler_slide = dbmod.create_slide(filler_deck, 1, "Filler", "Filler content")
        filler_requirement = dbmod.create_requirement("__filler_requirement__", "")
        filler_color = dbmod.create_color("__filler_color__", "")
        hidden_config = dbmod.get_config_by_name(model_profiles.NATIVE_IMAGE_DIRECT_CONFIG_NAME)
        for _ in range(7):
            filler_run = dbmod.create_run(
                filler_deck,
                filler_requirement,
                filler_color,
                hidden_config["id"],
                engine="html",
                strategy="html_default",
            )
            dbmod.create_run_slide(filler_run, filler_slide, 1, "cover")
        for position in range(2, 5):
            dbmod.create_run_slide(filler_run, filler_slide, position, "content")

        deck_id = dbmod.create_deck("Deck", "# Cover")
        slide_id = dbmod.create_slide(deck_id, 1, "Cover", "Content")
        requirement_id = dbmod.create_requirement("__public_empty_requirement__", "")
        color_id = dbmod.create_color("__public_empty_color__", "")
        public_config = dbmod.get_config_by_name(model_profiles.NATIVE_IMAGE_3_0_CONFIG_NAME)
        source_batch = dbmod.create_batch(
            deck_id, public_config["id"], [requirement_id], [color_id], total_runs=1,
        )
        source_run = dbmod.create_run(
            deck_id, requirement_id, color_id, public_config["id"], batch_id=source_batch,
            engine="image", strategy="image_3_0",
        )
        source_slide = dbmod.create_run_slide(source_run, slide_id, 1, "cover")
        target_batch = dbmod.create_batch(
            deck_id, public_config["id"], [requirement_id], [color_id], total_runs=1,
        )
        target_run = dbmod.create_run(
            deck_id, requirement_id, color_id, public_config["id"], batch_id=target_batch,
            engine="image", strategy="image_3_0",
        )
        target_slide = dbmod.create_run_slide(target_run, slide_id, 1, "cover")
        hidden_run = dbmod.create_run(
            deck_id, requirement_id, color_id, hidden_config["id"],
            engine="image", strategy="image_direct",
        )
        hidden_slide = dbmod.create_run_slide(hidden_run, slide_id, 1, "cover")
        ids = {
            "source_run": source_run,
            "target_run": target_run,
            "target_slide": target_slide,
            "source_slide": source_slide,
            "hidden_run": hidden_run,
            "hidden_slide": hidden_slide,
        }
        assert len(ids) == len(set(ids.values())), ids

        public_source_png = artifacts / "public-source" / "slide.png"
        public_target_png = artifacts / "public-target" / "slide.png"
        hidden_png = artifacts / "hidden-source" / "slide.png"
        public_source_png.parent.mkdir(parents=True, exist_ok=True)
        public_target_png.parent.mkdir(parents=True, exist_ok=True)
        hidden_png.parent.mkdir(parents=True, exist_ok=True)
        public_source_png.write_bytes(b"PUBLIC_ACTIVE_SOURCE")
        public_target_png.write_bytes(b"PUBLIC_TARGET_FALLBACK")
        hidden_png.write_bytes(b"PRIVATE_HIDDEN_SOURCE")
        native_public = {
            "requested_model": "gpt-5.6-luna",
            "actual_model": "gpt-5.6-luna",
            "requested_reasoning_effort": "low",
            "actual_reasoning_effort": "low",
            "cli_version": "test-cli",
            "binary_sha256": "test-binary",
            "attempt": 1,
            "terminal_state": "result_received",
            "retry": False,
            "timeout": False,
            "skip": False,
            "fallback_used": False,
            "failure_code": None,
            "business_image": {
                "png_valid": True,
                "width": 16,
                "height": 9,
                "bytes": len(public_source_png.read_bytes()),
                "sha256": hashlib.sha256(public_source_png.read_bytes()).hexdigest(),
            },
        }
        source_stage_artifacts = {"image": {"native_public": native_public}}
        dbmod.update_run_slide(
            source_slide,
            final_image_path=str(public_source_png),
            stage_artifacts=json.dumps(source_stage_artifacts),
            status="completed",
        )
        dbmod.update_run_slide(target_slide, final_image_path=str(public_target_png), status="completed")
        dbmod.update_run_slide(hidden_slide, final_image_path=str(hidden_png), status="completed")
        native_snapshot = {
            "slide_stage_artifacts": source_stage_artifacts,
        }
        dbmod.create_codex_invocation(
            run_id=source_run,
            run_slide_id=source_slide,
            stage_id="image-generation",
            role="image_generator",
            status="result_received",
            metadata={
                "native_evidence_discriminator": "codex_native_image_private_v1",
                **native_public,
                "business_image": {
                    **native_public["business_image"],
                    "path": "/PRIVATE_AUDIT/source.png",
                },
                "private_payload": "PRIVATE_AUDIT",
            },
        )
        dbmod.create_artifact_version(
            target_run_slide_id=target_slide,
            artifact_run_slide_id=source_slide,
            status="completed",
            final_image_path=str(public_source_png),
            evidence_snapshot=native_snapshot,
            make_active=True,
        )
        db = dbmod.get_db()
        db.execute(
            "UPDATE runs SET status = 'completed' WHERE id IN (?, ?, ?)",
            (source_run, target_run, hidden_run),
        )
        db.execute(
            "UPDATE batches SET status = 'completed' WHERE id IN (?, ?)",
            (source_batch, target_batch),
        )
        db.commit()
        db.close()

        def inspect_zip(response):
            if response.status_code != 200:
                return [], ""
            with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
                names = sorted(archive.namelist())
                payload = b"\\n".join(archive.read(name) for name in names).decode("utf-8", "ignore")
            return names, payload

        legal_detail = client.get(f"/api/runs/{target_run}")
        legal_artifact = client.get("/artifacts/public-source/slide.png")
        legal_evidence = client.get(f"/api/run-slides/{target_slide}/evidence-download")
        legal_run_download = client.get(f"/api/runs/{target_run}/download")
        legal_batch_download = client.get(f"/api/batches/{target_batch}/download")
        legal_evidence_names, legal_evidence_payload = inspect_zip(legal_evidence)
        legal_run_names, legal_run_payload = inspect_zip(legal_run_download)
        legal_batch_names, legal_batch_payload = inspect_zip(legal_batch_download)

        # A public target may use a public source, and all source-derived
        # surfaces must expose the source PNG consistently.
        legal_detail_payload = legal_detail.get_json()
        assert legal_detail_payload["slides"][0]["final_image_path"] == "/artifacts/public-source/slide.png"

        # Switch the same target to a hidden source.  The target remains a
        # public run, but hidden bytes and paths must not cross any boundary.
        dbmod.create_artifact_version(
            target_run_slide_id=target_slide,
            artifact_run_slide_id=hidden_slide,
            status="completed",
            final_image_path=str(hidden_png),
            evidence_snapshot=native_snapshot,
            make_active=True,
        )
        hidden_direct = [
            client.get(f"/api/runs/{hidden_run}"),
            client.get(f"/api/runs/{hidden_run}/status"),
            client.get(f"/api/runs/{hidden_run}/download"),
            client.get(f"/api/run-slides/{hidden_slide}/evidence-download"),
            client.get("/artifacts/hidden-source/slide.png"),
        ]
        target_detail = client.get(f"/api/runs/{target_run}")
        target_artifact = client.get("/artifacts/public-target/slide.png")
        target_evidence = client.get(f"/api/run-slides/{target_slide}/evidence-download")
        target_run_download = client.get(f"/api/runs/{target_run}/download")
        target_batch_download = client.get(f"/api/batches/{target_batch}/download")
        target_run_names, target_run_payload = inspect_zip(target_run_download)
        target_batch_names, target_batch_payload = inspect_zip(target_batch_download)

        print("PUBLIC_RESULT:" + json.dumps({
            "ids": ids,
            "legal": {
                "statuses": [
                    legal_detail.status_code,
                    legal_artifact.status_code,
                    legal_evidence.status_code,
                    legal_run_download.status_code,
                    legal_batch_download.status_code,
                ],
                "detail_path": legal_detail_payload["slides"][0]["final_image_path"],
                "artifact": legal_artifact.data.decode("utf-8"),
                "evidence_names": legal_evidence_names,
                "run_names": legal_run_names,
                "batch_names": legal_batch_names,
                "payload": legal_evidence_payload + legal_run_payload + legal_batch_payload,
            },
            "hidden_direct_statuses": [response.status_code for response in hidden_direct],
            "target_after_hidden": {
                "detail_status": target_detail.status_code,
                "detail": target_detail.get_json(),
                "artifact_status": target_artifact.status_code,
                "evidence_status": target_evidence.status_code,
                "download_statuses": [target_run_download.status_code, target_batch_download.status_code],
                "run_names": target_run_names,
                "batch_names": target_batch_names,
                "payload": target_run_payload + target_batch_payload,
            },
        }))
        """,
    )

    ids = result["ids"]
    assert len(ids) == len(set(ids.values()))
    assert result["legal"]["statuses"] == [200, 200, 200, 200, 200]
    assert result["legal"]["detail_path"] == "/artifacts/public-source/slide.png"
    assert result["legal"]["artifact"] == "PUBLIC_ACTIVE_SOURCE"
    assert result["legal"]["evidence_names"] == [
        "active_artifact/slide.png",
        "manifest.json",
        "native_audit/projection.json",
    ]
    assert result["legal"]["run_names"] == ["manifest.json", "slides/slide-01.png"]
    assert result["legal"]["batch_names"] == [
        "manifest.json",
        f"runs/run-{ids['target_run']}/slides/slide-01.png",
    ]
    assert "PRIVATE_HIDDEN_SOURCE" not in result["legal"]["payload"]
    assert result["hidden_direct_statuses"] == [404, 404, 404, 404, 404]
    target_after_hidden = result["target_after_hidden"]
    assert target_after_hidden["detail_status"] == 200
    assert target_after_hidden["detail"]["slides"][0]["final_image_path"] is None
    assert target_after_hidden["artifact_status"] == 404
    assert target_after_hidden["evidence_status"] == 404
    assert target_after_hidden["download_statuses"] == [200, 200]
    assert target_after_hidden["run_names"] == ["manifest.json"]
    assert target_after_hidden["batch_names"] == ["manifest.json"]
    assert "PRIVATE_HIDDEN_SOURCE" not in target_after_hidden["payload"]
    assert "/artifacts/hidden-source/slide.png" not in target_after_hidden["payload"]


def test_public_generate_rejects_every_non_contract_payload_before_any_side_effect(tmp_path):
    result = _run_public_case(
        tmp_path,
        """
        from backend.services import public_image_surface

        two_slide_deck = dbmod.create_deck("Two Slides", "# Cover\\n# Seed")
        dbmod.create_slide(two_slide_deck, 1, "Cover", "Cover content")
        dbmod.create_slide(two_slide_deck, 2, "Seed", "Seed content")
        one_slide_deck = dbmod.create_deck("One Slide", "# Cover")
        dbmod.create_slide(one_slide_deck, 1, "Cover", "Cover content")
        zero_slide_deck = dbmod.create_deck("No Slides", "# Cover")
        public_configs = public_image_surface.public_configs()
        assert len(public_configs) == 2
        public_config_id = public_configs[0]["id"]
        hidden_config = dbmod.get_config_by_name(model_profiles.NATIVE_IMAGE_DIRECT_CONFIG_NAME)
        assert hidden_config is not None

        launcher_ledger = []
        def forbidden_launcher(run_ids, db_path, max_concurrent_runs):
            launcher_ledger.append({
                "run_ids": list(run_ids),
                "db_path": db_path,
                "max_concurrent_runs": max_concurrent_runs,
            })
        public_server.server.launch_batch_runs = forbidden_launcher

        valid = {
            "deck_id": two_slide_deck,
            "config_id": public_config_id,
            "engine": "image",
            "strategy": "image_3_0",
            "requirement_ids": [],
            "color_ids": [],
        }
        cases = []
        for field in valid:
            payload = dict(valid)
            payload.pop(field)
            cases.append((f"missing:{field}", payload))
        for extra_key, extra_value in [
            ("mode", "manual"),
            ("route_metadata", {}),
            ("designer_prompt_id", 1),
            ("html_prompt_id", 1),
            ("prompt_id", 1),
            ("model", "gpt-5.6-luna"),
            ("model_id", 1),
            ("profile", "native"),
            ("profile_id", 1),
            ("renderer", "banana"),
            ("image_renderer", "banana"),
            ("native_image", {"adapter": "codex_native_image"}),
            ("native_route", "image_3_0"),
            ("native_image_route", "image_3_0"),
            ("route", "image_3_0"),
            ("unknown", True),
        ]:
            payload = dict(valid)
            payload[extra_key] = extra_value
            cases.append((f"extra:{extra_key}", payload))
        for engine, strategy in [
            ("html", "html_default"),
            ("html", "image_3_0"),
            ("image", "html_default"),
            ("image", "image_1_0"),
            ("image", "image_3_2"),
            ("image", "image_5_0"),
            ("image", "image_direct"),
            ("banana", "image_3_0"),
            ("image", "unknown"),
        ]:
            payload = dict(valid)
            payload.update({"engine": engine, "strategy": strategy})
            cases.append((f"route:{engine}/{strategy}", payload))
        for field, value in [
            ("requirement_ids", [1]),
            ("color_ids", [1]),
            ("requirement_ids", "not-an-array"),
            ("color_ids", "not-an-array"),
            ("requirement_ids", None),
            ("color_ids", None),
        ]:
            payload = dict(valid)
            payload[field] = value
            cases.append((f"dimension:{field}={value!r}", payload))
        for field, value in [
            ("deck_id", True),
            ("deck_id", False),
            ("deck_id", "1"),
            ("deck_id", 1.5),
            ("deck_id", 0),
            ("deck_id", -1),
            ("deck_id", 999999),
            ("config_id", True),
            ("config_id", False),
            ("config_id", "1"),
            ("config_id", 1.5),
            ("config_id", 0),
            ("config_id", -1),
            ("config_id", 999999),
            ("config_id", hidden_config["id"]),
        ]:
            payload = dict(valid)
            payload[field] = value
            cases.append((f"identity:{field}={value!r}", payload))
        for deck_id in (zero_slide_deck, one_slide_deck):
            payload = dict(valid)
            payload["deck_id"] = deck_id
            cases.append((f"slides:{deck_id}", payload))

        def snapshot():
            db = dbmod.get_db()
            try:
                return {
                    table: db.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()["count"]
                    for table in ("batches", "runs", "run_slides", "requirements", "colors")
                }
            finally:
                db.close()

        before = snapshot()
        observations = []
        for label, payload in cases:
            response = client.post("/api/generate", json=payload)
            observations.append({
                "label": label,
                "status": response.status_code,
                "body": response.get_json(silent=True),
            })
        after = snapshot()
        print("PUBLIC_RESULT:" + json.dumps({
            "case_count": len(cases),
            "observations": observations,
            "before": before,
            "after": after,
            "launcher_ledger": launcher_ledger,
        }))
        """,
    )

    assert result["case_count"] >= 50
    assert all(item["status"] == 404 for item in result["observations"]), result["observations"]
    assert all(item["body"] == {"error": "Not found"} for item in result["observations"])
    assert result["after"] == result["before"]
    assert result["launcher_ledger"] == []


def test_public_generate_accepts_only_two_verified_configs_and_creates_one_native_run(tmp_path):
    result = _run_public_case(
        tmp_path,
        """
        from backend.services import public_image_surface

        deck_id = dbmod.create_deck("Two Slides", "# Cover\\n# Seed")
        dbmod.create_slide(deck_id, 1, "Cover", "Cover content")
        dbmod.create_slide(deck_id, 2, "Seed", "Seed content")
        configs = public_image_surface.public_configs()
        assert len(configs) == 2
        def count_batches(deck, config):
            db = dbmod.get_db()
            try:
                return db.execute(
                    "SELECT COUNT(*) AS count FROM batches WHERE deck_id = ? AND config_id = ?",
                    (deck, config),
                ).fetchone()["count"]
            finally:
                db.close()
        def count_runs(deck):
            db = dbmod.get_db()
            try:
                return db.execute(
                    "SELECT COUNT(*) AS count FROM runs WHERE deck_id = ?", (deck,)
                ).fetchone()["count"]
            finally:
                db.close()
        launcher_ledger = []
        def capture_launcher(run_ids, db_path, max_concurrent_runs):
            launcher_ledger.append({
                "run_ids": list(run_ids),
                "db_path": db_path,
                "max_concurrent_runs": max_concurrent_runs,
            })
        public_server.server.launch_batch_runs = capture_launcher

        results = []
        for config in configs:
            response = client.post("/api/generate", json={
                "deck_id": deck_id,
                "config_id": config["id"],
                "engine": "image",
                "strategy": "image_3_0",
                "requirement_ids": [],
                "color_ids": [],
            })
            payload = response.get_json()
            run_id = payload["run_ids"][0] if payload and payload.get("run_ids") else None
            run = dbmod.get_run(run_id) if run_id else None
            run_slides = dbmod.list_run_slides(run_id) if run_id else []
            results.append({
                "status": response.status_code,
                "payload": payload,
                "run": run,
                "run_slide_count": len(run_slides),
                "run_slide_ids": [slide["slide_id"] for slide in run_slides],
                "batches_for_config": count_batches(deck_id, config["id"]),
            })
        db = dbmod.get_db()
        try:
            sentinel_names = [
                row["title"]
                for row in db.execute(
                    "SELECT title FROM requirements WHERE title = 'System Empty Requirement'"
                ).fetchall()
            ] + [
                row["title"]
                for row in db.execute(
                    "SELECT title FROM colors WHERE title = 'System Empty Color'"
                ).fetchall()
            ]
        finally:
            db.close()
        print("PUBLIC_RESULT:" + json.dumps({
            "configs": [config["id"] for config in configs],
            "results": results,
            "launcher_ledger": launcher_ledger,
            "sentinel_names": sentinel_names,
            "batches": sum(item["batches_for_config"] for item in results),
            "runs": count_runs(deck_id),
        }))
        """,
    )

    assert result["configs"] and len(result["configs"]) == 2
    assert [item["status"] for item in result["results"]] == [202, 202]
    assert [item["batches_for_config"] for item in result["results"]] == [1, 1]
    assert result["batches"] == 2
    assert result["runs"] == 2
    assert result["launcher_ledger"] and len(result["launcher_ledger"]) == 2
    assert all(len(item["run_ids"]) == 1 for item in result["launcher_ledger"])
    assert all(item["payload"]["total_runs"] == 1 for item in result["results"])
    assert all(item["payload"]["slides_per_run"] == 2 for item in result["results"])
    assert all(item["run"]["engine"] == "image" for item in result["results"])
    assert all(item["run"]["strategy"] == "image_3_0" for item in result["results"])
    assert all(item["run_slide_count"] == 2 for item in result["results"])
    assert all(item["run_slide_ids"] == [1, 2] for item in result["results"])
    assert all("System Empty" not in json.dumps(item["payload"]) for item in result["results"])
