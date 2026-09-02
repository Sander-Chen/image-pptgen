"""Build safe per-batch artifact download archives."""

from __future__ import annotations

import json
import re
import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

import db as dbmod
from backend.domain import status as run_status
from backend.services import codex_audit

TERMINAL_STATUSES = run_status.TERMINAL_STATUSES
NON_TERMINAL_STATUSES = run_status.ACTIVE_STATUSES
HTML_SUFFIXES = {".html", ".htm"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}


@dataclass
class BatchDownloadArchive:
    data: BytesIO
    filename: str
    manifest: dict[str, Any]


class BatchDownloadError(Exception):
    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def build_batch_download(batch_id: int, artifacts_root: Path) -> BatchDownloadArchive:
    dbmod.update_batch_statuses()
    batch = dbmod.get_batch_detail(batch_id)
    if not batch:
        raise BatchDownloadError("Batch not found", 404)

    status = batch.get("status")
    if status in NON_TERMINAL_STATUSES or status not in TERMINAL_STATUSES:
        raise BatchDownloadError("Batch is not ready for download", 409)

    root = artifacts_root.resolve()
    manifest: dict[str, Any] = {
        "batch_id": batch_id,
        "status": status,
        "engine": batch.get("engine"),
        "strategy": batch.get("strategy"),
        "included": [],
        "skipped": [],
    }
    archive = BytesIO()
    used_names: set[str] = set()

    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for run_index, run in enumerate(batch.get("runs") or [], start=1):
            run_folder = _run_folder(run_index, run)
            engine = run.get("engine") or batch.get("engine") or "html"
            for slide in dbmod.list_run_slides(run["id"]):
                slide = _slide_for_active_version(slide)
                if engine == "image":
                    _add_existing_artifact(
                        zf,
                        used_names,
                        manifest,
                        root,
                        run,
                        slide,
                        slide.get("final_image_path"),
                        f"{run_folder}/slide-{slide.get('position', 0):02d}-final-image{_suffix(slide.get('final_image_path'), '.png')}",
                        "final_image",
                        IMAGE_SUFFIXES,
                    )
                else:
                    _add_html_screenshot(
                        zf,
                        used_names,
                        manifest,
                        root,
                        run,
                        slide,
                        run_folder,
                    )
                    _add_html_code(zf, used_names, manifest, root, run, slide, run_folder)

        zf.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))

    archive.seek(0)
    filename = f"batch-{batch_id}-{_slug(batch.get('engine') or 'run')}-artifacts.zip"
    return BatchDownloadArchive(data=archive, filename=filename, manifest=manifest)


def build_run_download(run_id: int, artifacts_root: Path) -> BatchDownloadArchive:
    dbmod.update_batch_statuses()
    run = dbmod.get_run(run_id)
    if not run:
        raise BatchDownloadError("Run not found", 404)

    status = run.get("status")
    if status in NON_TERMINAL_STATUSES or status not in TERMINAL_STATUSES:
        raise BatchDownloadError("Run is not ready for download", 409)

    root = artifacts_root.resolve()
    route = {
        "engine": run.get("engine") or "html",
        "strategy": run.get("strategy") or "html_default",
    }
    manifest: dict[str, Any] = {
        "run_id": run_id,
        "batch_id": run.get("batch_id"),
        "status": status,
        "route": route,
        "lineage": _lineage_for_run(run),
        "artifacts": [],
        "included": [],
        "skipped": [],
    }
    archive = BytesIO()
    used_names: set[str] = set()
    run_folder = f"run-{run_id}-{_slug(route['engine'])}-{_slug(route['strategy'])}"

    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for slide in dbmod.list_run_slides(run_id):
            slide = _slide_for_active_version(slide)
            if route["engine"] == "image":
                _add_existing_artifact(
                    zf,
                    used_names,
                    manifest,
                    root,
                    run,
                    slide,
                    slide.get("final_image_path"),
                    f"{run_folder}/slide-{slide.get('position', 0):02d}-final-image{_suffix(slide.get('final_image_path'), '.png')}",
                    "final_image",
                    IMAGE_SUFFIXES,
                )
            else:
                _add_html_screenshot(zf, used_names, manifest, root, run, slide, run_folder)
                _add_html_code(zf, used_names, manifest, root, run, slide, run_folder)

        manifest["artifacts"] = [item["zip_path"] for item in manifest["included"]]
        zf.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))

    archive.seek(0)
    filename = f"run-{run_id}-{_slug(route['engine'])}-artifacts.zip"
    return BatchDownloadArchive(data=archive, filename=filename, manifest=manifest)


def build_slide_evidence_download(run_slide_id: int, artifacts_root: Path) -> BatchDownloadArchive:
    active_version = dbmod.get_active_artifact_version(run_slide_id)
    if not active_version:
        raise BatchDownloadError("Slide has no active version evidence", 409)
    root = artifacts_root.resolve()
    archive = BytesIO()
    manifest: dict[str, Any] = {
        "run_slide_id": run_slide_id,
        "active_version": _version_manifest(active_version, root),
        "included": [],
        "skipped": [],
    }
    snapshot = active_version.get("evidence_snapshot") or {}
    if not isinstance(snapshot, dict):
        snapshot = {}
    native_evidence = codex_audit.contains_nested_native_private_evidence(snapshot)
    native_private_evidence = codex_audit.find_nested_native_private_evidence(snapshot)
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        artifact_path = active_version.get("final_image_path") or active_version.get("screenshot_path") or active_version.get("html_path")
        artifact_name = Path(str(artifact_path or "active-artifact.bin")).name
        _add_evidence_artifact(zf, manifest, root, active_version, artifact_path, f"active_artifact/{artifact_name}")
        if native_evidence and native_private_evidence is not None:
            native_projection_path = "native_audit/projection.json"
            zf.writestr(
                native_projection_path,
                json.dumps(
                    codex_audit.public_native_image_projection(native_private_evidence),
                    indent=2,
                    ensure_ascii=False,
                ),
            )
            manifest["included"].append(
                {
                    "kind": "native_audit_projection",
                    "zip_path": native_projection_path,
                    "source": "safe_native_projection",
                }
            )
        else:
            prompt_source = _prompt_source_from_snapshot(snapshot, root)
            if prompt_source:
                zf.writestr("prompt/prompt.txt", prompt_source)
            rendered_prompt = _rendered_prompt_from_snapshot(snapshot, root)
            zf.writestr("prompt/rendered_prompt.txt", str(rendered_prompt))
            zf.writestr("config/config.json", json.dumps(_safe_metadata(snapshot.get("config") or {}, root), indent=2, ensure_ascii=False))
            zf.writestr("response/provider_response.json", json.dumps(_safe_metadata(snapshot.get("response") or _json_value(active_version.get("raw_response")) or {}, root), indent=2, ensure_ascii=False))
            blueprint_xml = snapshot.get("blueprint_xml") or active_version.get("xml_clean") or active_version.get("xml_raw") or ""
            if blueprint_xml:
                zf.writestr("blueprint/blueprint.xml", str(blueprint_xml))
            request_chain = _request_chain_from_snapshot(snapshot)
            if request_chain:
                _add_request_chain_evidence(zf, manifest, root, request_chain)
            zf.writestr(
                "generation_history/history.json",
                json.dumps(_safe_metadata(dbmod.list_generation_history(target_run_slide_id=run_slide_id), root), indent=2, ensure_ascii=False),
            )
            zf.writestr(
                "versions/versions.json",
                json.dumps(_safe_metadata(dbmod.list_artifact_versions(run_slide_id), root), indent=2, ensure_ascii=False),
            )
        zf.writestr("manifest.json", json.dumps(_safe_metadata(manifest, root), indent=2, ensure_ascii=False))
    archive.seek(0)
    return BatchDownloadArchive(data=archive, filename=f"slide-{run_slide_id}-evidence.zip", manifest=manifest)


def _add_html_screenshot(
    zf: zipfile.ZipFile,
    used_names: set[str],
    manifest: dict[str, Any],
    root: Path,
    run: dict[str, Any],
    slide: dict[str, Any],
    run_folder: str,
) -> None:
    candidates: list[tuple[str | None, str]] = [
        (slide.get("screenshot_path"), "screenshot_path"),
    ]

    html_path = slide.get("html_path")
    resolved_html, _reason = _resolve_artifact_path(html_path, root)
    if resolved_html and resolved_html.suffix.lower() in HTML_SUFFIXES:
        candidates.append((str(resolved_html.with_suffix(".png")), "html_sibling_png"))

    last_reason = "missing_artifact"
    last_source = slide.get("screenshot_path") or html_path
    for source_value, _source_kind in candidates:
        resolved, reason = _resolve_artifact_path(source_value, root)
        if not resolved:
            last_reason = reason
            last_source = source_value or last_source
            continue
        if resolved.suffix.lower() not in IMAGE_SUFFIXES:
            last_reason = "invalid_artifact_type"
            last_source = source_value
            continue
        zip_name = _dedupe(
            f"{run_folder}/slide-{slide.get('position', 0):02d}-screenshot{resolved.suffix.lower() or '.png'}",
            used_names,
        )
        zf.write(resolved, zip_name)
        _mark_included(manifest, run, slide, "html_screenshot", zip_name, resolved, root)
        return

    _mark_skipped(manifest, run, slide, "html_screenshot", last_reason, last_source, root)


def _add_html_code(
    zf: zipfile.ZipFile,
    used_names: set[str],
    manifest: dict[str, Any],
    root: Path,
    run: dict[str, Any],
    slide: dict[str, Any],
    run_folder: str,
) -> None:
    zip_name = _dedupe(f"{run_folder}/slide-{slide.get('position', 0):02d}.html", used_names)
    html_path = slide.get("html_path")
    if html_path:
        resolved, reason = _resolve_artifact_path(html_path, root)
        if resolved and resolved.suffix.lower() in HTML_SUFFIXES:
            zf.write(resolved, zip_name)
            _mark_included(manifest, run, slide, "html_code", zip_name, resolved, root)
            return
        _mark_skipped(
            manifest,
            run,
            slide,
            "html_code",
            "invalid_artifact_type" if resolved else reason,
            html_path,
            root,
        )

    clean_html = slide.get("clean_html")
    if clean_html:
        zf.writestr(zip_name, clean_html)
        _mark_included(manifest, run, slide, "html_code", zip_name, "clean_html", root)
        return

    if not html_path:
        _mark_skipped(manifest, run, slide, "html_code", "missing_artifact", None, root)


def _add_existing_artifact(
    zf: zipfile.ZipFile,
    used_names: set[str],
    manifest: dict[str, Any],
    root: Path,
    run: dict[str, Any],
    slide: dict[str, Any],
    source_value: str | None,
    zip_name: str,
    kind: str,
    allowed_suffixes: set[str],
) -> None:
    resolved, reason = _resolve_artifact_path(source_value, root)
    if not resolved:
        _mark_skipped(manifest, run, slide, kind, reason, source_value, root)
        return
    if resolved.suffix.lower() not in allowed_suffixes:
        _mark_skipped(manifest, run, slide, kind, "invalid_artifact_type", source_value, root)
        return
    safe_zip_name = _dedupe(zip_name, used_names)
    zf.write(resolved, safe_zip_name)
    _mark_included(manifest, run, slide, kind, safe_zip_name, resolved, root)


def _slide_for_active_version(slide: dict[str, Any]) -> dict[str, Any]:
    active_version = dbmod.get_active_artifact_version(slide["id"])
    if not active_version:
        return slide
    merged = dict(slide)
    for key in ("html_path", "screenshot_path", "final_image_path", "clean_html", "xml_raw", "xml_clean", "raw_response"):
        if active_version.get(key):
            merged[key] = active_version[key]
    merged["_active_version"] = active_version
    return merged


def _version_manifest(version: dict[str, Any] | None, root: Path | None = None) -> dict[str, Any] | None:
    if not version:
        return None
    manifest = {
        "id": version.get("id"),
        "target_run_slide_id": version.get("target_run_slide_id"),
        "artifact_run_slide_id": version.get("artifact_run_slide_id"),
        "version_number": version.get("version_number"),
        "status": version.get("status"),
        "html_path": version.get("html_path"),
        "screenshot_path": version.get("screenshot_path"),
        "final_image_path": version.get("final_image_path"),
        "created_at": version.get("created_at"),
    }
    return _safe_metadata(manifest, root) if root else manifest


def _add_evidence_artifact(
    zf: zipfile.ZipFile,
    manifest: dict[str, Any],
    root: Path,
    version: dict[str, Any],
    source_value: str | None,
    zip_name: str,
) -> None:
    resolved, reason = _resolve_artifact_path(source_value, root)
    if not resolved:
        manifest["skipped"].append({
            "kind": "active_artifact",
            "reason": reason,
            "source": _manifest_source(source_value, root, reason),
        })
        return
    zf.write(resolved, zip_name)
    manifest["included"].append({
        "kind": "active_artifact",
        "zip_path": zip_name,
        "source": _manifest_source(resolved, root),
        "version": _version_manifest(version, root),
    })


def _safe_metadata(value: Any, root: Path | None = None, key_name: str = "") -> Any:
    value = codex_audit.project_native_public_value(value)
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            if re.search(r"api[_-]?key|authorization|token|secret", str(key), re.I):
                redacted[key] = "<redacted>"
            else:
                redacted[key] = _safe_metadata(item, root, str(key))
        return redacted
    if isinstance(value, list):
        return [_safe_metadata(item, root, key_name) for item in value]
    if isinstance(value, str) and root:
        stripped = value.strip()
        if stripped.startswith("{") or stripped.startswith("["):
            try:
                return _safe_metadata(json.loads(stripped), root, key_name)
            except json.JSONDecodeError:
                pass
        if re.search(r"path$", key_name, re.I) or value.startswith("/"):
            candidate = Path(value)
            if candidate.is_absolute():
                return _manifest_source(value, root, "unsafe_artifact_path")
            if ".." in candidate.parts:
                return "<redacted:unsafe_artifact_path>"
            return value
    return value


def _redact(value: Any) -> Any:
    return _safe_metadata(value)


def _json_value(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str) or not value:
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return {"raw_response": value}


def _request_chain_from_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    direct = snapshot.get("request_chain")
    if isinstance(direct, dict):
        return direct
    slide_stage_artifacts = snapshot.get("slide_stage_artifacts")
    if isinstance(slide_stage_artifacts, dict) and isinstance(slide_stage_artifacts.get("request_chain"), dict):
        return slide_stage_artifacts["request_chain"]
    return {}


def _add_request_chain_evidence(
    zf: zipfile.ZipFile,
    manifest: dict[str, Any],
    root: Path,
    request_chain: dict[str, Any],
) -> None:
    zf.writestr(
        "request_chain/request_chain.json",
        json.dumps(_safe_metadata(request_chain, root), indent=2, ensure_ascii=False),
    )
    manifest["included"].append(
        {
            "kind": "request_chain",
            "zip_path": "request_chain/request_chain.json",
            "source": "evidence_snapshot.slide_stage_artifacts.request_chain",
        }
    )
    stages = request_chain.get("stages")
    if not isinstance(stages, list):
        return
    for index, stage in enumerate(stages, start=1):
        if not isinstance(stage, dict):
            continue
        stage_id = str(stage.get("id") or stage.get("stage_name") or f"stage-{index}")
        stage_folder = f"request_chain/stages/{index:02d}-{_slug(stage_id)}"
        for path_key, artifact_kind in (
            ("prompt_path", "prompt"),
            ("request_path", "request"),
            ("response_path", "response"),
        ):
            source_value = stage.get(path_key)
            if not source_value:
                continue
            resolved, reason = _resolve_artifact_path(str(source_value), root)
            kind = f"request_chain_stage_{artifact_kind}"
            if not resolved:
                manifest["skipped"].append(
                    {
                        "kind": kind,
                        "stage_id": stage_id,
                        "reason": reason,
                        "source": _manifest_source(str(source_value), root, reason),
                    }
                )
                continue
            suffix = resolved.suffix.lower() or (".txt" if artifact_kind == "prompt" else ".json")
            zip_path = f"{stage_folder}/{artifact_kind}{suffix}"
            _write_stage_artifact(zf, resolved, zip_path, root)
            manifest["included"].append(
                {
                    "kind": kind,
                    "stage_id": stage_id,
                    "zip_path": zip_path,
                    "source": _manifest_source(resolved, root),
                }
            )


def _write_stage_artifact(zf: zipfile.ZipFile, source: Path, zip_path: str, root: Path) -> None:
    text = source.read_text(encoding="utf-8", errors="replace")
    if source.suffix.lower() == ".json":
        zf.writestr(
            zip_path,
            json.dumps(_safe_metadata(_json_value(text), root), indent=2, ensure_ascii=False),
        )
        return
    zf.writestr(zip_path, text)


def _nested_prompt_path(value: Any, ancestors: tuple[str, ...] = ()) -> str | None:
    if isinstance(value, dict):
        for key, item in value.items():
            next_ancestors = (*ancestors, str(key).lower())
            if isinstance(item, str) and (str(key).lower().endswith("path") or str(key).lower() == "path"):
                if any("prompt" in part for part in next_ancestors):
                    return item
            found = _nested_prompt_path(item, next_ancestors)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _nested_prompt_path(item, ancestors)
            if found:
                return found
    return None


def _rendered_prompt_from_snapshot(snapshot: dict[str, Any], root: Path) -> str:
    prompt = snapshot.get("prompt") if isinstance(snapshot.get("prompt"), dict) else {}
    rendered = prompt.get("rendered_prompt") or snapshot.get("rendered_prompt")
    if rendered:
        return str(rendered)
    prompt_path = prompt.get("path") or prompt.get("prompt_path") or _nested_prompt_path(snapshot)
    resolved, _reason = _resolve_artifact_path(prompt_path, root)
    if not resolved:
        return ""
    text = resolved.read_text(encoding="utf-8", errors="replace")
    if resolved.suffix.lower() == ".json":
        parsed = _json_value(text)
        if isinstance(parsed, dict):
            return str(parsed.get("rendered_prompt") or parsed.get("prompt") or text)
    return text


def _prompt_source_from_snapshot(snapshot: dict[str, Any], root: Path) -> str:
    prompt = snapshot.get("prompt") if isinstance(snapshot.get("prompt"), dict) else {}
    for key in ("source", "content", "text", "prompt"):
        if prompt.get(key):
            return str(prompt[key])
    prompt_path = prompt.get("path") or prompt.get("prompt_path") or _nested_prompt_path(snapshot)
    resolved, _reason = _resolve_artifact_path(prompt_path, root)
    if not resolved:
        fallback = prompt.get("rendered_prompt") or snapshot.get("rendered_prompt")
        return str(fallback) if fallback else ""
    text = resolved.read_text(encoding="utf-8", errors="replace")
    if resolved.suffix.lower() == ".json":
        parsed = _json_value(text)
        if isinstance(parsed, dict):
            return str(parsed.get("prompt") or parsed.get("text") or parsed.get("rendered_prompt") or text)
    return text


def _resolve_artifact_path(path_value: str | None, root: Path) -> tuple[Path | None, str]:
    if not path_value:
        return None, "missing_artifact"
    raw = str(path_value)
    try:
        if raw.startswith("/artifacts/"):
            candidate = root / raw[len("/artifacts/") :]
        elif raw.startswith("artifacts/"):
            candidate = root / raw[len("artifacts/") :]
        else:
            candidate = Path(raw)
            if not candidate.is_absolute():
                candidate = root / candidate

        resolved = candidate.resolve()
        if resolved != root and root not in resolved.parents:
            return None, "unsafe_artifact_path"
        if resolved != root and ".codex-private" in resolved.relative_to(root).parts:
            return None, "private_artifact"
        if not resolved.exists() or resolved.is_dir():
            return None, "missing_artifact"
        return resolved, ""
    except OSError:
        return None, "unsafe_artifact_path"


def _manifest_source(source: Path | str | None, root: Path, reason: str = "") -> str | None:
    if source is None:
        return None
    if source == "clean_html":
        return "clean_html"
    try:
        resolved = Path(str(source)).resolve()
        if resolved == root or root in resolved.parents:
            if resolved != root and ".codex-private" in resolved.relative_to(root).parts:
                return "<redacted:private_artifact>"
            return resolved.relative_to(root).as_posix()
    except (OSError, ValueError):
        pass
    if reason == "unsafe_artifact_path":
        return "<redacted:unsafe_artifact_path>"
    if reason == "private_artifact":
        return "<redacted:private_artifact>"
    return "<redacted:absolute_path>"


def _run_folder(run_index: int, run: dict[str, Any]) -> str:
    requirement = f"req-{run.get('requirement_id', 'na')}-{_slug(run.get('requirement_title') or 'requirement')}"
    color = f"color-{run.get('color_id', 'na')}-{_slug(run.get('color_title') or 'color')}"
    candidate = run.get("auto_candidate_index")
    candidate_label = f"candidate-{candidate}" if candidate is not None else "manual"
    run_label = f"run-{run.get('id')}-{_slug(run.get('engine') or 'html')}-{_slug(run.get('strategy') or 'default')}"
    return f"combination-{run_index:02d}__{requirement}__{color}__{candidate_label}/{run_label}"


def _slug(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text[:48] or "item"


def _suffix(path_value: str | None, fallback: str) -> str:
    if not path_value:
        return fallback
    suffix = Path(str(path_value)).suffix.lower()
    return suffix if suffix else fallback


def _dedupe(name: str, used_names: set[str]) -> str:
    if name not in used_names:
        used_names.add(name)
        return name
    path = Path(name)
    stem = str(path.with_suffix(""))
    suffix = path.suffix
    index = 2
    while True:
        candidate = f"{stem}-{index}{suffix}"
        if candidate not in used_names:
            used_names.add(candidate)
            return candidate
        index += 1


def _mark_included(
    manifest: dict[str, Any],
    run: dict[str, Any],
    slide: dict[str, Any],
    kind: str,
    zip_path: str,
    source: Path | str,
    root: Path,
) -> None:
    manifest["included"].append(
        {
            "run_id": run.get("id"),
            "slide_id": slide.get("id"),
            "slide_position": slide.get("position"),
            "kind": kind,
            "zip_path": zip_path,
            "source": _manifest_source(source, root),
            "lineage": _lineage_for_run(run, slide),
            "version": _version_manifest(slide.get("_active_version"), root),
        }
    )


def _mark_skipped(
    manifest: dict[str, Any],
    run: dict[str, Any],
    slide: dict[str, Any],
    kind: str,
    reason: str,
    source: str | None,
    root: Path,
) -> None:
    manifest["skipped"].append(
        {
            "run_id": run.get("id"),
            "slide_id": slide.get("id"),
            "slide_position": slide.get("position"),
            "kind": kind,
            "reason": reason,
            "source": _manifest_source(source, root, reason),
        }
    )


def _lineage_for_run(run: dict[str, Any], slide: dict[str, Any] | None = None) -> dict[str, Any]:
    lineage: dict[str, Any] = {}
    for value in (run.get("stage_artifacts"), run.get("route_metadata")):
        parsed = _parse_json(value)
        if isinstance(parsed.get("lineage"), dict):
            lineage.update(parsed["lineage"])
        elif parsed.get("source_run_id"):
            lineage.update(parsed)
    if slide:
        parsed_slide = _parse_json(slide.get("stage_artifacts"))
        slide_lineage = parsed_slide.get("lineage")
        if isinstance(slide_lineage, dict):
            lineage.update(slide_lineage)
    return lineage


def _parse_json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}
