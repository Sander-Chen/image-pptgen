from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tarfile
from zipfile import ZipFile

import pytest


ROOT = Path(__file__).resolve().parents[1]
FALLBACK_ROOT = ROOT / "packaging" / "image" / "fallback"
LOCK_PATH = FALLBACK_ROOT / "fallback-lock.json"
BUILDER_PATH = FALLBACK_ROOT / "build_frozen_assets.py"
R25_FREEZE_ID = "pbs-20260718-cp311-plus-cp312-v4"

WINDOWS_CP312_WHEELS = {
    "charset_normalizer-3.5.1-cp312-cp312-win_amd64.whl": {
        "bytes": 200551,
        "sha256": "3617ac3cfd8b9888f145ad89dd6e692285834b0201c6074a5eeaad3fd4d668c2",
        "source_url": "https://files.pythonhosted.org/packages/9d/7a/4c6c298171e6b3e745633180ff59350fc0ca0db1ffd28df1e369e0579f71/charset_normalizer-3.5.1-cp312-cp312-win_amd64.whl",
    },
    "markupsafe-3.0.3-cp312-cp312-win_amd64.whl": {
        "bytes": 15105,
        "sha256": "26a5784ded40c9e318cfc2bdb30fe164bdb8665ded9cd64d500a34fb42067b1c",
        "source_url": "https://files.pythonhosted.org/packages/aa/5b/bec5aa9bbbb2c946ca2733ef9c4ca91c91b6a24580193e891b5f7dbe8e1e/markupsafe-3.0.3-cp312-cp312-win_amd64.whl",
    },
    "pillow-12.1.1-cp312-cp312-win_amd64.whl": {
        "bytes": 7033367,
        "sha256": "21329ec8c96c6e979cd0dfd29406c40c1d52521a90544463057d2aaa937d66a6",
        "source_url": "https://files.pythonhosted.org/packages/3d/17/688626d192d7261bbbf98846fc98995726bddc2c945344b65bec3a29d731/pillow-12.1.1-cp312-cp312-win_amd64.whl",
    },
}

WINDOWS_MARKER_WHEEL = {
    "filename": "colorama-0.4.6-py2.py3-none-any.whl",
    "bytes": 25335,
    "sha256": "4f1d9991f5acc0ca119f9d443620b77f9d6b33703e51011c16baf57afb285fc6",
    "source_url": "https://files.pythonhosted.org/packages/d1/d6/3965ed04c63042e047cb6a3e6ed1a63a35087b6a609aa3a15ed8ac56c221/colorama-0.4.6-py2.py3-none-any.whl",
}


def _load_lock() -> dict[str, object]:
    return json.loads(LOCK_PATH.read_text(encoding="utf-8"))


def _load_builder():
    spec = importlib.util.spec_from_file_location("image_fallback_builder", BUILDER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_fallback_lock_freezes_supported_platforms_dependencies_and_policy() -> None:
    lock = _load_lock()

    assert lock["schema_version"] == 1
    assert lock["freeze_id"] == R25_FREEZE_ID
    assert lock["release"]["commit"] == "0e4d9c24b72d28573e622518f09b16aef4a33be8"
    assert lock["release"]["python_version"] == "3.11.15"
    assert lock["policy"] == {
        "fallback_activation": "after_two_approach_different_official_runtime_failures",
        "fallback_user_choice": False,
        "hard_first_download_bytes": 100 * 1024 * 1024,
        "network_during_install": False,
        "official_probe_max_attempts_per_platform": 2,
        "official_runtime_first": True,
        "target_first_download_bytes": 60 * 1024 * 1024,
    }

    wheel_sources = lock["wheel_sources"]
    assert len(wheel_sources) == 16
    assert {item["name"] for item in wheel_sources if item["direct"]} == {
        "flask",
        "flask-cors",
        "pillow",
        "requests",
        "waitress",
    }
    assert "playwright" not in {item["name"] for item in wheel_sources}
    click_source = next(item for item in wheel_sources if item["name"] == "click")
    assert click_source["requires_dist"] == ["colorama; platform_system == \"Windows\""]
    colorama_source = next(item for item in wheel_sources if item["name"] == "colorama")
    assert colorama_source["version"] == "0.4.6"
    assert colorama_source["platform_marker"] == "platform_system == \"Windows\""

    assert set(lock["platforms"]) == {"macos-arm64", "windows-amd64"}
    for platform_id, platform in lock["platforms"].items():
        assert platform["runtime_asset"]["ship_to_user"] is True
        assert platform["metadata_asset"]["ship_to_user"] is False
        expected_wheel_count = 19 if platform_id == "windows-amd64" else 18
        expected_license_count = 40 if platform_id == "windows-amd64" else 39
        assert platform["wheelhouse_bundle"]["count"] == expected_wheel_count
        assert platform["license_bundle"]["member_count"] == expected_license_count
        assert len(platform["wheels"]) == expected_wheel_count
        assert len({wheel["filename"] for wheel in platform["wheels"]}) == expected_wheel_count
        assert all(len(wheel["sha256"]) == 64 and wheel["bytes"] > 0 for wheel in platform["wheels"])

        expected_download = sum(
            (
                platform["runtime_asset"]["bytes"],
                platform["wheelhouse_bundle"]["bytes"],
                platform["license_bundle"]["bytes"],
                lock["application_payload_reference"]["bytes"],
            )
        )
        assert platform["budget"]["first_download_bytes"] == expected_download
        assert expected_download < lock["policy"]["target_first_download_bytes"]
        assert expected_download < lock["policy"]["hard_first_download_bytes"]

    macos_wheels = {
        wheel["filename"] for wheel in lock["platforms"]["macos-arm64"]["wheels"]
    }
    for distribution in ("charset_normalizer", "markupsafe", "pillow"):
        for tag in ("cp311", "cp312"):
            assert any(
                filename.startswith(f"{distribution}-")
                and f"-{tag}-{tag}-macosx_" in filename
                for filename in macos_wheels
            )

    windows_wheels = {
        wheel["filename"]: wheel
        for wheel in lock["platforms"]["windows-amd64"]["wheels"]
    }
    for filename, expected in WINDOWS_CP312_WHEELS.items():
        assert windows_wheels[filename] == {"filename": filename, **expected}
    assert windows_wheels[WINDOWS_MARKER_WHEEL["filename"]] == WINDOWS_MARKER_WHEEL
    for distribution in ("charset_normalizer", "markupsafe", "pillow"):
        for tag in ("cp311", "cp312"):
            assert any(
                filename.startswith(f"{distribution}-")
                and f"-{tag}-{tag}-win_amd64.whl" in filename
                for filename in windows_wheels
            )
    assert lock["platforms"]["windows-amd64"]["runtime_archive_layout"] == {
        "member_root": "python",
        "python_exe": "python.exe",
    }


def test_real_windows_runtime_archive_member_matches_delivery_layout(tmp_path: Path) -> None:
    """Release jobs supply the immutable asset; fixtures cannot close this oracle."""

    archive_value = os.environ.get("PUBLIC_IMAGE_WINDOWS_FALLBACK_ARCHIVE")
    if not archive_value:
        pytest.skip("requires immutable release input PUBLIC_IMAGE_WINDOWS_FALLBACK_ARCHIVE")
    archive_path = Path(archive_value)
    lock = _load_lock()
    platform = lock["platforms"]["windows-amd64"]
    runtime = platform["runtime_asset"]
    layout = platform["runtime_archive_layout"]
    builder = _load_builder()

    builder.verify_file(
        archive_path,
        expected_size=runtime["bytes"],
        expected_sha256=runtime["sha256"],
    )
    builder._verify_windows_runtime_archive_layout(archive_path, platform)
    with tarfile.open(archive_path, "r:gz") as archive:
        member = archive.getmember(f"{layout['member_root']}/{layout['python_exe']}")
        assert member.isfile()
        assert "python/install/python.exe" not in archive.getnames()
        archive.extract(member, tmp_path / "fallback-stage", filter="data")

    extracted_root = (tmp_path / "fallback-stage" / layout["member_root"]).resolve()
    receipt_python = (extracted_root / layout["python_exe"]).resolve()
    assert receipt_python.is_file()
    contract = json.loads(
        (ROOT / "packaging" / "image" / "platform" / "windows" / "contract.json").read_text(
            encoding="utf-8"
        )
    )
    frozen = contract["runtime_selection"]["frozen_inputs"]
    assert frozen["runtime_archive_member_root"] == layout["member_root"]
    assert frozen["runtime_extracted_python"] == layout["python_exe"]
    installer_expected = (extracted_root / frozen["runtime_extracted_python"]).resolve()
    assert receipt_python == installer_expected
    installer = (
        ROOT / "packaging" / "image" / "platform" / "windows" / "install.ps1"
    ).read_text(encoding="utf-8")
    assert "Join-Path $FallbackRoot 'python.exe'" in installer


def test_windows_requirements_lock_preserves_cp311_and_accepts_cp312_hashes() -> None:
    requirements = (ROOT / "packaging" / "image" / "platform" / "windows" / "requirements.lock").read_text(
        encoding="utf-8"
    )
    expected_hashes = {
        "charset-normalizer": WINDOWS_CP312_WHEELS[
            "charset_normalizer-3.5.1-cp312-cp312-win_amd64.whl"
        ]["sha256"],
        "MarkupSafe": WINDOWS_CP312_WHEELS[
            "markupsafe-3.0.3-cp312-cp312-win_amd64.whl"
        ]["sha256"],
        "Pillow": WINDOWS_CP312_WHEELS[
            "pillow-12.1.1-cp312-cp312-win_amd64.whl"
        ]["sha256"],
    }
    for package, cp312_hash in expected_hashes.items():
        line = next(line for line in requirements.splitlines() if line.startswith(f"{package}=="))
        assert "--hash=sha256:" in line
        assert cp312_hash in line
        assert line.count("--hash=sha256:") == 2


def test_supplemental_license_is_source_bound_and_present() -> None:
    lock = _load_lock()
    item = lock["supplemental_licenses"][0]
    path = FALLBACK_ROOT / item["repository_path"]

    assert item["component"] == "flask-cors"
    assert item["source_commit"] == "fa55dcbec68b3524a39e5057c35c29c221a27d64"
    assert path.stat().st_size == item["bytes"]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == item["sha256"]


def test_macos_freeze_rejects_missing_official_runtime_or_fallback_abi_wheels() -> None:
    builder = _load_builder()
    lock = _load_lock()
    platform = json.loads(json.dumps(lock["platforms"]["macos-arm64"]))
    platform["wheels"] = [
        wheel
        for wheel in platform["wheels"]
        if "-cp312-cp312-macosx_" not in wheel["filename"]
    ]

    with pytest.raises(builder.FreezeError, match="official Runtime CPython 3.12"):
        builder._validate_macos_dual_abi_wheel_inventory(platform)


def test_windows_freeze_rejects_missing_official_runtime_or_fallback_abi_wheels() -> None:
    builder = _load_builder()
    lock = _load_lock()
    platform = json.loads(json.dumps(lock["platforms"]["windows-amd64"]))
    platform["wheels"] = [
        wheel
        for wheel in platform["wheels"]
        if "-cp312-cp312-win_amd64.whl" not in wheel["filename"]
    ]

    with pytest.raises(builder.FreezeError, match="official Runtime CPython 3.12"):
        builder._validate_windows_dual_abi_wheel_inventory(platform)


def test_windows_freeze_rejects_missing_marker_dependency_wheel() -> None:
    builder = _load_builder()
    lock = _load_lock()
    platform = json.loads(json.dumps(lock["platforms"]["windows-amd64"]))
    platform["wheels"] = [
        wheel
        for wheel in platform["wheels"]
        if wheel["filename"] != WINDOWS_MARKER_WHEEL["filename"]
    ]

    with pytest.raises(builder.FreezeError, match="marker dependency"):
        builder._validate_windows_marker_wheel_inventory(platform)


def test_stored_zip_writer_is_order_independent_and_has_fixed_metadata(tmp_path: Path) -> None:
    builder = _load_builder()
    first = tmp_path / "first.zip"
    second = tmp_path / "second.zip"
    members = [("b/file.txt", b"second"), ("a/file.txt", b"first")]

    builder.write_stored_zip(first, members)
    builder.write_stored_zip(second, reversed(members))

    assert first.read_bytes() == second.read_bytes()
    with ZipFile(first) as archive:
        assert archive.namelist() == ["a/file.txt", "b/file.txt"]
        assert all(info.date_time == (1980, 1, 1, 0, 0, 0) for info in archive.infolist())
        assert all((info.external_attr >> 16) & 0o777 == 0o644 for info in archive.infolist())


def test_verify_file_rejects_size_or_digest_drift(tmp_path: Path) -> None:
    builder = _load_builder()
    candidate = tmp_path / "candidate.bin"
    candidate.write_bytes(b"frozen")
    digest = hashlib.sha256(b"frozen").hexdigest()

    builder.verify_file(candidate, expected_size=6, expected_sha256=digest)

    for size, sha256 in ((7, digest), (6, "0" * 64)):
        try:
            builder.verify_file(candidate, expected_size=size, expected_sha256=sha256)
        except builder.FreezeError:
            pass
        else:
            raise AssertionError("freeze drift must fail closed")
