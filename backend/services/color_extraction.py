"""Image palette extraction workflow."""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import uuid
from pathlib import Path
from xml.etree import ElementTree

import requests
from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

import db as dbmod
from backend.services.llm_concurrency import acquire_provider_slot

BASE_DIR = Path(__file__).resolve().parents[2]
PROMPT_PATH = dbmod.IMAGE_PROMPT_SOURCE_FILES["image_palette_extraction"]
UPLOAD_DIR = BASE_DIR / "artifacts" / "color_uploads"
PALETTE_MODEL = "gemini-3-flash-preview"
GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models"


class ColorExtractionError(ValueError):
    status_code = 400

    def __init__(self, *args: object, provider_http_status: int | None = None):
        super().__init__(*args)
        self.provider_http_status = provider_http_status


def extract_palette_xml(
    image_path: Path,
    prompt: str,
    mime_type: str,
    *,
    api_key: str | None = None,
    model: str | None = None,
    endpoint: str | None = None,
) -> str:
    """Call Gemini multimodal to extract a PPT palette.

    Kept as a module-level hook so tests can monkeypatch the expensive call.
    """
    effective_api_key = api_key or os.environ.get("GEMINI_API_KEY")
    if not effective_api_key:
        raise ColorExtractionError("GEMINI_API_KEY is required for image palette extraction")

    image_data = base64.b64encode(image_path.read_bytes()).decode("ascii")
    base_endpoint = (endpoint or GEMINI_ENDPOINT).rstrip("/")
    effective_model = (model or PALETTE_MODEL).removeprefix("google/")
    url = f"{base_endpoint}/{effective_model}:generateContent"
    with acquire_provider_slot({"api_type": "gemini", "endpoint": base_endpoint}):
        response = requests.post(
            url,
            headers={"Content-Type": "application/json", "x-goog-api-key": effective_api_key},
            json={
                "contents": [
                    {
                        "parts": [
                            {"text": prompt},
                            {"inline_data": {"mime_type": mime_type, "data": image_data}},
                        ]
                    }
                ],
                "generationConfig": {"temperature": 0.2},
            },
            timeout=180,
        )
    if response.status_code != 200:
        raise ColorExtractionError(
            f"Palette extraction failed: {response.text[:300]}",
            provider_http_status=response.status_code,
        )
    parts = response.json()["candidates"][0]["content"]["parts"]
    return parts[-1]["text"]


def create_color_from_image(title: str, image: FileStorage) -> dict:
    if not title:
        raise ColorExtractionError("title is required")
    if not image or not image.filename:
        raise ColorExtractionError("image is required")

    mime_type = image.mimetype or mimetypes.guess_type(image.filename)[0] or ""
    if not mime_type.startswith("image/"):
        raise ColorExtractionError("Uploaded file must be an image")

    image_path, size = save_image(image)
    if size == 0:
        raise ColorExtractionError("Uploaded image is empty")

    prompt = PROMPT_PATH.read_text(encoding="utf-8")
    xml = validate_palette_xml(extract_palette_xml(image_path, prompt, mime_type))
    metadata = {
        "original_filename": image.filename,
        "mime_type": mime_type,
        "size_bytes": size,
        "model": PALETTE_MODEL,
    }

    db = dbmod.get_db()
    cur = db.execute(
        """INSERT INTO colors (title, content, source_type, source_image_path, source_metadata)
           VALUES (?, ?, 'image', ?, ?)""",
        (title, xml, str(image_path), json.dumps(metadata, ensure_ascii=False)),
    )
    db.commit()
    color_id = cur.lastrowid
    row = db.execute("SELECT * FROM colors WHERE id = ?", (color_id,)).fetchone()
    db.close()
    return dict(row)


def extract_palette_xml_from_file(
    image_path: Path | str,
    *,
    api_key: str | None = None,
    model: str | None = None,
    endpoint: str | None = None,
) -> str:
    """Extract palette XML from an existing generated image without creating a Color row."""
    path = Path(image_path)
    if not path.exists():
        raise ColorExtractionError("image file does not exist")
    if path.stat().st_size == 0:
        raise ColorExtractionError("image file is empty")
    mime_type = mimetypes.guess_type(path.name)[0] or "image/png"
    if not mime_type.startswith("image/"):
        raise ColorExtractionError("Generated file must be an image")
    prompt = PROMPT_PATH.read_text(encoding="utf-8")
    return validate_palette_xml(
        extract_palette_xml(path, prompt, mime_type, api_key=api_key, model=model, endpoint=endpoint)
    )


def save_image(image: FileStorage) -> tuple[Path, int]:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    filename = secure_filename(image.filename or "image")
    path = UPLOAD_DIR / f"{uuid.uuid4().hex}-{filename}"
    image.save(path)
    return path, path.stat().st_size


def validate_palette_xml(xml: str) -> str:
    stripped = xml.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`").removeprefix("xml").strip()
    if not stripped.startswith("<pptPalette") or not stripped.endswith("</pptPalette>"):
        raise ColorExtractionError("Palette XML must be a single <pptPalette> document")

    try:
        root = ElementTree.fromstring(stripped)
    except ElementTree.ParseError as exc:
        raise ColorExtractionError(f"Palette XML is invalid: {exc}") from exc

    children = list(root)
    if root.tag != "pptPalette" or [child.tag for child in children] != ["textBackground", "accents"]:
        raise ColorExtractionError("Palette XML must contain textBackground and accents")
    for color in list(children[0]) + list(children[1]):
        if color.tag != "color":
            raise ColorExtractionError("Palette XML may only contain color nodes")
        if color.attrib and set(color.attrib) != {"name", "hex", "rgb", "luminance"}:
            raise ColorExtractionError("Each color must include name, hex, rgb, and luminance")
    return stripped
