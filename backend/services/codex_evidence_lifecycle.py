"""Run-scoped retention, purge, and local diagnostic export for Codex evidence."""

from __future__ import annotations

import json
import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

import config
import db as dbmod

SENSITIVE_WARNING = (
    "LOCAL DIAGNOSTIC ONLY: contains sensitive raw model events, prompts, user material, "
    "commands, paths, and possible credentials. Do not share or upload this file."
)


class UnsafeRawEvidencePath(ValueError):
    pass


class RawDiagnosticOptInRequired(RuntimeError):
    pass


def _artifacts_root() -> Path:
    return Path(config.ARTIFACTS_DIR).expanduser().resolve()


def _native_run_private_root(run_id: int) -> Path:
    """Return the only lifecycle-managed Native private root for one Run."""
    try:
        normalized_run_id = int(run_id)
    except (TypeError, ValueError) as exc:
        raise UnsafeRawEvidencePath("Native private evidence requires a numeric Run id") from exc
    if normalized_run_id <= 0:
        raise UnsafeRawEvidencePath("Native private evidence requires a positive Run id")
    return _artifacts_root() / ".codex-private" / "native-image" / f"run-{normalized_run_id}"


def _validate_raw_path(value: str | None) -> Path | None:
    if not value:
        return None
    path = Path(value).expanduser().resolve()
    try:
        path.relative_to(_artifacts_root())
    except ValueError as exc:
        raise UnsafeRawEvidencePath("raw Codex evidence is outside the configured artifacts root") from exc
    return path


def secure_raw_evidence_file(path: str | Path) -> None:
    os.chmod(Path(path), 0o600)


def _invocations_for_run(run_id: int) -> list[dict]:
    return dbmod.list_codex_invocations(run_id=run_id)


def _invocations_for_deck(deck_id: int) -> list[dict]:
    db = dbmod.get_db()
    try:
        rows = db.execute(
            """SELECT ci.*
               FROM codex_invocations ci
               JOIN runs r ON r.id = ci.run_id
               WHERE r.deck_id = ?
               ORDER BY ci.id""",
            (deck_id,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        db.close()


def protect_deck_raw_evidence(deck_id: int) -> None:
    """Fail closed on foreign paths and enforce service-only file permissions."""
    for invocation in _invocations_for_deck(deck_id):
        path = _validate_raw_path(invocation.get("raw_jsonl_path"))
        if path and path.exists():
            secure_raw_evidence_file(path)


def _quarantine(paths: list[Path]) -> list[tuple[Path, Path]]:
    moved: list[tuple[Path, Path]] = []
    try:
        for source in paths:
            if not source.exists():
                continue
            trash = source.parent / ".pptgen-evidence-trash"
            trash.mkdir(mode=0o700, exist_ok=True)
            os.chmod(trash, 0o700)
            target = trash / f"{uuid.uuid4().hex}-{source.name}"
            os.replace(source, target)
            moved.append((source, target))
        return moved
    except Exception:
        _restore_quarantine(moved)
        raise


def _restore_quarantine(moved: list[tuple[Path, Path]]) -> None:
    for source, target in reversed(moved):
        if target.exists():
            source.parent.mkdir(parents=True, exist_ok=True)
            os.replace(target, source)


def _discard_quarantine(moved: list[tuple[Path, Path]]) -> None:
    for _source, target in moved:
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=False)
        else:
            target.unlink(missing_ok=True)
        try:
            target.parent.rmdir()
        except OSError:
            pass


def _validated_raw_paths(invocations: list[dict]) -> list[Path]:
    paths: list[Path] = []
    for invocation in invocations:
        for column in ("raw_jsonl_path", "observed_jsonl_path", "output_path"):
            path = _validate_raw_path(invocation.get(column))
            if path is not None and path not in paths:
                paths.append(path)
    return paths


def _manifest_paths(value: object) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise UnsafeRawEvidencePath("Native private-file manifest files must be a list of paths")
    return value


def _typed_manifest_paths(value: object) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, dict):
        raise UnsafeRawEvidencePath("Native private-file manifest typed_files must be an object")
    paths: list[str] = []
    for typed_value in value.values():
        if isinstance(typed_value, str):
            paths.append(typed_value)
        elif isinstance(typed_value, list) and all(isinstance(item, str) for item in typed_value):
            paths.extend(typed_value)
        else:
            raise UnsafeRawEvidencePath("Native private-file manifest typed_files contains an invalid path")
    return paths


def validate_native_private_file_manifest(*, run_id: int, manifest: object) -> dict[str, object]:
    """Fail closed unless every declared Native file belongs to this Run-private root.

    Source sessions and generated images live in CODEX_HOME and are deliberately
    not deletion candidates.  They are checked only to ensure a malformed
    manifest cannot reclassify a private archive as a source file.
    """
    if not isinstance(manifest, dict):
        raise UnsafeRawEvidencePath("Native private-file manifest must be an object")
    private_root = _native_run_private_root(run_id).resolve()
    declared_root = manifest.get("private_root")
    if not isinstance(declared_root, str) or Path(declared_root).expanduser().resolve() != private_root:
        raise UnsafeRawEvidencePath("Native private-file manifest root does not match the Run-private root")

    private_files = _manifest_paths(manifest.get("files"))
    private_files.extend(_typed_manifest_paths(manifest.get("typed_files")))
    validated_files: list[Path] = []
    for value in private_files:
        path = Path(value).expanduser().resolve()
        try:
            path.relative_to(private_root)
        except ValueError as exc:
            raise UnsafeRawEvidencePath("Native private-file manifest contains a foreign path") from exc
        if path == private_root:
            raise UnsafeRawEvidencePath("Native private-file manifest must name files below the Run-private root")
        if path not in validated_files:
            validated_files.append(path)

    source_paths = _manifest_paths(manifest.get("canonical_source_paths"))
    validated_sources: list[Path] = []
    for value in source_paths:
        source = Path(value).expanduser().resolve()
        try:
            source.relative_to(private_root)
        except ValueError:
            pass
        else:
            raise UnsafeRawEvidencePath("canonical source evidence cannot be inside the Run-private root")
        if source not in validated_sources:
            validated_sources.append(source)

    return {
        "private_root": private_root,
        "files": validated_files,
        "canonical_source_paths": validated_sources,
    }


def _native_manifests_for_invocations(run_ids: list[int], invocations: list[dict]) -> None:
    allowed_run_ids = set(run_ids)
    for invocation in invocations:
        run_id = invocation.get("run_id")
        if run_id is None:
            continue
        try:
            normalized_run_id = int(run_id)
        except (TypeError, ValueError) as exc:
            raise UnsafeRawEvidencePath("Codex invocation has an invalid Run id") from exc
        if normalized_run_id not in allowed_run_ids:
            raise UnsafeRawEvidencePath("Codex invocation does not belong to the deletion Run")
        metadata = invocation.get("metadata")
        if not isinstance(metadata, dict):
            continue
        manifest = metadata.get("native_private_manifest")
        if manifest is not None:
            validate_native_private_file_manifest(run_id=normalized_run_id, manifest=manifest)


def _path_is_in_any_native_run_root(path: Path, run_ids: list[int]) -> bool:
    for run_id in run_ids:
        try:
            path.relative_to(_native_run_private_root(run_id).resolve())
            return True
        except ValueError:
            continue
    return False


def _quarantine_native_run_private_roots(run_ids: list[int]) -> list[tuple[Path, Path]]:
    roots = [_native_run_private_root(run_id).resolve() for run_id in run_ids]
    return _quarantine(roots)


def _quarantine_run_evidence(run_ids: list[int], invocations: list[dict]) -> list[tuple[Path, Path]]:
    """Stage ordinary raw files and complete Native Run roots before a DB change.

    Moving the whole private root catches abandoned runner files that never made
    it into a manifest.  The returned moves can be restored if the DB action
    fails, preserving the existing lifecycle transaction shape.
    """
    _native_manifests_for_invocations(run_ids, invocations)
    raw_paths = _validated_raw_paths(invocations)
    raw_paths = [path for path in raw_paths if not _path_is_in_any_native_run_root(path, run_ids)]
    moved = _quarantine(raw_paths)
    try:
        moved.extend(_quarantine_native_run_private_roots(run_ids))
        return moved
    except Exception:
        _restore_quarantine(moved)
        raise


def delete_native_run_private_evidence(*, run_id: int, manifest: object | None = None) -> bool:
    """Delete exactly one Native Run-private subtree via a reversible quarantine move."""
    if manifest is not None:
        validate_native_private_file_manifest(run_id=run_id, manifest=manifest)
    moved = _quarantine_native_run_private_roots([run_id])
    _discard_quarantine(moved)
    return bool(moved)


def _run_ids_for_deck(deck_id: int) -> list[int]:
    db = dbmod.get_db()
    try:
        return [int(row["id"]) for row in db.execute("SELECT id FROM runs WHERE deck_id = ?", (deck_id,)).fetchall()]
    finally:
        db.close()


def purge_deck_raw_evidence_and_mark(deck_id: int, previous_status: str) -> None:
    """Purge reconstructable Codex evidence and mark a Deck purged as one DB unit."""
    invocations = _invocations_for_deck(deck_id)
    invocation_ids = [int(item["id"]) for item in invocations]
    moved = _quarantine_run_evidence(_run_ids_for_deck(deck_id), invocations)
    db = dbmod.get_db()
    try:
        db.execute("BEGIN IMMEDIATE")
        for invocation_id in invocation_ids:
            db.execute("DELETE FROM codex_event_raw_lines WHERE invocation_id = ?", (invocation_id,))
            db.execute("DELETE FROM codex_events WHERE invocation_id = ?", (invocation_id,))
            db.execute(
                """UPDATE codex_invocations
                   SET status = 'evidence_purged', command_json = NULL, cwd = NULL,
                       prompt_sha256 = NULL, raw_jsonl_path = NULL,
                       observed_jsonl_path = NULL, output_path = NULL,
                       error_message = NULL, metadata = NULL
                   WHERE id = ?""",
                (invocation_id,),
            )
        db.execute(
            """UPDATE decks
               SET lifecycle_status = 'purged', previous_lifecycle_status = ?,
                   purged_at = datetime('now'), updated_at = datetime('now')
               WHERE id = ?""",
            (previous_status, deck_id),
        )
        db.commit()
    except Exception:
        db.rollback()
        _restore_quarantine(moved)
        raise
    finally:
        db.close()
    _discard_quarantine(moved)


def delete_run_with_raw_evidence(run_id: int) -> bool:
    invocations = _invocations_for_run(run_id)
    moved = _quarantine_run_evidence([run_id], invocations)
    db = dbmod.get_db()
    try:
        db.execute("BEGIN IMMEDIATE")
        cur = db.execute("DELETE FROM runs WHERE id = ?", (run_id,))
        changed = cur.rowcount > 0
        db.commit()
    except Exception:
        db.rollback()
        _restore_quarantine(moved)
        raise
    finally:
        db.close()
    _discard_quarantine(moved)
    return changed


def delete_batch_with_raw_evidence(batch_id: int) -> bool:
    db = dbmod.get_db()
    try:
        run_ids = [
            int(row["id"])
            for row in db.execute("SELECT id FROM runs WHERE batch_id = ?", (batch_id,)).fetchall()
        ]
    finally:
        db.close()
    invocations = [item for run_id in run_ids for item in _invocations_for_run(run_id)]
    moved = _quarantine_run_evidence(run_ids, invocations)
    db = dbmod.get_db()
    try:
        db.execute("BEGIN IMMEDIATE")
        for run_id in run_ids:
            db.execute("DELETE FROM run_slides WHERE run_id = ?", (run_id,))
        db.execute("DELETE FROM runs WHERE batch_id = ?", (batch_id,))
        db.execute("DELETE FROM batch_requirements WHERE batch_id = ?", (batch_id,))
        db.execute("DELETE FROM batch_colors WHERE batch_id = ?", (batch_id,))
        cur = db.execute("DELETE FROM batches WHERE id = ?", (batch_id,))
        changed = cur.rowcount > 0
        db.commit()
    except Exception:
        db.rollback()
        _restore_quarantine(moved)
        raise
    finally:
        db.close()
    _discard_quarantine(moved)
    return changed


def export_run_diagnostics(
    run_id: int,
    destination: str | Path,
    *,
    include_raw: bool = False,
    acknowledge_sensitive: bool = False,
) -> str:
    """Write a single-Run local diagnostic file; raw content is explicit opt-in only."""
    if include_raw and not acknowledge_sensitive:
        raise RawDiagnosticOptInRequired("raw export requires explicit sensitive-data acknowledgement")
    invocations = _invocations_for_run(run_id)
    payload: dict = {
        "diagnostic_scope": "single_run",
        "run_id": run_id,
        "contains_sensitive_raw": bool(include_raw),
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "invocations": [
            {
                "id": item["id"],
                "stage_id": item.get("stage_id"),
                "role": item.get("role"),
                "status": item.get("status"),
                "model": item.get("model"),
                "reasoning_effort": item.get("reasoning_effort"),
                "event_count": item.get("event_count"),
                "error_event_count": item.get("error_event_count"),
                "usage_json": item.get("usage_json"),
                "started_at": item.get("started_at"),
                "ended_at": item.get("ended_at"),
                "elapsed_ms": item.get("elapsed_ms"),
            }
            for item in invocations
        ],
    }
    if include_raw:
        payload["warning"] = SENSITIVE_WARNING
        payload["audit"] = {"explicit_opt_in": True, "scope": "single_run", "run_id": run_id}
        payload["raw_events"] = []
        for invocation in invocations:
            for row in dbmod.list_codex_event_raw_lines_for_diagnostics(int(invocation["id"])):
                raw = row["raw_bytes"]
                if isinstance(raw, memoryview):
                    raw = raw.tobytes()
                payload["raw_events"].append(
                    {
                        "invocation_id": invocation["id"],
                        "sequence": row["sequence"],
                        "raw": bytes(raw).decode("utf-8", errors="replace"),
                        "sha256": row["raw_sha256"],
                    }
                )
    destination_path = Path(destination).expanduser().resolve()
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    destination_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(destination_path, 0o600)
    return str(destination_path)
