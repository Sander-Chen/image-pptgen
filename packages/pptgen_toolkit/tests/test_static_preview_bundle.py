from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import zipfile

import pytest


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_ROOT))

from pptgen_toolkit.static_preview_bundle import (  # noqa: E402
    BundlePage,
    looks_like_image_only_gallery,
    manifest_content_identity,
    viewer_has_product_hierarchy,
    viewer_has_required_controls,
    write_static_preview_bundle,
)


def _png(tag: bytes) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + tag + b"IEND"


def test_bundle_binds_ordered_pages_manifest_hashes_and_viewer_local_zip(tmp_path: Path) -> None:
    written = write_static_preview_bundle(
        artifacts_root=tmp_path,
        run_id=91,
        pages=[
            BundlePage(position=2, image_bytes=_png(b"two"), title="Body"),
            BundlePage(position=1, image_bytes=_png(b"one"), title="Cover"),
            BundlePage(position=3, image_bytes=_png(b"three"), title="Close"),
        ],
    )

    assert written.paths.directory == tmp_path / "static-preview-bundles" / "run-91"
    assert written.paths.zip_path == tmp_path / "static-preview-bundles" / "run-91.zip"
    manifest = json.loads(written.paths.manifest_path.read_text(encoding="utf-8"))
    assert manifest["kind"] == "macos-static-preview-bundle"
    assert manifest["manifest_version"] == 1
    assert manifest["run_id"] == 91
    assert [page["position"] for page in manifest["pages"]] == [1, 2, 3]
    assert [page["name"] for page in manifest["pages"]] == [
        "page-001.png",
        "page-002.png",
        "page-003.png",
    ]
    expected = [_png(b"one"), _png(b"two"), _png(b"three")]
    for page, image_bytes in zip(manifest["pages"], expected):
        assert (written.paths.directory / page["path"]).read_bytes() == image_bytes
        assert page["sha256"] == hashlib.sha256(image_bytes).hexdigest()
        assert page["size"] == len(image_bytes)

    archive_bytes = written.paths.zip_path.read_bytes()
    assert (written.paths.directory / written.paths.zip_path.name).read_bytes() == archive_bytes
    assert manifest["zip"]["sha256"] == hashlib.sha256(archive_bytes).hexdigest()
    assert manifest["zip"]["size"] == len(archive_bytes)
    with zipfile.ZipFile(written.paths.zip_path) as archive:
        names = archive.namelist()
        assert written.paths.zip_path.name not in names
        assert archive.read("pages/page-002.png") == _png(b"two")
        archived_manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
        assert "index.html" in names
    assert manifest_content_identity(archived_manifest) == manifest_content_identity(manifest)
    assert archived_manifest["pages"] == manifest["pages"]
    assert archived_manifest["zip"]["name"] == manifest["zip"]["name"]
    assert archived_manifest["zip"]["path"] == manifest["zip"]["path"]
    assert "size" not in archived_manifest["zip"]
    assert "sha256" not in archived_manifest["zip"]

    viewer = written.paths.viewer_path.read_text(encoding="utf-8")
    assert viewer_has_required_controls(viewer)
    assert viewer_has_product_hierarchy(viewer)
    assert not looks_like_image_only_gallery(viewer)
    assert 'href="run-91.zip"' in viewer
    assert "127.0.0.1:3130" not in viewer
    assert "localhost" not in viewer
    assert "addEventListener" in viewer
    assert "requestFullscreen" in viewer
    assert "color-scheme: dark" not in viewer
    assert "Model ·" not in viewer
    assert "Reasoning ·" not in viewer
    assert "<h1>Presentation 91</h1>" in viewer
    assert "Ready to present" in viewer
    assert "3 of 3 slides available" in viewer
    assert "Download presentation package" in viewer
    assert 'class="presentation-preview-thumbnail-title">Cover</span>' in viewer
    assert 'class="presentation-preview-thumbnail-title">Body</span>' in viewer
    assert 'src="pages/page-001.png"' in viewer


def test_bundle_uses_arbitrary_page_counts_and_run_scoped_paths(tmp_path: Path) -> None:
    first = write_static_preview_bundle(
        artifacts_root=tmp_path,
        run_id=91,
        pages=[BundlePage(position=1, image_bytes=_png(b"first"))],
    )
    second = write_static_preview_bundle(
        artifacts_root=tmp_path,
        run_id=92,
        pages=[BundlePage(position=index, image_bytes=_png(bytes([index]))) for index in range(1, 7)],
    )
    assert first.paths.directory != second.paths.directory
    assert first.paths.zip_path != second.paths.zip_path
    assert (first.paths.directory / "pages" / "page-001.png").read_bytes() == _png(b"first")
    assert json.loads(second.paths.manifest_path.read_text(encoding="utf-8"))["page_count"] == 6
    assert (second.paths.directory / "pages" / "page-006.png").is_file()
    assert json.loads(first.paths.manifest_path.read_text(encoding="utf-8"))["run_id"] == 91
    assert json.loads(second.paths.manifest_path.read_text(encoding="utf-8"))["run_id"] == 92


def test_bundle_repeat_write_replaces_same_run_without_recursive_zip_growth(
    tmp_path: Path,
) -> None:
    pages = [
        BundlePage(position=1, image_bytes=_png(b"one"), title="Cover"),
        BundlePage(position=2, image_bytes=_png(b"two"), title="Body"),
    ]
    first = write_static_preview_bundle(artifacts_root=tmp_path, run_id=91, pages=pages)
    first_zip = first.paths.zip_path.read_bytes()
    second = write_static_preview_bundle(
        artifacts_root=tmp_path,
        run_id=91,
        pages=[
            BundlePage(position=1, image_bytes=_png(b"new-one"), title="Cover"),
            BundlePage(position=2, image_bytes=_png(b"new-two"), title="Body"),
        ],
    )
    second_zip = second.paths.zip_path.read_bytes()
    assert first.paths.directory == second.paths.directory
    assert first.paths.zip_path == second.paths.zip_path
    assert first_zip != second_zip
    with zipfile.ZipFile(second.paths.zip_path) as archive:
        names = archive.namelist()
        assert "run-91.zip" not in names
        assert all(not name.endswith(".zip") for name in names)
        assert archive.read("pages/page-001.png") == _png(b"new-one")
        assert names.count("index.html") == 1
        assert names.count("manifest.json") == 1
    assert (second.paths.directory / "pages" / "page-001.png").read_bytes() == _png(b"new-one")
    nested = list(second.paths.directory.rglob("*.zip"))
    assert [path.name for path in nested] == ["run-91.zip"]


def test_bundle_rejects_empty_duplicate_non_png_or_empty_image_pages(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="at least one page"):
        write_static_preview_bundle(artifacts_root=tmp_path, run_id=1, pages=[])
    with pytest.raises(ValueError, match="duplicate page position"):
        write_static_preview_bundle(
            artifacts_root=tmp_path,
            run_id=1,
            pages=[
                BundlePage(position=1, image_bytes=_png(b"one")),
                BundlePage(position=1, image_bytes=_png(b"two")),
            ],
        )
    with pytest.raises(ValueError, match="has no PNG bytes"):
        write_static_preview_bundle(
            artifacts_root=tmp_path,
            run_id=1,
            pages=[BundlePage(position=1, image_bytes=b"")],
        )
    with pytest.raises(ValueError, match="is not a PNG"):
        write_static_preview_bundle(
            artifacts_root=tmp_path,
            run_id=1,
            pages=[BundlePage(position=1, image_bytes=b"not-a-png")],
        )


def test_image_only_gallery_is_not_enough_for_static_preview() -> None:
    gallery = '<html><body><img src="data:image/png;base64,abc"></body></html>'
    assert looks_like_image_only_gallery(gallery)
    assert not viewer_has_required_controls(gallery)
    assert not viewer_has_product_hierarchy(gallery)


def test_optional_presentation_title_is_viewer_only(tmp_path: Path) -> None:
    written = write_static_preview_bundle(
        artifacts_root=tmp_path,
        run_id=91,
        pages=[BundlePage(position=1, image_bytes=_png(b"one"), title="Cover")],
        title="中国海洋论文",
    )
    viewer = written.paths.viewer_path.read_text(encoding="utf-8")
    manifest = json.loads(written.paths.manifest_path.read_text(encoding="utf-8"))
    assert "<h1>中国海洋论文</h1>" in viewer
    assert "title" not in manifest or manifest.get("title") is None
    assert manifest["pages"][0]["title"] == "Cover"
