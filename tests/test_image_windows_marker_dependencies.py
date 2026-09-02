from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.tags import Tag
from packaging.utils import parse_wheel_filename
import pytest


ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = ROOT / "packaging" / "image" / "fallback" / "fallback-lock.json"
BUILDER_PATH = ROOT / "packaging" / "image" / "fallback" / "build_frozen_assets.py"
COLORAMA_FILENAME = "colorama-0.4.6-py2.py3-none-any.whl"
COLORAMA_SHA256 = "4f1d9991f5acc0ca119f9d443620b77f9d6b33703e51011c16baf57afb285fc6"


def _load_lock() -> dict[str, object]:
    return json.loads(LOCK_PATH.read_text(encoding="utf-8"))


def _load_builder():
    spec = importlib.util.spec_from_file_location("image_fallback_builder", BUILDER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _marker_environment(
    platform_system: str,
    sys_platform: str,
    *,
    python_version: str,
    python_full_version: str,
) -> dict[str, str]:
    environment = default_environment()
    environment.update(
        {
            "platform_system": platform_system,
            "sys_platform": sys_platform,
            "platform_machine": "AMD64",
            "python_version": python_version,
            "python_full_version": python_full_version,
        }
    )
    return environment


def test_click_windows_marker_is_evaluated_and_colorama_is_locked() -> None:
    lock = _load_lock()
    click = next(item for item in lock["wheel_sources"] if item["name"] == "click")
    requirement = Requirement(click["requires_dist"][0])

    assert requirement.name == "colorama"
    assert str(requirement.specifier) == ""
    for python_version, python_full_version in (("3.11", "3.11.15"), ("3.12", "3.12.13")):
        assert requirement.marker.evaluate(
            _marker_environment(
                "Windows",
                "win32",
                python_version=python_version,
                python_full_version=python_full_version,
            )
        )
    assert not requirement.marker.evaluate(
        _marker_environment(
            "Linux",
            "linux",
            python_version="3.12",
            python_full_version="3.12.13",
        )
    )

    colorama = next(item for item in lock["wheel_sources"] if item["name"] == "colorama")
    assert colorama["version"] == "0.4.6"
    assert colorama["platform_marker"] == "platform_system == \"Windows\""
    assert colorama["source_metadata"] == "https://pypi.org/pypi/colorama/0.4.6/json"

    windows = lock["platforms"]["windows-amd64"]["wheels"]
    macos = lock["platforms"]["macos-arm64"]["wheels"]
    windows_colorama = next(item for item in windows if item["filename"] == COLORAMA_FILENAME)
    assert windows_colorama["bytes"] == 25335
    assert windows_colorama["sha256"] == COLORAMA_SHA256
    assert COLORAMA_FILENAME not in {item["filename"] for item in macos}


def test_colorama_universal_wheel_is_valid_for_both_windows_cpython_targets() -> None:
    _name, _version, _build, wheel_tags = parse_wheel_filename(COLORAMA_FILENAME)
    assert Tag("py3", "none", "any") in wheel_tags
    requirements = (
        ROOT / "packaging" / "image" / "platform" / "windows" / "requirements.lock"
    ).read_text(encoding="utf-8")
    colorama_line = next(line for line in requirements.splitlines() if line.startswith("colorama=="))
    assert colorama_line == (
        "colorama==0.4.6 --hash=sha256:"
        + COLORAMA_SHA256
    )
    for _target in ("cp311", "cp312"):
        assert Tag("py3", "none", "any") in wheel_tags, _target


def test_builder_rejects_windows_marker_wheel_inventory_drift() -> None:
    builder = _load_builder()
    lock = _load_lock()
    platform = json.loads(json.dumps(lock["platforms"]["windows-amd64"]))
    platform["wheels"] = [
        wheel for wheel in platform["wheels"] if wheel["filename"] != COLORAMA_FILENAME
    ]

    with pytest.raises(builder.FreezeError, match="marker dependency"):
        builder._validate_windows_marker_wheel_inventory(platform)
