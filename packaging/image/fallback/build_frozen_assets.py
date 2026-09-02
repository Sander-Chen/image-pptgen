#!/usr/bin/env python3
"""Verify frozen fallback inputs and build deterministic offline bundles."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tarfile
import tempfile
from collections.abc import Iterable
from pathlib import Path, PurePosixPath
from zipfile import ZIP_STORED, ZipFile, ZipInfo


class FreezeError(RuntimeError):
    """A frozen input or deterministic output drifted."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_file(path: Path, *, expected_size: int, expected_sha256: str) -> None:
    if not path.is_file():
        raise FreezeError(f"missing frozen input: {path}")
    observed_size = path.stat().st_size
    if observed_size != expected_size:
        raise FreezeError(f"size drift for {path}: expected {expected_size}, observed {observed_size}")
    observed_sha256 = sha256_file(path)
    if observed_sha256 != expected_sha256:
        raise FreezeError(
            f"sha256 drift for {path}: expected {expected_sha256}, observed {observed_sha256}"
        )


def _validate_member_name(name: str) -> None:
    path = PurePosixPath(name)
    if not name or path.is_absolute() or ".." in path.parts or "\\" in name:
        raise FreezeError(f"unsafe bundle member: {name!r}")


def write_stored_zip(path: Path, members: Iterable[tuple[str, bytes]]) -> None:
    ordered = sorted(members, key=lambda item: item[0])
    names = [name for name, _data in ordered]
    if len(names) != len(set(names)):
        raise FreezeError("duplicate bundle member")
    for name in names:
        _validate_member_name(name)

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with ZipFile(temporary, "w", compression=ZIP_STORED) as archive:
            for name, data in ordered:
                info = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = ZIP_STORED
                info.create_system = 3
                info.external_attr = 0o100644 << 16
                archive.writestr(info, data)
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _runtime_license_member(name: str) -> bool:
    return (
        name == "python/PYTHON.json"
        or name.startswith("python/licenses/")
        or name in {"python/install/LICENSE.txt", "python/install/lib/python3.11/LICENSE.txt"}
    )


_WHEEL_LICENSE = re.compile(r"\.dist-info/(?:license|copying|notice)(?:\.[^/]*)?$", re.IGNORECASE)
_NATIVE_WHEEL_DISTRIBUTIONS = (
    "charset_normalizer",
    "markupsafe",
    "pillow",
)
_SUPPORTED_CPYTHON_TAGS = ("cp311", "cp312")
_WINDOWS_MARKER_WHEELS = ("colorama-0.4.6-py2.py3-none-any.whl",)


def _wheel_license_member(name: str) -> bool:
    lowered = name.lower()
    return "/licenses/" in lowered or bool(_WHEEL_LICENSE.search(name))


def _validate_dual_abi_wheel_inventory(
    platform: dict[str, object], *, platform_label: str, wheel_platform_tag: str
) -> None:
    """Require offline wheels for both fallback CPython and Desktop CPython."""

    wheels = platform.get("wheels")
    if not isinstance(wheels, list):
        raise FreezeError(f"{platform_label} wheel inventory is unavailable")
    filenames = {
        wheel.get("filename")
        for wheel in wheels
        if isinstance(wheel, dict) and isinstance(wheel.get("filename"), str)
    }
    missing = [
        f"{distribution}:{tag}"
        for distribution in _NATIVE_WHEEL_DISTRIBUTIONS
        for tag in _SUPPORTED_CPYTHON_TAGS
        if not any(
            filename.startswith(f"{distribution}-")
            and f"-{tag}-{tag}-{wheel_platform_tag}" in filename
            for filename in filenames
        )
    ]
    if missing:
        raise FreezeError(
            f"{platform_label} frozen wheelhouse must support both fallback CPython 3.11 "
            "and official Runtime CPython 3.12: "
            + ", ".join(missing)
        )


def _validate_macos_dual_abi_wheel_inventory(platform: dict[str, object]) -> None:
    _validate_dual_abi_wheel_inventory(
        platform, platform_label="macOS", wheel_platform_tag="macosx_"
    )


def _validate_windows_dual_abi_wheel_inventory(platform: dict[str, object]) -> None:
    _validate_dual_abi_wheel_inventory(
        platform, platform_label="Windows", wheel_platform_tag="win_amd64.whl"
    )


def _validate_windows_marker_wheel_inventory(platform: dict[str, object]) -> None:
    wheels = platform.get("wheels")
    if not isinstance(wheels, list):
        raise FreezeError("Windows wheel inventory is unavailable")
    filenames = {
        wheel.get("filename")
        for wheel in wheels
        if isinstance(wheel, dict) and isinstance(wheel.get("filename"), str)
    }
    missing = [filename for filename in _WINDOWS_MARKER_WHEELS if filename not in filenames]
    if missing:
        raise FreezeError(
            "Windows marker dependency wheelhouse is missing: " + ", ".join(missing)
        )


def _load_lock(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FreezeError(f"cannot read fallback lock {path}: {exc}") from exc
    if payload.get("schema_version") != 1:
        raise FreezeError("unsupported fallback lock schema")
    return payload


def _verify_windows_runtime_archive_layout(runtime_path: Path, platform: dict[str, object]) -> None:
    """Bind the shipped Windows tar layout, not its PGO metadata layout."""

    layout = platform.get("runtime_archive_layout")
    if not isinstance(layout, dict):
        raise FreezeError("Windows runtime archive layout is unavailable")
    root = layout.get("member_root")
    executable = layout.get("python_exe")
    if not isinstance(root, str) or not isinstance(executable, str):
        raise FreezeError("Windows runtime archive layout is invalid")
    _validate_member_name(root)
    _validate_member_name(executable)
    member_name = f"{root}/{executable}"
    try:
        with tarfile.open(runtime_path, "r:*") as archive:
            try:
                member = archive.getmember(member_name)
            except KeyError as exc:
                raise FreezeError(
                    f"Windows shipped runtime is missing executable member {member_name}"
                ) from exc
            if not member.isfile():
                raise FreezeError(
                    f"Windows shipped runtime executable member is not a file: {member_name}"
                )
    except (OSError, tarfile.TarError) as exc:
        raise FreezeError(f"cannot inspect Windows runtime archive {runtime_path}: {exc}") from exc


def _verified_members(
    *,
    lock: dict[str, object],
    platform: dict[str, object],
    source_dir: Path,
    wheel_dir: Path,
    lock_dir: Path,
) -> tuple[list[tuple[str, bytes]], list[tuple[str, bytes]]]:
    runtime = platform["runtime_asset"]
    metadata = platform["metadata_asset"]
    verify_file(
        source_dir / runtime["filename"],
        expected_size=runtime["bytes"],
        expected_sha256=runtime["sha256"],
    )
    if platform.get("target_triple") == "x86_64-pc-windows-msvc":
        _verify_windows_runtime_archive_layout(source_dir / runtime["filename"], platform)
    metadata_path = source_dir / metadata["filename"]
    verify_file(
        metadata_path,
        expected_size=metadata["bytes"],
        expected_sha256=metadata["sha256"],
    )

    license_members: list[tuple[str, bytes]] = []
    try:
        with tarfile.open(metadata_path, "r:*") as archive:
            for member in sorted(archive.getmembers(), key=lambda item: item.name):
                if not member.isfile() or not _runtime_license_member(member.name):
                    continue
                stream = archive.extractfile(member)
                if stream is None:
                    raise FreezeError(f"cannot read metadata member {member.name}")
                license_members.append((f"runtime/{member.name.removeprefix('python/')}", stream.read()))
    except (OSError, tarfile.TarError) as exc:
        raise FreezeError(f"cannot inspect metadata archive {metadata_path}: {exc}") from exc

    python_json = platform["python_json"]
    python_payload = dict(license_members)["runtime/PYTHON.json"]
    if len(python_payload) != python_json["bytes"] or hashlib.sha256(python_payload).hexdigest() != python_json["sha256"]:
        raise FreezeError("PYTHON.json drift")

    wheel_members: list[tuple[str, bytes]] = []
    for wheel in platform["wheels"]:
        wheel_path = wheel_dir / wheel["filename"]
        verify_file(wheel_path, expected_size=wheel["bytes"], expected_sha256=wheel["sha256"])
        wheel_members.append((f"wheelhouse/{wheel_path.name}", wheel_path.read_bytes()))
        found_license = False
        with ZipFile(wheel_path) as archive:
            for member_name in sorted(archive.namelist()):
                if member_name.endswith("/") or not _wheel_license_member(member_name):
                    continue
                wheel_members_name = f"wheels/{wheel_path.name}/{member_name}"
                license_members.append((wheel_members_name, archive.read(member_name)))
                found_license = True
        if not found_license:
            if not wheel_path.name.startswith("flask_cors-6.0.2-"):
                raise FreezeError(f"wheel has no frozen license source: {wheel_path.name}")
            supplemental = lock["supplemental_licenses"][0]
            supplemental_path = lock_dir / supplemental["repository_path"]
            verify_file(
                supplemental_path,
                expected_size=supplemental["bytes"],
                expected_sha256=supplemental["sha256"],
            )
            license_members.append(
                (f"wheels/{wheel_path.name}/SUPPLEMENTAL-LICENSE.txt", supplemental_path.read_bytes())
            )

    return wheel_members, license_members


def build_platform(
    *,
    lock_path: Path,
    source_dir: Path,
    wheel_dir: Path,
    output_dir: Path,
    platform_name: str,
) -> dict[str, object]:
    lock = _load_lock(lock_path)
    try:
        platform = lock["platforms"][platform_name]
    except KeyError as exc:
        raise FreezeError(f"unsupported frozen platform: {platform_name}") from exc
    if platform_name == "macos-arm64":
        _validate_macos_dual_abi_wheel_inventory(platform)
    elif platform_name == "windows-amd64":
        _validate_windows_dual_abi_wheel_inventory(platform)
        _validate_windows_marker_wheel_inventory(platform)
    wheel_members, license_members = _verified_members(
        lock=lock,
        platform=platform,
        source_dir=source_dir,
        wheel_dir=wheel_dir,
        lock_dir=lock_path.parent,
    )
    outputs = []
    for spec, members in (
        (platform["wheelhouse_bundle"], wheel_members),
        (platform["license_bundle"], license_members),
    ):
        if len(members) != spec.get("count", spec.get("member_count")):
            raise FreezeError(f"member count drift for {spec['filename']}")
        target = output_dir / spec["filename"]
        write_stored_zip(target, members)
        verify_file(target, expected_size=spec["bytes"], expected_sha256=spec["sha256"])
        outputs.append({"filename": target.name, "bytes": target.stat().st_size, "sha256": sha256_file(target)})
    receipt = {"freeze_id": lock["freeze_id"], "platform": platform_name, "status": "verified", "outputs": outputs}
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", type=Path, default=Path(__file__).with_name("fallback-lock.json"))
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--wheel-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--platform", choices=("macos-arm64", "windows-amd64"), required=True)
    args = parser.parse_args()
    try:
        receipt = build_platform(
            lock_path=args.lock.resolve(),
            source_dir=args.source_dir.resolve(),
            wheel_dir=args.wheel_dir.resolve(),
            output_dir=args.output_dir.resolve(),
            platform_name=args.platform,
        )
    except FreezeError as exc:
        parser.exit(2, f"fallback freeze verification failed: {exc}\n")
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
