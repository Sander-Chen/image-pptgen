#!/usr/bin/env python3
"""Exact public-source and package allowlist for Image PPTGen 3.0.

The internal checkout may still contain Windows, mockups, historical briefs,
and other private material. Public snapshots and installable archives are
constructed only from this allowlist.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Iterable, Mapping


PUBLIC_TARGET_PLATFORMS = ("linux-x86_64", "macos-arm64")
PUBLIC_FALLBACK_PLATFORMS = ("macos-arm64",)
IMAGE_SKILL = "generate-image-presentation"

APP_PYTHON_FILES = (
    "server.py",
    "public_server.py",
    "db.py",
    "pipeline.py",
    "config.py",
    "splitter.py",
)

RUNTIME_SPLIT_PROMPT_FILES = ()

IMAGE_PROMPT_SOURCE_FILES = (
    "example/prompts/cover.md",
    "example/prompts/seed-slide.md",
    "example/prompts/subsequent-slide.md",
    "example/prompts/faithful-split.md",
    "example/prompts/palette-extraction.md",
)

FRONTEND_CONFIG_FILES = (
    "frontend/index.html",
    "frontend/package.json",
    "frontend/package-lock.json",
    "frontend/tsconfig.json",
    "frontend/tsconfig.app.json",
    "frontend/tsconfig.node.json",
    "frontend/vite.config.ts",
    "frontend/eslint.config.js",
)

PACKAGING_PUBLIC_FILES = (
    "packaging/image/launcher.py",
    "packaging/image/runtime_manager.py",
    "packaging/image/image-pptgen-wrapper.sh",
    "packaging/image/image-pptgen-server-wrapper.sh",
    "packaging/image/requirements.txt",
    "packaging/image/install.sh",
    "packaging/image/docs/install.md",
    "packaging/image/docs/install.json",
    "packaging/image_build_release.py",
    "packaging/image_public_allowlist.py",
    "packaging/image_han_scan.py",
    "packaging/image_han_exceptions.json",
)

PUBLIC_ROOT_FILES = (
    "README.md",
)

STARTER_FRONTEND_NAMES = frozenset({"hero.png", "react.svg", "vite.svg", "icons.svg"})

_FORBIDDEN_PATH_PATTERNS = (
    re.compile(r"(^|/)evaluation-prototype(/|$)"),
    re.compile(r"(^|/)mockups(/|$)"),
    re.compile(r"(^|/)platform/windows(/|$)"),
    re.compile(r"(^|/)windows-amd64"),
    re.compile(r"(^|/)install\.ps1$"),
    re.compile(r"(^|/)windows_installer\.py$"),
    re.compile(r"\.ps1$"),
    re.compile(r"(^|/)ppt\.db$"),
    re.compile(r"(^|/)\.env(?:$|\..+)$"),
    re.compile(r"(^|/)PPT_gen_support_image\.md$"),
    re.compile(r"image_ppt_input/"),
    re.compile(r"(^|/)" + "\u6ce8\u610f\u4e8b\u9879\u4e0e" + r"setup\.md$"),
    re.compile("\u5c01\u9762\u9875PPT\u751f\u6210"),
    re.compile("\u81ea\u52a8\u5207\u5206"),
    re.compile("\u63d0\u53d6\u914d\u56fe\u989c\u8272"),
    re.compile("\u76f4\u63a5\u6587\u751f\u56fe\u5206\u652f"),
    re.compile("\u8bbe\u8ba1\u603b\u76d1\u5206\u652f"),
    re.compile(r"(^|/)hero\.png$"),
    re.compile(r"(^|/)vite\.svg$"),
    re.compile(r"(^|/)react\.svg$"),
    re.compile(r"(^|/)icons\.svg$"),
    re.compile(r"(^|/)example/Database(/|$)"),
    re.compile(r"(^|/)example/example(/|$)"),
)


class AllowlistError(ValueError):
    """A required public file is missing or a forbidden path was selected."""


def required_prompt_relative_paths() -> tuple[str, ...]:
    return IMAGE_PROMPT_SOURCE_FILES + RUNTIME_SPLIT_PROMPT_FILES


def frontend_dist_member_allowed(relative: str) -> bool:
    posix = relative.replace("\\", "/")
    if posix.startswith("evaluation-prototype/") or posix == "evaluation-prototype":
        return False
    name = posix.rsplit("/", 1)[-1]
    if name in STARTER_FRONTEND_NAMES:
        return False
    if posix == "index.html" or posix == "favicon.svg":
        return True
    return posix.startswith("assets/") and not posix.endswith("/")


def frontend_src_member_allowed(relative: str) -> bool:
    posix = relative.replace("\\", "/")
    parts = tuple(part for part in posix.split("/") if part)
    if "mockups" in parts:
        return False
    if parts and parts[-1] in STARTER_FRONTEND_NAMES:
        return False
    return True


def forbidden_public_paths(relative_paths: Iterable[str]) -> list[str]:
    findings: list[str] = []
    for relative in relative_paths:
        posix = relative.replace("\\", "/")
        if any(pattern.search(posix) for pattern in _FORBIDDEN_PATH_PATTERNS):
            findings.append(posix)
    return findings


def public_fallback_lock(lock: Mapping[str, object]) -> dict[str, object]:
    platforms = lock.get("platforms")
    if not isinstance(lock, Mapping) or not isinstance(platforms, Mapping):
        raise AllowlistError("fallback authority is malformed")
    public_platforms = {
        platform_id: spec
        for platform_id, spec in platforms.items()
        if platform_id in PUBLIC_FALLBACK_PLATFORMS
    }
    missing = [item for item in PUBLIC_FALLBACK_PLATFORMS if item not in public_platforms]
    if missing:
        raise AllowlistError("fallback authority is missing platform: " + ", ".join(missing))
    payload = dict(lock)
    payload["platforms"] = public_platforms
    return payload


def _regular_files(root: Path) -> list[Path]:
    if root.is_symlink() or not root.is_dir():
        raise AllowlistError(f"required public directory is missing: {root}")
    files: list[Path] = []
    for path in sorted(root.rglob("*")):
        mode_symlink = path.is_symlink()
        if mode_symlink:
            raise AllowlistError(f"public source contains a symlink: {path}")
        if path.is_file():
            files.append(path)
    return files


def _require_file(path: Path) -> Path:
    if path.is_symlink() or not path.is_file():
        raise AllowlistError(f"required public file is missing: {path}")
    return path


def _add_file(relative_paths: set[str], repo_root: Path, relative: str) -> None:
    _require_file(repo_root / relative)
    relative_paths.add(relative.replace("\\", "/"))


def _add_python_tree(relative_paths: set[str], repo_root: Path, relative_dir: str) -> None:
    root = repo_root / relative_dir
    for path in _regular_files(root):
        if path.suffix != ".py" or "__pycache__" in path.parts or ".egg-info" in path.parts:
            continue
        relative_paths.add(path.relative_to(repo_root).as_posix())


def _add_skill_tree(relative_paths: set[str], repo_root: Path) -> None:
    root = repo_root / "skills" / IMAGE_SKILL
    for path in _regular_files(root):
        if path.suffix.lower() == ".ps1" or "__pycache__" in path.parts:
            continue
        relative_paths.add(path.relative_to(repo_root).as_posix())


def _add_macos_packaging(relative_paths: set[str], repo_root: Path) -> None:
    root = repo_root / "packaging" / "image" / "platform" / "macos"
    for path in _regular_files(root):
        if "__pycache__" in path.parts:
            continue
        relative_paths.add(path.relative_to(repo_root).as_posix())


def _add_frontend_src(relative_paths: set[str], repo_root: Path) -> None:
    root = repo_root / "frontend" / "src"
    for path in _regular_files(root):
        relative = path.relative_to(root).as_posix()
        if not frontend_src_member_allowed(relative):
            continue
        relative_paths.add(path.relative_to(repo_root).as_posix())


def _add_eval_materials(relative_paths: set[str], repo_root: Path) -> None:
    root = repo_root / "eval-materials"
    if not root.is_dir():
        return
    for path in _regular_files(root):
        relative_paths.add(path.relative_to(repo_root).as_posix())


def frontend_dist_relative_paths(repo_root: Path, *, required: bool) -> list[str]:
    root = repo_root / "frontend" / "dist"
    if root.is_symlink() or not root.is_dir():
        if required:
            raise AllowlistError(f"required frontend dist is missing: {root}")
        return []
    selected: list[str] = []
    for path in _regular_files(root):
        relative = path.relative_to(root).as_posix()
        if frontend_dist_member_allowed(relative):
            selected.append("frontend/dist/" + relative)
    if required and "frontend/dist/index.html" not in selected:
        raise AllowlistError("frontend dist is missing index.html")
    return sorted(set(selected))


def runtime_relative_paths(repo_root: Path) -> list[str]:
    repo_root = repo_root.resolve()
    selected: set[str] = set()
    for relative in APP_PYTHON_FILES + required_prompt_relative_paths():
        _add_file(selected, repo_root, relative)
    _add_python_tree(selected, repo_root, "backend")
    _add_python_tree(selected, repo_root, "packages/pptgen_toolkit/src")
    _add_skill_tree(selected, repo_root)
    selected.update(frontend_dist_relative_paths(repo_root, required=True))
    for relative in (
        "packaging/image/launcher.py",
        "packaging/image/runtime_manager.py",
        "packaging/image/image-pptgen-wrapper.sh",
        "packaging/image/image-pptgen-server-wrapper.sh",
        "packaging/image/requirements.txt",
    ):
        _add_file(selected, repo_root, relative)
    ordered = sorted(selected)
    violations = forbidden_public_paths(ordered)
    if violations:
        raise AllowlistError("forbidden path selected for runtime: " + ", ".join(violations))
    return ordered


def source_snapshot_relative_paths(repo_root: Path) -> list[str]:
    repo_root = repo_root.resolve()
    selected: set[str] = set()
    for relative in (
        PUBLIC_ROOT_FILES
        + APP_PYTHON_FILES
        + required_prompt_relative_paths()
        + FRONTEND_CONFIG_FILES
        + PACKAGING_PUBLIC_FILES
    ):
        _add_file(selected, repo_root, relative)
    _add_python_tree(selected, repo_root, "backend")
    _add_python_tree(selected, repo_root, "packages/pptgen_toolkit/src")
    _add_file(selected, repo_root, "packages/pptgen_toolkit/pyproject.toml")
    _add_skill_tree(selected, repo_root)
    _add_macos_packaging(selected, repo_root)
    _add_frontend_src(selected, repo_root)
    _add_file(selected, repo_root, "frontend/public/favicon.svg")
    _add_eval_materials(selected, repo_root)
    selected.update(frontend_dist_relative_paths(repo_root, required=False))
    ordered = sorted(selected)
    violations = forbidden_public_paths(ordered)
    if violations:
        raise AllowlistError("forbidden path selected for public source: " + ", ".join(violations))
    return ordered


def copy_public_source_snapshot(repo_root: Path, snapshot_root: Path) -> list[str]:
    repo_root = repo_root.expanduser().resolve()
    snapshot_root = snapshot_root.expanduser().resolve()
    if snapshot_root.exists():
        shutil.rmtree(snapshot_root)
    snapshot_root.mkdir(parents=True)
    copied: list[str] = []
    for relative in source_snapshot_relative_paths(repo_root):
        source = _require_file(repo_root / relative)
        target = snapshot_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        copied.append(relative)
    lock_source = repo_root / "packaging" / "image" / "fallback" / "fallback-lock.json"
    lock = json.loads(_require_file(lock_source).read_text(encoding="utf-8"))
    public_lock = public_fallback_lock(lock)
    lock_target = snapshot_root / "packaging" / "image" / "fallback" / "fallback-lock.json"
    lock_target.parent.mkdir(parents=True, exist_ok=True)
    lock_target.write_bytes(
        (json.dumps(public_lock, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    )
    if "packaging/image/fallback/fallback-lock.json" not in copied:
        copied.append("packaging/image/fallback/fallback-lock.json")
        copied.sort()
    violations = forbidden_public_paths(
        path.relative_to(snapshot_root).as_posix()
        for path in snapshot_root.rglob("*")
        if path.is_file()
    )
    if violations:
        raise AllowlistError("forbidden path present in public snapshot: " + ", ".join(violations))
    return copied
