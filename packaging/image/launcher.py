#!/usr/bin/env python3
"""Loopback-only launcher for the installed Image PPTGen runtime."""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path

from backend.services.platform_runtime import write_private_json


IMAGE_PRODUCT = "image-pptgen"
IMAGE_SERVICE = "image-pptgen-server"
IMAGE_SKILL = "generate-image-presentation"
IMAGE_SURFACE = "public_image_3_0"
IMAGE_DATA_NAMESPACE = "image-pptgen"
IMAGE_CONFIG_NAMESPACE = "image-pptgen"
IMAGE_HOST = "127.0.0.1"
IMAGE_PORT = 3130


def _write_private_json(path: Path, payload: dict[str, str]) -> None:
    write_private_json(path, payload)


def _data_root() -> Path:
    explicit = os.environ.get("IMAGE_PPTGEN_DATA_ROOT")
    if explicit:
        return Path(explicit).expanduser().resolve()
    active_root = os.environ.get("PPTGEN_DATA_ROOT")
    if active_root:
        return Path(active_root).expanduser().resolve()
    if os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            return (Path(local_app_data).expanduser() / "ImagePPTGen").resolve()
        return (Path.home() / "AppData" / "Local" / "ImagePPTGen").resolve()
    data_home = Path(
        os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")
    ).expanduser()
    return (data_home / IMAGE_DATA_NAMESPACE).resolve()


def _config_root() -> Path:
    config_home = Path(
        os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")
    ).expanduser()
    return (config_home / IMAGE_CONFIG_NAMESPACE).resolve()


def _release_identity(app_root: Path) -> dict[str, object]:
    path = (app_root / "release-identity.json").resolve()
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Image release identity is unavailable: {path}") from exc
    if not isinstance(value, dict):
        raise RuntimeError("Image release identity is invalid")
    required = (
        "build_id",
        "version",
        "source_commit",
        "skill_sha256",
        "runtime_content_sha256",
    )
    if any(not isinstance(value.get(field), str) or not value[field].strip() for field in required):
        raise RuntimeError("Image release identity is invalid")
    if (
        value.get("product") != IMAGE_PRODUCT
        or value.get("service") != IMAGE_SERVICE
        or value.get("surface") != IMAGE_SURFACE
    ):
        raise RuntimeError("Image release identity is invalid")
    return value


def prepare_runtime_identity(
    app_root: Path,
    *,
    host: str = IMAGE_HOST,
    port: int = IMAGE_PORT,
) -> dict[str, str]:
    """Bind isolated Image roots and persist one stable instance identity."""
    if host != IMAGE_HOST:
        raise ValueError("Image PPTGen may bind only to 127.0.0.1")
    if port != IMAGE_PORT:
        raise ValueError("Image PPTGen listens only on port 3130")

    app_root = app_root.resolve()
    release_path = app_root / "release-identity.json"
    release = _release_identity(app_root)
    data_root = _data_root()
    config_root = _config_root()
    state_root = data_root / "state"
    public_data_root = state_root / "data"
    db_path = public_data_root / "ppt.db"
    artifacts_root = public_data_root / "artifacts"
    historical_root = public_data_root / "historical-data"
    instance_path = state_root / "runtime-instance.json"

    try:
        instance = json.loads(instance_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        instance = {}
    if not isinstance(instance, dict) or not isinstance(instance.get("instance_id"), str):
        instance = {"instance_id": str(uuid.uuid4())}
        _write_private_json(instance_path, instance)
    else:
        instance_path.chmod(0o600)

    identity = {
        "base_url": f"http://{host}:{port}",
        "build_id": str(release["build_id"]),
        "config_root": str(config_root),
        "data_root": str(data_root),
        "instance_id": str(instance["instance_id"]),
        "product": IMAGE_PRODUCT,
        "release_root": str(app_root.parent.resolve()),
        "service": IMAGE_SERVICE,
        "skill": IMAGE_SKILL,
        "skill_sha256": str(release.get("skill_sha256", "")),
        "source_commit": str(release.get("source_commit", "")),
        "surface": IMAGE_SURFACE,
        "version": str(release.get("version", "")),
    }
    os.environ.update(
        {
            "PPTGEN_BASE_URL": identity["base_url"],
            "PPTGEN_DATA_ROOT": identity["data_root"],
            "PPTGEN_INSTANCE_ID_PATH": str(instance_path),
            "PPTGEN_RELEASE_IDENTITY_PATH": str(release_path),
            "PPTGEN_RELEASE_ROOT": identity["release_root"],
            "PPTGEN_PUBLIC_DATA_DIR": str(public_data_root),
            "PPTGEN_HISTORICAL_DATA_DIR": str(historical_root),
            "PPTGEN_IMAGE_RUNTIME_MODE": "installed",
            "PPT_DB_PATH": str(db_path),
            "PPT_ARTIFACTS_DIR": str(artifacts_root),
            "PORT": str(port),
        }
    )
    return identity


def main() -> int:
    parser = argparse.ArgumentParser(description="Start the installed Image PPTGen runtime")
    parser.add_argument("--host", default=os.environ.get("PPTGEN_HOST", IMAGE_HOST))
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("PPTGEN_PORT", str(IMAGE_PORT)))
    )
    args = parser.parse_args()
    if args.host != IMAGE_HOST:
        parser.error("installed Image PPTGen may bind only to 127.0.0.1")
    if args.port != IMAGE_PORT:
        parser.error("installed Image PPTGen listens only on port 3130")

    app_root = Path(__file__).resolve().parent
    prepare_runtime_identity(app_root, host=args.host, port=args.port)
    sys.path.insert(0, str(app_root))
    import public_server  # noqa: PLC0415
    from waitress import serve  # noqa: PLC0415

    serve(public_server.app, host=args.host, port=args.port, threads=4)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
