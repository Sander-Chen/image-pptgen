#!/usr/bin/env python3
"""Build the additive Linux Image PPTGen distribution.

The HTML release builder is intentionally left untouched.  This module owns a
separate archive, installer, runtime launcher, Skill, and user namespace for
the fixed Public Image PPT 3.0 workflow.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
from pathlib import Path, PurePosixPath


IMAGE_PRODUCT = "image-pptgen"
IMAGE_SERVICE = "image-pptgen-server"
IMAGE_SKILL = "generate-image-presentation"
IMAGE_COMMAND = "image-pptgen"
IMAGE_SERVICE_COMMAND = "image-pptgen-server"
IMAGE_BASE_URL = "http://127.0.0.1:3130"
IMAGE_DATA_ROOT = "~/.local/share/image-pptgen"
IMAGE_CONFIG_ROOT = "~/.config/image-pptgen"
PLATFORM = "linux-x86_64"
PAGES_FILE_LIMIT = 25 * 1024 * 1024


class BuildResult:
    def __init__(
        self,
        *,
        archive_path: Path,
        bootstrap_path: Path,
        manifest_path: Path,
        checksums_path: Path,
        docs_dir: Path,
        dist_dir: Path,
        reports_dir: Path,
    ) -> None:
        self.archive_path = archive_path
        self.bootstrap_path = bootstrap_path
        self.manifest_path = manifest_path
        self.checksums_path = checksums_path
        self.docs_dir = docs_dir
        self.dist_dir = dist_dir
        self.reports_dir = reports_dir


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_tree(path: Path) -> str:
    digest = hashlib.sha256()
    for file_path in sorted(item for item in path.rglob("*") if item.is_file()):
        relative = file_path.relative_to(path).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(bytes.fromhex(sha256_file(file_path)))
    return digest.hexdigest()


def _source_commit(repo_root: Path) -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        check=True,
        text=True,
        capture_output=True,
    )
    return completed.stdout.strip()


def _copy_file(source: Path, target: Path) -> None:
    if source.is_symlink() or not source.is_file():
        raise FileNotFoundError(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)


def _assert_regular_source_tree(source: Path) -> None:
    if source.is_symlink() or not source.is_dir():
        raise FileNotFoundError(source)
    for path in sorted(source.rglob("*")):
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode) or not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            raise ValueError(f"release source contains an unsafe filesystem entry: {path}")


def _copy_python_tree(source: Path, target: Path) -> None:
    _assert_regular_source_tree(source)
    for path in sorted(source.rglob("*.py")):
        if "__pycache__" in path.parts or ".egg-info" in path.parts:
            continue
        _copy_file(path, target / path.relative_to(source))


def _copy_tree(source: Path, target: Path) -> None:
    _assert_regular_source_tree(source)
    for path in sorted(source.rglob("*")):
        if (
            not path.is_file()
            or "__pycache__" in path.parts
            or ".egg-info" in path.parts
            or path.suffix in {".pyc", ".pyo"}
        ):
            continue
        _copy_file(path, target / path.relative_to(source))


def _ensure_skill_dispatcher_executable(app_root: Path) -> None:
    """Require and mark the POSIX Skill dispatcher executable.

    ``shutil.copyfile`` deliberately copies content only, so the Skill tree's
    executable bit is otherwise lost in the release staging directory.  This
    is intentionally scoped to the one POSIX dispatcher; changing the generic
    copy helper or applying a recursive chmod would alter unrelated payload
    files.  Resolve the destination with ``lstat`` so missing, non-regular, or
    symlinked entries fail closed before an archive is created.
    """

    dispatcher = app_root / "skills" / IMAGE_SKILL / "scripts" / "image-pptgen-dispatch"
    try:
        mode = dispatcher.lstat().st_mode
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"required Skill dispatcher is missing: {dispatcher}") from exc
    if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
        raise ValueError(f"required Skill dispatcher is not a regular file: {dispatcher}")
    dispatcher.chmod(0o755)


def _image_pyproject() -> str:
    return """[build-system]
requires = [\"setuptools>=69\"]
build-backend = \"setuptools.build_meta\"

[project]
name = \"image-pptgen-toolkit\"
version = \"0.1.0\"
description = \"Thin CLI control plane for Public Image PPT 3.0\"
requires-python = \">=3.11\"

[project.scripts]
image-pptgen = \"pptgen_toolkit.image_cli:main\"

[tool.setuptools.packages.find]
where = [\"src\"]
"""


def _populate_runtime(repo_root: Path, app_root: Path) -> None:
    # The shared Flask core is needed by the public Image boundary, but no
    # frontend source, HTML prompts, HTML Skill, or HTML wrapper is included.
    for name in ("server.py", "public_server.py", "db.py", "pipeline.py", "config.py", "splitter.py"):
        _copy_file(repo_root / name, app_root / name)
    _copy_python_tree(repo_root / "backend", app_root / "backend")
    _copy_tree(repo_root / "frontend" / "dist", app_root / "frontend" / "dist")

    example_target = app_root / "example"
    _copy_tree(repo_root / "example" / "image_ppt_input", example_target / "image_ppt_input")
    for name in ("自动切分.md", "自动切分-编辑重构.md", "提取配图颜色.md"):
        _copy_file(repo_root / "example" / name, example_target / name)

    toolkit = repo_root / "packages" / "pptgen_toolkit"
    package_target = app_root / "packages" / "pptgen_toolkit"
    (package_target / "pyproject.toml").parent.mkdir(parents=True, exist_ok=True)
    (package_target / "pyproject.toml").write_text(_image_pyproject(), encoding="utf-8")
    _copy_python_tree(toolkit / "src", package_target / "src")

    skill_source = repo_root / "skills" / IMAGE_SKILL
    if not skill_source.is_dir():
        raise FileNotFoundError(f"current Image Skill is unavailable: {skill_source}")
    _copy_tree(skill_source, app_root / "skills" / IMAGE_SKILL)
    _ensure_skill_dispatcher_executable(app_root)

    image_packaging = repo_root / "packaging" / "image"
    for name in (
        "launcher.py",
        "runtime_manager.py",
        "image-pptgen-wrapper.sh",
        "image-pptgen-server-wrapper.sh",
        "requirements.txt",
    ):
        _copy_file(image_packaging / name, app_root / name.replace("launcher.py", "image-launcher.py"))
    for executable in (
        app_root / "image-launcher.py",
        app_root / "image-pptgen-wrapper.sh",
        app_root / "image-pptgen-server-wrapper.sh",
    ):
        executable.chmod(0o755)


def _release_identity(repo_root: Path, app_root: Path, *, version: str) -> dict[str, object]:
    identity: dict[str, object] = {
        "schema_version": 1,
        "product": IMAGE_PRODUCT,
        "service": IMAGE_SERVICE,
        "surface": "public_image_3_0",
        "version": version,
        "platform": PLATFORM,
        "command": IMAGE_COMMAND,
        "service_command": IMAGE_SERVICE_COMMAND,
        "skill": IMAGE_SKILL,
        "base_url": IMAGE_BASE_URL,
        "data_root": IMAGE_DATA_ROOT,
        "config_root": IMAGE_CONFIG_ROOT,
        "source_commit": _source_commit(repo_root),
        "skill_sha256": sha256_tree(app_root / "skills" / IMAGE_SKILL),
        "runtime_content_sha256": sha256_tree(app_root),
    }
    canonical = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    identity["build_id"] = hashlib.sha256(canonical).hexdigest()
    return identity


def validate_archive(path: Path) -> None:
    """Reject traversal, duplicate, link, and special archive members."""
    with tarfile.open(path, "r:gz") as handle:
        seen: set[str] = set()
        for member in handle.getmembers():
            name = member.name
            relative = PurePosixPath(name)
            if (
                not name
                or "\x00" in name
                or relative.is_absolute()
                or ".." in relative.parts
                or name in seen
                or not (member.isfile() or member.isdir())
            ):
                raise ValueError(f"unsafe archive member: {name}")
            seen.add(name)


def _tar_filter(info: tarfile.TarInfo) -> tarfile.TarInfo:
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mtime = 0
    if info.isfile():
        info.mode = 0o755 if info.mode & 0o111 else 0o644
    elif info.isdir():
        info.mode = 0o755
    return info


def _create_archive(source_root: Path, archive: Path) -> None:
    archive.parent.mkdir(parents=True, exist_ok=True)
    members = [source_root, *sorted(source_root.rglob("*"), key=lambda item: item.relative_to(source_root).as_posix())]
    with archive.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w") as handle:
                for path in members:
                    arcname = source_root.name
                    if path != source_root:
                        arcname += "/" + path.relative_to(source_root).as_posix()
                    handle.add(path, arcname=arcname, recursive=False, filter=_tar_filter)


SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"AIza[0-9A-Za-z_-]{20,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9._-]{20,}", re.IGNORECASE),
    re.compile(
        r"api[_-]?key['\"]?\s*[:=]\s*['\"][A-Za-z0-9_-]{20,}['\"]",
        re.IGNORECASE,
    ),
)


def _scan_text_files(tree: Path) -> list[str]:
    findings: list[str] = []
    for path in sorted(tree.rglob("*")):
        if not path.is_file() or path.suffix in {".gz", ".png", ".jpg", ".jpeg", ".pyc"}:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for pattern in SECRET_PATTERNS:
            if pattern.search(content):
                findings.append(f"{path.relative_to(tree).as_posix()}: {pattern.pattern}")
    return findings


def _render_docs(repo_root: Path, target: Path, *, version: str, dist_base_url: str) -> None:
    source = repo_root / "packaging" / "image" / "docs"
    for path in sorted(source.rglob("*")):
        if not path.is_file():
            continue
        rendered = (
            path.read_text(encoding="utf-8")
            .replace("__VERSION__", version)
            .replace("__DIST_BASE_URL__", dist_base_url.rstrip("/"))
        )
        output = target / path.relative_to(source)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")


def _assert_pages_limits(*trees: Path) -> None:
    for tree in trees:
        for path in tree.rglob("*"):
            if path.is_file() and path.stat().st_size >= PAGES_FILE_LIMIT:
                raise ValueError(f"Pages file exceeds 25 MiB: {path}")


def _write_reports(output_root: Path, dist_dir: Path, docs_dir: Path) -> Path:
    reports = output_root / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    files: list[dict[str, object]] = []
    for label, tree in (("dist", dist_dir), ("docs", docs_dir)):
        for path in sorted(tree.rglob("*")):
            if path.is_file():
                files.append(
                    {
                        "tree": label,
                        "path": path.relative_to(tree).as_posix(),
                        "size": path.stat().st_size,
                        "sha256": sha256_file(path),
                    }
                )
    (reports / "file-manifest.json").write_text(
        json.dumps(files, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    largest = sorted(files, key=lambda item: int(item["size"]), reverse=True)
    (reports / "size-report.txt").write_text(
        "\n".join(f"{item['size']} {item['tree']}/{item['path']}" for item in largest) + "\n",
        encoding="utf-8",
    )
    findings = _scan_text_files(output_root)
    if findings:
        raise ValueError("secret-like value in Image release output: " + "; ".join(findings))
    (reports / "secret-scan.txt").write_text(
        "PASS: no API key, bearer token, Codex auth, cookie, or secret-like value matched.\n",
        encoding="utf-8",
    )
    return reports


def _assert_image_archive_members(archive: Path) -> None:
    forbidden_names = {
        "generate-presentation",
        "pptgen-wrapper.sh",
        "pptgen-server-wrapper.sh",
    }
    with tarfile.open(archive, "r:gz") as handle:
        names = [member.name for member in handle.getmembers()]
    violations = [
        name
        for name in names
        if PurePosixPath(name).name in forbidden_names
        or (
            "frontend" in PurePosixPath(name).parts
            and not (
                PurePosixPath(name).parts[1:3] == ("app", "frontend")
                and (
                    len(PurePosixPath(name).parts) == 3
                    or PurePosixPath(name).parts[3:4] == ("dist",)
                )
            )
        )
    ]
    if violations:
        raise ValueError("HTML presentation artifact in Image release: " + ", ".join(violations))


def build_release(
    repo_root: Path,
    output_root: Path,
    *,
    version: str,
    dist_base_url: str = "https://image-pptgen-dist.pages.dev",
) -> BuildResult:
    repo_root = repo_root.resolve()
    output_root = output_root.resolve()
    if output_root.exists():
        shutil.rmtree(output_root)
    dist_dir = output_root / "pages-dist"
    docs_dir = output_root / "pages-docs"
    release_dir = dist_dir / "releases" / version
    release_dir.mkdir(parents=True)

    archive_name = f"image-pptgen-{version}-linux-x86_64.tar.gz"
    archive_path = release_dir / archive_name
    with tempfile.TemporaryDirectory(prefix="image-pptgen-release-build-") as tmp:
        bundle = Path(tmp) / f"image-pptgen-{version}"
        _populate_runtime(repo_root, bundle / "app")
        identity = _release_identity(repo_root, bundle / "app", version=version)
        (bundle / "app" / "release-identity.json").write_text(
            json.dumps(identity, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        runtime_findings = _scan_text_files(bundle)
        if runtime_findings:
            raise ValueError("secret-like value in Image runtime bundle: " + "; ".join(runtime_findings))
        _create_archive(bundle, archive_path)
    validate_archive(archive_path)
    _assert_image_archive_members(archive_path)

    archive_sha = sha256_file(archive_path)
    manifest = {
        "schema_version": 1,
        "product": IMAGE_PRODUCT,
        "version": version,
        "platform": PLATFORM,
        "archive": {
            "name": archive_name,
            "sha256": archive_sha,
            "size": archive_path.stat().st_size,
        },
        "identity": identity,
        "skill": IMAGE_SKILL,
        "command": IMAGE_COMMAND,
        "service_command": IMAGE_SERVICE_COMMAND,
        "doctor_command": "image-pptgen doctor --json",
    }
    manifest_path = release_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    checksums_path = release_dir / "SHA256SUMS"
    checksums_path.write_text(f"{archive_sha}  {archive_name}\n", encoding="utf-8")

    image_dir = repo_root / "packaging" / "image"
    bootstrap_path = dist_dir / "install.sh"
    bootstrap_path.write_text(
        (image_dir / "install.sh").read_text(encoding="utf-8").replace("__VERSION__", version),
        encoding="utf-8",
    )
    bootstrap_path.chmod(0o755)
    (dist_dir / "_headers").write_text(
        "/*\n  X-Robots-Tag: noindex, nofollow, noarchive\n"
        "  X-Content-Type-Options: nosniff\n  Cache-Control: no-store\n",
        encoding="utf-8",
    )
    (dist_dir / "robots.txt").write_text("User-agent: *\nDisallow: /\n", encoding="utf-8")
    (dist_dir / "index.html").write_text(
        '<!doctype html><meta name="robots" content="noindex,nofollow">'
        "<title>Image PPTGen distribution</title><p>Image PPTGen distribution endpoint.</p>\n",
        encoding="utf-8",
    )
    _render_docs(repo_root, docs_dir, version=version, dist_base_url=dist_base_url)
    _assert_pages_limits(dist_dir, docs_dir)
    reports_dir = _write_reports(output_root, dist_dir, docs_dir)
    return BuildResult(
        archive_path=archive_path,
        bootstrap_path=bootstrap_path,
        manifest_path=manifest_path,
        checksums_path=checksums_path,
        docs_dir=docs_dir,
        dist_dir=dist_dir,
        reports_dir=reports_dir,
    )


build_image_release = build_release


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build Image PPTGen release")
    parser.add_argument("repo_root", type=Path)
    parser.add_argument("output_root", type=Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--dist-base-url", default="https://image-pptgen-dist.pages.dev")
    args = parser.parse_args(argv)
    result = build_release(
        args.repo_root,
        args.output_root,
        version=args.version,
        dist_base_url=args.dist_base_url,
    )
    print(result.archive_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
