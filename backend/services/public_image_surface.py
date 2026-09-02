"""Fail-closed public HTTP surface for the Image PPT 3.0 product."""

from __future__ import annotations

import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import re
from typing import Any
import zipfile

from flask import Response, jsonify, request, send_file

import db as dbmod
from backend.domain import status as run_status
from backend.services import (
    codex_activity_projection,
    codex_audit,
    deck_split_drafts,
    folders,
    model_profiles,
)


DEFAULT_PUBLIC_CONFIG_NAMES = (
    model_profiles.NATIVE_IMAGE_3_0_CONFIG_NAME,
    model_profiles.NATIVE_IMAGE_3_0_LUNA_DIRECTOR_CONFIG_NAME,
)

_PUBLIC_GENERATE_FIELDS = {
    "deck_id",
    "config_id",
    "engine",
    "strategy",
    "requirement_ids",
    "color_ids",
}

_PUBLIC_SPLIT_MODEL = "gpt-5.6-luna"
_PUBLIC_SPLIT_PROFILE_NAME = "AutoSplit · GPT-5.6 Luna"
_PUBLIC_TERRA_SPLIT_MODEL = "gpt-5.6-terra"
_PUBLIC_TERRA_SPLIT_PROFILE_NAME = "AutoSplit · GPT-5.6 Terra"
_PUBLIC_SPLIT_THINKING = "low"
_PUBLIC_SPLIT_CONTENT_MODE = "faithful"
_UNSET_TARGET_PAGE_COUNT = object()


def _public_config_names() -> tuple[str, ...]:
    if model_profiles.image_pptgen_e2e_terra_low_enabled():
        return (*DEFAULT_PUBLIC_CONFIG_NAMES, model_profiles.NATIVE_IMAGE_3_0_TERRA_E2E_CONFIG_NAME)
    return DEFAULT_PUBLIC_CONFIG_NAMES


def _public_split_model() -> str:
    """Return the production Luna model, or the explicit Terra E2E seam."""
    if model_profiles.image_pptgen_e2e_terra_low_enabled():
        return _PUBLIC_TERRA_SPLIT_MODEL
    return _PUBLIC_SPLIT_MODEL


def _public_split_profile_name() -> str:
    if model_profiles.image_pptgen_e2e_terra_low_enabled():
        return _PUBLIC_TERRA_SPLIT_PROFILE_NAME
    return _PUBLIC_SPLIT_PROFILE_NAME

_ALLOWED_API_RULES = {
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

_PUBLIC_RUN_FIELDS = {
    "id",
    "batch_id",
    "deck_id",
    "config_id",
    "engine",
    "strategy",
    "status",
    "error_message",
    "started_at",
    "completed_at",
    "created_at",
    "updated_at",
    "deck_title",
    "deck_content",
    "config_name",
    "progress",
    "stage_artifacts",
    "slides",
    "codex_audit",
}

_PUBLIC_SLIDE_FIELDS = {
    "id",
    "run_id",
    "slide_id",
    "position",
    "slide_type",
    "status",
    "error_message",
    "created_at",
    "updated_at",
    "slide_title",
    "slide_content",
    "slide_title_snapshot",
    "slide_content_snapshot",
    "stage_artifacts",
    "seed_dependency",
    "has_displayable_artifact",
    "final_image_path",
}

_PUBLIC_BATCH_FIELDS = {
    "id",
    "deck_id",
    "config_id",
    "status",
    "total_runs",
    "queued_runs",
    "running_runs",
    "completed_runs",
    "completed_with_failures_runs",
    "failed_runs",
    "timed_out_runs",
    "failure_rate",
    "engine",
    "strategy",
    "representative_run_id",
    "error_message",
    "created_at",
    "updated_at",
    "deck_title",
    "config_name",
    "runs",
}

_DROP = object()
_SENSITIVE_KEY_PARTS = {
    "apikey",
    "authorization",
    "colorcontent",
    "colorid",
    "command",
    "cwd",
    "designerprompt",
    "endpoint",
    "generationhistory",
    "htmlprompt",
    "machineqa",
    "modelcallmetadata",
    "password",
    "private",
    "prompt",
    "rawresponse",
    "requirementcontent",
    "requirementid",
    "routemetadata",
    "secret",
    "session",
    "stderr",
    "stdout",
    "thread",
    "token",
    "transcript",
}
_PUBLIC_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")

_IMAGE_RUNTIME_PRODUCT = "image-pptgen"
_IMAGE_RUNTIME_SERVICE = "image-pptgen-server"
_IMAGE_RUNTIME_SURFACE = "public_image_3_0"
_IMAGE_RUNTIME_BASE_URL = "http://127.0.0.1:3130"
_IMAGE_RUNTIME_DATA_ROOT = "image-pptgen/state/data"
_IMAGE_RUNTIME_ARTIFACTS_ROOT = "image-pptgen/state/data/artifacts"
_IMAGE_RUNTIME_RELEASE_FIELDS = (
    "build_id",
    "version",
    "source_commit",
    "skill_sha256",
    "runtime_content_sha256",
)
_SOURCE_DEV_IMAGE_RUNTIME_IDENTITY = {
    "artifacts_root": _IMAGE_RUNTIME_ARTIFACTS_ROOT,
    "base_url": _IMAGE_RUNTIME_BASE_URL,
    "build_id": "source-dev-public-image-3-0",
    "data_root": _IMAGE_RUNTIME_DATA_ROOT,
    "instance_id": "source-dev-public-image-3-0",
    "product": _IMAGE_RUNTIME_PRODUCT,
    "service": _IMAGE_RUNTIME_SERVICE,
    "skill_sha256": "source-dev-public-image-3-0",
    "source_commit": "source-dev-public-image-3-0",
    "surface": _IMAGE_RUNTIME_SURFACE,
    "runtime_content_sha256": "source-dev-public-image-3-0",
    "version": "source-dev",
}


def _not_found():
    return jsonify({"error": "Not found"}), 404


def _public_image_runtime_identity() -> dict[str, str]:
    """Return the allowlisted identity of this public Image service only.

    Source/dev imports intentionally use deterministic, non-path defaults.
    Installed releases opt in through the launcher-owned mode and derive their
    release values from the immutable candidate file, never from HTTP input or
    arbitrary environment JSON.
    """
    if os.environ.get("PPTGEN_IMAGE_RUNTIME_MODE") != "installed":
        return dict(_SOURCE_DEV_IMAGE_RUNTIME_IDENTITY)

    release_path_value = os.environ.get("PPTGEN_RELEASE_IDENTITY_PATH")
    instance_path_value = os.environ.get("PPTGEN_INSTANCE_ID_PATH")
    if not release_path_value or not instance_path_value:
        raise RuntimeError("installed Image runtime identity is unavailable")
    try:
        release = json.loads(Path(release_path_value).read_text(encoding="utf-8"))
        instance = json.loads(Path(instance_path_value).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("installed Image runtime identity is unavailable") from exc
    if not isinstance(release, dict) or not isinstance(instance, dict):
        raise RuntimeError("installed Image runtime identity is unavailable")
    if (
        release.get("product") != _IMAGE_RUNTIME_PRODUCT
        or release.get("service") != _IMAGE_RUNTIME_SERVICE
        or release.get("surface") != _IMAGE_RUNTIME_SURFACE
    ):
        raise RuntimeError("installed Image runtime identity is invalid")
    release_values: dict[str, str] = {}
    for field in _IMAGE_RUNTIME_RELEASE_FIELDS:
        value = release.get(field)
        if not isinstance(value, str) or not value.strip():
            raise RuntimeError("installed Image runtime identity is invalid")
        release_values[field] = value
    instance_id = instance.get("instance_id")
    if not isinstance(instance_id, str) or not instance_id.strip():
        raise RuntimeError("installed Image runtime identity is invalid")
    return {
        "artifacts_root": _IMAGE_RUNTIME_ARTIFACTS_ROOT,
        "base_url": _IMAGE_RUNTIME_BASE_URL,
        "data_root": _IMAGE_RUNTIME_DATA_ROOT,
        "instance_id": instance_id,
        "product": _IMAGE_RUNTIME_PRODUCT,
        "service": _IMAGE_RUNTIME_SERVICE,
        "surface": _IMAGE_RUNTIME_SURFACE,
        **release_values,
    }


def _profile_matches(profile: dict[str, Any] | None, expected_name: str) -> bool:
    if not profile:
        return False
    specs = list(model_profiles.NATIVE_IMAGE_PROFILE_SPECS)
    if model_profiles.image_pptgen_e2e_terra_low_enabled():
        specs.extend(model_profiles.NATIVE_IMAGE_TERRA_E2E_PROFILE_SPECS)
    expected = next((spec for spec in specs if spec.name == expected_name), None)
    if expected is None:
        return False
    matches = all(
        profile.get(key) == value
        for key, value in {
            "role": expected.role,
            "name": expected.name,
            "api_type": expected.api_type,
            "endpoint": expected.endpoint,
            "model": expected.model,
            "thinking": expected.thinking,
            "status": "active",
        }.items()
    )
    if not matches or float(profile.get("temperature") or 0) != float(expected.temperature):
        return False
    if expected.api_type in {model_profiles.CODEX_EXEC_API_TYPE, model_profiles.NATIVE_IMAGE_API_TYPE}:
        return not profile.get("api_key")
    return True


def _validated_public_config(name: str) -> dict[str, Any] | None:
    row = dbmod.get_config_by_name(name)
    if not row:
        return None
    try:
        config = model_profiles.resolve_config(int(row["id"]))
    except ValueError:
        return None
    bindings = config.get("route_model_bindings")
    if not isinstance(bindings, dict):
        return None
    expected_profiles = {
        model_profiles.NATIVE_IMAGE_3_0_CONFIG_NAME: (
            model_profiles.NATIVE_IMAGE_DIRECTOR_PROFILE_NAME,
            model_profiles.NATIVE_IMAGE_LAUNCHER_PROFILE_NAME,
        ),
        model_profiles.NATIVE_IMAGE_3_0_LUNA_DIRECTOR_CONFIG_NAME: (
            model_profiles.NATIVE_IMAGE_LUNA_DIRECTOR_PROFILE_NAME,
            model_profiles.NATIVE_IMAGE_LAUNCHER_PROFILE_NAME,
        ),
    }
    if model_profiles.image_pptgen_e2e_terra_low_enabled():
        expected_profiles[model_profiles.NATIVE_IMAGE_3_0_TERRA_E2E_CONFIG_NAME] = (
            model_profiles.NATIVE_IMAGE_TERRA_DIRECTOR_PROFILE_NAME,
            model_profiles.NATIVE_IMAGE_TERRA_LAUNCHER_PROFILE_NAME,
        )
    expected = expected_profiles.get(name)
    if expected is None:
        return None
    expected_director, expected_launcher = expected
    expected_binding_keys = {
        "image_designer",
        "image_generator",
        "image_palette_extractor",
        "native_image",
    }
    if (
        type(config.get("id")) is not int
        or int(config["id"]) <= 0
        or config.get("name") != name
        or config.get("type") != "image"
        or int(config.get("timeout_minutes") or 0) != 30
        or int(config.get("max_concurrent_runs") or 0) != model_profiles.NATIVE_IMAGE_MAX_CONCURRENT_RUNS
        or not model_profiles.is_system_managed_native_config(config)
        or model_profiles.native_image_route_for_config(config) != model_profiles.NATIVE_IMAGE_3_0_ROUTE
        or set(bindings) != expected_binding_keys
        or bindings.get("native_image")
        != {
            "adapter": model_profiles.NATIVE_IMAGE_ADAPTER,
            "route": model_profiles.NATIVE_IMAGE_3_0_ROUTE,
        }
    ):
        return None
    profiles: dict[str, dict[str, Any] | None] = {}
    for role in ("image_designer", "image_generator", "image_palette_extractor"):
        binding = bindings.get(role)
        if (
            not isinstance(binding, dict)
            or set(binding) != {"profile_id"}
            or type(binding.get("profile_id")) is not int
            or binding["profile_id"] <= 0
        ):
            return None
        profiles[role] = model_profiles.get_profile(binding["profile_id"])
    profile_ids = {int(bindings[role]["profile_id"]) for role in profiles}
    if not (
        len(profile_ids) == 2
        and _profile_matches(profiles["image_designer"], expected_director)
        and _profile_matches(profiles["image_generator"], expected_launcher)
        and profiles["image_palette_extractor"] == profiles["image_designer"]
        and int(config.get("designer_profile_id") or 0) == int(bindings["image_designer"]["profile_id"])
        and int(config.get("html_agent_profile_id") or 0) == int(bindings["image_generator"]["profile_id"])
    ):
        return None
    config["_public_profiles"] = profiles
    return config


def _public_config_dto(config: dict[str, Any]) -> dict[str, Any]:
    profiles = config["_public_profiles"]
    director = profiles["image_designer"]
    renderer = profiles["image_generator"]
    palette = profiles["image_palette_extractor"]
    return {
        "id": int(config["id"]),
        "name": config["name"],
        "type": "image",
        "route": model_profiles.NATIVE_IMAGE_3_0_ROUTE,
        "timeout_minutes": int(config.get("timeout_minutes") or 30),
        "max_concurrent_runs": int(
            config.get("max_concurrent_runs") or model_profiles.NATIVE_IMAGE_MAX_CONCURRENT_RUNS
        ),
        "director": {"model": director["model"], "reasoning_effort": director["thinking"]},
        "renderer": {"model": renderer["model"], "reasoning_effort": renderer["thinking"]},
        "palette": {
            "model": palette["model"],
            "reasoning_effort": palette["thinking"],
        },
    }


def public_configs() -> list[dict[str, Any]]:
    configs = [_validated_public_config(name) for name in _public_config_names()]
    if any(config is None for config in configs):
        return []
    sol, luna, *optional = configs
    assert sol is not None and luna is not None
    sol_bindings = sol["route_model_bindings"]
    luna_bindings = luna["route_model_bindings"]
    if not (
        sol_bindings["image_generator"] == luna_bindings["image_generator"]
        and sol_bindings["image_designer"] == sol_bindings["image_palette_extractor"]
        and luna_bindings["image_designer"] == luna_bindings["image_palette_extractor"]
        and sol_bindings["image_designer"] != luna_bindings["image_designer"]
    ):
        return []
    result = [_public_config_dto(sol), _public_config_dto(luna)]
    if optional:
        terra = optional[0]
        assert terra is not None
        terra_bindings = terra["route_model_bindings"]
        if not (
            terra_bindings["image_designer"]
            == terra_bindings["image_palette_extractor"]
            and terra_bindings["image_generator"]
            != luna_bindings["image_generator"]
            and terra_bindings["image_designer"]
            not in (sol_bindings["image_designer"], luna_bindings["image_designer"])
        ):
            return []
        result.append(_public_config_dto(terra))
    return result


def _public_config_ids() -> set[int]:
    return {config["id"] for config in public_configs()}


def _run_is_public(run: dict[str, Any] | None) -> bool:
    return bool(
        run
        and run.get("engine") == "image"
        and run.get("strategy") == model_profiles.NATIVE_IMAGE_3_0_ROUTE
        and int(run.get("config_id") or 0) in _public_config_ids()
    )


def _run_id_is_public(run_id: int) -> bool:
    return _run_is_public(dbmod.get_run(run_id))


def _batch_id_is_public(batch_id: int) -> bool:
    runs = dbmod.list_runs_for_batch(batch_id)
    return bool(runs) and all(_run_is_public(run) for run in runs)


def _run_slide_id_is_public(run_slide_id: int) -> bool:
    row = _run_slide_record(run_slide_id)
    return bool(row) and _run_id_is_public(int(row["run_id"]))


def _run_slide_record(run_slide_id: int) -> dict[str, Any] | None:
    db = dbmod.get_db()
    try:
        row = db.execute("SELECT * FROM run_slides WHERE id = ?", (run_slide_id,)).fetchone()
    finally:
        db.close()
    return dict(row) if row else None


def _artifact_candidate(root: Path, value: str) -> Path | None:
    try:
        if value.startswith("/artifacts/"):
            candidate = root / value[len("/artifacts/") :]
        elif value.startswith("artifacts/"):
            candidate = root / value[len("artifacts/") :]
        else:
            candidate = Path(value)
            if not candidate.is_absolute():
                candidate = root / candidate
        resolved = candidate.resolve()
        resolved.relative_to(root)
        return resolved
    except (OSError, ValueError):
        return None


def _active_version_source_is_public(
    target_slide: dict[str, Any],
    active_version: dict[str, Any],
) -> bool:
    source_slide_id = active_version.get("artifact_run_slide_id")
    if type(source_slide_id) is not int or source_slide_id <= 0:
        return False
    source_slide = _run_slide_record(source_slide_id)
    if not source_slide or not _run_slide_id_is_public(source_slide_id):
        return False
    try:
        return bool(
            int(active_version.get("target_run_slide_id") or 0) == int(target_slide["id"])
            and int(source_slide["slide_id"]) == int(target_slide["slide_id"])
            and int(source_slide["position"]) == int(target_slide["position"])
            and int(active_version.get("slide_id") or 0) == int(target_slide["slide_id"])
            and int(active_version.get("position") or 0) == int(target_slide["position"])
            and int(active_version.get("source_run_id") or 0) == int(source_slide["run_id"])
        )
    except (KeyError, TypeError, ValueError):
        return False


def _public_png_path(slide: dict[str, Any], artifacts_root: Path) -> Path | None:
    active_version = slide.get("active_version")
    if active_version is not None and (
        not isinstance(active_version, dict)
        or not _active_version_source_is_public(slide, active_version)
    ):
        return None
    target = _artifact_candidate(artifacts_root.resolve(), str(slide.get("final_image_path") or ""))
    if (
        target is None
        or target.suffix.lower() != ".png"
        or not target.is_file()
        or ".codex-private" in target.relative_to(artifacts_root.resolve()).parts
    ):
        return None
    return target


def _snapshot_native_public_projection(snapshot: object) -> dict[str, Any] | None:
    if not isinstance(snapshot, dict):
        return None
    stage_artifacts = snapshot.get("slide_stage_artifacts")
    if not isinstance(stage_artifacts, dict):
        return None
    image = stage_artifacts.get("image")
    if not isinstance(image, dict):
        return None
    projection = image.get("native_public")
    return projection if isinstance(projection, dict) else None


def _native_audit_marker_for_active_version(
    *,
    active_version: dict[str, Any],
    image_bytes: bytes,
) -> dict[str, Any] | None:
    """Resolve one public Native marker, projecting legacy snapshots in memory."""
    source_slide_id = active_version.get("artifact_run_slide_id")
    if type(source_slide_id) is not int or source_slide_id <= 0:
        return None
    candidate = dbmod.native_public_audit_marker_for_run_slide(source_slide_id)
    if candidate is None:
        return None
    binding = codex_audit.native_public_audit_binding(candidate)
    if binding is None:
        return None
    image = binding["public_projection"].get("business_image")
    if not isinstance(image, dict) or image.get("sha256") != hashlib.sha256(image_bytes).hexdigest():
        return None
    snapshot = active_version.get("evidence_snapshot")
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    stored = codex_audit.find_nested_native_private_evidence(snapshot)
    if stored is not None:
        stored_binding = codex_audit.native_public_audit_binding(stored)
        if stored_binding != binding:
            return None
        return candidate
    if _snapshot_native_public_projection(snapshot) != binding["public_projection"]:
        return None
    return candidate


def _artifact_is_public(filename: str, artifacts_root: Path) -> bool:
    root = artifacts_root.resolve()
    target = _artifact_candidate(root, filename)
    if target is None:
        return False
    for run in dbmod.list_runs():
        if not _run_is_public(run):
            continue
        for slide in dbmod.list_run_slides(int(run["id"])):
            if _public_png_path(slide, root) == target:
                return True
    return False


def _sanitize(value: object, key_name: str = "") -> object:
    normalized_key = "".join(character for character in key_name.lower() if character.isalnum())
    if normalized_key in {"error", "errormessage"} or normalized_key.endswith("errormessage"):
        return codex_audit.redact_audit_error(value)
    if normalized_key == "jsonl":
        return _sanitize_public_jsonl(value)
    if any(part in normalized_key for part in _SENSITIVE_KEY_PARTS):
        return _DROP
    if key_name.lower() == "path" or key_name.lower().endswith("_path"):
        return _DROP
    if isinstance(value, dict):
        projected = {}
        for key, child in value.items():
            safe_child = _sanitize(child, str(key))
            if safe_child is not _DROP:
                projected[key] = safe_child
        return projected
    if isinstance(value, list):
        return [safe for child in value if (safe := _sanitize(child, key_name)) is not _DROP]
    if isinstance(value, str) and (".codex-private" in value or (value.startswith("/") and not value.startswith("/artifacts/"))):
        return _DROP
    return value


def _public_sha256(value: object) -> str | None:
    if isinstance(value, str) and _PUBLIC_SHA256_PATTERN.fullmatch(value):
        return value
    return None


def _sanitize_public_jsonl(value: object) -> object:
    """Keep only path-free JSONL integrity summaries on public audit detail."""
    if not isinstance(value, dict):
        return _DROP

    projected: dict[str, object] = {}
    for key in ("raw", "observed"):
        reference = value.get(key)
        if not isinstance(reference, dict):
            continue
        digest = _public_sha256(reference.get("sha256"))
        if digest is not None:
            projected[key] = {"sha256": digest}

    canonical = value.get("canonical_session")
    if isinstance(canonical, dict):
        session: dict[str, object] = {}
        byte_count = canonical.get("bytes")
        if type(byte_count) is int and byte_count >= 0:
            session["bytes"] = byte_count
        digest = _public_sha256(canonical.get("sha256"))
        if digest is not None:
            session["sha256"] = digest
        if session:
            projected["canonical_session"] = session
    return projected


def _public_artifact_url(value: object, artifacts_root: Path) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = _artifact_candidate(artifacts_root.resolve(), value)
    if candidate is None:
        return None
    relative = candidate.relative_to(artifacts_root.resolve())
    if ".codex-private" in relative.parts:
        return None
    return f"/artifacts/{relative.as_posix()}"


def _project_slide(slide: dict[str, Any], artifacts_root: Path) -> dict[str, Any]:
    projected: dict[str, Any] = {}
    for key in _PUBLIC_SLIDE_FIELDS:
        if key not in slide:
            continue
        if key == "final_image_path":
            image_path = _public_png_path(slide, artifacts_root)
            projected[key] = _public_artifact_url(str(image_path), artifacts_root) if image_path else None
            continue
        safe = _sanitize(slide[key], key)
        if safe is not _DROP:
            projected[key] = safe
    return projected


def _project_run(run: dict[str, Any], artifacts_root: Path) -> dict[str, Any]:
    projected: dict[str, Any] = {}
    for key in _PUBLIC_RUN_FIELDS:
        if key not in run:
            continue
        if key == "slides" and isinstance(run[key], list):
            projected[key] = [_project_slide(slide, artifacts_root) for slide in run[key]]
            continue
        safe = _sanitize(run[key], key)
        if safe is not _DROP:
            projected[key] = safe
    return projected


def _project_batch(batch: dict[str, Any], artifacts_root: Path) -> dict[str, Any]:
    projected: dict[str, Any] = {}
    for key in _PUBLIC_BATCH_FIELDS:
        if key not in batch:
            continue
        if key == "runs" and isinstance(batch[key], list):
            projected[key] = [
                _project_run(run, artifacts_root) for run in batch[key] if _run_is_public(run)
            ]
            continue
        safe = _sanitize(batch[key], key)
        if safe is not _DROP:
            projected[key] = safe
    return projected


def _parse_json_dict(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _public_run_detail(run_id: int, artifacts_root: Path) -> dict[str, Any] | None:
    run = dbmod.get_run(run_id)
    if not _run_is_public(run):
        return None
    assert run is not None
    run["progress"] = dbmod.get_run_progress(run_id)
    run["stage_artifacts"] = _parse_json_dict(run.get("stage_artifacts"))
    slides = dbmod.list_run_slides(run_id)
    for slide in slides:
        slide["stage_artifacts"] = _parse_json_dict(slide.get("stage_artifacts"))
        slide["seed_dependency"] = _parse_json_dict(slide.get("seed_dependency"))
    run["slides"] = slides
    audit = codex_audit.get_codex_run_audit(run_id)
    audit.pop("machine_qa", None)
    audit.pop("machine_qa_summary", None)
    run["codex_audit"] = audit
    return _project_run(run, artifacts_root)


def _public_run_summaries(artifacts_root: Path) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for run in dbmod.list_runs():
        if not _run_is_public(run):
            continue
        run["progress"] = dbmod.get_run_progress(int(run["id"]))
        run["stage_artifacts"] = _parse_json_dict(run.get("stage_artifacts"))
        summaries.append(_project_run(run, artifacts_root))
    return summaries


def _batch_ids() -> list[int]:
    db = dbmod.get_db()
    try:
        rows = db.execute("SELECT id FROM batches ORDER BY id DESC").fetchall()
    finally:
        db.close()
    return [int(row["id"]) for row in rows]


def _public_batch_summary(batch_id: int, artifacts_root: Path) -> dict[str, Any] | None:
    if not _batch_id_is_public(batch_id):
        return None
    batch = dbmod.get_batch_summary(batch_id)
    return _project_batch(batch, artifacts_root) if batch else None


def _public_batch_detail(batch_id: int, artifacts_root: Path) -> dict[str, Any] | None:
    batch = _public_batch_summary(batch_id, artifacts_root)
    if batch is None:
        return None
    batch["runs"] = []
    for run in dbmod.list_runs_for_batch(batch_id):
        detail = _public_run_detail(int(run["id"]), artifacts_root)
        if detail is None:
            return None
        batch["runs"].append(detail)
    return batch


def _public_batch_summaries(artifacts_root: Path) -> list[dict[str, Any]]:
    summaries = []
    for batch_id in _batch_ids():
        summary = _public_batch_summary(batch_id, artifacts_root)
        if summary is not None:
            summaries.append(summary)
    return summaries


def _public_active_batch(artifacts_root: Path) -> dict[str, Any] | None:
    for batch in _public_batch_summaries(artifacts_root):
        if batch.get("status") in run_status.ACTIVE_STATUSES:
            return batch
    return None


def _zip_pngs_for_run(
    archive: zipfile.ZipFile,
    run: dict[str, Any],
    artifacts_root: Path,
    *,
    prefix: str,
) -> list[dict[str, Any]]:
    included: list[dict[str, Any]] = []
    for slide in dbmod.list_run_slides(int(run["id"])):
        image_path = _public_png_path(slide, artifacts_root)
        if image_path is None:
            continue
        data = image_path.read_bytes()
        position = int(slide.get("position") or 0)
        zip_path = f"{prefix}slides/slide-{position:02d}.png"
        archive.writestr(zip_path, data)
        included.append(
            {
                "run_slide_id": int(slide["id"]),
                "position": position,
                "zip_path": zip_path,
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
    return included


def _zip_response(buffer: BytesIO, filename: str):
    buffer.seek(0)
    return send_file(
        buffer,
        mimetype="application/zip",
        as_attachment=True,
        download_name=filename,
    )


def _public_run_download(run_id: int, artifacts_root: Path):
    run = dbmod.get_run(run_id)
    if not _run_is_public(run):
        return _not_found()
    assert run is not None
    if not run_status.is_terminal(run.get("status")):
        return jsonify({"error": "Run is not ready for download"}), 409
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        included = _zip_pngs_for_run(archive, run, artifacts_root, prefix="")
        manifest = {
            "surface": "public_image_3_0",
            "run_id": run_id,
            "batch_id": run.get("batch_id"),
            "status": run.get("status"),
            "route": {"engine": "image", "strategy": model_profiles.NATIVE_IMAGE_3_0_ROUTE},
            "included": included,
        }
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    return _zip_response(buffer, f"run-{run_id}-image-3-0.zip")


def _public_batch_download(batch_id: int, artifacts_root: Path):
    if not _batch_id_is_public(batch_id):
        return _not_found()
    batch = dbmod.get_batch(batch_id)
    runs = dbmod.list_runs_for_batch(batch_id)
    if (
        not batch
        or not run_status.is_terminal(batch.get("status"))
        or not all(run_status.is_terminal(run.get("status")) for run in runs)
    ):
        return jsonify({"error": "Batch is not ready for download"}), 409
    buffer = BytesIO()
    included: list[dict[str, Any]] = []
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for run in runs:
            included.extend(
                _zip_pngs_for_run(
                    archive,
                    run,
                    artifacts_root,
                    prefix=f"runs/run-{int(run['id'])}/",
                )
            )
        manifest = {
            "surface": "public_image_3_0",
            "batch_id": batch_id,
            "status": batch.get("status"),
            "route": {"engine": "image", "strategy": model_profiles.NATIVE_IMAGE_3_0_ROUTE},
            "included": included,
        }
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    return _zip_response(buffer, f"batch-{batch_id}-image-3-0.zip")


def _public_slide_evidence_download(run_slide_id: int, artifacts_root: Path):
    if not _run_slide_id_is_public(run_slide_id):
        return _not_found()
    active = dbmod.get_active_artifact_version(run_slide_id)
    if not active:
        return _not_found()
    db = dbmod.get_db()
    try:
        row = db.execute("SELECT * FROM run_slides WHERE id = ?", (run_slide_id,)).fetchone()
    finally:
        db.close()
    if not row:
        return _not_found()
    slide = dict(row)
    slide["active_version"] = active
    slide["final_image_path"] = active.get("final_image_path") or slide.get("final_image_path")
    image_path = _public_png_path(slide, artifacts_root)
    if image_path is None:
        return _not_found()
    image_bytes = image_path.read_bytes()
    resolved_marker = _native_audit_marker_for_active_version(
        active_version=active,
        image_bytes=image_bytes,
    )
    if resolved_marker is None:
        return _not_found()
    marker = resolved_marker
    binding = codex_audit.native_public_audit_binding(marker)
    if binding is None:
        return _not_found()
    projection = binding["public_projection"]
    manifest = {
        "surface": "public_image_3_0",
        "run_slide_id": run_slide_id,
        "included": ["active_artifact/slide.png", "native_audit/projection.json"],
        "png_sha256": hashlib.sha256(image_bytes).hexdigest(),
    }
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("active_artifact/slide.png", image_bytes)
        archive.writestr(
            "native_audit/projection.json",
            json.dumps(projection, ensure_ascii=False, indent=2),
        )
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    return _zip_response(buffer, f"slide-{run_slide_id}-evidence.zip")


def _folder_is_public(folder_id: object) -> bool:
    if type(folder_id) is not int or folder_id <= 0:
        return False
    folder = folders.get_folder(folder_id)
    return bool(folder and folder.get("scope") == "deck")


def _folder_parent_is_public(payload: dict[str, Any]) -> bool:
    parent_id = payload.get("parent_id")
    return parent_id is None or _folder_is_public(parent_id)


def _public_generate_payload_is_valid(payload: object) -> bool:
    if not isinstance(payload, dict) or set(payload) != _PUBLIC_GENERATE_FIELDS:
        return False
    deck_id = payload["deck_id"]
    config_id = payload["config_id"]
    if (
        type(deck_id) is not int
        or deck_id <= 0
        or not dbmod.get_deck(deck_id)
        or len(dbmod.list_slides(deck_id)) < 2
        or type(config_id) is not int
        or config_id <= 0
        or config_id not in _public_config_ids()
        or payload["engine"] != "image"
        or payload["strategy"] != model_profiles.NATIVE_IMAGE_3_0_ROUTE
        or type(payload["requirement_ids"]) is not list
        or payload["requirement_ids"] != []
        or type(payload["color_ids"]) is not list
        or payload["color_ids"] != []
    ):
        return False
    return True


def _public_split_execution() -> dict[str, Any]:
    """Return the server-owned faithful split execution identity."""
    return _public_split_execution_for_model(_public_split_model())


def _public_split_execution_for_model(model: str) -> dict[str, Any]:
    """Resolve one of the two server-owned recovery identities."""
    identities = {
        _PUBLIC_SPLIT_MODEL: _PUBLIC_SPLIT_PROFILE_NAME,
        _PUBLIC_TERRA_SPLIT_MODEL: _PUBLIC_TERRA_SPLIT_PROFILE_NAME,
    }
    try:
        profile_name = identities[str(model)]
    except KeyError as exc:
        raise deck_split_drafts.SplitDraftError(
            "Public Image split model is not configured"
        ) from exc
    db = dbmod.get_db()
    try:
        row = db.execute(
            """SELECT * FROM model_profiles
               WHERE role = 'auto_spill'
                 AND name = ?
                 AND model = ?
                 AND api_type = ?
                 AND endpoint = ?
                 AND status = 'active'
               ORDER BY id""",
            (
                profile_name,
                model,
                model_profiles.CODEX_EXEC_API_TYPE,
                model_profiles.CODEX_EXEC_ENDPOINT,
            ),
        ).fetchone()
    finally:
        db.close()
    if not row:
        raise deck_split_drafts.SplitDraftError(
            "Public Image split profile is not configured"
        )
    if (
        str(row["api_key"] or "").strip()
        or float(row["temperature"] or 0) != 1.0
    ):
        raise deck_split_drafts.SplitDraftError(
            "Public Image split profile is not configured"
        )
    config = dict(row)
    config.update(
        {
            "profile_id": int(row["id"]),
            "profile_name": row["name"],
            "thinking": _PUBLIC_SPLIT_THINKING,
            "content_mode": _PUBLIC_SPLIT_CONTENT_MODE,
        }
    )
    return config


def _public_split_identity_matches(row: dict[str, Any], config: dict[str, Any]) -> bool:
    return bool(
        row.get("mode") == "llm_auto"
        and row.get("model") == config.get("model")
        and row.get("model") in {_PUBLIC_SPLIT_MODEL, _PUBLIC_TERRA_SPLIT_MODEL}
        and row.get("model_profile_id") == config.get("profile_id")
        and row.get("thinking_effort") == _PUBLIC_SPLIT_THINKING
        and row.get("content_mode") == _PUBLIC_SPLIT_CONTENT_MODE
    )


def _public_split_draft_record(draft_id: int) -> dict[str, Any] | None:
    db = dbmod.get_db()
    try:
        row = db.execute(
            "SELECT * FROM deck_split_drafts WHERE id = ?", (draft_id,)
        ).fetchone()
    finally:
        db.close()
    return dict(row) if row else None


def _public_split_body_tokens(value: object) -> list[str]:
    """Tokenize non-heading source text without changing punctuation or numbers."""
    text = str(value or "")
    lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not re.match(r"^#{1,6}\s+", line.strip())
    ]
    return re.findall(r"\w+|[^\w\s]", " ".join(lines), flags=re.UNICODE)


def _public_split_source_parity(
    deck_content: str,
    slides: list[dict[str, str]],
) -> None:
    """Reject faithful output that drops, reorders, paraphrases, or changes source facts."""
    expected = _public_split_body_tokens(deck_content)
    observed = _public_split_body_tokens(
        "\n".join(str(slide.get("content") or "") for slide in slides)
    )
    if expected != observed:
        raise deck_split_drafts.SplitDraftError(
            "Auto Split integrity check failed: public faithful source parity mismatch"
        )


def _public_split_draft_dto(row: dict[str, Any]) -> dict[str, Any]:
    raw_slides = row.get("slides")
    if raw_slides is None:
        raw_slides = row.get("slides_json")
    if isinstance(raw_slides, str):
        try:
            raw_slides = json.loads(raw_slides)
        except json.JSONDecodeError:
            raw_slides = []
    if not isinstance(raw_slides, list):
        raw_slides = []
    slides = [
        {
            "title": str(slide.get("title") or "").strip(),
            "content": str(slide.get("content") or "").strip(),
        }
        for slide in raw_slides
        if isinstance(slide, dict)
    ]
    return {
        "id": int(row["id"]),
        "deck_id": int(row["deck_id"]),
        "status": str(row.get("status") or ""),
        "model": str(row.get("model") or ""),
        "attempt_count": int(row.get("attempt_count") or 0),
        "content_mode": _PUBLIC_SPLIT_CONTENT_MODE,
        "page_count": len(slides),
        "slides": slides,
    }


def _public_split_error(error: Exception):
    if isinstance(error, deck_split_drafts.TargetPageCountUnavailable):
        return (
            jsonify({"error": error.code, "message": str(error)}),
            error.status_code,
        )
    if (
        isinstance(error, deck_split_drafts.SplitDraftExecutionError)
        and error.code == "resource_unavailable"
    ):
        return (
            jsonify(
                {
                    "error": error.code,
                    "message": str(error),
                    "draft": _public_split_draft_dto(error.draft),
                }
            ),
            error.status_code,
        )
    if (
        isinstance(error, deck_split_drafts.SplitDraftExecutionError)
        and error.code == "executable_identity_unavailable"
    ):
        return (
            jsonify(
                {
                    "error": error.code,
                    "message": str(error),
                }
            ),
            error.status_code,
        )
    if isinstance(error, deck_split_drafts.SplitDraftExecutionError):
        return (
            jsonify(
                {
                    "error": str(error),
                    "draft": _public_split_draft_dto(error.draft),
                }
            ),
            error.status_code,
        )
    if isinstance(error, deck_split_drafts.SplitDraftError):
        return jsonify({"error": str(error)}), error.status_code
    return jsonify({"error": "Public Image split failed"}), 500


def _public_record_split_attempt(draft_id: int, config: dict[str, Any]) -> None:
    """Persist the identity immediately before one real provider attempt."""
    db = dbmod.get_db()
    try:
        updated = db.execute(
            """UPDATE deck_split_drafts
               SET model = ?, model_profile_id = ?, thinking_effort = ?,
                   content_mode = ?, attempt_count = COALESCE(attempt_count, 0) + 1,
                   last_error_code = NULL, error_message = NULL
               WHERE id = ? AND status IN ('running', 'pending')""",
            (
                str(config["model"]),
                int(config["profile_id"]),
                _PUBLIC_SPLIT_THINKING,
                _PUBLIC_SPLIT_CONTENT_MODE,
                draft_id,
            ),
        )
        if updated.rowcount != 1:
            db.rollback()
            raise deck_split_drafts.SplitDraftConflict(
                "Split draft changed before execution started"
            )
        db.commit()
    finally:
        db.close()


def _public_record_split_failure(
    draft_id: int, failure: deck_split_drafts.SplitExecutionFailure
) -> None:
    """Keep the latest failed public attempt visible without discarding a draft."""
    db = dbmod.get_db()
    try:
        db.execute(
            """UPDATE deck_split_drafts
               SET last_error_code = ?, error_message = ?
               WHERE id = ?""",
            (failure.code, failure.message, draft_id),
        )
        db.commit()
    finally:
        db.close()


def _public_run_split_sequence(
    draft_id: int,
    operation,
    *,
    initial_config: dict[str, Any] | None = None,
):
    """Run only the approved Public faithful Luna/Luna/Terra sequence."""
    config = initial_config or _public_split_execution()
    if config.get("model") == _PUBLIC_TERRA_SPLIT_MODEL:
        _public_record_split_attempt(draft_id, config)
        try:
            return operation(config), None
        except Exception as error:
            return None, deck_split_drafts._failure_for_exception(error)
    for attempt_index in range(3):
        if attempt_index == 2:
            try:
                config = _public_split_execution_for_model(_PUBLIC_TERRA_SPLIT_MODEL)
            except Exception as error:
                return None, deck_split_drafts.SplitExecutionFailure(
                    "configuration", str(error)
                )
        _public_record_split_attempt(draft_id, config)
        try:
            return operation(config), None
        except Exception as error:
            failure = deck_split_drafts._failure_for_exception(error)
            if (
                failure.transport_only
                and config.get("model") == _PUBLIC_SPLIT_MODEL
                and attempt_index < 2
            ):
                continue
            return None, failure
    return None, deck_split_drafts.SplitExecutionFailure(
        "provider_rejected", "Public Image split sequence failed"
    )


def _public_create_split_draft(deck_id: int) -> dict[str, Any]:
    deck = dbmod.get_deck(deck_id)
    if not deck:
        raise deck_split_drafts.SplitDraftError("Deck not found")
    config = _public_split_execution()
    db = dbmod.get_db()
    try:
        cur = db.execute(
            """INSERT INTO deck_split_drafts
               (deck_id, status, mode, model, model_profile_id, thinking_effort,
                content_mode, attempt_count, last_error_code, error_message, slides_json)
               VALUES (?, 'running', 'llm_auto', ?, ?, ?, ?, 0, NULL, NULL, '[]')""",
            (
                deck_id,
                _public_split_model(),
                int(config["profile_id"]),
                _PUBLIC_SPLIT_THINKING,
                _PUBLIC_SPLIT_CONTENT_MODE,
            ),
        )
        db.commit()
        draft_id = int(cur.lastrowid)
    finally:
        db.close()

    prompt = deck_split_drafts.prompt_path_for_mode(
        _PUBLIC_SPLIT_CONTENT_MODE
    ).read_text(encoding="utf-8").replace("{{Context}}", deck["content"])
    def propose(current_config: dict[str, Any]):
        slides = deck_split_drafts.normalize_slides(
            deck_split_drafts.generate_llm_split(
                deck["content"], current_config, prompt
            )
        )
        deck_split_drafts.validate_split_for_mode(
            _PUBLIC_SPLIT_CONTENT_MODE, deck["content"], slides
        )
        _public_split_source_parity(deck["content"], slides)
        return slides

    try:
        slides, failure = _public_run_split_sequence(draft_id, propose)
        if failure is not None:
            raise failure
    except Exception as error:
        failure = deck_split_drafts._failure_for_exception(error)
        failed = deck_split_drafts._mark_draft_failed(draft_id, failure)
        raise deck_split_drafts.SplitDraftExecutionError(
            failure.message,
            code=failure.code,
            draft=failed,
        ) from error

    db = dbmod.get_db()
    try:
        updated = db.execute(
            """UPDATE deck_split_drafts
               SET status = 'pending', slides_json = ?, last_error_code = NULL,
                   error_message = NULL
               WHERE id = ? AND status = 'running'""",
            (json.dumps(slides, ensure_ascii=False), draft_id),
        )
        if updated.rowcount != 1:
            db.rollback()
            raise deck_split_drafts.SplitDraftConflict(
                "Split draft changed before execution completed"
            )
        db.commit()
        row = db.execute(
            "SELECT * FROM deck_split_drafts WHERE id = ?", (draft_id,)
        ).fetchone()
    finally:
        db.close()
    return _public_split_draft_dto(dict(row))


def _public_revise_split_draft(
    draft_id: int,
    instruction: str | None = None,
    target_page_count: int | None | object = _UNSET_TARGET_PAGE_COUNT,
) -> dict[str, Any]:
    target_page_count_supplied = target_page_count is not _UNSET_TARGET_PAGE_COUNT
    if (instruction is not None) == target_page_count_supplied:
        raise deck_split_drafts.SplitDraftError(
            "Split revision requires exactly one of instruction or target_page_count"
        )
    draft = _public_split_draft_record(draft_id)
    if not draft:
        raise deck_split_drafts.SplitDraftError("Split draft not found")
    if draft.get("status") != "pending":
        raise deck_split_drafts.SplitDraftConflict(
            "Only a pending split draft can be revised"
        )
    try:
        config = _public_split_execution_for_model(str(draft.get("model") or ""))
    except deck_split_drafts.SplitDraftError as error:
        raise deck_split_drafts.SplitDraftConflict(
            "Split draft is not a public Image faithful draft"
        ) from error
    if not _public_split_identity_matches(draft, config):
        raise deck_split_drafts.SplitDraftConflict(
            "Split draft is not a public Image faithful draft"
        )
    deck = dbmod.get_deck(int(draft["deck_id"]))
    if not deck:
        raise deck_split_drafts.SplitDraftError("Deck not found")
    if target_page_count_supplied:
        revised = deck_split_drafts.revise_split_draft_target_page_count(
            draft_id,
            target_page_count,  # type: ignore[arg-type]
        )
        return _public_split_draft_dto(revised)
    try:
        current_slides = deck_split_drafts.normalize_slides(
            json.loads(draft.get("slides_json") or "[]")
        )

        def revise(current_config: dict[str, Any]):
            revised = deck_split_drafts.normalize_slides(
                deck_split_drafts.generate_split_revision(
                    deck["content"],
                    current_slides,
                    instruction,
                    current_config,
                    content_mode=_PUBLIC_SPLIT_CONTENT_MODE,
                )
            )
            deck_split_drafts.validate_split_for_mode(
                _PUBLIC_SPLIT_CONTENT_MODE, deck["content"], revised
            )
            _public_split_source_parity(deck["content"], revised)
            return revised

        revised_slides, failure = _public_run_split_sequence(
            draft_id,
            revise,
            initial_config=config,
        )
        if failure is not None:
            raise failure
    except Exception as error:
        failure = deck_split_drafts._failure_for_exception(error)
        _public_record_split_failure(draft_id, failure)
        current = _public_split_draft_record(draft_id) or draft
        raise deck_split_drafts.SplitDraftExecutionError(
            failure.message,
            code=failure.code,
            draft=current,
        ) from error

    db = dbmod.get_db()
    try:
        updated = db.execute(
            """UPDATE deck_split_drafts
               SET slides_json = ?
               WHERE id = ? AND status = 'pending'""",
            (json.dumps(revised_slides, ensure_ascii=False), draft_id),
        )
        if updated.rowcount != 1:
            db.rollback()
            raise deck_split_drafts.SplitDraftConflict(
                "Split draft changed before revision completed"
            )
        db.commit()
        row = db.execute(
            "SELECT * FROM deck_split_drafts WHERE id = ?", (draft_id,)
        ).fetchone()
    finally:
        db.close()
    return _public_split_draft_dto(dict(row))


def _public_confirm_split_draft(draft_id: int) -> dict[str, Any]:
    result = deck_split_drafts.confirm_image_skill_split_draft(draft_id)
    slides = result.get("slides") if isinstance(result, dict) else None
    deck_id = result.get("deck_id") if isinstance(result, dict) else None
    if deck_id is None and isinstance(slides, list) and slides:
        deck_id = slides[0].get("deck_id") if isinstance(slides[0], dict) else None
    slide_ids = result.get("slide_ids") if isinstance(result, dict) else None
    if not isinstance(slide_ids, list):
        slide_ids = []
    return {
        "deck_id": int(deck_id),
        "draft_id": draft_id,
        "slide_count": len(slide_ids),
        "slide_ids": [int(slide_id) for slide_id in slide_ids],
        "status": "confirmed",
    }


def _public_split_draft_is_public(draft_id: object) -> bool:
    if type(draft_id) is not int or draft_id <= 0:
        return False
    draft = _public_split_draft_record(draft_id)
    if not draft or draft.get("status") != "pending":
        return False
    try:
        config = _public_split_execution_for_model(str(draft.get("model") or ""))
    except deck_split_drafts.SplitDraftError:
        return False
    return _public_split_identity_matches(draft, config)


def _request_is_allowed(artifacts_root: Path):
    path = request.path
    if path.startswith("/artifacts/"):
        filename = str((request.view_args or {}).get("filename") or path[len("/artifacts/") :])
        return None if request.method == "GET" and _artifact_is_public(filename, artifacts_root) else _not_found()
    if not path.startswith("/api/"):
        return None
    rule = request.url_rule.rule if request.url_rule else ""
    if (request.method, rule) not in _ALLOWED_API_RULES:
        return _not_found()

    view_args = request.view_args or {}
    if rule == "/api/configs/<int:config_id>" and int(view_args["config_id"]) not in _public_config_ids():
        return _not_found()
    if rule == "/api/folders":
        if request.method == "GET":
            if set(request.args) != {"scope"} or request.args.get("scope") != "deck":
                return _not_found()
        else:
            payload = request.get_json(silent=True)
            if (
                not isinstance(payload, dict)
                or not set(payload).issubset({"scope", "name", "parent_id"})
                or payload.get("scope") != "deck"
                or not _folder_parent_is_public(payload)
            ):
                return _not_found()
    if rule == "/api/folders/<int:folder_id>":
        folder = folders.get_folder(int(view_args["folder_id"]))
        if not folder or folder.get("scope") != "deck":
            return _not_found()
        payload = request.get_json(silent=True)
        if (
            not isinstance(payload, dict)
            or not set(payload).issubset({"name", "parent_id"})
            or not _folder_parent_is_public(payload)
        ):
            return _not_found()
    if rule == "/api/decks/<int:deck_id>/folders":
        payload = request.get_json(silent=True)
        folder_ids = payload.get("folder_ids") if isinstance(payload, dict) else None
        if (
            not isinstance(payload, dict)
            or set(payload) != {"folder_ids"}
            or not isinstance(folder_ids, list)
            or not all(_folder_is_public(folder_id) for folder_id in folder_ids)
        ):
            return _not_found()
    if rule == "/api/bulk-actions":
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or payload.get("entity_type") != "deck":
            return _not_found()
    if rule == "/api/decks/<int:deck_id>/split":
        payload = request.get_json(silent=True)
        if payload is None:
            payload = {}
        if (
            not isinstance(payload, dict)
            or not set(payload).issubset({"replace"})
            or ("replace" in payload and type(payload["replace"]) is not bool)
        ):
            return _not_found()
    if rule == "/api/decks/<int:deck_id>/split-drafts":
        payload = request.get_json(silent=True)
        if (
            request.args
            or (request.data and payload is None)
            or (payload is not None and payload != {})
            or not dbmod.get_deck(int(view_args["deck_id"]))
        ):
            return _not_found()
    if rule == "/api/deck-split-drafts/<int:draft_id>/revise":
        payload = request.get_json(silent=True)
        instruction = payload.get("instruction") if isinstance(payload, dict) else None
        target_page_count = (
            payload.get("target_page_count") if isinstance(payload, dict) else None
        )
        payload_keys = set(payload) if isinstance(payload, dict) else set()
        valid_instruction = (
            payload_keys == {"instruction"}
            and isinstance(instruction, str)
            and bool(instruction.strip())
        )
        valid_target_page_count = payload_keys == {"target_page_count"}
        if (
            request.args
            or not isinstance(payload, dict)
            or not (valid_instruction or valid_target_page_count)
            or not _public_split_draft_is_public(int(view_args["draft_id"]))
        ):
            return _not_found()
    if rule == "/api/deck-split-drafts/<int:draft_id>/confirm":
        payload = request.get_json(silent=True)
        if (
            request.args
            or (request.data and payload is None)
            or (payload is not None and payload != {})
            or not _public_split_draft_is_public(int(view_args["draft_id"]))
        ):
            return _not_found()
    if rule == "/api/generate":
        if not _public_generate_payload_is_valid(request.get_json(silent=True)):
            return _not_found()
    if "run_id" in view_args and not _run_id_is_public(int(view_args["run_id"])):
        return _not_found()
    if "batch_id" in view_args and not _batch_id_is_public(int(view_args["batch_id"])):
        return _not_found()
    if "run_slide_id" in view_args and not _run_slide_id_is_public(int(view_args["run_slide_id"])):
        return _not_found()
    return None


def _replace_json(response: Response, payload: object) -> Response:
    response.set_data(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    response.content_type = "application/json; charset=utf-8"
    return response


def _project_response(response: Response, artifacts_root: Path) -> Response:
    if response.status_code < 200 or response.status_code >= 300 or not response.is_json:
        return response
    rule = request.url_rule.rule if request.url_rule else ""
    payload = response.get_json()
    if rule == "/api/configs":
        return _replace_json(response, public_configs())
    if rule == "/api/configs/<int:config_id>":
        config_id = int((request.view_args or {})["config_id"])
        config = next((item for item in public_configs() if item["id"] == config_id), None)
        return _replace_json(response, config)
    if rule == "/api/runs" and isinstance(payload, list):
        return _replace_json(
            response,
            [_project_run(run, artifacts_root) for run in payload if _run_is_public(run)],
        )
    if rule == "/api/runs/<int:run_id>" and isinstance(payload, dict):
        return _replace_json(response, _project_run(payload, artifacts_root))
    if rule == "/api/runs/<int:run_id>/status" and isinstance(payload, dict):
        safe = _sanitize(payload)
        return _replace_json(response, {} if safe is _DROP else safe)
    if rule == "/api/batches" and isinstance(payload, list):
        return _replace_json(
            response,
            [
                _project_batch(batch, artifacts_root)
                for batch in payload
                if _batch_id_is_public(int(batch["id"]))
            ],
        )
    if rule == "/api/batches/active":
        if not isinstance(payload, dict) or not _batch_id_is_public(int(payload.get("id") or 0)):
            return _replace_json(response, None)
        return _replace_json(response, _project_batch(payload, artifacts_root))
    if rule == "/api/batches/<int:batch_id>" and isinstance(payload, dict):
        return _replace_json(response, _project_batch(payload, artifacts_root))
    if rule == "/api/runs/<int:run_id>/codex-audit/invocations/<int:invocation_id>" and isinstance(payload, dict):
        # The audit service already owns an explicit business-safe DTO. Re-project
        # that DTO here instead of applying the generic response denylist, which
        # would erase Run-owned prompt/output/tool/imagegen semantics.
        return _replace_json(response, codex_audit.public_native_audit_detail_projection(payload))
    if "/codex-audit/" in rule and isinstance(payload, dict):
        safe = _sanitize(payload)
        return _replace_json(response, {} if safe is _DROP else safe)
    if rule in {
        "/api/decks/<int:deck_id>/force-delete",
        "/api/bulk-actions",
    } and isinstance(payload, (dict, list)):
        safe = _sanitize(payload)
        return _replace_json(response, [] if safe is _DROP else safe)
    return response


def _route_endpoint(app, rule_text: str, method: str = "GET") -> str:
    for rule in app.url_map.iter_rules():
        if rule.rule == rule_text and method in rule.methods:
            return rule.endpoint
    raise RuntimeError(f"Public route is not registered: {method} {rule_text}")


def _install_public_read_views(app, artifacts_root: Path) -> None:
    def runtime_identity():
        try:
            return jsonify(_public_image_runtime_identity())
        except RuntimeError:
            return jsonify({"error": "Image runtime identity is unavailable"}), 503

    def list_configs():
        return jsonify(public_configs())

    def get_config(config_id: int):
        config = next((item for item in public_configs() if item["id"] == config_id), None)
        return jsonify(config) if config is not None else _not_found()

    def list_runs():
        return jsonify(_public_run_summaries(artifacts_root))

    def get_run(run_id: int):
        run = _public_run_detail(run_id, artifacts_root)
        return jsonify(run) if run is not None else _not_found()

    def get_run_status(run_id: int):
        run = dbmod.get_run(run_id)
        if not _run_is_public(run):
            return _not_found()
        assert run is not None
        try:
            activity = codex_activity_projection.project_run_activity(
                run_id,
                after_cursor=request.args.get("activity_after"),
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        payload = {
            "run_id": run_id,
            "status": run.get("status"),
            "progress": dbmod.get_run_progress(run_id),
            "error_message": run.get("error_message"),
            "started_at": run.get("started_at"),
            "completed_at": run.get("completed_at"),
            "activity": activity,
        }
        safe = _sanitize(payload)
        return jsonify({} if safe is _DROP else safe)

    def list_batches():
        return jsonify(_public_batch_summaries(artifacts_root))

    def get_active_batch():
        return jsonify(_public_active_batch(artifacts_root))

    def get_batch(batch_id: int):
        batch = _public_batch_detail(batch_id, artifacts_root)
        return jsonify(batch) if batch is not None else _not_found()

    def download_run(run_id: int):
        return _public_run_download(run_id, artifacts_root)

    def download_batch(batch_id: int):
        return _public_batch_download(batch_id, artifacts_root)

    def download_slide_evidence(run_slide_id: int):
        return _public_slide_evidence_download(run_slide_id, artifacts_root)

    def propose_split_draft(deck_id: int):
        try:
            draft = _public_create_split_draft(deck_id)
        except Exception as error:
            return _public_split_error(error)
        return jsonify(draft), 201

    def revise_split_draft(draft_id: int):
        payload = request.get_json(silent=True) or {}
        try:
            if "target_page_count" in payload:
                draft = _public_revise_split_draft(
                    draft_id,
                    target_page_count=payload.get("target_page_count"),
                )
            else:
                draft = _public_revise_split_draft(
                    draft_id,
                    instruction=str(payload.get("instruction") or ""),
                )
        except Exception as error:
            return _public_split_error(error)
        return jsonify(draft)

    def confirm_split_draft(draft_id: int):
        try:
            result = _public_confirm_split_draft(draft_id)
        except Exception as error:
            return _public_split_error(error)
        return jsonify(result)

    replacements = {
        "/api/runtime-identity": runtime_identity,
        "/api/configs": list_configs,
        "/api/configs/<int:config_id>": get_config,
        "/api/runs": list_runs,
        "/api/runs/<int:run_id>": get_run,
        "/api/runs/<int:run_id>/status": get_run_status,
        "/api/batches": list_batches,
        "/api/batches/active": get_active_batch,
        "/api/batches/<int:batch_id>": get_batch,
        "/api/runs/<int:run_id>/download": download_run,
        "/api/batches/<int:batch_id>/download": download_batch,
        "/api/run-slides/<int:run_slide_id>/evidence-download": download_slide_evidence,
        "/api/decks/<int:deck_id>/split-drafts": propose_split_draft,
        "/api/deck-split-drafts/<int:draft_id>/revise": revise_split_draft,
        "/api/deck-split-drafts/<int:draft_id>/confirm": confirm_split_draft,
    }
    for rule_text, view in replacements.items():
        method = (
            "POST"
            if rule_text
            in {
                "/api/decks/<int:deck_id>/split-drafts",
                "/api/deck-split-drafts/<int:draft_id>/revise",
                "/api/deck-split-drafts/<int:draft_id>/confirm",
            }
            else "GET"
        )
        app.view_functions[_route_endpoint(app, rule_text, method)] = view


def install_public_image_surface(app, *, artifacts_root: Path) -> None:
    """Install one idempotent fail-closed boundary on the shared Flask app."""
    if app.extensions.get("public_image_surface_installed"):
        return
    app.extensions["public_image_surface_installed"] = True
    _install_public_read_views(app, artifacts_root)

    @app.before_request
    def public_image_surface_guard():
        return _request_is_allowed(artifacts_root)

    @app.after_request
    def public_image_surface_projection(response: Response):
        return _project_response(response, artifacts_root)
