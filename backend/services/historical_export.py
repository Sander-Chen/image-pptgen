"""Historical JSON export for data purged from the product UI."""

from __future__ import annotations

import json
from datetime import datetime, timezone
import os
from pathlib import Path

_DEFAULT_HISTORICAL_DATA_DIR = Path(__file__).resolve().parents[2] / "historical_data"


def _configured_historical_data_dir() -> Path:
    configured = os.environ.get("PPTGEN_HISTORICAL_DATA_DIR")
    if not configured:
        return _DEFAULT_HISTORICAL_DATA_DIR
    path = Path(configured).expanduser().resolve()
    public_data = os.environ.get("PPTGEN_PUBLIC_DATA_DIR")
    if public_data:
        public_root = Path(public_data).expanduser().resolve()
        try:
            path.relative_to(public_root)
        except ValueError as exc:
            raise RuntimeError(
                "PPTGEN_HISTORICAL_DATA_DIR must be inside PPTGEN_PUBLIC_DATA_DIR"
            ) from exc
        if path == public_root:
            raise RuntimeError(
                "PPTGEN_HISTORICAL_DATA_DIR must be a child of PPTGEN_PUBLIC_DATA_DIR"
            )
    return path


HISTORICAL_DATA_DIR = _configured_historical_data_dir()


def write_historical_export(entity_type: str, record: dict, payload: dict) -> str:
    HISTORICAL_DATA_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = HISTORICAL_DATA_DIR / f"{entity_type}_{record['id']}_{timestamp}.json"
    export_payload = {
        "entity_type": entity_type,
        "exported_at": timestamp,
        "record": record,
        **payload,
    }
    path.write_text(json.dumps(export_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)
