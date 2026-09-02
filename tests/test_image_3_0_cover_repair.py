import json
import sys
import threading
import time
from dataclasses import fields, is_dataclass
from hashlib import sha256
from pathlib import Path
from typing import Literal, get_type_hints

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
TOOLKIT_SRC = ROOT / "packages" / "pptgen_toolkit" / "src"
if str(TOOLKIT_SRC) not in sys.path:
    sys.path.insert(0, str(TOOLKIT_SRC))

import db as dbmod
import pipeline
import server
from backend.services import color_extraction
from pptgen_toolkit.cli import _result_projection


PALETTE_XML = (
    '<pptPalette><textBackground>'
    '<color name="Dark Text 1" hex="#111111" rgb="17,17,17" luminance="1" />'
    '<color name="Light Background 1" hex="#FFFFFF" rgb="255,255,255" luminance="100" />'
    '<color name="Dark Background 2" hex="#002B36" rgb="0,43,54" luminance="2" />'
    '<color name="Light Text 2" hex="#FDF6E3" rgb="253,246,227" luminance="92" />'
    '</textBackground><accents>'
    '<color name="Accent 1" hex="#C94132" rgb="201,65,50" luminance="16" />'
    '<color name="Accent 2" hex="#D4B258" rgb="212,178,88" luminance="47" />'
    '<color name="Accent 3" hex="#21667B" rgb="33,102,123" luminance="11" />'
    '<color name="Accent 4" hex="#E89999" rgb="232,153,153" luminance="42" />'
    '<color name="Accent 5" hex="#E67E39" rgb="230,126,57" luminance="32" />'
    '<color name="Accent 6" hex="#6B4E3E" rgb="107,78,62" luminance="9" />'
    '</accents></pptPalette>'
)
PALETTE_COLORS = (
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
)
SEED_PNG_BYTES = b"phase-c-image-3-0-position-two-seed"
SEED_STYLE_DNA = "phase-c-seed-style-dna"


def test_native_image_3_0_palette_uses_canonical_baseline_validator_and_pass_through():
    validator = color_extraction.validate_palette_xml
    assert validator(PALETTE_XML) == PALETTE_XML
    assert validator(f"```xml\n{PALETTE_XML}\n```") == PALETTE_XML

    invalid_palettes = (
        "<pptPalette><textBackground /></pptPalette>",
        "<pptPalette><textBackground><shape /></textBackground><accents /></pptPalette>",
        PALETTE_XML.replace('name="Accent 1"', 'name="Accent 1" extra="no"', 1),
    )
    for invalid_palette in invalid_palettes:
        with pytest.raises(color_extraction.ColorExtractionError):
            validator(invalid_palette)


@pytest.fixture()
def isolated_app(tmp_path, monkeypatch):
    test_db = tmp_path / "ppt-test.db"
    monkeypatch.setattr(dbmod, "DB_PATH", test_db)
    app = server.create_app()
    app.config.update(TESTING=True)
    return app


def _seed_native_image_3_0_run() -> dict:
    from backend.services import generation, model_profiles

    deck_id = dbmod.create_deck(
        "Phase C Image 3.0",
        "# Cover\n# Position 2 Seed\n# Position 3\n# Position 4",
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
    native_config = dbmod.get_config_by_name(model_profiles.NATIVE_IMAGE_3_0_CONFIG_NAME)
    assert native_config is not None
    plan = generation.build_generation_plan(
        {
            "deck_id": deck_id,
            "config_id": native_config["id"],
            "engine": "image",
            "strategy": "image_3_0",
            "requirement_ids": [],
            "color_ids": [],
        }
    )
    run_id = dbmod.create_run(
        deck_id,
        plan["requirement_id"],
        plan["color_id"],
        native_config["id"],
        engine="image",
        strategy="image_3_0",
        route_metadata=plan["route_metadata"],
    )
    run_slide_ids = {
        "cover": dbmod.create_run_slide(
            run_id, slide_ids["cover"], 1, slide_type="cover"
        ),
        "seed": dbmod.create_run_slide(
            run_id, slide_ids["seed"], 2, slide_type="content"
        ),
        "later_3": dbmod.create_run_slide(
            run_id, slide_ids["later_3"], 3, slide_type="content"
        ),
        "later_4": dbmod.create_run_slide(
            run_id, slide_ids["later_4"], 4, slide_type="content"
        ),
    }
    return {
        "run_id": run_id,
        "run_slide_ids": run_slide_ids,
        "position_by_run_slide_id": {
            run_slide_id: position
            for position, run_slide_id in enumerate(run_slide_ids.values(), start=1)
        },
    }


def _slides_by_position(run_id: int) -> dict[int, dict]:
    return {int(slide["position"]): slide for slide in dbmod.list_run_slides(run_id)}


def _native_result(
    *,
    prompt: str,
    output_path: Path,
    run_id: int,
    run_slide_id: int,
    stage_id: str,
    marker: str,
) -> dict:
    from backend.services import codex_native_image

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(
        SEED_PNG_BYTES if marker == "seed" else f"phase-c-{marker}".encode("utf-8")
    )
    active_prompt = dbmod.get_active_prompt("image_generator")
    assert active_prompt is not None
    prompt_content = str(active_prompt["content"])
    transport_prompt = codex_native_image._native_image_generator_transport_prompt(
        prompt_content=prompt_content,
        business_prompt=prompt,
        has_attached_references=False,
    )
    rendered_hash = sha256(transport_prompt.encode("utf-8")).hexdigest()
    dbmod.create_codex_invocation(
        run_id=run_id,
        run_slide_id=run_slide_id,
        stage_id=stage_id,
        role="image_generator",
        status="result_received",
        prompt_sha256=rendered_hash,
    )
    return {
        "response": {"image_path": str(output_path), "conversation_id": None},
        "native_public": {
            "attempt": 1,
            "terminal_state": "result_received",
            "business_image": {
                "path": str(output_path),
                "sha256": sha256(output_path.read_bytes()).hexdigest(),
            },
        },
        "native_prompt_lineage": {
            "role": "image_generator",
            "prompt_id": int(active_prompt["id"]),
            "prompt_content_sha256": sha256(
                prompt_content.encode("utf-8")
            ).hexdigest(),
            "rendered_prompt_sha256": rendered_hash,
        },
    }


def _install_successful_native_route(
    monkeypatch,
    tmp_path,
    fixture,
    *,
    palette_impl=None,
    director_impl=None,
    renderer_impl=None,
):
    from backend.services import codex_native_image

    run_id = fixture["run_id"]
    run_slide_ids = fixture["run_slide_ids"]
    positions = fixture["position_by_run_slide_id"]
    events: list[tuple[str, int | None]] = []
    director_calls: list[dict] = []
    renderer_calls: list[dict] = []
    palette_calls: list[dict] = []
    lock = threading.Lock()
    monkeypatch.setattr(pipeline, "ARTIFACTS_DIR", str(tmp_path / "artifacts"))

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
        position = positions[run_slide_id]
        call = {
            "prompt": prompt,
            "run_id": run_id,
            "run_slide_id": run_slide_id,
            "position": position,
            "stage_id": stage_id,
            "reference_image_paths": list(reference_image_paths or []),
        }
        with lock:
            director_calls.append(call)
            events.append(("director", position))
        if director_impl is not None:
            return director_impl(call)
        if stage_id == "cover-prompt-generation":
            return f"## Image prompt\n{prompt}", [
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
        position = positions[run_slide_id]
        call = {
            "prompt": prompt,
            "output_path": str(output_path),
            "run_id": run_id,
            "run_slide_id": run_slide_id,
            "position": position,
            "stage_id": stage_id,
            "reference_image_paths": list(reference_image_paths or []),
            "metadata": dict(metadata or {}),
        }
        with lock:
            renderer_calls.append(call)
            events.append(("renderer", position))
        if renderer_impl is not None:
            return renderer_impl(call)
        marker = "seed" if run_slide_id == run_slide_ids["seed"] else f"position-{position}"
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
        call = {
            "agent_config": dict(agent_config),
            "run_id": run_id,
            "run_slide_id": run_slide_id,
            "image_path": str(seed_png_path),
            "timeout_seconds": timeout_seconds,
        }
        with lock:
            palette_calls.append(call)
            events.append(("palette", 2))
        if palette_impl is not None:
            return palette_impl(call)
        return PALETTE_XML

    monkeypatch.setattr(pipeline, "run_native_image_three_zero_director", fake_director)
    monkeypatch.setattr(pipeline, "run_native_image_three_zero_palette", fake_palette)
    monkeypatch.setattr(codex_native_image, "generate_codex_native_image", fake_renderer)
    return {
        "events": events,
        "director_calls": director_calls,
        "renderer_calls": renderer_calls,
        "palette_calls": palette_calls,
        "lock": lock,
    }


def test_image_3_0_exposes_explicit_route_outcome_and_seed_palette_lineage_contract():
    assert is_dataclass(pipeline.ImageRouteOutcome)
    assert [field.name for field in fields(pipeline.ImageRouteOutcome)] == [
        "status",
        "reason",
        "completed_slide_ids",
        "failed_slide_ids",
    ]
    assert is_dataclass(pipeline.SeedPaletteLineage)
    assert [field.name for field in fields(pipeline.SeedPaletteLineage)] == [
        "run_id",
        "run_slide_id",
        "deck_position",
        "extraction_stage",
        "seed_png_sha256",
        "palette_sha256",
        "colors",
        "effective_color",
    ]
    outcome_hints = get_type_hints(pipeline.ImageRouteOutcome)
    assert outcome_hints["status"] == Literal[
        "completed", "completed_with_failures", "failed"
    ]
    assert outcome_hints["completed_slide_ids"] == tuple[int, ...]
    assert outcome_hints["failed_slide_ids"] == tuple[int, ...]
    lineage_hints = get_type_hints(pipeline.SeedPaletteLineage)
    assert lineage_hints["colors"] == tuple[str, ...]
    assert lineage_hints["effective_color"] == dict[str, str]
    assert (
        get_type_hints(pipeline.run_image_route)["return"]
        is pipeline.ImageRouteOutcome
    )


def test_image_3_0_persists_position_two_before_palette_and_drives_cover_without_png(
    isolated_app, tmp_path, monkeypatch
):
    fixture = _seed_native_image_3_0_run()
    seed_run_slide_id = fixture["run_slide_ids"]["seed"]
    palette_observation = {}

    def observe_palette(call):
        seed_slide = _slides_by_position(fixture["run_id"])[2]
        palette_observation.update(
            {
                "status": seed_slide["status"],
                "final_image_path": seed_slide["final_image_path"],
                "xml_raw": seed_slide["xml_raw"],
                "xml_clean": seed_slide["xml_clean"],
                "stage_artifacts": json.loads(seed_slide["stage_artifacts"]),
            }
        )
        assert seed_slide["id"] == seed_run_slide_id
        assert seed_slide["status"] == "completed"
        assert Path(seed_slide["final_image_path"]).read_bytes() == SEED_PNG_BYTES
        assert SEED_STYLE_DNA in seed_slide["xml_raw"]
        assert SEED_STYLE_DNA in seed_slide["xml_clean"]
        assert call["image_path"] == seed_slide["final_image_path"]
        return PALETTE_XML

    def concise_cover_director(call):
        if call["position"] == 1:
            return "## Image prompt\nMinimal cover composition.", [
                {"attempt": 1, "status": "result_received"}
            ]
        return (
            "<Visual>"
            f"<Deck_Style_DNA>{SEED_STYLE_DNA}</Deck_Style_DNA>"
            f"<Title>Position {call['position']}</Title>"
            "<Quality_Checklist>remove during cleanup</Quality_Checklist>"
            "</Visual>",
            [{"attempt": 1, "status": "result_received"}],
        )

    ledger = _install_successful_native_route(
        monkeypatch,
        tmp_path,
        fixture,
        palette_impl=observe_palette,
        director_impl=concise_cover_director,
    )

    pipeline.run_pipeline_from_db(fixture["run_id"], str(dbmod.DB_PATH))

    events = ledger["events"]
    assert events[0] == ("director", 2)
    assert events.index(("renderer", 2)) < events.index(("palette", 2))
    assert events.index(("palette", 2)) < events.index(("director", 1))
    assert events.index(("palette", 2)) < events.index(("director", 3))
    assert events.index(("palette", 2)) < events.index(("director", 4))
    assert palette_observation["status"] == "completed"

    seed_path = palette_observation["final_image_path"]
    seed_hash = sha256(SEED_PNG_BYTES).hexdigest()
    palette_hash = sha256(PALETTE_XML.encode("utf-8")).hexdigest()
    assert len(ledger["palette_calls"]) == 1
    assert ledger["palette_calls"][0]["image_path"] == seed_path
    assert ledger["palette_calls"][0]["agent_config"]["api_type"] == "codex_exec"
    assert ledger["palette_calls"][0]["agent_config"]["model"] == "gpt-5.6-sol"
    assert ledger["palette_calls"][0]["agent_config"]["thinking"] == "low"
    assert ledger["palette_calls"][0]["run_id"] == fixture["run_id"]
    assert ledger["palette_calls"][0]["run_slide_id"] == seed_run_slide_id
    cover_director = next(
        call for call in ledger["director_calls"] if call["position"] == 1
    )
    cover_renderer = next(
        call for call in ledger["renderer_calls"] if call["position"] == 1
    )
    assert all(color in cover_director["prompt"] for color in PALETTE_COLORS)
    assert seed_hash in cover_director["prompt"]
    assert palette_hash in cover_director["prompt"]
    assert seed_path not in cover_director["prompt"]
    assert SEED_PNG_BYTES.decode("utf-8") not in cover_director["prompt"]
    assert "re-extract" not in cover_director["prompt"].lower()
    assert cover_director["reference_image_paths"] == []
    assert cover_renderer["reference_image_paths"] == []
    assert cover_renderer["prompt"].startswith("Minimal cover composition.")
    assert all(color in cover_renderer["prompt"] for color in PALETTE_COLORS)
    assert seed_hash in cover_renderer["prompt"]
    assert palette_hash in cover_renderer["prompt"]
    assert seed_path not in json.dumps(cover_renderer, ensure_ascii=False)

    seed_xml = palette_observation["xml_clean"]
    for position in (3, 4):
        remaining_director = next(
            call
            for call in ledger["director_calls"]
            if call["position"] == position
        )
        remaining_renderer = next(
            call
            for call in ledger["renderer_calls"]
            if call["position"] == position
        )
        assert remaining_director["reference_image_paths"] == [seed_path]
        assert seed_xml in remaining_director["prompt"]
        assert SEED_STYLE_DNA in remaining_director["prompt"]
        assert [str(path) for path in remaining_renderer["reference_image_paths"]] == [
            seed_path
        ]
        assert remaining_renderer["metadata"]["seed_dependency"] == {
            "seed_slide_id": seed_run_slide_id,
            "seed_slide_position": 2,
        }
        assert seed_xml in remaining_renderer["metadata"]["reference_context"]
        assert SEED_STYLE_DNA in remaining_renderer["metadata"]["reference_context"]

        remaining_slide = _slides_by_position(fixture["run_id"])[position]
        remaining_artifacts = json.loads(remaining_slide["stage_artifacts"])
        assert remaining_artifacts["request_chain"]["references"]["seed_png"][
            "sent"
        ] is True
        assert remaining_artifacts["request_chain"]["seed_xml"] == {
            "status": "present",
            "source_slide_id": seed_run_slide_id,
            "content_length": len(seed_xml),
        }

    run_artifacts = json.loads(dbmod.get_run(fixture["run_id"])["stage_artifacts"])
    assert run_artifacts["seed_palette_lineage"] == {
        "run_id": fixture["run_id"],
        "run_slide_id": seed_run_slide_id,
        "deck_position": 2,
        "extraction_stage": "seed_palette_extraction",
        "seed_png_sha256": seed_hash,
        "palette_sha256": palette_hash,
        "colors": list(PALETTE_COLORS),
        "effective_color": {
            "content": PALETTE_XML,
            "sha256": palette_hash,
        },
    }
    seed_artifacts = palette_observation["stage_artifacts"]
    assert "cover_png" not in seed_artifacts["request_chain"]["references"]
    assert dbmod.get_run(fixture["run_id"])["status"] == "completed"


def test_image_3_0_cover_receives_complete_palette_and_bounded_lineage(
    isolated_app, tmp_path, monkeypatch
):
    """Cover generation receives normalized palette values plus bounded lineage."""

    fixture = _seed_native_image_3_0_run()
    colors_before = dbmod.list_colors()

    def concise_cover_director(call):
        if call["position"] == 1:
            return "## Image prompt\nMinimal cover composition.", [
                {"attempt": 1, "status": "result_received"}
            ]
        return (
            "<Visual>"
            f"<Deck_Style_DNA>{SEED_STYLE_DNA}</Deck_Style_DNA>"
            f"<Title>Position {call['position']}</Title>"
            "</Visual>",
            [{"attempt": 1, "status": "result_received"}],
        )

    ledger = _install_successful_native_route(
        monkeypatch,
        tmp_path,
        fixture,
        director_impl=concise_cover_director,
    )

    pipeline.run_pipeline_from_db(fixture["run_id"], str(dbmod.DB_PATH))

    cover_director = next(
        call for call in ledger["director_calls"] if call["position"] == 1
    )
    cover_renderer = next(
        call for call in ledger["renderer_calls"] if call["position"] == 1
    )
    seed_run_slide_id = fixture["run_slide_ids"]["seed"]
    seed_hash = sha256(SEED_PNG_BYTES).hexdigest()
    palette_hash = sha256(PALETTE_XML.encode("utf-8")).hexdigest()
    expected_lineage = {
        "run_id": fixture["run_id"],
        "run_slide_id": seed_run_slide_id,
        "deck_position": 2,
        "extraction_stage": "seed_palette_extraction",
        "seed_png_sha256": seed_hash,
        "palette_sha256": palette_hash,
        "colors": list(PALETTE_COLORS),
        "effective_color": {
            "content": PALETTE_XML,
            "sha256": palette_hash,
        },
    }
    # Raw extractor XML is not a public prompt contract. Every normalized
    # palette value and its content hash are, while the lineage object stays
    # bounded to the documented fields.
    for call in (cover_director, cover_renderer):
        assert all(color in call["prompt"] for color in PALETTE_COLORS)
        assert palette_hash in call["prompt"]
        assert PALETTE_XML not in call["prompt"]
        marker = "# Seed Palette Lineage\n"
        assert marker in call["prompt"]
        lineage_json = call["prompt"].split(marker, 1)[1].splitlines()[0]
        assert json.loads(lineage_json) == expected_lineage
    assert cover_director["reference_image_paths"] == []
    assert cover_renderer["reference_image_paths"] == []
    assert dbmod.list_colors() == colors_before


def test_force_image_3_0_child_terminalizes_run_slides_history_and_api(
    isolated_app, tmp_path, monkeypatch
):
    from backend.services import scheduler

    fixture = _seed_native_image_3_0_run()
    source_run = dbmod.get_run(fixture["run_id"])
    batch_id = dbmod.create_batch(
        source_run["deck_id"],
        source_run["config_id"],
        [],
        [],
        total_runs=1,
    )
    dbmod.update_run(
        fixture["run_id"],
        batch_id=batch_id,
        status="completed",
        completed_at=dbmod.current_timestamp(),
    )
    for position, source_slide in _slides_by_position(fixture["run_id"]).items():
        source_path = tmp_path / "source" / f"{position}.png"
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_bytes(f"source-{position}".encode("utf-8"))
        dbmod.update_run_slide(
            source_slide["id"],
            status="completed",
            final_image_path=str(source_path),
        )
    dbmod.update_batch_statuses()

    monkeypatch.setattr(server, "pump_batch_queue", lambda *_args, **_kwargs: [])
    response = isolated_app.test_client().post(
        "/api/generation-actions",
        json={
            "action": "force_regenerate",
            "scope": "run",
            "target_id": fixture["run_id"],
            "force_mode": "overwrite_current",
        },
    )

    assert response.status_code == 202
    payload = response.get_json()
    assert len(payload["created_run_ids"]) == 1
    child_run_id = payload["created_run_ids"][0]
    child_before = dbmod.get_run(child_run_id)
    child_route_metadata = child_before["route_metadata"]
    expected_lineage = json.loads(child_before["stage_artifacts"])["lineage"]
    expected_model_summary = json.loads(child_before["model_call_metadata"])
    child_slides = _slides_by_position(child_run_id)
    child_fixture = {
        "run_id": child_run_id,
        "run_slide_ids": {
            "cover": child_slides[1]["id"],
            "seed": child_slides[2]["id"],
            "later_3": child_slides[3]["id"],
            "later_4": child_slides[4]["id"],
        },
        "position_by_run_slide_id": {
            slide["id"]: position for position, slide in child_slides.items()
        },
    }
    _install_successful_native_route(monkeypatch, tmp_path, child_fixture)
    monkeypatch.setattr(pipeline, "provider_limit_for_config", lambda _config: 1)

    thread = scheduler.launch_run_for_batch(child_run_id, str(dbmod.DB_PATH))
    thread.join(timeout=10)
    assert not thread.is_alive()

    child = dbmod.get_run(child_run_id)
    terminal_slides = _slides_by_position(child_run_id)
    assert child["status"] == "completed"
    assert child["completed_at"] is not None
    assert child["route_metadata"] == child_route_metadata == source_run["route_metadata"]
    assert json.loads(child["stage_artifacts"])["lineage"] == expected_lineage
    terminal_model_metadata = json.loads(child["model_call_metadata"])
    assert {
        key: terminal_model_metadata[key] for key in ("action", "source_run_id")
    } == expected_model_summary
    assert len(terminal_slides) == 4
    assert all(slide["status"] == "completed" for slide in terminal_slides.values())
    assert all(
        Path(slide["final_image_path"]).is_file()
        for slide in terminal_slides.values()
    )

    for position, source_slide in _slides_by_position(fixture["run_id"]).items():
        action_history = [
            row
            for row in source_slide["generation_history"]
            if row["action"] == "force_regenerate"
            and row["created_run_id"] == child_run_id
        ]
        assert len(action_history) == 1
        history = action_history[0]
        assert history["status"] == "success"
        assert history["artifact_run_slide_id"] == terminal_slides[position]["id"]
        assert history["version_id"] is not None
        assert history["metadata"] == {
            **expected_lineage,
            "source_run_slide_id": source_slide["id"],
        }

    client = isolated_app.test_client()
    child_detail = client.get(f"/api/runs/{child_run_id}")
    child_status = client.get(f"/api/runs/{child_run_id}/status")
    source_detail = client.get(f"/api/runs/{fixture['run_id']}")
    batch_detail = client.get(f"/api/batches/{batch_id}")
    run_list = client.get("/api/runs")
    assert child_detail.status_code == child_status.status_code == 200
    assert source_detail.status_code == batch_detail.status_code == 200
    assert run_list.status_code == 200
    assert child_detail.get_json()["status"] == "completed"
    assert child_detail.get_json()["progress"] == {
        "total": 4,
        "completed": 4,
        "failed": 0,
        "running": 0,
        "pending": 0,
        "displayable": 4,
        "missing_displayable": 0,
    }
    assert child_status.get_json()["status"] == "completed"
    assert all(
        history["status"] == "success"
        for slide in source_detail.get_json()["slides"]
        for history in slide["generation_history"]
        if history["created_run_id"] == child_run_id
    )
    assert batch_detail.get_json()["status"] == "completed"
    assert next(
        run
        for run in batch_detail.get_json()["runs"]
        if run["id"] == child_run_id
    )["status"] == "completed"
    assert next(
        run for run in run_list.get_json() if run["id"] == child_run_id
    )["status"] == "completed"


def test_force_image_3_0_early_failure_keeps_action_evidence_and_terminal_history(
    isolated_app, tmp_path, monkeypatch
):
    from backend.services import scheduler

    fixture = _seed_native_image_3_0_run()
    source_run = dbmod.get_run(fixture["run_id"])
    batch_id = dbmod.create_batch(
        source_run["deck_id"],
        source_run["config_id"],
        [],
        [],
        total_runs=1,
    )
    dbmod.update_run(
        fixture["run_id"],
        batch_id=batch_id,
        status="completed",
        completed_at=dbmod.current_timestamp(),
    )
    for source_slide in _slides_by_position(fixture["run_id"]).values():
        dbmod.update_run_slide(source_slide["id"], status="completed")
    dbmod.update_batch_statuses()

    monkeypatch.setattr(server, "pump_batch_queue", lambda *_args, **_kwargs: [])
    response = isolated_app.test_client().post(
        "/api/generation-actions",
        json={
            "action": "force_regenerate",
            "scope": "run",
            "target_id": fixture["run_id"],
            "force_mode": "overwrite_current",
        },
    )
    assert response.status_code == 202
    child_run_id = response.get_json()["created_run_ids"][0]
    child_before = dbmod.get_run(child_run_id)
    expected_lineage = json.loads(child_before["stage_artifacts"])["lineage"]
    expected_model_summary = json.loads(child_before["model_call_metadata"])
    child_slides = _slides_by_position(child_run_id)
    child_fixture = {
        "run_id": child_run_id,
        "run_slide_ids": {
            "cover": child_slides[1]["id"],
            "seed": child_slides[2]["id"],
            "later_3": child_slides[3]["id"],
            "later_4": child_slides[4]["id"],
        },
        "position_by_run_slide_id": {
            slide["id"]: position for position, slide in child_slides.items()
        },
    }
    ledger = _install_successful_native_route(
        monkeypatch,
        tmp_path,
        child_fixture,
    )

    def fail_before_provider(*_args, **_kwargs):
        raise RuntimeError("force-child-pre-provider-failure")

    monkeypatch.setattr(
        pipeline,
        "_render_image_prompt_with_lineage",
        fail_before_provider,
    )
    thread = scheduler.launch_run_for_batch(child_run_id, str(dbmod.DB_PATH))
    thread.join(timeout=10)
    assert not thread.is_alive()

    child = dbmod.get_run(child_run_id)
    assert child["status"] == "failed"
    assert all(
        slide["status"] == "failed"
        for slide in _slides_by_position(child_run_id).values()
    )
    assert ledger["director_calls"] == []
    assert ledger["renderer_calls"] == []
    assert ledger["palette_calls"] == []
    assert json.loads(child["stage_artifacts"])["lineage"] == expected_lineage
    child_model_metadata = json.loads(child["model_call_metadata"])
    assert {
        key: child_model_metadata[key] for key in ("action", "source_run_id")
    } == expected_model_summary
    for source_slide in _slides_by_position(fixture["run_id"]).values():
        action_history = [
            history
            for history in source_slide["generation_history"]
            if history["created_run_id"] == child_run_id
        ]
        assert len(action_history) == 1
        assert action_history[0]["status"] == "failed"
        assert action_history[0]["metadata"] == {
            **expected_lineage,
            "source_run_slide_id": source_slide["id"],
        }


def test_image_3_0_cover_and_remaining_pages_overlap_after_palette(
    isolated_app, tmp_path, monkeypatch
):
    fixture = _seed_native_image_3_0_run()
    fanout_started: set[int] = set()
    fanout_gate = threading.Event()
    palette_complete = threading.Event()
    lock = threading.Lock()

    def palette_impl(_call):
        palette_complete.set()
        return PALETTE_XML

    def director_impl(call):
        position = call["position"]
        if position != 2:
            assert palette_complete.is_set()
            with lock:
                fanout_started.add(position)
                if len(fanout_started) >= 2:
                    fanout_gate.set()
            assert fanout_gate.wait(2.0), "cover and a remaining page did not overlap"
        if position == 1:
            return f"## Image prompt\n{call['prompt']}", [
                {"attempt": 1, "status": "result_received"}
            ]
        return (
            "<Visual>"
            f"<Deck_Style_DNA>{SEED_STYLE_DNA}</Deck_Style_DNA>"
            f"<Title>Position {position}</Title>"
            "</Visual>",
            [{"attempt": 1, "status": "result_received"}],
        )

    _install_successful_native_route(
        monkeypatch,
        tmp_path,
        fixture,
        palette_impl=palette_impl,
        director_impl=director_impl,
    )
    monkeypatch.setattr(pipeline, "provider_limit_for_config", lambda _config: 2)

    pipeline.run_pipeline_from_db(fixture["run_id"], str(dbmod.DB_PATH))

    assert 1 in fanout_started
    assert len(fanout_started) >= 2
    assert dbmod.get_run(fixture["run_id"])["status"] == "completed"


def test_image_3_0_seed_failure_returns_failed_without_palette_or_fanout(
    isolated_app, tmp_path, monkeypatch
):
    fixture = _seed_native_image_3_0_run()

    def fail_seed(call):
        if call["position"] == 2:
            raise RuntimeError("seed-director-failed")
        raise AssertionError("cover or remaining page started after seed failure")

    ledger = _install_successful_native_route(
        monkeypatch,
        tmp_path,
        fixture,
        director_impl=fail_seed,
    )

    pipeline.run_pipeline_from_db(fixture["run_id"], str(dbmod.DB_PATH))

    assert [call["position"] for call in ledger["director_calls"]] == [2]
    assert ledger["palette_calls"] == []
    assert dbmod.get_run(fixture["run_id"])["status"] == "failed"
    slides = _slides_by_position(fixture["run_id"])
    assert slides[2]["status"] == "failed"
    assert all(slide["status"] == "failed" for slide in slides.values())
    outcome = json.loads(dbmod.get_run(fixture["run_id"])["stage_artifacts"])[
        "image_route_outcome"
    ]
    assert outcome["status"] == "failed"
    assert outcome["completed_slide_ids"] == []
    assert sorted(outcome["failed_slide_ids"]) == sorted(
        fixture["run_slide_ids"].values()
    )


def test_image_3_0_seed_pre_provider_failure_terminalizes_every_slide(
    isolated_app, tmp_path, monkeypatch
):
    fixture = _seed_native_image_3_0_run()
    ledger = _install_successful_native_route(monkeypatch, tmp_path, fixture)
    original_render = pipeline._render_image_prompt_with_lineage

    def fail_seed_prompt(role, deck, requirement, color, run_slide, *, full_content):
        if int(run_slide["position"]) == 2:
            raise RuntimeError("seed-prompt-render-failed")
        return original_render(
            role,
            deck,
            requirement,
            color,
            run_slide,
            full_content=full_content,
        )

    monkeypatch.setattr(
        pipeline,
        "_render_image_prompt_with_lineage",
        fail_seed_prompt,
    )

    pipeline.run_pipeline_from_db(fixture["run_id"], str(dbmod.DB_PATH))

    assert ledger["director_calls"] == []
    assert dbmod.get_run(fixture["run_id"])["status"] == "failed"
    slides = _slides_by_position(fixture["run_id"])
    assert all(slide["status"] == "failed" for slide in slides.values())
    assert not any(
        slide["status"] in {"pending", "running"} for slide in slides.values()
    )


@pytest.mark.parametrize("failed_position", [1, 3])
def test_image_3_0_fanout_pre_provider_failure_terminalizes_affected_slide(
    isolated_app, tmp_path, monkeypatch, failed_position
):
    fixture = _seed_native_image_3_0_run()
    _install_successful_native_route(monkeypatch, tmp_path, fixture)
    original_render = pipeline._render_image_prompt_with_lineage

    def fail_fanout_prompt(role, deck, requirement, color, run_slide, *, full_content):
        if int(run_slide["position"]) == failed_position:
            raise RuntimeError(f"position-{failed_position}-prompt-render-failed")
        return original_render(
            role,
            deck,
            requirement,
            color,
            run_slide,
            full_content=full_content,
        )

    monkeypatch.setattr(
        pipeline,
        "_render_image_prompt_with_lineage",
        fail_fanout_prompt,
    )

    pipeline.run_pipeline_from_db(fixture["run_id"], str(dbmod.DB_PATH))

    run = dbmod.get_run(fixture["run_id"])
    slides = _slides_by_position(fixture["run_id"])
    assert run["status"] == "completed_with_failures"
    assert slides[failed_position]["status"] == "failed"
    assert "prompt-render-failed" in slides[failed_position]["error_message"]
    assert not any(
        slide["status"] in {"pending", "running"} for slide in slides.values()
    )


def test_image_3_0_palette_failure_preserves_seed_and_returns_completed_with_failures(
    isolated_app, tmp_path, monkeypatch
):
    fixture = _seed_native_image_3_0_run()

    def fail_palette(_call):
        raise RuntimeError("seed-palette-extraction-failed")

    ledger = _install_successful_native_route(
        monkeypatch,
        tmp_path,
        fixture,
        palette_impl=fail_palette,
    )

    pipeline.run_pipeline_from_db(fixture["run_id"], str(dbmod.DB_PATH))

    assert [call["position"] for call in ledger["director_calls"]] == [2]
    assert [call["position"] for call in ledger["renderer_calls"]] == [2]
    assert len(ledger["palette_calls"]) == 1
    run = dbmod.get_run(fixture["run_id"])
    slides = _slides_by_position(fixture["run_id"])
    assert run["status"] == "completed_with_failures"
    assert slides[2]["status"] == "completed"
    assert Path(slides[2]["final_image_path"]).read_bytes() == SEED_PNG_BYTES
    assert all(
        slides[position]["status"] == "failed" for position in (1, 3, 4)
    )
    assert all(
        "seed_palette_extraction_failed"
        in str(slides[position]["error_message"])
        for position in (1, 3, 4)
    )
    outcome = json.loads(run["stage_artifacts"])["image_route_outcome"]
    assert outcome["status"] == "completed_with_failures"
    assert outcome["completed_slide_ids"] == [fixture["run_slide_ids"]["seed"]]
    assert sorted(outcome["failed_slide_ids"]) == sorted(
        run_slide_id
        for name, run_slide_id in fixture["run_slide_ids"].items()
        if name != "seed"
    )
    detail_response = isolated_app.test_client().get(f"/api/runs/{fixture['run_id']}")
    assert detail_response.status_code == 200
    detail = detail_response.get_json()
    assert [slide["position"] for slide in detail["slides"]] == [1, 2, 3, 4]
    projected = _result_projection(detail)
    assert projected["platform_status"] == "completed_with_failures"
    assert projected["status"] == "partially_completed"
    assert projected["successful_slide_count"] == 1
    assert projected["slides"][1]["position"] == 2
    assert projected["slides"][1]["html_path"] is None
    assert projected["slides"][1]["status"] == "completed"


def test_image_3_0_fanout_partial_settles_every_launched_slide(
    isolated_app, tmp_path, monkeypatch
):
    fixture = _seed_native_image_3_0_run()
    positions = fixture["position_by_run_slide_id"]
    fanout_started: set[int] = set()
    fanout_settled: set[int] = set()
    all_started = threading.Event()
    palette_complete = threading.Event()
    lock = threading.Lock()

    def palette_impl(_call):
        palette_complete.set()
        return PALETTE_XML

    def renderer_impl(call):
        position = call["position"]
        if position == 2:
            return _native_result(
                prompt=call["prompt"],
                output_path=Path(call["output_path"]),
                run_id=fixture["run_id"],
                run_slide_id=call["run_slide_id"],
                stage_id=call["stage_id"],
                marker="seed",
            )
        assert palette_complete.is_set()
        with lock:
            fanout_started.add(position)
            if fanout_started == {1, 3, 4}:
                all_started.set()
        try:
            assert all_started.wait(2.0), "not every fan-out renderer was launched"
            if position == 3:
                raise RuntimeError("position-three-render-failed")
            return _native_result(
                prompt=call["prompt"],
                output_path=Path(call["output_path"]),
                run_id=fixture["run_id"],
                run_slide_id=call["run_slide_id"],
                stage_id=call["stage_id"],
                marker=f"position-{position}",
            )
        finally:
            with lock:
                fanout_settled.add(position)

    _install_successful_native_route(
        monkeypatch,
        tmp_path,
        fixture,
        palette_impl=palette_impl,
        renderer_impl=renderer_impl,
    )
    monkeypatch.setattr(pipeline, "provider_limit_for_config", lambda _config: 3)

    pipeline.run_pipeline_from_db(fixture["run_id"], str(dbmod.DB_PATH))

    assert fanout_started == {1, 3, 4}
    assert fanout_settled == {1, 3, 4}
    run = dbmod.get_run(fixture["run_id"])
    slides = _slides_by_position(fixture["run_id"])
    assert run["status"] == "completed_with_failures"
    assert [slides[position]["status"] for position in (1, 2, 3, 4)] == [
        "completed",
        "completed",
        "failed",
        "completed",
    ]
    outcome = json.loads(run["stage_artifacts"])["image_route_outcome"]
    assert outcome["status"] == "completed_with_failures"
    assert outcome["failed_slide_ids"] == [fixture["run_slide_ids"]["later_3"]]
    assert positions[fixture["run_slide_ids"]["later_3"]] == 3


def test_image_3_0_preview_status_stays_running_until_partial_fanout_settles(
    isolated_app, tmp_path, monkeypatch
):
    fixture = _seed_native_image_3_0_run()
    fanout_started: set[int] = set()
    all_started = threading.Event()
    release_successes = threading.Event()
    failure_raised = threading.Event()
    lock = threading.Lock()

    def renderer_impl(call):
        position = call["position"]
        if position == 2:
            return _native_result(
                prompt=call["prompt"],
                output_path=Path(call["output_path"]),
                run_id=fixture["run_id"],
                run_slide_id=call["run_slide_id"],
                stage_id=call["stage_id"],
                marker="seed",
            )
        with lock:
            fanout_started.add(position)
            if fanout_started == {1, 3, 4}:
                all_started.set()
        assert all_started.wait(2.0), "fan-out did not launch all three renderers"
        if position == 3:
            failure_raised.set()
            raise RuntimeError("position-three-render-failed")
        assert release_successes.wait(3.0), "successful fan-out pages were not released"
        return _native_result(
            prompt=call["prompt"],
            output_path=Path(call["output_path"]),
            run_id=fixture["run_id"],
            run_slide_id=call["run_slide_id"],
            stage_id=call["stage_id"],
            marker=f"position-{position}",
        )

    _install_successful_native_route(
        monkeypatch,
        tmp_path,
        fixture,
        renderer_impl=renderer_impl,
    )
    monkeypatch.setattr(pipeline, "provider_limit_for_config", lambda _config: 3)

    pipeline_thread = threading.Thread(
        target=pipeline.run_pipeline_from_db,
        args=(fixture["run_id"], str(dbmod.DB_PATH)),
        daemon=True,
    )
    pipeline_thread.start()

    assert failure_raised.wait(2.0), "fan-out failure was not raised"
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        slides = _slides_by_position(fixture["run_id"])
        if slides[3]["status"] == "failed":
            break
        time.sleep(0.01)
    else:
        raise AssertionError("failed fan-out page was not persisted")

    try:
        assert slides[1]["status"] == "running"
        assert slides[4]["status"] == "running"
        client = isolated_app.test_client()
        detail_response = client.get(f"/api/runs/{fixture['run_id']}")
        status_response = client.get(f"/api/runs/{fixture['run_id']}/status")
        assert detail_response.status_code == 200
        assert status_response.status_code == 200
        assert detail_response.get_json()["status"] == "running"
        assert status_response.get_json()["status"] == "running"
    finally:
        release_successes.set()
        pipeline_thread.join(3.0)

    assert not pipeline_thread.is_alive()
    assert dbmod.get_run(fixture["run_id"])["status"] == "completed_with_failures"
    assert [slide["status"] for slide in _slides_by_position(fixture["run_id"]).values()] == [
        "completed",
        "completed",
        "failed",
        "completed",
    ]


@pytest.mark.parametrize(
    ("engine", "strategy"),
    [
        ("html", "html_default"),
        ("image", "image_3_2"),
        ("image", "image_5_0"),
        ("image", "image_direct"),
    ],
)
def test_reconcile_preserves_failed_terminal_for_non_image_3_0_mixed_runs(
    isolated_app, engine, strategy
):
    fixture = _seed_native_image_3_0_run()
    source_run = dbmod.get_run(fixture["run_id"])
    source_slides = dbmod.list_run_slides(fixture["run_id"])
    run_id = dbmod.create_run(
        source_run["deck_id"],
        source_run["requirement_id"],
        source_run["color_id"],
        source_run["config_id"],
        engine=engine,
        strategy=strategy,
    )
    completed_id = dbmod.create_run_slide(
        run_id,
        source_slides[0]["slide_id"],
        1,
        slide_type=source_slides[0]["slide_type"],
    )
    failed_id = dbmod.create_run_slide(
        run_id,
        source_slides[1]["slide_id"],
        2,
        slide_type=source_slides[1]["slide_type"],
    )
    dbmod.update_run(run_id, status="running")
    dbmod.update_run_slide(completed_id, status="completed")
    dbmod.update_run_slide(failed_id, status="failed")

    dbmod.reconcile_run_statuses(timeout_minutes=30)

    assert dbmod.get_run(run_id)["status"] == "failed"
