#!/usr/bin/env python3
"""Supported runtime entrypoint for the public Image PPT 3.0 product."""

from __future__ import annotations

import logging
import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
PUBLIC_DATA_DIR = Path(
    os.environ.get("PPTGEN_PUBLIC_DATA_DIR", BASE_DIR / "public-data")
).expanduser().resolve()
PUBLIC_DB_PATH = Path(
    os.environ.get("PPT_DB_PATH", PUBLIC_DATA_DIR / "ppt.db")
).expanduser().resolve()
PUBLIC_ARTIFACTS_DIR = Path(
    os.environ.get("PPT_ARTIFACTS_DIR", PUBLIC_DATA_DIR / "artifacts")
).expanduser().resolve()
PUBLIC_HISTORICAL_DATA_DIR = Path(
    os.environ.get(
        "PPTGEN_HISTORICAL_DATA_DIR", PUBLIC_DATA_DIR / "historical-data"
    )
).expanduser().resolve()
SOURCE_FULL_ROOT = (BASE_DIR.parents[1] / "ppt-gen-platform").resolve()


def _paths_overlap(left: Path, right: Path) -> bool:
    try:
        left.relative_to(right)
        return True
    except ValueError:
        try:
            right.relative_to(left)
            return True
        except ValueError:
            return False


protected_roots = [
    SOURCE_FULL_ROOT,
    (BASE_DIR / "ppt.db").resolve(),
    (BASE_DIR / "artifacts").resolve(),
]
protected_roots.extend(
    Path(value).expanduser().resolve()
    for value in os.environ.get("PPTGEN_PROTECTED_DATA_ROOTS", "").split(os.pathsep)
    if value.strip()
)
if any(
    _paths_overlap(candidate, protected_root)
    for candidate in (PUBLIC_DATA_DIR, PUBLIC_DB_PATH, PUBLIC_ARTIFACTS_DIR)
    for protected_root in protected_roots
):
    raise RuntimeError("Public data roots must not overlap a protected data root")


def _require_public_child(path: Path, variable_name: str) -> None:
    try:
        path.relative_to(PUBLIC_DATA_DIR)
    except ValueError as exc:
        raise RuntimeError(
            f"{variable_name} must be inside PPTGEN_PUBLIC_DATA_DIR"
        ) from exc


_require_public_child(PUBLIC_DB_PATH, "PPT_DB_PATH")
_require_public_child(PUBLIC_ARTIFACTS_DIR, "PPT_ARTIFACTS_DIR")
_require_public_child(PUBLIC_HISTORICAL_DATA_DIR, "PPTGEN_HISTORICAL_DATA_DIR")
if PUBLIC_HISTORICAL_DATA_DIR == PUBLIC_DATA_DIR:
    raise RuntimeError(
        "PPTGEN_HISTORICAL_DATA_DIR must be a child of PPTGEN_PUBLIC_DATA_DIR"
    )
if any(
    _paths_overlap(PUBLIC_HISTORICAL_DATA_DIR, candidate)
    for candidate in (PUBLIC_DB_PATH, PUBLIC_ARTIFACTS_DIR)
):
    raise RuntimeError("Public historical export root must not overlap database or artifacts")
if PUBLIC_DB_PATH == PUBLIC_ARTIFACTS_DIR or PUBLIC_ARTIFACTS_DIR == PUBLIC_DATA_DIR:
    raise RuntimeError("Public database and artifact roots must be distinct child paths")

os.environ["PPT_DB_PATH"] = str(PUBLIC_DB_PATH)
os.environ["PPT_ARTIFACTS_DIR"] = str(PUBLIC_ARTIFACTS_DIR)
os.environ["PPTGEN_HISTORICAL_DATA_DIR"] = str(PUBLIC_HISTORICAL_DATA_DIR)
PUBLIC_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
PUBLIC_ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)

import server as server  # noqa: E402  roots must be bound before shared imports
from backend.services.public_image_surface import install_public_image_surface  # noqa: E402


app = server.create_app()
install_public_image_surface(app, artifacts_root=server.ARTIFACTS_DIR)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    port = int(os.environ.get("PORT", "3100"))
    server.log.info("Starting Public Image PPT 3.0 server on port %s", port)
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("FLASK_DEBUG") == "1")


if __name__ == "__main__":
    main()
