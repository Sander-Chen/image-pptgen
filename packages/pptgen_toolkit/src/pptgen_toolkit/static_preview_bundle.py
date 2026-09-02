"""Per-Run offline static preview bundle for completed Image runs."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import html
import json
from pathlib import Path
import shutil
import tempfile
from typing import Any, Sequence
import zipfile


BUNDLE_KIND = "macos-static-preview-bundle"
MANIFEST_VERSION = 1
BUNDLE_ROOT_NAME = "static-preview-bundles"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


@dataclass(frozen=True)
class BundlePage:
    position: int
    image_bytes: bytes
    title: str = ""


@dataclass(frozen=True)
class BundlePaths:
    directory: Path
    viewer_path: Path
    manifest_path: Path
    zip_path: Path


@dataclass(frozen=True)
class WrittenBundle:
    run_id: int
    paths: BundlePaths
    manifest: dict[str, Any]


def bundle_paths(artifacts_root: Path, run_id: int) -> BundlePaths:
    normalized = _require_run_id(run_id)
    directory = (
        Path(artifacts_root).expanduser().resolve()
        / BUNDLE_ROOT_NAME
        / f"run-{normalized}"
    )
    return BundlePaths(
        directory=directory,
        viewer_path=directory / "index.html",
        manifest_path=directory / "manifest.json",
        zip_path=directory.parent / f"run-{normalized}.zip",
    )


def write_static_preview_bundle(
    *,
    artifacts_root: Path,
    run_id: int,
    pages: Sequence[BundlePage],
    title: str | None = None,
) -> WrittenBundle:
    """Write one Run's ordered PNGs, offline viewer, manifest, and sibling ZIP.

    The downloadable archive is intentionally not a member of itself.  A byte-identical
    copy is put next to ``index.html`` so a local ``file:`` viewer can download it
    without escaping the Run directory.
    """

    normalized_run_id = _require_run_id(run_id)
    ordered = _require_pages(pages)
    paths = bundle_paths(artifacts_root, normalized_run_id)
    paths.directory.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=f".run-{normalized_run_id}-", dir=paths.directory.parent)
    )
    archive_temporary = paths.zip_path.with_name(f".{paths.zip_path.name}.tmp")
    try:
        staged_viewer = staging / "index.html"
        staged_manifest = staging / "manifest.json"
        pages_dir = staging / "pages"
        pages_dir.mkdir()

        page_records: list[dict[str, Any]] = []
        for page in ordered:
            name = f"page-{page.position:03d}.png"
            target = pages_dir / name
            target.write_bytes(page.image_bytes)
            page_records.append(
                {
                    "position": page.position,
                    "name": name,
                    "path": f"pages/{name}",
                    "title": page.title,
                    "size": len(page.image_bytes),
                    "sha256": hashlib.sha256(page.image_bytes).hexdigest(),
                }
            )

        staged_viewer.write_text(
            _render_viewer_html(
                run_id=normalized_run_id,
                pages=page_records,
                title=title,
            ),
            encoding="utf-8",
        )
        zip_name = paths.zip_path.name
        # Frozen Run/page identity is written once and archived as-is. ZIP size and
        # digest are added only to the on-disk copy so the archive cannot hash itself.
        content_identity: dict[str, Any] = {
            "kind": BUNDLE_KIND,
            "manifest_version": MANIFEST_VERSION,
            "run_id": normalized_run_id,
            "page_count": len(page_records),
            "pages": page_records,
            "viewer": {"name": "index.html"},
            "zip": {"name": zip_name, "path": zip_name},
        }
        _write_json(staged_manifest, content_identity)

        _write_zip(archive_temporary, directory=staging)
        archive_bytes = archive_temporary.read_bytes()
        # Keep this copy outside the archive construction so the ZIP never contains
        # itself and cannot recursively grow on repeat result calls.
        (staging / zip_name).write_bytes(archive_bytes)
        manifest = json.loads(json.dumps(content_identity, ensure_ascii=False))
        manifest["zip"] = {
            "name": zip_name,
            "path": zip_name,
            "size": len(archive_bytes),
            "sha256": hashlib.sha256(archive_bytes).hexdigest(),
        }
        _write_json(staged_manifest, manifest)

        if paths.directory.exists():
            shutil.rmtree(paths.directory)
        staging.replace(paths.directory)
        if paths.zip_path.exists():
            paths.zip_path.unlink()
        archive_temporary.replace(paths.zip_path)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        archive_temporary.unlink(missing_ok=True)
        raise

    return WrittenBundle(run_id=normalized_run_id, paths=paths, manifest=manifest)


def manifest_content_identity(manifest: dict[str, Any]) -> dict[str, Any]:
    """Compare archive and on-disk manifests without ZIP byte-integrity fields.

    Size and sha256 describe the sibling ZIP file. They cannot be stored inside
    that ZIP without hashing a document that contains its own digest.
    """

    identity = json.loads(json.dumps(manifest, ensure_ascii=False))
    zip_meta = dict(identity.get("zip") or {})
    zip_meta.pop("size", None)
    zip_meta.pop("sha256", None)
    identity["zip"] = zip_meta
    return identity


def viewer_has_required_controls(document: str) -> bool:
    required = (
        'aria-label="Previous slide"',
        'aria-label="Next slide"',
        'aria-label="Select slide ',
        'aria-label="Direct page selection"',
        'aria-label="Zoom out"',
        'aria-label="Zoom in"',
        'aria-label="Fit to window"',
        'aria-label="Fullscreen preview"',
        'id="download-zip"',
        "requestFullscreen",
    )
    return all(token in document for token in required)


def viewer_has_product_hierarchy(document: str) -> bool:
    required = (
        "color-scheme: light",
        "presentation-preview-page",
        "presentation-preview-hero",
        "presentation-preview-stage",
        "presentation-preview-summary",
        "Ready to present",
        "Download presentation package",
        "Presentation story (",
        "presentation-preview-rail",
        "presentation-preview-thumbnail",
        "presentation-preview-thumbnail-title",
        "is-selected",
    )
    return all(token in document for token in required)


def looks_like_image_only_gallery(document: str) -> bool:
    """Detect R58's insufficient image-only standalone gallery."""

    has_image = "data:image/png;base64," in document or "<img" in document
    return has_image and not viewer_has_required_controls(document)


def _write_zip(destination: Path, *, directory: Path) -> None:
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        for member in sorted(directory.rglob("*")):
            if member.is_file() and member.suffix != ".zip":
                archive.write(member, member.relative_to(directory).as_posix())


def _require_run_id(run_id: int) -> int:
    try:
        normalized = int(run_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("static preview bundle requires a numeric run_id") from exc
    if normalized <= 0:
        raise ValueError("static preview bundle requires a positive run_id")
    return normalized


def _require_pages(pages: Sequence[BundlePage]) -> list[BundlePage]:
    if not pages:
        raise ValueError("static preview bundle requires at least one page")
    ordered = sorted(pages, key=lambda page: int(page.position))
    seen: set[int] = set()
    for page in ordered:
        position = int(page.position)
        if position <= 0:
            raise ValueError("static preview bundle page positions must be positive")
        if position in seen:
            raise ValueError(f"static preview bundle has duplicate page position {position}")
        if not page.image_bytes:
            raise ValueError(f"static preview bundle page {position} has no PNG bytes")
        if not page.image_bytes.startswith(PNG_SIGNATURE):
            raise ValueError(f"static preview bundle page {position} is not a PNG")
        seen.add(position)
    return ordered


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _slide_title(page: dict[str, Any]) -> str:
    title = str(page.get("title") or "").strip()
    return title or f"Slide {page['position']}"


def _thumbnail_markup(index: int, page: dict[str, Any]) -> str:
    position = int(page["position"])
    title = _slide_title(page)
    selected = " is-selected" if index == 0 else ""
    pressed = "true" if index == 0 else "false"
    src = html.escape(str(page["path"]), quote=True)
    safe_title = html.escape(title)
    label = html.escape(f"Select slide {position} {title}", quote=True)
    return (
        f'<button type="button" class="presentation-preview-thumbnail{selected}" '
        f'data-index="{index}" aria-pressed="{pressed}" aria-label="{label}">'
        f'<span class="presentation-preview-thumbnail-art">'
        f'<img src="{src}" alt="">'
        f'<span class="presentation-preview-thumbnail-number">{position}</span>'
        f"</span>"
        f'<span class="presentation-preview-thumbnail-title">{safe_title}</span>'
        f"</button>"
    )


def _render_viewer_html(
    *,
    run_id: int,
    pages: Sequence[dict[str, Any]],
    title: str | None = None,
) -> str:
    page_count = len(pages)
    presentation_title = (title or "").strip() or f"Presentation {run_id}"
    first = pages[0]
    first_title = _slide_title(first)
    zip_name = f"run-{run_id}.zip"
    page_list = json.dumps(
        [
            {"position": page["position"], "src": page["path"], "title": _slide_title(page)}
            for page in pages
        ],
        ensure_ascii=False,
    ).replace("<", "\\u003c")
    thumbnails = "\n        ".join(
        _thumbnail_markup(index, page) for index, page in enumerate(pages)
    )
    replacements = {
        "%%DOC_TITLE%%": html.escape(presentation_title),
        "%%PRESENTATION_TITLE%%": html.escape(presentation_title),
        "%%PAGE_COUNT%%": str(page_count),
        "%%STATUS_DESCRIPTION%%": html.escape(f"{page_count} of {page_count} slides available"),
        "%%ZIP_NAME%%": html.escape(zip_name, quote=True),
        "%%FIRST_SRC%%": html.escape(str(first["path"]), quote=True),
        "%%FIRST_ALT%%": html.escape(f"Slide {first['position']}: {first_title}", quote=True),
        "%%COUNTER%%": f"1 / {page_count}",
        "%%GOTO_MAX%%": str(page_count),
        "%%GOTO_VALUE%%": str(int(first["position"])),
        "%%THUMBNAILS%%": thumbnails,
        "%%PAGE_LIST%%": page_list,
    }
    rendered = _VIEWER_HTML
    for token, value in replacements.items():
        rendered = rendered.replace(token, value)
    return rendered


_VIEWER_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light">
  <title>%%DOC_TITLE%%</title>
  <style>
    :root { color-scheme: light; }
    * { box-sizing: border-box; }
    html, body { margin: 0; min-height: 100%; }
    body {
      font-family: "Segoe UI", "PingFang SC", "Noto Sans CJK SC", "Noto Sans SC", sans-serif;
      background: #f7f7f4;
      color: #202733;
    }
    button, a, input { font: inherit; }
    .presentation-preview-page { min-height: 100vh; overflow-x: hidden; background: #f7f7f4; color: #202733; }
    .presentation-preview-content {
      width: min(100%, 1500px);
      margin: 0 auto;
      padding: 58px 68px 46px;
    }
    .presentation-preview-hero {
      display: grid;
      grid-template-columns: minmax(0, 2.18fr) minmax(320px, 0.82fr);
      gap: 48px;
      align-items: center;
    }
    .presentation-preview-stage {
      position: relative;
      overflow: hidden;
      aspect-ratio: 16 / 9;
      border: 1px solid rgba(22, 38, 57, 0.12);
      border-radius: 11px;
      background: #e9ecef;
      box-shadow: 0 18px 38px rgba(23, 34, 48, 0.14), 0 3px 8px rgba(23, 34, 48, 0.08);
    }
    .presentation-preview-stage img,
    .presentation-preview-fullscreen-stage img {
      display: block;
      width: 100%;
      height: 100%;
      object-fit: contain;
      background: #eef0f2;
      transform-origin: center center;
    }
    .presentation-preview-stage.fit img { transform: none; }
    .presentation-preview-summary { min-width: 0; padding: 18px 0; }
    .presentation-preview-status {
      display: inline-flex;
      gap: 11px;
      align-items: center;
      margin-bottom: 24px;
      color: #15974e;
      font-size: 14px;
      font-weight: 750;
      letter-spacing: 0.055em;
      text-transform: uppercase;
    }
    .presentation-preview-status svg { width: 22px; height: 22px; }
    .presentation-preview-summary h1 {
      max-width: 12ch;
      margin: 0 0 26px;
      color: #202733;
      font-size: clamp(34px, 3.1vw, 50px);
      font-weight: 760;
      line-height: 1.13;
      letter-spacing: -0.035em;
    }
    .presentation-preview-facts {
      display: grid;
      gap: 17px;
      margin-bottom: 22px;
      color: #343c47;
      font-size: 16px;
    }
    .presentation-preview-facts > div {
      display: grid;
      grid-template-columns: 24px minmax(0, 1fr);
      gap: 12px;
      align-items: center;
    }
    .presentation-preview-facts svg { width: 21px; height: 21px; color: #15974e; }
    .presentation-preview-actions { display: grid; gap: 12px; }
    .btn {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
      width: 100%;
      min-height: 52px;
      padding: 0 16px;
      border-radius: 9px;
      font-weight: 650;
      text-decoration: none;
      cursor: pointer;
    }
    .btn svg { width: 18px; height: 18px; }
    .btn-primary { border: 0; background: #1769ed; color: #ffffff; box-shadow: none; }
    .btn-secondary { border: 1px solid #d9d9d9; background: #ffffff; color: #202733; }
    .presentation-preview-controls {
      display: flex;
      gap: 24px;
      align-items: center;
      justify-content: center;
      width: calc((100% - 48px) * 0.727);
      min-height: 76px;
    }
    .presentation-preview-controls strong,
    .presentation-preview-fullscreen-controls strong {
      min-width: 58px;
      color: #323a46;
      text-align: center;
      font-variant-numeric: tabular-nums;
    }
    .nav-btn {
      width: 42px;
      height: 42px;
      border: 1px solid #e1e4e8;
      border-radius: 50%;
      background: #ffffff;
      color: #202733;
      box-shadow: 0 3px 9px rgba(24, 35, 49, 0.07);
      cursor: pointer;
    }
    .nav-btn:disabled { opacity: 0.45; cursor: not-allowed; }
    .presentation-preview-tools {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      align-items: center;
      justify-content: center;
      width: calc((100% - 48px) * 0.727);
      margin: 0 0 18px;
      color: #68717d;
      font-size: 13px;
    }
    .presentation-preview-tools button,
    .presentation-preview-tools input {
      min-height: 32px;
      padding: 0 10px;
      border: 1px solid #e1e4e8;
      border-radius: 8px;
      background: #ffffff;
      color: #323a46;
    }
    .presentation-preview-story { padding-top: 25px; border-top: 1px solid #dfe2e5; }
    .presentation-preview-story h2 {
      margin: 0 0 22px;
      color: #2a313b;
      font-size: 17px;
      font-weight: 650;
    }
    .presentation-preview-rail {
      display: grid;
      grid-auto-columns: minmax(180px, 1fr);
      grid-auto-flow: column;
      gap: 16px;
      overflow-x: auto;
      padding: 2px 2px 16px;
      scrollbar-width: thin;
      scroll-snap-type: x proximity;
    }
    .presentation-preview-thumbnail {
      min-width: 0;
      padding: 0;
      border: 0;
      outline-offset: 4px;
      background: transparent;
      color: #2d3540;
      text-align: left;
      cursor: pointer;
      scroll-snap-align: start;
    }
    .presentation-preview-thumbnail-art {
      position: relative;
      display: block;
      overflow: hidden;
      aspect-ratio: 16 / 9;
      border: 2px solid transparent;
      border-radius: 9px;
      background: #e8ebee;
      box-shadow: 0 4px 12px rgba(23, 34, 48, 0.08);
    }
    .presentation-preview-thumbnail:hover .presentation-preview-thumbnail-art { border-color: #90b6f5; }
    .presentation-preview-thumbnail.is-selected .presentation-preview-thumbnail-art {
      border-color: #1769ed;
      box-shadow: 0 0 0 2px rgba(23, 105, 237, 0.12), 0 5px 14px rgba(23, 34, 48, 0.1);
    }
    .presentation-preview-thumbnail-art img {
      display: block;
      width: 100%;
      height: 100%;
      object-fit: cover;
    }
    .presentation-preview-thumbnail-number {
      position: absolute;
      bottom: -1px;
      left: -1px;
      display: grid;
      width: 26px;
      height: 28px;
      place-items: center;
      border-radius: 0 7px 0 0;
      background: #506789;
      color: #ffffff;
      font-size: 13px;
      font-weight: 700;
    }
    .presentation-preview-thumbnail.is-selected .presentation-preview-thumbnail-number { background: #1769ed; }
    .presentation-preview-thumbnail-title {
      display: -webkit-box;
      min-height: 42px;
      margin-top: 10px;
      overflow: hidden;
      -webkit-box-orient: vertical;
      -webkit-line-clamp: 2;
      font-size: 14px;
      line-height: 1.45;
    }
    .presentation-preview-fullscreen {
      display: none;
      position: fixed;
      inset: 0;
      z-index: 40;
      flex-direction: column;
      padding: 18px 24px 20px;
      background: #121820;
      color: #f5f7fa;
    }
    .presentation-preview-fullscreen.is-open,
    .presentation-preview-fullscreen:fullscreen { display: flex; width: 100%; height: 100%; }
    .presentation-preview-fullscreen-bar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 12px;
      color: #f5f7fa;
      font-weight: 650;
    }
    .presentation-preview-fullscreen-stage {
      overflow: hidden;
      width: min(100%, calc((100vh - 130px) * 16 / 9));
      margin: auto;
      aspect-ratio: 16 / 9;
      border-radius: 8px;
      background: #202833;
      box-shadow: 0 18px 50px rgba(0, 0, 0, 0.36);
      touch-action: pan-y;
      user-select: none;
    }
    .presentation-preview-fullscreen-controls {
      display: flex;
      gap: 22px;
      align-items: center;
      justify-content: center;
      padding-top: 14px;
    }
    .presentation-preview-fullscreen-controls strong { color: #f5f7fa; }
    .presentation-preview-fullscreen .nav-btn {
      background: #1c2530;
      border-color: #314050;
      color: #f5f7fa;
    }
    .fs-close {
      width: 36px;
      height: 36px;
      border: 0;
      background: transparent;
      color: #f5f7fa;
      font-size: 24px;
      cursor: pointer;
    }
    @media (max-width: 1100px) {
      .presentation-preview-content { padding: 38px 36px; }
      .presentation-preview-hero {
        grid-template-columns: minmax(0, 1.75fr) minmax(290px, 0.75fr);
        gap: 32px;
      }
      .presentation-preview-summary h1 { font-size: 35px; }
      .presentation-preview-controls,
      .presentation-preview-tools { width: calc((100% - 32px) * 0.7); }
    }
    @media (max-width: 900px) {
      .presentation-preview-content { padding: 30px 28px 38px; }
      .presentation-preview-hero { grid-template-columns: minmax(0, 1fr); gap: 24px; }
      .presentation-preview-summary { padding: 4px 0 0; }
      .presentation-preview-summary h1 { max-width: none; }
      .presentation-preview-actions { grid-template-columns: 1fr 1fr; }
      .presentation-preview-controls,
      .presentation-preview-tools { width: 100%; }
    }
    @media (max-width: 600px) {
      .presentation-preview-content { padding: 18px 16px 30px; }
      .presentation-preview-hero { gap: 20px; }
      .presentation-preview-stage { border-radius: 7px; }
      .presentation-preview-status { margin-bottom: 15px; font-size: 12px; }
      .presentation-preview-summary h1 { margin-bottom: 18px; font-size: 31px; }
      .presentation-preview-facts { gap: 12px; margin-bottom: 17px; font-size: 14px; }
      .presentation-preview-actions { grid-template-columns: 1fr; }
      .presentation-preview-controls { min-height: 68px; }
      .presentation-preview-story { padding-top: 20px; }
      .presentation-preview-rail { grid-auto-columns: 72%; }
    }
  </style>
</head>
<body>
  <main class="presentation-preview-page">
    <div class="presentation-preview-content">
      <section class="presentation-preview-hero" aria-label="Presentation preview">
        <div class="presentation-preview-stage" id="stage">
          <img id="page" alt="%%FIRST_ALT%%" src="%%FIRST_SRC%%">
        </div>
        <aside class="presentation-preview-summary">
          <div class="presentation-preview-status is-success">
            <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="10" fill="currentColor"/><path d="M7.8 12.4l2.6 2.6 5.8-6.4" fill="none" stroke="#fff" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>
            <span>Ready to present</span>
          </div>
          <h1>%%PRESENTATION_TITLE%%</h1>
          <div class="presentation-preview-facts">
            <div>
              <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="10" fill="currentColor"/><path d="M7.8 12.4l2.6 2.6 5.8-6.4" fill="none" stroke="#fff" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>
              <span>%%STATUS_DESCRIPTION%%</span>
            </div>
          </div>
          <div class="presentation-preview-actions">
            <a id="download-zip" class="btn btn-primary" href="%%ZIP_NAME%%" download="%%ZIP_NAME%%" aria-label="Download presentation package">
              <svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M12 3v10.2l3.2-3.2 1.4 1.4L12 16.4 7.4 11.8l1.4-1.4 3.2 3.2V3h2zM5 19h14v2H5z"/></svg>
              Download presentation package
            </a>
            <button type="button" id="fullscreen" class="btn btn-secondary" aria-label="Fullscreen preview">
              <svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M4 4h7v2H6v5H4V4zm9 0h7v7h-2V6h-5V4zM4 13h2v5h5v2H4v-7zm14 0h2v7h-7v-2h5v-5z"/></svg>
              Fullscreen preview
            </button>
          </div>
        </aside>
      </section>
      <nav class="presentation-preview-controls" aria-label="Slide navigation">
        <button type="button" class="nav-btn" id="prev" aria-label="Previous slide">
          <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path fill="currentColor" d="M15.5 5.5L9 12l6.5 6.5-1.4 1.4L6.2 12l7.9-7.9z"/></svg>
        </button>
        <strong id="counter" aria-live="polite">%%COUNTER%%</strong>
        <button type="button" class="nav-btn" id="next" aria-label="Next slide">
          <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path fill="currentColor" d="M8.5 5.5L16 12l-7.5 6.5 1.4 1.4 8.9-7.9-8.9-7.9z"/></svg>
        </button>
      </nav>
      <div class="presentation-preview-tools">
        <button type="button" id="zoom-out" aria-label="Zoom out">Zoom out</button>
        <button type="button" id="zoom-in" aria-label="Zoom in">Zoom in</button>
        <button type="button" id="fit" aria-label="Fit to window">Fit</button>
        <label>Go to page
          <input id="goto" type="number" min="1" max="%%GOTO_MAX%%" value="%%GOTO_VALUE%%" aria-label="Direct page selection">
        </label>
      </div>
      <section class="presentation-preview-story" aria-labelledby="presentation-story-title">
        <h2 id="presentation-story-title">Presentation story (%%PAGE_COUNT%% slides)</h2>
        <div class="presentation-preview-rail" role="list">
        %%THUMBNAILS%%
        </div>
      </section>
    </div>
  </main>
  <div class="presentation-preview-fullscreen" id="overlay">
    <div class="presentation-preview-fullscreen-bar">
      <span>Presentation fullscreen preview</span>
      <button type="button" class="fs-close" id="fs-close" aria-label="Close fullscreen">&times;</button>
    </div>
    <div class="presentation-preview-fullscreen-stage" id="fs-stage">
      <img id="fs-page" alt="%%FIRST_ALT%%" src="%%FIRST_SRC%%">
    </div>
    <div class="presentation-preview-fullscreen-controls">
      <button type="button" class="nav-btn" id="fs-prev" aria-label="Previous fullscreen slide">
        <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path fill="currentColor" d="M15.5 5.5L9 12l6.5 6.5-1.4 1.4L6.2 12l7.9-7.9z"/></svg>
      </button>
      <strong id="fs-counter" aria-live="polite">%%COUNTER%%</strong>
      <button type="button" class="nav-btn" id="fs-next" aria-label="Next fullscreen slide">
        <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path fill="currentColor" d="M8.5 5.5L16 12l-7.5 6.5 1.4 1.4 8.9-7.9-8.9-7.9z"/></svg>
      </button>
    </div>
  </div>
  <script>
    const PAGES = %%PAGE_LIST%%;
    const pageImage = document.getElementById("page");
    const fsPage = document.getElementById("fs-page");
    const counter = document.getElementById("counter");
    const fsCounter = document.getElementById("fs-counter");
    const prev = document.getElementById("prev");
    const next = document.getElementById("next");
    const fsPrev = document.getElementById("fs-prev");
    const fsNext = document.getElementById("fs-next");
    const gotoInput = document.getElementById("goto");
    const stage = document.getElementById("stage");
    const overlay = document.getElementById("overlay");
    const fsStage = document.getElementById("fs-stage");
    let index = 0;
    let scale = 1;
    let fit = false;
    let swipe = null;
    function show(nextIndex) {
      index = Math.max(0, Math.min(PAGES.length - 1, nextIndex));
      const page = PAGES[index];
      const label = "Slide " + page.position + ": " + (page.title || ("Slide " + page.position));
      pageImage.src = page.src;
      pageImage.alt = label;
      fsPage.src = page.src;
      fsPage.alt = label;
      const count = (index + 1) + " / " + PAGES.length;
      counter.textContent = count;
      fsCounter.textContent = count;
      gotoInput.value = String(page.position);
      prev.disabled = index === 0;
      next.disabled = index === PAGES.length - 1;
      fsPrev.disabled = index === 0;
      fsNext.disabled = index === PAGES.length - 1;
      document.querySelectorAll(".presentation-preview-thumbnail").forEach((button, thumbIndex) => {
        const selected = thumbIndex === index;
        button.classList.toggle("is-selected", selected);
        button.setAttribute("aria-pressed", selected ? "true" : "false");
      });
    }
    function applyZoom() {
      stage.classList.toggle("fit", fit);
      pageImage.style.transform = fit ? "none" : "scale(" + scale + ")";
    }
    function openFullscreen() {
      overlay.classList.add("is-open");
      const request = overlay.requestFullscreen || overlay.webkitRequestFullscreen;
      if (request) {
        try {
          const result = request.call(overlay);
          if (result && result.catch) result.catch(function () {});
        } catch (error) {}
      }
    }
    function closeFullscreen() {
      overlay.classList.remove("is-open");
      if (document.fullscreenElement && document.exitFullscreen) document.exitFullscreen();
    }
    prev.addEventListener("click", () => show(index - 1));
    next.addEventListener("click", () => show(index + 1));
    fsPrev.addEventListener("click", () => show(index - 1));
    fsNext.addEventListener("click", () => show(index + 1));
    gotoInput.addEventListener("change", () => {
      const position = Number(gotoInput.value);
      const found = PAGES.findIndex((page) => page.position === position);
      show(found < 0 ? index : found);
    });
    document.querySelectorAll(".presentation-preview-thumbnail").forEach((button) => {
      button.addEventListener("click", () => show(Number(button.dataset.index)));
    });
    document.getElementById("zoom-in").addEventListener("click", () => {
      fit = false; scale = Math.min(4, scale + 0.25); applyZoom();
    });
    document.getElementById("zoom-out").addEventListener("click", () => {
      fit = false; scale = Math.max(0.25, scale - 0.25); applyZoom();
    });
    document.getElementById("fit").addEventListener("click", () => {
      fit = true; scale = 1; applyZoom();
    });
    document.getElementById("fullscreen").addEventListener("click", openFullscreen);
    document.getElementById("fs-close").addEventListener("click", closeFullscreen);
    document.addEventListener("fullscreenchange", () => {
      if (!document.fullscreenElement) overlay.classList.remove("is-open");
    });
    document.addEventListener("keydown", (event) => {
      if (event.target === gotoInput) return;
      if (event.key === "ArrowLeft") { event.preventDefault(); show(index - 1); }
      if (event.key === "ArrowRight") { event.preventDefault(); show(index + 1); }
      if (event.key === "Escape") closeFullscreen();
    });
    fsStage.addEventListener("pointerdown", (event) => {
      if (event.button !== 0) return;
      swipe = { pointerId: event.pointerId, x: event.clientX };
      fsStage.setPointerCapture(event.pointerId);
    });
    fsStage.addEventListener("pointerup", (event) => {
      if (!swipe || swipe.pointerId !== event.pointerId) return;
      const distance = event.clientX - swipe.x;
      swipe = null;
      if (Math.abs(distance) < 48) return;
      show(distance < 0 ? index + 1 : index - 1);
    });
    fsStage.addEventListener("pointercancel", () => { swipe = null; });
    show(0);
  </script>
</body>
</html>
"""
