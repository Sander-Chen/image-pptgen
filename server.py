#!/usr/bin/env python3
"""Flask backend for the HTML-PPT-Gen web platform.

Endpoints:
  CRUD:      /api/configs, /api/decks, /api/requirements, /api/colors, /api/slides
  Split:     POST /api/decks/:id/split
  Generate:  POST /api/generate
  Status:    GET  /api/runs/:id/status
  History:   GET  /api/runs, GET /api/runs/:id, DELETE /api/runs/:id
  Static:    /artifacts/*  (generated files)
             /*            (frontend SPA from frontend/dist/)
"""

import json
import logging
import os
import csv
import ipaddress
from io import StringIO
from pathlib import Path
from urllib.parse import unquote

from flask import Flask, Response, jsonify, request, send_file, send_from_directory, abort
from flask_cors import CORS

import db as dbmod
from backend.services.generation import GenerationRequestError, create_generation_batch
from backend.services import color_extraction
from backend.services import auto_split_settings
from backend.services import codex_audit
from backend.services import codex_activity_projection
from backend.services import codex_evidence_lifecycle
from backend.services import codex_session_reader
from backend.services import bulk_actions
from backend.services import data_lifecycle
from backend.services import deck_split_drafts
from backend.services import defaults
from backend.services import evaluation_machine_qa
from backend.services import evaluations
from backend.services import folders
from backend.services import model_profile_tests
from backend.services import model_profiles
from backend.services import system_settings
from backend.services import system_variables
from backend.services.prompt_assistant import assist_prompt_variables
from backend.services.prompt_variables import EmptyPromptContentError, analyze_prompt_variables
from backend.services import scheduler as scheduler_service
from backend.services.generation_actions import GenerationActionError, apply_generation_action, run_due_retry_poll
from backend.services import run_history
from backend.services import runtime_identity
from backend.services.batch_download import BatchDownloadError, build_batch_download, build_run_download, build_slide_evidence_download
from splitter import split_deck

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).parent


def _runtime_path(env_name: str, default_path: Path) -> Path:
    configured = os.environ.get(env_name)
    if configured:
        return Path(configured).expanduser().resolve()
    return default_path


ARTIFACTS_DIR = _runtime_path("PPT_ARTIFACTS_DIR", BASE_DIR / "artifacts")
FRONTEND_DIR = BASE_DIR / "frontend" / "dist"

app = Flask(__name__, static_folder=None)
CORS(app)

log = logging.getLogger("ppt-server")
DEFAULT_BATCH_RUN_LIMIT = 10


# ---------------------------------------------------------------------------
# Error handlers
# ---------------------------------------------------------------------------

@app.errorhandler(400)
def bad_request(e):
    return jsonify({"error": str(e.description)}), 400


@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "Not found"}), 404


@app.errorhandler(500)
def internal_error(e):
    return jsonify({"error": "Internal server error"}), 500


def error_response(msg: str, status: int = 400):
    """Helper to return a JSON error with a given status code."""
    return jsonify({"error": msg}), status


def _codex_session_reader_response(payload: dict, status: int = 200) -> Response:
    """Return one compact, bounded reader envelope without Flask JSON padding."""
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > codex_session_reader.MAX_RESPONSE_BYTES:
        encoded = b'{"error":"Codex session reader response exceeds the bounded output budget"}'
        status = 500
    return Response(encoded, status=status, mimetype="application/json")


def _codex_session_reader_error(message: str, status: int) -> Response:
    return _codex_session_reader_response({"error": message}, status)


def _is_loopback_request() -> bool:
    try:
        return ipaddress.ip_address(request.remote_addr or "").is_loopback
    except ValueError:
        return False


def _private_codex_session_reader_enabled(*, raw: bool = False) -> Response | None:
    if os.environ.get("PPTGEN_ENABLE_CODEX_SESSION_READER") != "1":
        return _codex_session_reader_error("Codex session reader is disabled", 403)
    if not _is_loopback_request():
        return _codex_session_reader_error("Codex session reader is available only from loopback", 403)
    if raw and os.environ.get("PPTGEN_ENABLE_CODEX_SESSION_RAW") != "1":
        return _codex_session_reader_error("Codex session raw access is disabled", 403)
    return None


def _configured_codex_session_reader() -> codex_session_reader.CodexSessionReader:
    sessions_root = os.environ.get("PPTGEN_CODEX_SESSIONS_ROOT")
    cache_root = os.environ.get("PPTGEN_CODEX_SESSION_READER_CACHE")
    if not sessions_root or not cache_root:
        raise codex_session_reader.SessionNotFound("Codex session reader roots are not configured")
    return codex_session_reader.CodexSessionReader(sessions_root=sessions_root, cache_root=cache_root)


def _codex_session_reader_query(allowed: set[str]) -> dict[str, str] | Response:
    unexpected = set(request.args) - allowed
    repeated = [key for key in allowed if len(request.args.getlist(key)) > 1]
    if unexpected or repeated:
        return _codex_session_reader_error("Codex session reader received unsupported query selectors", 400)
    return {key: request.args[key] for key in allowed if key in request.args}


def _codex_session_reader_sequence(value: str | None) -> int | None:
    if (
        value is None
        or not value.isascii()
        or not value.isdecimal()
        or value.startswith("0")
        or len(value) > 19
        or (len(value) == 19 and value > "9223372036854775807")
    ):
        return None
    return int(value)


def _read_codex_session(session_id: str, *, level: str, allowed_query: set[str], raw: bool = False) -> Response:
    access_error = _private_codex_session_reader_enabled(raw=raw)
    if access_error is not None:
        return access_error
    query = _codex_session_reader_query(allowed_query)
    if isinstance(query, Response):
        return query
    filters: dict[str, str | int] = {
        key: query[key] for key in ("turn_id", "role", "kind", "phase", "tool_name") if key in query
    }
    if level == "L3":
        sequence = _codex_session_reader_sequence(query.get("sequence"))
        if sequence is None or "cursor" in query:
            return _codex_session_reader_error(
                "Codex session detail requires a positive base-10 sequence selector", 400
            )
        filters["sequence"] = sequence
    try:
        payload = _configured_codex_session_reader().read(
            session_id,
            level=level,
            cursor=query.get("cursor"),
            filters=filters,
        )
    except (codex_session_reader.InvalidSessionId, codex_session_reader.InvalidCursor) as error:
        return _codex_session_reader_error(str(error), 400)
    except codex_session_reader.SessionNotFound as error:
        return _codex_session_reader_error(str(error), 404)
    except codex_session_reader.SourceChanged as error:
        return _codex_session_reader_error(str(error), 409)
    except codex_session_reader.SessionReaderError as error:
        return _codex_session_reader_error(str(error), 400)
    return _codex_session_reader_response(payload)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def parse_json_body():
    """Parse and return the JSON body, or abort with 400."""
    data = request.get_json(silent=True)
    if data is None:
        abort(400, description="Request body must be valid JSON")
    return data


def require_fields(data: dict, *fields):
    """Abort 400 if any required fields are missing from data."""
    missing = [f for f in fields if f not in data or data[f] is None]
    if missing:
        abort(400, description=f"Missing required fields: {', '.join(missing)}")


def parse_json_field(value):
    if not value:
        return {} if value is None else value
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return value


SECRET_FIELD_MARKERS = ("api_key", "apikey", "authorization", "token", "secret")


def redact_artifact_contents(value):
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            lowered = str(key).lower().replace("-", "_")
            if any(marker in lowered for marker in SECRET_FIELD_MARKERS):
                redacted[key] = "[REDACTED]"
            else:
                redacted[key] = redact_artifact_contents(item)
        return redacted
    if isinstance(value, list):
        return [redact_artifact_contents(item) for item in value]
    if isinstance(value, str):
        if "Bearer " in value:
            return value.split("Bearer ", 1)[0] + "Bearer ***REDACTED***"
        return value
    return value


LOADABLE_ARTIFACT_TEXT_SUFFIXES = {
    ".csv",
    ".htm",
    ".html",
    ".json",
    ".jsonl",
    ".log",
    ".md",
    ".txt",
    ".xml",
}


def read_safe_artifact(path_value: str | None):
    if not path_value:
        return None
    try:
        path = Path(path_value)
        if not path.is_absolute():
            path = BASE_DIR / path
        resolved = path.resolve()
        artifacts_root = ARTIFACTS_DIR.resolve()
        if artifacts_root not in resolved.parents and resolved != artifacts_root:
            return None
        if ".codex-private" in resolved.relative_to(artifacts_root).parts:
            return None
        if not resolved.exists() or resolved.is_dir() or resolved.stat().st_size > 512_000:
            return None
        if resolved.suffix.lower() not in LOADABLE_ARTIFACT_TEXT_SUFFIXES:
            return None
        text = resolved.read_text(encoding="utf-8", errors="replace")
        if resolved.suffix.lower() == ".json":
            return redact_artifact_contents(parse_json_field(text))
        if resolved.suffix.lower() == ".jsonl":
            redacted_lines = []
            for line in text.splitlines():
                if not line.strip():
                    continue
                redacted_lines.append(json.dumps(redact_artifact_contents(parse_json_field(line)), ensure_ascii=False))
            return "\n".join(redacted_lines)
        return text
    except OSError:
        return None


def _decoded_artifact_request(filename: str) -> str:
    """Normalize repeated URL encoding before artifact-path policy checks."""
    decoded = filename
    for _index in range(5):
        next_value = unquote(decoded)
        if next_value == decoded:
            break
        decoded = next_value
    return decoded


def _private_artifact_request(filename: str) -> bool:
    """Deny private Native paths even if Flask only decoded one URL layer."""
    decoded = _decoded_artifact_request(filename)
    return ".codex-private" in decoded.replace("\\", "/").split("/")


def _native_business_artifact_reference(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    root = ARTIFACTS_DIR.resolve()
    try:
        if value.startswith("/artifacts/"):
            candidate = root / value[len("/artifacts/") :]
        elif value.startswith("artifacts/"):
            candidate = root / value[len("artifacts/") :]
        else:
            candidate = Path(value)
            if not candidate.is_absolute():
                candidate = root / candidate
        relative = candidate.resolve().relative_to(root)
    except (OSError, ValueError):
        return None
    if ".codex-private" in relative.parts:
        return None
    return f"/artifacts/{relative.as_posix()}"


def _redact_native_payload_paths(value: object, key_name: str = "") -> object:
    """Keep only the final business artifact reference on a Native response."""
    unsafe_key = key_name.lower()
    normalized_key = "".join(character for character in unsafe_key if character.isalnum())
    if unsafe_key in {
        "prompt",
        "rendered_prompt",
        "raw_response",
        "clean_html",
        "xml_raw",
        "xml_clean",
        "response",
        "request",
        "error",
        "error_message",
        "command",
        "stdout",
        "stderr",
        "transcript",
    } or unsafe_key.endswith(("_xml", "_html")):
        return None
    if (
        "prompt" in normalized_key
        or "argument" in normalized_key
        or any(part in normalized_key for part in ("apikey", "authorization", "token", "secret", "password", "account"))
    ):
        return None
    if isinstance(value, dict):
        return {key: _redact_native_payload_paths(child, str(key)) for key, child in value.items()}
    if isinstance(value, list):
        return [_redact_native_payload_paths(child, key_name) for child in value]
    if unsafe_key == "final_image_path":
        return _native_business_artifact_reference(value)
    if unsafe_key == "path" or unsafe_key.endswith("_path"):
        return None
    return value


def _project_native_run_payload(run: dict) -> None:
    """Replace typed Native metadata and absolute business paths before JSON output."""
    for key in ("route_metadata", "stage_artifacts", "model_call_metadata"):
        run[key] = _redact_native_payload_paths(codex_audit.project_native_public_value(run.get(key)))
    # Native failure state is carried by the whitelisted audit failure code; raw
    # run output, design artifacts, and diagnostics must not become a second
    # public evidence channel.
    for key in ("output_dir", "design_principle_raw", "design_principle_json", "error_message"):
        run[key] = None
    for slide in run.get("slides", []):
        for key in ("stage_artifacts", "seed_dependency", "artifact_contents", "active_version", "generation_history", "versions"):
            if key in slide:
                slide[key] = _redact_native_payload_paths(codex_audit.project_native_public_value(slide.get(key)))
        for key in (
            "raw_response",
            "clean_html",
            "html_path",
            "screenshot_path",
            "screenshot_path_source",
            "xml_raw",
            "xml_clean",
            "error_message",
        ):
            slide[key] = None
        public_business_path = _native_business_artifact_reference(slide.get("final_image_path"))
        if public_business_path is not None:
            slide["final_image_path"] = public_business_path
        else:
            slide["final_image_path"] = None
        # Native must never reuse the raw Codex thread in this legacy field.
        slide["conversation_id"] = None


def artifact_content_key(key: str) -> str:
    if key.endswith("_path"):
        return key[:-5]
    return key


def collect_artifact_contents(value):
    if isinstance(value, dict):
        contents: dict[str, object] = {}
        for key, item in value.items():
            if (key.endswith("_path") or key == "path") and isinstance(item, str):
                loaded = read_safe_artifact(item)
                if loaded is not None:
                    contents[artifact_content_key(key)] = loaded
                continue
            nested = collect_artifact_contents(item)
            if nested is not None:
                contents[key] = nested
        return contents or None
    if isinstance(value, list):
        items = []
        has_contents = False
        for item in value:
            nested = collect_artifact_contents(item)
            items.append(nested if nested is not None else {})
            has_contents = has_contents or nested is not None
        return items if has_contents else None
    return None


def enrich_slide_artifact_contents(slide: dict) -> None:
    artifacts = slide.get("stage_artifacts")
    if not isinstance(artifacts, dict):
        return
    contents: dict[str, object] = {}
    for stage_name, stage_value in artifacts.items():
        if not isinstance(stage_value, (dict, list)):
            continue
        stage_contents = collect_artifact_contents(stage_value)
        if stage_contents:
            contents[stage_name] = stage_contents
    if contents:
        slide["artifact_contents"] = contents


def project_active_version_evidence(slide: dict) -> None:
    active_version = slide.get("active_version")
    if not isinstance(active_version, dict):
        return
    snapshot = active_version.get("evidence_snapshot")
    if not isinstance(snapshot, dict):
        return
    slide_stage_artifacts = snapshot.get("slide_stage_artifacts")
    if isinstance(slide_stage_artifacts, dict) and slide_stage_artifacts:
        slide["stage_artifacts"] = slide_stage_artifacts
    seed_dependency = snapshot.get("seed_dependency")
    if seed_dependency and not slide.get("seed_dependency"):
        slide["seed_dependency"] = seed_dependency


def launch_run_thread(run_id: int, db_path: str):
    return scheduler_service.launch_run_thread(run_id, db_path)


def run_pipeline_and_pump(run_id: int, db_path: str):
    return scheduler_service.run_pipeline_and_pump(run_id, db_path)


def launch_run_for_batch(run_id: int, db_path: str):
    return scheduler_service.launch_run_for_batch(run_id, db_path)


def pump_batch_queue(batch_id: int, db_path: str, max_concurrent_runs: int | None = None) -> list[int]:
    return scheduler_service.pump_batch_queue(
        batch_id,
        db_path,
        max_concurrent_runs=max_concurrent_runs,
        run_launcher=launch_run_for_batch,
    )


def launch_batch_runs(run_ids: list[int], db_path: str, max_concurrent_runs: int):
    return scheduler_service.launch_batch_runs(
        run_ids,
        db_path,
        max_concurrent_runs,
        run_launcher=launch_run_for_batch,
    )


# ---------------------------------------------------------------------------
# System settings and variables
# ---------------------------------------------------------------------------

@app.route("/api/system-settings", methods=["GET"])
def api_get_system_settings():
    return jsonify(system_settings.get_system_settings())


@app.route("/api/system-settings", methods=["PUT"])
def api_update_system_settings():
    data = parse_json_body()
    try:
        return jsonify(system_settings.update_system_settings(data))
    except ValueError as e:
        return error_response(str(e), 400)


@app.route("/api/system-variables", methods=["GET"])
def api_list_system_variables():
    try:
        variables = system_variables.list_system_variables(
            agent_type=request.args.get("agent_type"),
            status=request.args.get("status"),
        )
    except ValueError as e:
        return error_response(str(e), 400)
    return jsonify(variables)


@app.route("/api/system-variables", methods=["POST"])
def api_create_system_variable():
    data = parse_json_body()
    require_fields(data, "agent_type", "name")
    try:
        variable = system_variables.create_system_variable(
            agent_type=data["agent_type"],
            name=data["name"],
            description=data.get("description"),
            status=data.get("status", "active"),
        )
    except ValueError as e:
        return error_response(str(e), 400)
    except Exception as e:
        if "UNIQUE" in str(e):
            return error_response("System variable already exists for this role", 409)
        raise
    return jsonify(variable), 201


@app.route("/api/system-variables/<int:variable_id>", methods=["PUT"])
def api_update_system_variable(variable_id):
    data = parse_json_body()
    try:
        variable = system_variables.update_system_variable(variable_id, data)
    except ValueError as e:
        return error_response(str(e), 400)
    except Exception as e:
        if "UNIQUE" in str(e):
            return error_response("System variable already exists for this role", 409)
        raise
    if not variable:
        return error_response("System variable not found", 404)
    return jsonify(variable)


@app.route("/api/system-variables/<int:variable_id>/references", methods=["GET"])
def api_get_system_variable_references(variable_id):
    if not system_variables.get_system_variable(variable_id):
        return error_response("System variable not found", 404)
    return jsonify({"references": system_variables.list_variable_references(variable_id)})


# ---------------------------------------------------------------------------
# Folders
# ---------------------------------------------------------------------------

@app.route("/api/folders", methods=["GET"])
def api_list_folders():
    try:
        return jsonify(folders.list_folders(scope=request.args.get("scope")))
    except ValueError as e:
        return error_response(str(e), 400)


@app.route("/api/folders", methods=["POST"])
def api_create_folder():
    data = parse_json_body()
    require_fields(data, "scope", "name")
    try:
        folder = folders.create_folder(
            scope=data["scope"],
            name=data["name"],
            parent_id=data.get("parent_id"),
        )
    except ValueError as e:
        return error_response(str(e), 400)
    except Exception as e:
        if "UNIQUE" in str(e):
            return error_response("Folder already exists in this location", 409)
        raise
    return jsonify(folder), 201


@app.route("/api/folders/<int:folder_id>", methods=["PUT"])
def api_update_folder(folder_id):
    data = parse_json_body()
    try:
        folder = folders.update_folder(folder_id, data)
    except ValueError as e:
        return error_response(str(e), 400)
    except Exception as e:
        if "UNIQUE" in str(e):
            return error_response("Folder already exists in this location", 409)
        raise
    if not folder:
        return error_response("Folder not found", 404)
    return jsonify(folder)


@app.route("/api/bulk-actions", methods=["POST"])
def api_bulk_actions():
    try:
        results, has_error = bulk_actions.apply_bulk_action(parse_json_body())
    except ValueError as e:
        return error_response(str(e), 400)
    return jsonify({"results": results}), 207 if has_error else 200


# ---------------------------------------------------------------------------
# CRUD: Configs
# ---------------------------------------------------------------------------

@app.route("/api/model-profiles", methods=["GET"])
def api_list_model_profiles():
    try:
        profiles = model_profiles.list_profiles(
            role=request.args.get("role"),
            status=request.args.get("status"),
        )
    except ValueError as e:
        return error_response(str(e), 400)
    return jsonify(profiles)


@app.route("/api/model-profiles/test", methods=["POST"])
def api_test_model_profile():
    data = parse_json_body()
    try:
        profile_id = data.get("id")
        profile = model_profiles.get_profile(int(profile_id)) if profile_id else None
        if model_profiles.is_system_managed_native_profile(
            profile
        ) or model_profiles.is_system_managed_native_profile(data):
            return error_response("System-managed Native model profiles cannot be tested", 403)
        return jsonify(model_profile_tests.run_profile_test(data))
    except ValueError as e:
        return error_response(str(e), 400)


@app.route("/api/model-profiles", methods=["POST"])
def api_create_model_profile():
    data = parse_json_body()
    try:
        model_profile_tests.consume_test_token(data.get("test_token"), data)
        profile_id = model_profiles.create_profile(data)
    except ValueError as e:
        return error_response(str(e), 400)
    except Exception as e:
        if "UNIQUE" in str(e):
            return error_response("Model profile already exists for this role", 409)
        raise
    return jsonify(model_profiles.get_profile(profile_id)), 201


@app.route("/api/model-profiles/<int:profile_id>", methods=["PUT"])
def api_update_model_profile(profile_id):
    data = parse_json_body()
    try:
        current = model_profiles.get_profile(profile_id)
        if not current:
            return error_response("Model profile not found", 404)
        if model_profiles.is_system_managed_native_profile(current):
            return error_response("System-managed Native model profiles cannot be edited", 403)
        model_profile_tests.consume_test_token(data.get("test_token"), {**current, **data})
        profile = model_profiles.update_profile(profile_id, data)
    except ValueError as e:
        return error_response(str(e), 400)
    except Exception as e:
        if "UNIQUE" in str(e):
            return error_response("Model profile already exists for this role", 409)
        raise
    return jsonify(profile)


@app.route("/api/model-profiles/<int:profile_id>", methods=["DELETE"])
def api_delete_model_profile(profile_id):
    profile = model_profiles.get_profile(profile_id)
    if not profile:
        return error_response("Model profile not found", 404)
    if model_profiles.is_system_managed_native_profile(profile):
        return error_response("System-managed Native model profiles cannot be deleted", 403)
    if not model_profiles.delete_profile(profile_id):
        return error_response("Model profile not found", 404)
    return jsonify({"ok": True})


def _resolved_config(config_id: int) -> dict:
    return model_profiles.resolve_config(config_id)


@app.route("/api/auto-split-settings", methods=["GET"])
def api_get_auto_split_settings():
    try:
        return jsonify(auto_split_settings.get_auto_split_settings())
    except auto_split_settings.AutoSplitSettingsError as e:
        return error_response(str(e), 400)


@app.route("/api/auto-split-settings", methods=["PUT"])
def api_update_auto_split_settings():
    data = parse_json_body()
    required_fields = {"model_profile_id", "thinking_effort"}
    allowed_fields = required_fields | {"content_mode"}
    if not required_fields.issubset(data) or not set(data).issubset(allowed_fields):
        return error_response(
            "AutoSplit settings require model_profile_id and thinking_effort, with optional content_mode",
            400,
        )
    try:
        model_profile_id = int(data["model_profile_id"])
        result = auto_split_settings.update_auto_split_settings(
            model_profile_id,
            data["thinking_effort"],
            data.get("content_mode"),
        )
    except (TypeError, ValueError, auto_split_settings.AutoSplitSettingsError) as e:
        return error_response(str(e), 400)
    return jsonify(result)


@app.route("/api/configs", methods=["GET"])
def api_list_configs():
    configs = dbmod.list_configs()
    return jsonify([_resolved_config(c["id"]) for c in configs])


@app.route("/api/configs/<int:config_id>", methods=["GET"])
def api_get_config(config_id):
    c = dbmod.get_config(config_id)
    if not c:
        return error_response("Config not found", 404)
    return jsonify(_resolved_config(config_id))


@app.route("/api/configs", methods=["POST"])
def api_create_config():
    data = parse_json_body()
    require_fields(data, "name")
    if "auto_spill_profile_id" in data:
        return error_response("auto_spill_profile_id is no longer a Combination field", 400)
    if model_profiles.has_native_image_binding(data.get("route_model_bindings")):
        return error_response("Native Image route bindings are server-managed", 400)
    profile_mode = all(
        key in data for key in ("designer_profile_id", "html_agent_profile_id")
    )
    if profile_mode:
        try:
            designer = model_profiles.profile_to_agent_config(int(data["designer_profile_id"]))
            html_agent = model_profiles.profile_to_agent_config(int(data["html_agent_profile_id"]))
        except ValueError as e:
            return error_response(str(e), 400)
    else:
        require_fields(data, "designer", "html_agent")
        designer = data["designer"]
        html_agent = data["html_agent"]
    try:
        config_id = dbmod.create_config(
            data["name"],
            designer,
            html_agent,
            timeout_minutes=data.get("timeout_minutes", 30),
            max_concurrent_runs=data.get("max_concurrent_runs", 2),
            designer_profile_id=data.get("designer_profile_id"),
            html_agent_profile_id=data.get("html_agent_profile_id"),
            route_model_bindings=data.get("route_model_bindings"),
            config_type=data.get("type", "html"),
        )
        if data.get("is_default"):
            defaults.set_default_config(config_id)
    except ValueError as e:
        return error_response(str(e), 400)
    except Exception as e:
        if "UNIQUE" in str(e):
            return error_response(f"Config name '{data['name']}' already exists", 409)
        raise
    return jsonify(_resolved_config(config_id)), 201


@app.route("/api/configs/<int:config_id>", methods=["PUT"])
def api_update_config(config_id):
    data = parse_json_body()
    if "auto_spill_profile_id" in data:
        return error_response("auto_spill_profile_id is no longer a Combination field", 400)
    current = dbmod.get_config(config_id)
    if not current:
        return error_response("Config not found", 404)
    if model_profiles.is_system_managed_native_config(current):
        return error_response("System-managed Native configs cannot be edited", 403)
    if model_profiles.has_native_image_binding(data.get("route_model_bindings")):
        return error_response("Native Image route bindings are server-managed", 400)
    fields = {}
    if "name" in data:
        fields["name"] = data["name"]
    if "designer" in data:
        fields["designer"] = data["designer"]
    if "html_agent" in data:
        fields["html_agent"] = data["html_agent"]
    for key in ("designer_profile_id", "html_agent_profile_id", "route_model_bindings", "type"):
        if key in data:
            fields[key] = data[key]
    if "timeout_minutes" in data:
        fields["timeout_minutes"] = data["timeout_minutes"]
    if "max_concurrent_runs" in data:
        fields["max_concurrent_runs"] = data["max_concurrent_runs"]
    if not fields:
        return error_response("No fields to update")
    try:
        ok = dbmod.update_config(config_id, **fields)
    except ValueError as e:
        return error_response(str(e), 400)
    if not ok:
        return error_response("Config not found", 404)
    if data.get("is_default"):
        defaults.set_default_config(config_id)
    return jsonify(_resolved_config(config_id))


@app.route("/api/configs/<int:config_id>/default", methods=["POST"])
def api_set_default_config(config_id):
    config = defaults.set_default_config(config_id)
    if not config:
        return error_response("Config not found", 404)
    return jsonify(_resolved_config(config_id))


@app.route("/api/configs/<int:config_id>", methods=["DELETE"])
def api_delete_config(config_id):
    config = dbmod.get_config(config_id)
    if model_profiles.is_system_managed_native_config(config):
        return error_response("System-managed Native configs cannot be deleted", 403)
    try:
        defaults.ensure_config_delete_allowed(config_id)
    except ValueError as e:
        return error_response(str(e), 422)
    ok = dbmod.delete_config(config_id)
    if not ok:
        return error_response("Config not found", 404)
    defaults.promote_default_config_if_needed(config_id)
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# CRUD: Decks
# ---------------------------------------------------------------------------

@app.route("/api/runtime-identity", methods=["GET"])
def api_runtime_identity():
    try:
        identity = runtime_identity.read_runtime_identity()
    except RuntimeError as exc:
        return error_response(str(exc), 503)
    return jsonify(identity)


@app.route("/api/decks", methods=["GET"])
def api_list_decks():
    status = request.args.get("status", "active")
    folder_id = request.args.get("folder_id", type=int)
    decks = data_lifecycle.list_data_entities("deck", status=status, folder_id=folder_id)
    # Include slide count for each deck
    conn = dbmod.get_db()
    for d in decks:
        cur = conn.execute("SELECT COUNT(*) FROM slides WHERE deck_id = ?", (d["id"],))
        d["slide_count"] = cur.fetchone()[0]
    conn.close()
    return jsonify(decks)


@app.route("/api/decks/<int:deck_id>", methods=["GET"])
def api_get_deck(deck_id):
    d = dbmod.get_deck(deck_id)
    if not d:
        return error_response("Deck not found", 404)
    d["slides"] = dbmod.list_slides(deck_id)
    return jsonify(d)


@app.route("/api/decks", methods=["POST"])
def api_create_deck():
    data = parse_json_body()
    require_fields(data, "title", "content")
    deck_id = dbmod.create_deck(data["title"], data["content"])
    return jsonify({"id": deck_id}), 201


@app.route("/api/decks/<int:deck_id>", methods=["PUT"])
def api_update_deck(deck_id):
    data = parse_json_body()
    fields = {}
    if "title" in data:
        fields["title"] = data["title"]
    if "content" in data:
        fields["content"] = data["content"]
    if not fields:
        return error_response("No fields to update")
    ok = dbmod.update_deck(deck_id, **fields)
    if not ok:
        return error_response("Deck not found", 404)
    return jsonify({"ok": True})


@app.route("/api/decks/<int:deck_id>", methods=["DELETE"])
def api_delete_deck(deck_id):
    try:
        deck = data_lifecycle.move_to_recycle_bin("deck", deck_id)
    except codex_evidence_lifecycle.UnsafeRawEvidencePath as exc:
        return error_response(str(exc), 409)
    if not deck:
        return error_response("Deck not found", 404)
    return jsonify(deck)


@app.route("/api/decks/<int:deck_id>/archive", methods=["POST"])
def api_archive_deck(deck_id):
    deck = data_lifecycle.archive_data_entity("deck", deck_id)
    if not deck:
        return error_response("Deck not found", 404)
    return jsonify(deck)


@app.route("/api/decks/<int:deck_id>/restore", methods=["POST"])
def api_restore_deck(deck_id):
    try:
        deck = data_lifecycle.restore_data_entity("deck", deck_id)
    except codex_evidence_lifecycle.UnsafeRawEvidencePath as exc:
        return error_response(str(exc), 409)
    if not deck:
        return error_response("Deck not found", 404)
    return jsonify(deck)


@app.route("/api/decks/<int:deck_id>/force-delete", methods=["POST"])
def api_force_delete_deck(deck_id):
    try:
        deck = data_lifecycle.force_delete_data_entity("deck", deck_id)
    except codex_evidence_lifecycle.UnsafeRawEvidencePath as exc:
        return error_response(str(exc), 409)
    if not deck:
        return error_response("Deck not found", 404)
    return jsonify(deck)


@app.route("/api/decks/<int:deck_id>/folders", methods=["PUT"])
def api_assign_deck_folders(deck_id):
    if not dbmod.get_deck(deck_id):
        return error_response("Deck not found", 404)
    data = parse_json_body()
    try:
        folder_ids = folders.assign_entity_folders("deck", deck_id, data.get("folder_ids", []))
    except ValueError as e:
        return error_response(str(e), 400)
    return jsonify({"id": deck_id, "folder_ids": folder_ids})


# ---------------------------------------------------------------------------
# CRUD: Requirements
# ---------------------------------------------------------------------------

@app.route("/api/requirements", methods=["GET"])
def api_list_requirements():
    status = request.args.get("status", "active")
    folder_id = request.args.get("folder_id", type=int)
    return jsonify(data_lifecycle.list_data_entities("requirement", status=status, folder_id=folder_id))


@app.route("/api/requirements/<int:req_id>", methods=["GET"])
def api_get_requirement(req_id):
    r = dbmod.get_requirement(req_id)
    if not r:
        return error_response("Requirement not found", 404)
    return jsonify(r)


@app.route("/api/requirements", methods=["POST"])
def api_create_requirement():
    data = parse_json_body()
    require_fields(data, "title", "content")
    req_id = dbmod.create_requirement(data["title"], data["content"])
    return jsonify({"id": req_id}), 201


@app.route("/api/requirements/<int:req_id>", methods=["PUT"])
def api_update_requirement(req_id):
    data = parse_json_body()
    fields = {}
    if "title" in data:
        fields["title"] = data["title"]
    if "content" in data:
        fields["content"] = data["content"]
    if not fields:
        return error_response("No fields to update")
    ok = dbmod.update_requirement(req_id, **fields)
    if not ok:
        return error_response("Requirement not found", 404)
    return jsonify({"ok": True})


@app.route("/api/requirements/<int:req_id>", methods=["DELETE"])
def api_delete_requirement(req_id):
    requirement = data_lifecycle.move_to_recycle_bin("requirement", req_id)
    if not requirement:
        return error_response("Requirement not found", 404)
    return jsonify(requirement)


@app.route("/api/requirements/<int:req_id>/archive", methods=["POST"])
def api_archive_requirement(req_id):
    requirement = data_lifecycle.archive_data_entity("requirement", req_id)
    if not requirement:
        return error_response("Requirement not found", 404)
    return jsonify(requirement)


@app.route("/api/requirements/<int:req_id>/restore", methods=["POST"])
def api_restore_requirement(req_id):
    requirement = data_lifecycle.restore_data_entity("requirement", req_id)
    if not requirement:
        return error_response("Requirement not found", 404)
    return jsonify(requirement)


@app.route("/api/requirements/<int:req_id>/force-delete", methods=["POST"])
def api_force_delete_requirement(req_id):
    requirement = data_lifecycle.force_delete_data_entity("requirement", req_id)
    if not requirement:
        return error_response("Requirement not found", 404)
    return jsonify(requirement)


@app.route("/api/requirements/<int:req_id>/folders", methods=["PUT"])
def api_assign_requirement_folders(req_id):
    if not dbmod.get_requirement(req_id):
        return error_response("Requirement not found", 404)
    data = parse_json_body()
    try:
        folder_ids = folders.assign_entity_folders("requirement", req_id, data.get("folder_ids", []))
    except ValueError as e:
        return error_response(str(e), 400)
    return jsonify({"id": req_id, "folder_ids": folder_ids})


# ---------------------------------------------------------------------------
# CRUD: Colors
# ---------------------------------------------------------------------------

@app.route("/api/colors", methods=["GET"])
def api_list_colors():
    status = request.args.get("status", "active")
    folder_id = request.args.get("folder_id", type=int)
    return jsonify(data_lifecycle.list_data_entities("color", status=status, folder_id=folder_id))


@app.route("/api/colors/<int:color_id>", methods=["GET"])
def api_get_color(color_id):
    c = dbmod.get_color(color_id)
    if not c:
        return error_response("Color not found", 404)
    return jsonify(c)


@app.route("/api/colors", methods=["POST"])
def api_create_color():
    data = parse_json_body()
    require_fields(data, "title", "content")
    color_id = dbmod.create_color(data["title"], data["content"])
    return jsonify({"id": color_id}), 201


@app.route("/api/colors/<int:color_id>", methods=["PUT"])
def api_update_color(color_id):
    data = parse_json_body()
    fields = {}
    if "title" in data:
        fields["title"] = data["title"]
    if "content" in data:
        fields["content"] = data["content"]
    if not fields:
        return error_response("No fields to update")
    ok = dbmod.update_color(color_id, **fields)
    if not ok:
        return error_response("Color not found", 404)
    return jsonify({"ok": True})


@app.route("/api/colors/<int:color_id>", methods=["DELETE"])
def api_delete_color(color_id):
    color = data_lifecycle.move_to_recycle_bin("color", color_id)
    if not color:
        return error_response("Color not found", 404)
    return jsonify(color)


@app.route("/api/colors/<int:color_id>/archive", methods=["POST"])
def api_archive_color(color_id):
    color = data_lifecycle.archive_data_entity("color", color_id)
    if not color:
        return error_response("Color not found", 404)
    return jsonify(color)


@app.route("/api/colors/<int:color_id>/restore", methods=["POST"])
def api_restore_color(color_id):
    color = data_lifecycle.restore_data_entity("color", color_id)
    if not color:
        return error_response("Color not found", 404)
    return jsonify(color)


@app.route("/api/colors/<int:color_id>/force-delete", methods=["POST"])
def api_force_delete_color(color_id):
    color = data_lifecycle.force_delete_data_entity("color", color_id)
    if not color:
        return error_response("Color not found", 404)
    return jsonify(color)


@app.route("/api/colors/<int:color_id>/folders", methods=["PUT"])
def api_assign_color_folders(color_id):
    if not dbmod.get_color(color_id):
        return error_response("Color not found", 404)
    data = parse_json_body()
    try:
        folder_ids = folders.assign_entity_folders("color", color_id, data.get("folder_ids", []))
    except ValueError as e:
        return error_response(str(e), 400)
    return jsonify({"id": color_id, "folder_ids": folder_ids})


@app.route("/api/colors/extract-from-image", methods=["POST"])
def api_extract_color_from_image():
    try:
        color = color_extraction.create_color_from_image(
            title=request.form.get("title", "").strip(),
            image=request.files.get("image"),
        )
    except color_extraction.ColorExtractionError as e:
        return error_response(str(e), e.status_code)
    return jsonify(color), 201


# ---------------------------------------------------------------------------
# CRUD: Slides (nested under decks)
# ---------------------------------------------------------------------------

@app.route("/api/decks/<int:deck_id>/slides", methods=["GET"])
def api_list_slides(deck_id):
    d = dbmod.get_deck(deck_id)
    if not d:
        return error_response("Deck not found", 404)
    return jsonify(dbmod.list_slides(deck_id))


@app.route("/api/slides/<int:slide_id>", methods=["GET"])
def api_get_slide(slide_id):
    s = dbmod.get_slide(slide_id)
    if not s:
        return error_response("Slide not found", 404)
    return jsonify(s)


@app.route("/api/decks/<int:deck_id>/slides", methods=["POST"])
def api_create_slide(deck_id):
    d = dbmod.get_deck(deck_id)
    if not d:
        return error_response("Deck not found", 404)
    data = parse_json_body()
    require_fields(data, "title", "content")
    if data.get("split_mode") == "image_skill_cover":
        return error_response("split_mode image_skill_cover is server-owned", 422)
    # Auto-assign position if not given
    existing = dbmod.list_slides(deck_id)
    position = data.get("position", len(existing) + 1)
    slide_id = dbmod.create_slide(
        deck_id, position, data["title"], data["content"],
        split_mode=data.get("split_mode", "manual"),
    )
    return jsonify({"id": slide_id}), 201


@app.route("/api/slides/<int:slide_id>", methods=["PUT"])
def api_update_slide(slide_id):
    data = parse_json_body()
    if data.get("split_mode") == "image_skill_cover":
        return error_response("split_mode image_skill_cover is server-owned", 422)
    fields = {}
    for key in ("title", "content", "position", "split_mode"):
        if key in data:
            fields[key] = data[key]
    if not fields:
        return error_response("No fields to update")
    ok = dbmod.update_slide(slide_id, **fields)
    if not ok:
        return error_response("Slide not found", 404)
    return jsonify({"ok": True})


@app.route("/api/slides/<int:slide_id>", methods=["DELETE"])
def api_delete_slide(slide_id):
    ok = dbmod.delete_slide(slide_id)
    if not ok:
        return error_response("Slide not found", 404)
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# CRUD: Prompts
# ---------------------------------------------------------------------------

@app.route("/api/prompts", methods=["GET"])
def api_list_prompts():
    agent_type = request.args.get("agent_type")
    status = request.args.get("status", "active")
    folder_id = request.args.get("folder_id", type=int)
    prompts = dbmod.list_prompts(agent_type=agent_type, status=status, folder_id=folder_id)
    return jsonify(folders.attach_folder_ids("prompt", prompts))


@app.route("/api/prompts/<int:prompt_id>", methods=["GET"])
def api_get_prompt(prompt_id):
    p = dbmod.get_prompt(prompt_id)
    if not p:
        return error_response("Prompt not found", 404)
    return jsonify(p)


@app.route("/api/prompts", methods=["POST"])
def api_create_prompt():
    data = parse_json_body()
    require_fields(data, "agent_type", "version", "name", "content")
    analysis = analyze_prompt_variables(data["agent_type"], data["content"])
    prompt_status = data.get("status") or "active"
    if prompt_status == "active" and analysis.get("disabled_variables"):
        return error_response(
            f"Prompt references disabled variables: {', '.join(analysis['disabled_variables'])}",
            422,
        )
    if prompt_status == "active" and not analysis.get("can_publish"):
        return error_response(
            "Prompt integrity checks failed; save as deprecated draft or fix the analysis blockers before publishing.",
            422,
        )
    try:
        should_default = prompt_status == "active" and (
            bool(data.get("is_default")) or dbmod.get_active_prompt(data["agent_type"]) is None
        )
        prompt_id = dbmod.create_prompt(
            data["agent_type"],
            data["version"],
            data["name"],
            data["content"],
            description=data.get("description"),
            is_default=should_default,
            status=prompt_status,
        )
        if data.get("is_default"):
            defaults.set_default_prompt(prompt_id)
    except Exception as e:
        if "UNIQUE" in str(e):
            return error_response(
                f"Prompt with agent_type='{data['agent_type']}' version='{data['version']}' already exists",
                409,
            )
        raise
    return jsonify(dbmod.get_prompt(prompt_id)), 201


def _prompt_publish_error(analysis: dict, prompt_status: str):
    if prompt_status != "active":
        return None
    if analysis.get("disabled_variables"):
        return (
            f"Prompt references disabled variables: {', '.join(analysis['disabled_variables'])}",
            422,
        )
    if not analysis.get("can_publish"):
        return (
            "Prompt integrity checks failed; save as deprecated draft or fix the analysis blockers before publishing.",
            422,
        )
    return None


@app.route("/api/prompts/analyze", methods=["POST"])
def api_analyze_prompt():
    data = parse_json_body()
    require_fields(data, "agent_type", "content")
    try:
        payload = analyze_prompt_variables(
            data["agent_type"],
            data["content"],
            baseline_prompt_id=data.get("baseline_prompt_id"),
        )
    except EmptyPromptContentError as e:
        return error_response(str(e), 422)
    except ValueError as e:
        return error_response(str(e), 400)
    return jsonify(payload)


@app.route("/api/prompts/assist-variables", methods=["POST"])
def api_assist_prompt_variables():
    data = parse_json_body()
    require_fields(data, "agent_type", "content")
    try:
        payload = assist_prompt_variables(
            data["agent_type"],
            data["content"],
            prefer_llm=bool(data.get("prefer_llm", True)),
        )
    except EmptyPromptContentError as e:
        return error_response(str(e), 422)
    except ValueError as e:
        return error_response(str(e), 400)
    return jsonify(payload)


@app.route("/api/prompts/<int:prompt_id>", methods=["PUT"])
def api_update_prompt(prompt_id):
    data = parse_json_body()
    fields = {}
    for key in ("agent_type", "version", "name", "content", "status", "description"):
        if key in data:
            fields[key] = data[key]
    if not fields:
        return error_response("No fields to update")
    existing = dbmod.get_prompt(prompt_id)
    if not existing:
        return error_response("Prompt not found", 404)
    next_agent_type = fields.get("agent_type", existing["agent_type"])
    next_content = fields.get("content", existing["content"])
    next_status = fields.get("status", existing.get("status") or "active")
    baseline_content = existing.get("publish_baseline_content")
    if not baseline_content and "content" in fields and next_content != existing["content"]:
        baseline_content = existing["content"]
    analysis = analyze_prompt_variables(
        next_agent_type,
        next_content,
        baseline_prompt_id=prompt_id,
        baseline_prompt_content=baseline_content,
    )
    publish_error = _prompt_publish_error(analysis, next_status)
    if publish_error:
        return error_response(*publish_error)
    if next_status == "active":
        fields["publish_baseline_content"] = None
    elif baseline_content and not analysis.get("can_publish"):
        fields["publish_baseline_content"] = baseline_content
    ok = dbmod.update_prompt(prompt_id, **fields)
    if not ok:
        return error_response("Prompt not found", 404)
    if data.get("is_default"):
        defaults.set_default_prompt(prompt_id)
    return jsonify(dbmod.get_prompt(prompt_id))


@app.route("/api/prompts/<int:prompt_id>/default", methods=["POST"])
def api_set_default_prompt(prompt_id):
    prompt = defaults.set_default_prompt(prompt_id)
    if not prompt:
        return error_response("Prompt not found", 404)
    return jsonify(prompt)


@app.route("/api/prompts/<int:prompt_id>", methods=["DELETE"])
def api_delete_prompt(prompt_id):
    p = dbmod.get_prompt(prompt_id)
    if not p:
        return error_response("Prompt not found", 404)
    try:
        defaults.ensure_prompt_archive_allowed(prompt_id)
    except ValueError as e:
        return error_response(str(e), 422)
    ok = dbmod.delete_prompt(prompt_id)
    if not ok:
        return error_response("Prompt not found", 404)
    defaults.promote_default_prompt_if_needed(p["agent_type"])
    return jsonify(dbmod.get_prompt(prompt_id))


@app.route("/api/prompts/<int:prompt_id>/restore", methods=["POST"])
def api_restore_prompt(prompt_id):
    if not dbmod.get_prompt(prompt_id):
        return error_response("Prompt not found", 404)
    dbmod.update_prompt(prompt_id, lifecycle_status="active", archived_at=None)
    prompt = dbmod.get_prompt(prompt_id)
    defaults.promote_default_prompt_if_needed(prompt["agent_type"])
    return jsonify(prompt)


@app.route("/api/prompts/<int:prompt_id>/folders", methods=["PUT"])
def api_assign_prompt_folders(prompt_id):
    if not dbmod.get_prompt(prompt_id):
        return error_response("Prompt not found", 404)
    data = parse_json_body()
    try:
        folder_ids = folders.assign_entity_folders("prompt", prompt_id, data.get("folder_ids", []))
    except ValueError as e:
        return error_response(str(e), 400)
    return jsonify({"id": prompt_id, "folder_ids": folder_ids})


@app.route("/api/prompts/<int:prompt_id>/duplicate", methods=["POST"])
def api_duplicate_prompt(prompt_id):
    """Create a copy of an existing prompt with an incremented version."""
    p = dbmod.get_prompt(prompt_id)
    if not p:
        return error_response("Prompt not found", 404)
    data = request.get_json(silent=True) or {}

    new_version = data.get("version")
    new_name = data.get("name")
    new_status = data.get("status")
    if new_status and new_status not in {"active", "deprecated"}:
        return error_response("Prompt status must be active or deprecated", 400)

    if not new_version:
        # Increment version: e.g. "v5.3" -> "v5.3-copy1", "v5.3-copy1" -> "v5.3-copy2"
        base_version = p["version"]
        suffix = 1
        new_version = f"{base_version}-copy{suffix}"
        # Keep incrementing until we find a unique version
        while True:
            existing = dbmod.list_prompts(agent_type=p["agent_type"])
            existing_versions = {ep["version"] for ep in existing}
            if new_version not in existing_versions:
                break
            suffix += 1
            new_version = f"{base_version}-copy{suffix}"
    if not new_name:
        new_name = f"{p['name']} (copy)"

    target_status = new_status or "active"
    baseline_content = p.get("publish_baseline_content")
    analysis = analyze_prompt_variables(
        p["agent_type"],
        p["content"],
        baseline_prompt_content=baseline_content,
    )
    publish_error = _prompt_publish_error(analysis, target_status)
    if publish_error:
        return error_response(*publish_error)

    try:
        new_id = dbmod.create_prompt(
            agent_type=p["agent_type"],
            version=new_version,
            name=new_name,
            content=p["content"],
            description=p.get("description"),
            status=target_status,
            publish_baseline_content=baseline_content if target_status != "active" else None,
        )
    except Exception as e:
        if "UNIQUE" in str(e):
            return error_response("Failed to create unique version for duplicate", 409)
        raise
    return jsonify({"id": new_id}), 201


# ---------------------------------------------------------------------------
# Split endpoint
# ---------------------------------------------------------------------------

@app.route("/api/decks/<int:deck_id>/split", methods=["POST"])
def api_split_deck(deck_id):
    """Split a deck's content into slides using markdown headings or LLM.

    Optional JSON body:
    {
        "config_id": 1,          // config for LLM fallback (optional)
        "replace": true           // replace existing slides (default: true)
    }
    """
    d = dbmod.get_deck(deck_id)
    if not d:
        return error_response("Deck not found", 404)

    data = request.get_json(silent=True) or {}
    replace = data.get("replace", True)

    # Build LLM config for fallback (optional)
    llm_config = None
    config_id = data.get("config_id")
    if config_id:
        config_row = dbmod.get_config(config_id)
        if config_row:
            if dbmod.normalize_config_type(config_row.get("type")) != "html":
                return error_response("Split deck requires an html config", 422)
            # Use the designer agent config for splitting
            llm_config = json.loads(config_row["designer"]) if isinstance(config_row["designer"], str) else config_row["designer"]

    try:
        slides_data = split_deck(d["content"], llm_config=llm_config)
    except ValueError as e:
        return error_response(str(e), 422)
    except Exception as e:
        log.error("Split failed: %s", e, exc_info=True)
        return error_response(f"Split failed: {e}", 500)

    if replace:
        dbmod.delete_slides_for_deck(deck_id)

    slide_ids = dbmod.bulk_create_slides(deck_id, slides_data)

    return jsonify({
        "deck_id": deck_id,
        "slide_count": len(slide_ids),
        "slide_ids": slide_ids,
        "slides": slides_data,
    }), 201


@app.route("/api/decks/<int:deck_id>/split-drafts", methods=["POST"])
def api_create_split_draft(deck_id):
    data = request.get_json(silent=True) or {}
    try:
        draft = deck_split_drafts.create_split_draft(
            deck_id,
            config_id=data.get("config_id"),
            mode=data.get("mode") or "llm",
        )
    except deck_split_drafts.SplitDraftExecutionError as e:
        return jsonify({"error": str(e), "draft": e.draft}), e.status_code
    except deck_split_drafts.SplitDraftError as e:
        return error_response(str(e), e.status_code)
    return jsonify(draft), 201


@app.route("/api/deck-split-drafts/<int:draft_id>/confirm", methods=["POST"])
def api_confirm_split_draft(draft_id):
    try:
        result = deck_split_drafts.confirm_split_draft(draft_id)
    except deck_split_drafts.SplitDraftError as e:
        return error_response(str(e), e.status_code)
    return jsonify(result)


@app.route("/api/deck-split-drafts/<int:draft_id>/retry", methods=["POST"])
def api_retry_split_draft(draft_id):
    data = request.get_json(silent=True)
    if data not in (None, {}):
        return error_response("Retry does not accept model or execution overrides", 400)
    try:
        result = deck_split_drafts.retry_split_draft(draft_id)
    except deck_split_drafts.SplitDraftExecutionError as e:
        return jsonify({"error": str(e), "draft": e.draft}), e.status_code
    except deck_split_drafts.SplitDraftError as e:
        return error_response(str(e), e.status_code)
    return jsonify(result)


@app.route("/api/deck-split-drafts/<int:draft_id>/revise", methods=["POST"])
def api_revise_split_draft(draft_id):
    data = request.get_json(silent=True) or {}
    try:
        result = deck_split_drafts.revise_split_draft(
            draft_id,
            data.get("instruction") or "",
        )
    except deck_split_drafts.SplitDraftError as e:
        return error_response(str(e), e.status_code)
    return jsonify(result)


@app.route("/api/deck-split-drafts/<int:draft_id>", methods=["DELETE"])
def api_delete_split_draft(draft_id):
    if not deck_split_drafts.delete_split_draft(draft_id):
        return error_response("Pending or failed split draft not found", 404)
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Generate endpoint
# ---------------------------------------------------------------------------

@app.route("/api/generate", methods=["POST"])
def api_generate():
    """Create pipeline runs and launch background threads.

    JSON body:
    {
        "deck_id": 1,
        "requirement_ids": [1, 2],
        "color_ids": [1, 3],
        "config_id": 1
    }

    Creates one run for each (requirement_id, color_id) combination.
    Returns the run IDs immediately.
    """
    try:
        payload = create_generation_batch(
            parse_json_body(),
            db_path=str(dbmod.DB_PATH),
            launch_batch_runs=launch_batch_runs,
            batch_run_limit=DEFAULT_BATCH_RUN_LIMIT,
        )
    except GenerationRequestError as exc:
        return error_response(exc.message, exc.status_code)

    return jsonify(payload), 202


# ---------------------------------------------------------------------------
# Evaluation endpoints
# ---------------------------------------------------------------------------

@app.route("/api/evaluations", methods=["GET"])
def api_list_evaluations():
    return jsonify(evaluations.list_evaluations())


@app.route("/api/evaluations/history-runs", methods=["GET"])
def api_list_evaluation_history_runs():
    return jsonify(evaluations.list_history_run_candidates())


@app.route("/api/evaluations/<int:evaluation_id>", methods=["GET"])
def api_get_evaluation(evaluation_id):
    evaluation = evaluations.get_evaluation(evaluation_id)
    if not evaluation:
        return error_response("Evaluation not found", 404)
    enrich_evaluation_detail_artifact_contents(evaluation)
    return jsonify(evaluation)


@app.route("/api/evaluations/history", methods=["POST"])
def api_create_history_evaluation():
    try:
        evaluation = evaluations.create_history_evaluation(parse_json_body())
    except evaluations.EvaluationRequestError as exc:
        return error_response(exc.message, exc.status_code)
    return jsonify(evaluation), 201


@app.route("/api/evaluations/blank", methods=["POST"])
def api_create_blank_evaluation():
    try:
        evaluation = evaluations.create_blank_evaluation(
            parse_json_body(),
            db_path=str(dbmod.DB_PATH),
            launch_batch_runs=launch_batch_runs,
        )
    except evaluations.EvaluationRequestError as exc:
        return error_response(exc.message, exc.status_code)
    return jsonify(evaluation), 202


@app.route("/api/evaluations/<int:evaluation_id>/variants/<int:variant_id>", methods=["PATCH"])
def api_update_evaluation_variant(evaluation_id, variant_id):
    try:
        return jsonify(evaluations.update_variant(evaluation_id, variant_id, parse_json_body()))
    except evaluations.EvaluationRequestError as exc:
        return error_response(exc.message, exc.status_code)


@app.route("/api/evaluations/<int:evaluation_id>/variants/<int:variant_id>/representative", methods=["PATCH"])
def api_update_evaluation_representative(evaluation_id, variant_id):
    data = parse_json_body()
    try:
        return jsonify(evaluations.update_representative(evaluation_id, variant_id, data.get("attempt_id")))
    except evaluations.EvaluationRequestError as exc:
        return error_response(exc.message, exc.status_code)


@app.route("/api/evaluations/<int:evaluation_id>/notes", methods=["POST"])
def api_create_evaluation_note(evaluation_id):
    try:
        return jsonify(evaluations.add_note(evaluation_id, parse_json_body())), 201
    except evaluations.EvaluationRequestError as exc:
        return error_response(exc.message, exc.status_code)


@app.route("/api/evaluations/<int:evaluation_id>/slide-tags", methods=["POST"])
def api_create_evaluation_slide_tag(evaluation_id):
    try:
        return jsonify(evaluations.add_slide_tag(evaluation_id, parse_json_body())), 201
    except evaluations.EvaluationRequestError as exc:
        return error_response(exc.message, exc.status_code)


@app.route("/api/evaluations/<int:evaluation_id>/machine-qa", methods=["POST"])
def api_create_evaluation_machine_qa(evaluation_id):
    try:
        return jsonify(evaluations.record_machine_qa(evaluation_id, parse_json_body())), 201
    except evaluations.EvaluationRequestError as exc:
        return error_response(exc.message, exc.status_code)


@app.route("/api/evaluations/<int:evaluation_id>/machine-qa/run", methods=["POST"])
def api_run_evaluation_machine_qa(evaluation_id):
    try:
        return jsonify(evaluation_machine_qa.run_machine_qa(evaluation_id, parse_json_body())), 201
    except evaluations.EvaluationRequestError as exc:
        return error_response(exc.message, exc.status_code)


@app.route("/api/evaluations/<int:evaluation_id>/export", methods=["POST"])
def api_export_evaluation(evaluation_id):
    try:
        archive = evaluations.build_export_archive(evaluation_id, parse_json_body())
    except evaluations.EvaluationRequestError as exc:
        return error_response(exc.message, exc.status_code)
    return send_file(
        archive.data,
        mimetype=archive.mimetype,
        as_attachment=True,
        download_name=archive.filename,
    )


# ---------------------------------------------------------------------------
# Status endpoint
# ---------------------------------------------------------------------------

@app.route("/api/runs/<int:run_id>/status", methods=["GET"])
def api_run_status(run_id):
    """Return the current progress of a pipeline run."""
    run_history.reconcile_default_timeout()
    run = dbmod.get_run(run_id)
    if not run:
        return error_response("Run not found", 404)

    progress = dbmod.get_run_progress(run_id)
    try:
        activity = codex_activity_projection.project_run_activity(
            run_id,
            after_cursor=request.args.get("activity_after"),
        )
    except ValueError as exc:
        return error_response(str(exc), 400)
    return jsonify({
        "run_id": run_id,
        "status": run["status"],
        "progress": progress,
        "error_message": run.get("error_message"),
        "started_at": run.get("started_at"),
        "completed_at": run.get("completed_at"),
        "activity": activity,
    })


# ---------------------------------------------------------------------------
# Batch endpoints
# ---------------------------------------------------------------------------

@app.route("/api/batches", methods=["GET"])
def api_list_batches():
    return jsonify(run_history.list_batches(str(dbmod.DB_PATH), pump_queue=pump_batch_queue))


@app.route("/api/batches/active", methods=["GET"])
def api_get_active_batch():
    return jsonify(run_history.get_active_batch(str(dbmod.DB_PATH), pump_queue=pump_batch_queue))


@app.route("/api/batches/<int:batch_id>", methods=["GET"])
def api_get_batch(batch_id):
    batch = run_history.get_batch_detail(
        batch_id,
        db_path=str(dbmod.DB_PATH),
        pump_queue=pump_batch_queue,
    )
    if not batch:
        return error_response("Batch not found", 404)
    return jsonify(batch)


@app.route("/api/generation-actions", methods=["POST"])
def api_generation_action():
    try:
        result = apply_generation_action(
            parse_json_body(),
            db_path=str(dbmod.DB_PATH),
            pump_queue=pump_batch_queue,
        )
    except GenerationActionError as exc:
        return error_response(exc.message, exc.status_code)
    return jsonify(result), 202


@app.route("/api/generation-actions/auto-retry-poll", methods=["POST"])
def api_generation_auto_retry_poll():
    try:
        result = run_due_retry_poll(db_path=str(dbmod.DB_PATH), pump_queue=pump_batch_queue)
    except GenerationActionError as exc:
        return error_response(exc.message, exc.status_code)
    return jsonify(result), 202


@app.route("/api/batches/<int:batch_id>/download", methods=["GET"])
def api_download_batch(batch_id):
    try:
        archive = build_batch_download(batch_id, ARTIFACTS_DIR)
    except BatchDownloadError as exc:
        return error_response(exc.message, exc.status_code)
    return send_file(
        archive.data,
        mimetype="application/zip",
        as_attachment=True,
        download_name=archive.filename,
    )


# ---------------------------------------------------------------------------
# RunFail Phase A endpoints
# ---------------------------------------------------------------------------

@app.route("/api/runfail/stats", methods=["GET"])
def api_runfail_stats():
    filters = {
        "route_type": request.args.get("route_type") or request.args.get("type"),
        "date_preset": request.args.get("date_preset") or request.args.get("preset"),
        "start_date": request.args.get("start_date"),
        "end_date": request.args.get("end_date"),
    }
    return jsonify(run_history.get_runfail_stats(filters))


@app.route("/api/runfail/export", methods=["GET"])
def api_runfail_export():
    export_format = (request.args.get("format") or "json").lower()
    filters = {
        "route_type": request.args.get("route_type") or request.args.get("type"),
        "date_preset": request.args.get("date_preset") or request.args.get("preset"),
        "start_date": request.args.get("start_date"),
        "end_date": request.args.get("end_date"),
    }
    payload = run_history.get_runfail_export_payload(filters)
    rows = payload["rows"]
    if export_format == "json":
        return Response(
            json.dumps(payload, ensure_ascii=False, indent=2),
            mimetype="application/json",
            headers={"Content-Disposition": "attachment; filename=runfail-phase-a.json"},
        )
    if export_format == "csv":
        output = StringIO()
        writer = csv.DictWriter(output, fieldnames=["section", "key", "count", "percent", "total_runs", "failed_or_timed_out", "insight", "recommended_action"], extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
        return Response(
            output.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=runfail-phase-a.csv"},
        )
    return error_response("Unsupported export format", 400)


# ---------------------------------------------------------------------------
# History endpoints (runs)
# ---------------------------------------------------------------------------

@app.route("/api/runs", methods=["GET"])
def api_list_runs():
    """List all runs with summary info."""
    run_history.reconcile_default_timeout()
    runs = dbmod.list_runs()
    # Add progress to each run
    for r in runs:
        r["progress"] = dbmod.get_run_progress(r["id"])
        evaluations.enrich_run_with_deck_snapshot(r)
    return jsonify(runs)


@app.route("/api/runs/<int:run_id>", methods=["GET"])
def api_get_run(run_id):
    """Get full run detail with lineage and all run_slides."""
    run_history.reconcile_default_timeout()
    run = dbmod.get_run(run_id)
    if not run:
        return error_response("Run not found", 404)

    run["progress"] = dbmod.get_run_progress(run_id)
    run["slides"] = dbmod.list_run_slides(run_id)
    for key in ("route_metadata", "stage_artifacts", "model_call_metadata"):
        run[key] = parse_json_field(run.get(key))
    for slide in run["slides"]:
        for key in ("stage_artifacts", "seed_dependency"):
            slide[key] = parse_json_field(slide.get(key))
        project_active_version_evidence(slide)
        enrich_slide_artifact_contents(slide)

    # Include source content for lineage tracking
    deck = dbmod.get_deck(run["deck_id"])
    req = dbmod.get_requirement(run["requirement_id"])
    color = dbmod.get_color(run["color_id"])
    if deck:
        run["deck_content"] = deck["content"]
    if req:
        run["requirement_content"] = req["content"]
    if color:
        run["color_content"] = color["content"]

    # Include prompt version info
    designer_prompt_id = run.get("designer_prompt_id")
    html_prompt_id = run.get("html_prompt_id")
    if designer_prompt_id:
        dp = dbmod.get_prompt(designer_prompt_id)
        run["designer_prompt_version"] = dp["version"] if dp else None
    else:
        run["designer_prompt_version"] = None
    if html_prompt_id:
        hp = dbmod.get_prompt(html_prompt_id)
        run["html_prompt_version"] = hp["version"] if hp else None
    else:
        run["html_prompt_version"] = None

    audit_summary = codex_audit.get_codex_run_audit(run_id)
    if codex_audit.run_has_native_invocation(run_id):
        _project_native_run_payload(run)
    if run.get("strategy") == "codex_html" or audit_summary.get("invocation_count"):
        run["codex_audit"] = audit_summary

    return jsonify(run)


@app.route("/api/runs/<int:run_id>/codex-audit/invocations/<int:invocation_id>", methods=["GET"])
def api_get_codex_audit_invocation_detail(run_id, invocation_id):
    """Load one explicit, Run-owned Native Codex audit detail.

    The endpoint deliberately accepts no path or file selector.  The audit
    service validates the persisted invocation ownership and derives all local
    evidence from that durable identity.
    """
    if request.args:
        return error_response("Codex audit detail does not accept filesystem paths or query selectors", 400)
    if not dbmod.get_run(run_id):
        return error_response("Run not found", 404)
    try:
        detail = codex_audit.get_native_codex_audit_detail(run_id=run_id, invocation_id=invocation_id)
    except codex_audit.CodexAuditDetailUnavailable:
        return error_response("Codex audit detail not found", 404)
    return jsonify(detail)


@app.route("/api/runs/<int:run_id>/codex-audit/invocations/<int:invocation_id>/events", methods=["GET"])
def api_get_codex_audit_invocation_events(run_id, invocation_id):
    """Return one explicit bounded event page for a Run-owned invocation."""
    unexpected_query = set(request.args) - {"cursor"}
    if unexpected_query:
        return error_response("Codex audit event page accepts only its signed cursor", 400)
    if not dbmod.get_run(run_id):
        return error_response("Run not found", 404)
    try:
        page = codex_audit.get_codex_audit_event_page(
            run_id=run_id,
            invocation_id=invocation_id,
            cursor=request.args.get("cursor"),
        )
    except codex_audit.CodexAuditEventPageUnavailable as exc:
        return error_response(str(exc), 400)
    return jsonify(page)


@app.route("/api/codex-sessions/<session_id>/summary", methods=["GET"])
def api_get_codex_session_summary(session_id):
    return _read_codex_session(session_id, level="L1", allowed_query=set())


@app.route("/api/codex-sessions/<session_id>/index", methods=["GET"])
def api_get_codex_session_index(session_id):
    return _read_codex_session(session_id, level="L2", allowed_query={"cursor"})


@app.route("/api/codex-sessions/<session_id>/detail", methods=["GET"])
def api_get_codex_session_detail(session_id):
    return _read_codex_session(
        session_id,
        level="L3",
        allowed_query={"sequence", "cursor", "turn_id", "role", "kind", "phase", "tool_name"},
    )


@app.route("/api/codex-sessions/<session_id>/raw", methods=["GET"])
def api_get_codex_session_raw(session_id):
    return _read_codex_session(session_id, level="L4", allowed_query={"cursor"}, raw=True)


def enrich_evaluation_detail_artifact_contents(evaluation: dict) -> None:
    for variant in evaluation.get("variants", []):
        for attempt in variant.get("attempts", []):
            for slide in attempt.get("slides", []):
                for key in ("stage_artifacts", "seed_dependency"):
                    slide[key] = parse_json_field(slide.get(key))
                project_active_version_evidence(slide)
                enrich_slide_artifact_contents(slide)


@app.route("/api/runs/<int:run_id>/download", methods=["GET"])
def api_download_run(run_id):
    try:
        archive = build_run_download(run_id, ARTIFACTS_DIR)
    except BatchDownloadError as exc:
        return error_response(exc.message, exc.status_code)
    return send_file(
        archive.data,
        mimetype="application/zip",
        as_attachment=True,
        download_name=archive.filename,
    )


@app.route("/api/artifact-versions/<int:version_id>/activate", methods=["POST"])
def api_activate_artifact_version(version_id):
    try:
        active = dbmod.restore_artifact_version(version_id)
    except ValueError as exc:
        return error_response(str(exc), 404)
    return jsonify({"ok": True, "active_version": active})


@app.route("/api/run-slides/<int:run_slide_id>/evidence-download", methods=["GET"])
def api_download_run_slide_evidence(run_slide_id):
    try:
        archive = build_slide_evidence_download(run_slide_id, ARTIFACTS_DIR)
    except BatchDownloadError as exc:
        return error_response(exc.message, exc.status_code)
    return send_file(
        archive.data,
        mimetype="application/zip",
        as_attachment=True,
        download_name=archive.filename,
    )


@app.route("/api/runs/<int:run_id>", methods=["DELETE"])
def api_delete_run(run_id):
    run = dbmod.get_run(run_id)
    if not run:
        return error_response("Run not found", 404)
    if run["status"] == "running":
        return error_response("Cannot delete a running pipeline", 409)
    try:
        ok = codex_evidence_lifecycle.delete_run_with_raw_evidence(run_id)
    except codex_evidence_lifecycle.UnsafeRawEvidencePath as exc:
        return error_response(str(exc), 409)
    if not ok:
        return error_response("Run not found", 404)
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Static file serving
# ---------------------------------------------------------------------------

@app.route("/artifacts/<path:filename>")
def serve_artifact(filename):
    """Serve generated files from the artifacts directory."""
    if not ARTIFACTS_DIR.exists():
        abort(404)
    if _private_artifact_request(filename):
        abort(404)
    decoded_filename = _decoded_artifact_request(filename)
    try:
        resolved = (ARTIFACTS_DIR / decoded_filename).resolve()
        relative = resolved.relative_to(ARTIFACTS_DIR.resolve())
    except (OSError, ValueError):
        abort(404)
    if ".codex-private" in relative.parts:
        abort(404)
    return send_from_directory(str(ARTIFACTS_DIR), decoded_filename)


# Frontend SPA fallback: serve index.html for non-API, non-artifact routes
@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def serve_frontend(path):
    """Serve frontend SPA from frontend/dist/ if it exists."""
    if path.startswith("api/"):
        return error_response("API endpoint not found", 404)

    if not FRONTEND_DIR.exists():
        return error_response(
            "Public frontend build is unavailable; run `cd frontend && npm run build` "
            "before starting `python3 public_server.py`.",
            503,
        )

    # Try to serve the exact file
    full_path = FRONTEND_DIR / path
    if full_path.is_file():
        return send_from_directory(str(FRONTEND_DIR), path)

    # SPA fallback: serve index.html
    index_path = FRONTEND_DIR / "index.html"
    if index_path.is_file():
        return send_from_directory(str(FRONTEND_DIR), "index.html")

    abort(404)


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

def create_app():
    """Factory function for creating and configuring the Flask app."""
    # Ensure DB is initialized
    dbmod.init_db()
    model_profiles.ensure_html_test_zenmux_combinations()
    model_profiles.ensure_codex_html_combination()
    model_profiles.ensure_native_image_combinations()
    model_profiles.ensure_gpt_image_2_product_combinations()
    model_profiles.ensure_image_direct_combinations()
    # Ensure artifacts directory exists
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    return app


if __name__ == "__main__":
    raise SystemExit(
        "Direct execution of server.py is disabled for the public build. "
        "Use `python3 public_server.py` instead."
    )
