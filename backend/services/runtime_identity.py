"""Read the exact PPTGen release and runtime instance identity."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


REQUIRED_RELEASE_FIELDS = (
    "build_id",
    "product",
    "skill_sha256",
    "source_commit",
    "version",
)


def _read_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{label} is unavailable: {path}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{label} is invalid: {path}")
    return payload


def read_runtime_identity() -> dict[str, str]:
    release_path_value = os.environ.get("PPTGEN_RELEASE_IDENTITY_PATH")
    instance_path_value = os.environ.get("PPTGEN_INSTANCE_ID_PATH")
    if not release_path_value or not instance_path_value:
        raise RuntimeError("runtime identity paths are not configured")

    release_path = Path(release_path_value).expanduser().resolve()
    instance_path = Path(instance_path_value).expanduser().resolve()
    release = _read_json(release_path, label="release identity")
    instance = _read_json(instance_path, label="runtime instance identity")
    missing = [field for field in REQUIRED_RELEASE_FIELDS if not release.get(field)]
    if missing:
        raise RuntimeError(f"release identity is missing: {', '.join(missing)}")
    instance_id = instance.get("instance_id")
    if not isinstance(instance_id, str) or not instance_id:
        raise RuntimeError("runtime instance identity is missing: instance_id")

    required_environment = {
        "base_url": os.environ.get("PPTGEN_BASE_URL"),
        "data_root": os.environ.get("PPTGEN_DATA_ROOT"),
        "release_root": os.environ.get("PPTGEN_RELEASE_ROOT"),
    }
    absent = [name for name, value in required_environment.items() if not value]
    if absent:
        raise RuntimeError(f"runtime identity environment is missing: {', '.join(absent)}")

    return {
        "base_url": str(required_environment["base_url"]),
        "build_id": str(release["build_id"]),
        "data_root": str(required_environment["data_root"]),
        "instance_id": instance_id,
        "product": str(release["product"]),
        "release_root": str(required_environment["release_root"]),
        "service": "pptgen-platform",
        "skill_sha256": str(release["skill_sha256"]),
        "source_commit": str(release["source_commit"]),
        "version": str(release["version"]),
    }
