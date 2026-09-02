"""Contract tests for the unified Public Image release builder.

The platform-specific installer tests exercise the Windows and macOS
controllers directly.  This module covers the release boundary that joins
those controllers to one deterministic three-platform manifest and keeps the
legacy Linux builder callable during the transition.
"""

from __future__ import annotations

import builtins
from concurrent.futures import ThreadPoolExecutor
import hashlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import io
import importlib.util
import inspect
import json
import os
import stat
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tarfile
import tempfile
import threading
from types import SimpleNamespace
import zipfile

import pytest


ROOT = Path(__file__).resolve().parents[1]
LEGACY_BUILDER_PATH = ROOT / "packaging" / "image_build_release.py"
UNIFIED_BUILDER_CANDIDATES = (
    ROOT / "packaging" / "image_multiplatform_release.py",
    LEGACY_BUILDER_PATH,
)

TARGET_PLATFORMS = ("linux-x86_64", "macos-arm64", "windows-amd64")
VERSION = "9.9.9-contract"
DIST_BASE_URL = "https://dist.example.test/image-pptgen-contract"
MANIFEST_URL = (
    "https://manifest.example.test/image-pptgen-contract/"
    f"{VERSION}/manifest.json"
)
R2_PREFIX = "acceptance/image-pptgen-contract"
R2_BASE_URL = "https://payloads.example.test"
OLD_DISTRIBUTION_URLS = (
    "https://image-pptgen-dist.pages.dev",
    "https://pptgen-dist.pages.dev",
)
PAGES_FILE_LIMIT = 25 * 1024 * 1024


def _load_module(path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec and spec.loader, f"cannot load builder module: {path}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_unified_builder():
    """Load the new API without treating the Linux-only API as a substitute."""
    for path in UNIFIED_BUILDER_CANDIDATES:
        if not path.is_file():
            continue
        module = _load_module(path, f"image_release_builder_{path.stem}")
        for function_name in ("build_multiplatform_release", "build_release"):
            function = getattr(module, function_name, None)
            if callable(function):
                return module, function
    pytest.fail(
        "unified release builder is not implemented: add "
        "packaging/image_multiplatform_release.py (or the compatibility "
        "module) with build_multiplatform_release(...) or extend the "
        "legacy build_release(...) wrapper to accept the unified contract "
        "(version, dist_base_url, manifest_url, r2_root, r2_prefix, "
        "r2_base_url, r2_ledger_path, fallback_assets_root)"
    )


def _load_public_bootstrap(builder, tmp_path: Path, *, version: str = VERSION):
    script = tmp_path / "install.py"
    script.write_text(
        builder._render_public_python_bootstrap(
            version=version,
            dist_base_url=DIST_BASE_URL,
            manifest_url=MANIFEST_URL,
            r2_base_url=R2_BASE_URL,
        ),
        encoding="utf-8",
    )
    spec = importlib.util.spec_from_file_location(
        f"image_public_install_{version.replace('.', '_').replace('-', '_')}", script
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, script


def _bootstrap_runtime(
    executable: Path,
    *,
    implementation: str = "cpython",
    version: tuple[int, int, int] = (3, 12, 0),
) -> SimpleNamespace:
    """Return the generated installer runtime seam without spawning a shim."""
    return SimpleNamespace(
        executable=str(executable),
        implementation=SimpleNamespace(name=implementation),
        version_info=version,
        stderr=sys.stderr,
        stdout=sys.stdout,
    )


def _managed_runtime_fixture(
    tmp_path: Path,
    *,
    python_version: str = "3.12.0",
    target_platform: str = "win32",
    target_arch: str = "x64",
    config_source: str | None = None,
) -> tuple[Path, Path, Path]:
    """Create the fixed Codex primary-runtime layout used by the bootstrap contract."""

    profile_root = tmp_path / "Admin"
    runtime_root = (
        profile_root
        / ".cache"
        / "codex-runtimes"
        / "codex-primary-runtime"
    )
    interpreter = runtime_root / "dependencies" / "python" / "python.exe"
    plugin_root = runtime_root / "plugins" / "openai-primary-runtime"
    interpreter.parent.mkdir(parents=True)
    plugin_root.mkdir(parents=True)
    interpreter.write_bytes(b"managed interpreter fixture")
    (runtime_root / "runtime.json").write_text(
        json.dumps(
            {
                "bundleFormatVersion": 2,
                "bundleVersion": "26.819.11345",
                "targetPlatform": target_platform,
                "targetArch": target_arch,
                "pythonVersion": python_version,
                "bundledPlugins": ["plugins/openai-primary-runtime"],
            }
        ),
        encoding="utf-8",
    )
    profile = profile_root / ".codex" / "config.toml"
    profile.parent.mkdir(parents=True)
    source = config_source if config_source is not None else str(plugin_root)
    profile.write_text(
        "[marketplaces.openai-primary-runtime]\n"
        "source_type = \"local\"\n"
        f"source = {json.dumps(source)}\n",
        encoding="utf-8",
    )
    return runtime_root, interpreter, profile


def _use_fixture_profile(monkeypatch: pytest.MonkeyPatch, profile: Path) -> None:
    monkeypatch.setenv("USERPROFILE", str(profile.parent.parent))


def test_public_windows_bootstrap_is_versioned_managed_python_and_stdlib_only(
    tmp_path: Path,
) -> None:
    builder, _build_fn = _load_unified_builder()
    module, script = _load_public_bootstrap(builder, tmp_path)
    source = script.read_text(encoding="utf-8")

    assert module.VERSION == VERSION
    assert "--bootstrap-python" in source
    assert "--managed-python" not in source
    assert "CPython 3.12" in source
    assert "Codex managed primary CPython 3.12" in source
    assert "external CPython 3.12" not in source
    assert "Codex Python" not in source
    assert "codex-primary-runtime" in source
    assert "runtime.json" in source
    assert "tomllib" in source
    assert "sys.executable" in source
    assert "urllib.request" in source
    assert "INSTALLER_USER_AGENT" in source
    assert '"User-Agent": INSTALLER_USER_AGENT' in source
    assert "zipfile" in source
    assert "subprocess" not in source
    assert "shell=True" not in source
    assert "powershell" not in source.casefold()
    assert "winget" not in source.casefold()
    assert "cmd.exe" not in source.casefold()
    assert "winreg" not in source.casefold()
    assert 'os.environ["PATH"]' not in source
    assert "subprocess" not in source


def test_public_windows_bootstrap_rejects_external_python_mismatch_with_structured_stderr(
    tmp_path: Path,
) -> None:
    builder, _build_fn = _load_unified_builder()
    _module, script = _load_public_bootstrap(builder, tmp_path)
    other_python = tmp_path / "other-python.exe"
    other_python.write_bytes(b"not a Python interpreter")
    completed = subprocess.run(
        [sys.executable, str(script), "--bootstrap-python", str(other_python.resolve())],
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 3
    assert completed.stdout == ""
    error = json.loads(completed.stderr)
    assert error["error_code"] == "bootstrap_python_mismatch"
    assert error["stages"][0]["stage_id"] == "bootstrap-python"
    assert error["stages"][0]["status"] == "failed"
    assert error["stages"][0]["error_code"] == "bootstrap_python_mismatch"


def test_public_windows_bootstrap_requires_external_python_without_argparse_noise(
    tmp_path: Path,
) -> None:
    builder, _build_fn = _load_unified_builder()
    _module, script = _load_public_bootstrap(builder, tmp_path)
    completed = subprocess.run(
        [sys.executable, str(script)], text=True, capture_output=True, check=False
    )
    assert completed.returncode == 3
    assert completed.stdout == ""
    error = json.loads(completed.stderr)
    assert error["error_code"] == "bootstrap_python_missing"
    assert error["stages"][0]["stage_id"] == "bootstrap-python"
    assert error["stages"][0]["error_code"] == "bootstrap_python_missing"


def test_public_windows_bootstrap_rejects_non_312_before_network_or_controller(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    builder, _build_fn = _load_unified_builder()
    module, _script = _load_public_bootstrap(builder, tmp_path)
    current = Path(sys.executable).resolve()
    monkeypatch.setattr(
        module,
        "sys",
        _bootstrap_runtime(current, version=(3, 14, 0)),
    )
    calls: list[str] = []
    monkeypatch.setattr(
        module,
        "_download_verified",
        lambda *_args, **_kwargs: calls.append("download"),
    )
    monkeypatch.setattr(
        module,
        "_load_controller",
        lambda *_args, **_kwargs: calls.append("controller"),
    )

    assert module.main(["--bootstrap-python", str(current)]) == 3

    error = json.loads(capsys.readouterr().err)
    assert error["error_code"] == "bootstrap_python_version_mismatch"
    assert len(error["stages"]) == 1
    stage = error["stages"][0]
    assert stage["stage_id"] == "bootstrap-python"
    assert stage["status"] == "failed"
    assert stage["error_code"] == "bootstrap_python_version_mismatch"
    assert stage["exit_code"] == 3
    assert calls == []


def test_public_windows_bootstrap_requires_regular_non_reparse_absolute_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    builder, _build_fn = _load_unified_builder()
    module, _script = _load_public_bootstrap(builder, tmp_path)
    current = tmp_path / "external-python.exe"
    current.write_bytes(b"fixture")
    monkeypatch.setattr(module, "sys", _bootstrap_runtime(current))

    with pytest.raises(module.BootstrapError) as failure:
        module._bootstrap_python(str(current.parent / "missing-python.exe"))
    assert failure.value.code == "bootstrap_python_unavailable"

    with pytest.raises(module.BootstrapError) as failure:
        module._bootstrap_python("external-python.exe")
    assert failure.value.code == "bootstrap_python_not_absolute"

    link = tmp_path / "external-python-link.exe"
    try:
        link.symlink_to(current)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are unavailable in this test environment")
    with pytest.raises(module.BootstrapError) as failure:
        module._bootstrap_python(str(link))
    assert failure.value.code == "bootstrap_python_reparse"

    with pytest.raises(module.BootstrapError) as failure:
        module._bootstrap_python(str(current.parent))
    assert failure.value.code == "bootstrap_python_not_regular"

    monkeypatch.setattr(module, "_is_reparse_point", lambda _file_stat: True)
    with pytest.raises(module.BootstrapError) as failure:
        module._bootstrap_python(str(current))
    assert failure.value.code == "bootstrap_python_reparse"


def test_public_windows_bootstrap_accepts_fixed_managed_runtime_and_long_config_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    builder, _build_fn = _load_unified_builder()
    module, _script = _load_public_bootstrap(builder, tmp_path)
    runtime_root, current, profile = _managed_runtime_fixture(tmp_path)
    _use_fixture_profile(monkeypatch, profile)
    plugin_root = runtime_root / "plugins" / "openai-primary-runtime"
    # The real Windows config may use the extended-length path spelling.
    _profile = runtime_root.parents[2] / ".codex" / "config.toml"
    _profile.write_text(
        "[marketplaces.openai-primary-runtime]\n"
        "source_type = \"local\"\n"
        f"source = {json.dumps('\\\\?\\' + str(plugin_root))}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "sys", _bootstrap_runtime(current))

    assert module._bootstrap_python(str(current)) == current.resolve()


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("targetPlatform", "linux"),
        ("targetArch", "arm64"),
        ("pythonVersion", "3.12.13"),
        ("bundleFormatVersion", 1),
        ("bundleVersion", ""),
        ("bundledPlugins", []),
    ),
)
def test_public_windows_bootstrap_rejects_invalid_managed_runtime_metadata_before_download(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    field: str,
    value: object,
) -> None:
    builder, _build_fn = _load_unified_builder()
    module, _script = _load_public_bootstrap(builder, tmp_path)
    runtime_root, current, profile = _managed_runtime_fixture(tmp_path)
    _use_fixture_profile(monkeypatch, profile)
    metadata = json.loads((runtime_root / "runtime.json").read_text(encoding="utf-8"))
    metadata[field] = value
    (runtime_root / "runtime.json").write_text(json.dumps(metadata), encoding="utf-8")
    monkeypatch.setattr(module, "sys", _bootstrap_runtime(current))
    calls: list[str] = []
    monkeypatch.setattr(
        module,
        "_download_verified",
        lambda *_args, **_kwargs: calls.append("download"),
    )
    monkeypatch.setattr(
        module,
        "_load_controller",
        lambda *_args, **_kwargs: calls.append("controller"),
    )

    assert module.main(["--bootstrap-python", str(current)]) == 3

    error = json.loads(capsys.readouterr().err)
    assert error["error_code"] == "managed_runtime_metadata_mismatch"
    assert [stage["stage_id"] for stage in error["stages"]] == ["bootstrap-python"]
    assert calls == []


def test_public_windows_bootstrap_rejects_missing_runtime_metadata_before_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    builder, _build_fn = _load_unified_builder()
    module, _script = _load_public_bootstrap(builder, tmp_path)
    runtime_root, current, profile = _managed_runtime_fixture(tmp_path)
    _use_fixture_profile(monkeypatch, profile)
    (runtime_root / "runtime.json").unlink()
    monkeypatch.setattr(module, "sys", _bootstrap_runtime(current))

    with pytest.raises(module.BootstrapError) as failure:
        module._bootstrap_python(str(current))
    assert failure.value.code == "managed_runtime_path_unavailable"


@pytest.mark.parametrize("config_mode", ("missing", "mismatch", "plugins", "wrong_type"))
def test_public_windows_bootstrap_rejects_missing_or_mismatched_desktop_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    config_mode: str,
) -> None:
    builder, _build_fn = _load_unified_builder()
    module, _script = _load_public_bootstrap(builder, tmp_path)
    runtime_root, current, profile = _managed_runtime_fixture(tmp_path)
    _use_fixture_profile(monkeypatch, profile)
    if config_mode == "missing":
        profile.unlink()
    elif config_mode == "plugins":
        profile.write_text(
            "[plugins.openai-primary-runtime]\n"
            "source_type = \"local\"\n"
            f"source = {json.dumps(str(runtime_root / 'plugins' / 'openai-primary-runtime'))}\n",
            encoding="utf-8",
        )
    elif config_mode == "wrong_type":
        profile.write_text(
            "[marketplaces.openai-primary-runtime]\n"
            "source_type = \"git\"\n"
            f"source = {json.dumps(str(runtime_root / 'plugins' / 'openai-primary-runtime'))}\n",
            encoding="utf-8",
        )
    else:
        profile.write_text(
            "[marketplaces.other-runtime]\n"
            "source_type = \"local\"\n"
            f"source = {json.dumps(str(runtime_root / 'plugins' / 'openai-primary-runtime'))}\n",
            encoding="utf-8",
        )
    monkeypatch.setattr(module, "sys", _bootstrap_runtime(current))

    with pytest.raises(module.BootstrapError) as failure:
        module._bootstrap_python(str(current))
    assert failure.value.code in {
        "managed_runtime_path_unavailable",
        "managed_runtime_profile_missing",
        "managed_runtime_config_mismatch",
    }


def test_public_windows_bootstrap_rejects_venv_template_and_external_python(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    builder, _build_fn = _load_unified_builder()
    module, _script = _load_public_bootstrap(builder, tmp_path)
    runtime_root, current, profile = _managed_runtime_fixture(tmp_path)
    _use_fixture_profile(monkeypatch, profile)
    monkeypatch.setattr(module, "sys", _bootstrap_runtime(current))
    template = runtime_root / "Lib" / "venv" / "scripts" / "nt" / "python.exe"
    template.parent.mkdir(parents=True)
    template.write_bytes(b"venv template")
    monkeypatch.setattr(module, "sys", _bootstrap_runtime(template))
    with pytest.raises(module.BootstrapError) as failure:
        module._bootstrap_python(str(template))
    assert failure.value.code == "bootstrap_python_not_managed_runtime"

    external = tmp_path / "Python312" / "python.exe"
    external.parent.mkdir()
    external.write_bytes(b"external Python")
    monkeypatch.setattr(module, "sys", _bootstrap_runtime(external))
    with pytest.raises(module.BootstrapError) as failure:
        module._bootstrap_python(str(external))
    assert failure.value.code == "bootstrap_python_not_managed_runtime"


def test_public_windows_bootstrap_rejects_primary_runtime_from_another_user(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    builder, _build_fn = _load_unified_builder()
    module, _script = _load_public_bootstrap(builder, tmp_path)
    _runtime_root, _current, profile = _managed_runtime_fixture(tmp_path)
    _other_root, other_python, _other_profile = _managed_runtime_fixture(tmp_path / "other")
    _use_fixture_profile(monkeypatch, profile)
    monkeypatch.setattr(module, "sys", _bootstrap_runtime(other_python))

    with pytest.raises(module.BootstrapError) as failure:
        module._bootstrap_python(str(other_python))
    assert failure.value.code == "bootstrap_python_not_managed_runtime"


def test_public_windows_bootstrap_rejects_reparse_managed_runtime_ancestor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    builder, _build_fn = _load_unified_builder()
    module, _script = _load_public_bootstrap(builder, tmp_path)
    runtime_root, current, profile = _managed_runtime_fixture(tmp_path)
    _use_fixture_profile(monkeypatch, profile)
    cache_directory = runtime_root.parents[1]
    original_assert = module._assert_managed_regular

    def mark_dependencies_reparse(path: Path, *, label: str, directory: bool) -> None:
        if path == cache_directory:
            monkeypatch.setattr(module, "_is_reparse_point", lambda _file_stat: True)
        original_assert(path, label=label, directory=directory)

    monkeypatch.setattr(module, "_assert_managed_regular", mark_dependencies_reparse)
    monkeypatch.setattr(module, "sys", _bootstrap_runtime(current))
    calls: list[str] = []
    monkeypatch.setattr(
        module,
        "_download_verified",
        lambda *_args, **_kwargs: calls.append("download"),
    )
    monkeypatch.setattr(
        module,
        "_load_controller",
        lambda *_args, **_kwargs: calls.append("controller"),
    )

    assert module.main(["--bootstrap-python", str(current)]) == 3
    failure = json.loads(capsys.readouterr().err)
    # The targeted layout ancestor must fail before any network/controller stage.
    assert failure["error_code"] == "managed_runtime_reparse"
    assert calls == []


def test_public_windows_bootstrap_rejects_non_cpython_before_network_or_controller(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    builder, _build_fn = _load_unified_builder()
    module, _script = _load_public_bootstrap(builder, tmp_path)
    current = Path(sys.executable).resolve()
    monkeypatch.setattr(
        module,
        "sys",
        _bootstrap_runtime(current, implementation="pypy"),
    )
    calls: list[str] = []
    monkeypatch.setattr(
        module,
        "_download_verified",
        lambda *_args, **_kwargs: calls.append("download"),
    )
    monkeypatch.setattr(
        module,
        "_load_controller",
        lambda *_args, **_kwargs: calls.append("controller"),
    )

    assert module.main(["--bootstrap-python", str(current)]) == 3

    error = json.loads(capsys.readouterr().err)
    assert error["error_code"] == "bootstrap_python_not_cpython"
    assert error["stages"][0]["stage_id"] == "bootstrap-python"
    assert calls == []


def test_public_windows_bootstrap_rejects_unsupported_host_before_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    builder, _build_fn = _load_unified_builder()
    module, _script = _load_public_bootstrap(builder, tmp_path)
    _runtime_root, current, profile = _managed_runtime_fixture(tmp_path)
    _use_fixture_profile(monkeypatch, profile)
    monkeypatch.setattr(module, "sys", _bootstrap_runtime(current))
    monkeypatch.setattr(module.platform, "system", lambda: "Linux")
    monkeypatch.setattr(module.platform, "machine", lambda: "x86_64")
    calls: list[str] = []
    monkeypatch.setattr(
        module,
        "_download_verified",
        lambda *_args, **_kwargs: calls.append("download"),
    )
    monkeypatch.setattr(
        module,
        "_load_controller",
        lambda *_args, **_kwargs: calls.append("controller"),
    )

    assert module.main(["--bootstrap-python", str(current)]) == 3

    error = json.loads(capsys.readouterr().err)
    assert error["error_code"] == "unsupported_platform"
    assert [stage["stage_id"] for stage in error["stages"]] == [
        "bootstrap-python",
        "platform",
    ]
    assert calls == []


def test_public_windows_bootstrap_download_and_payload_safety_helpers(
    tmp_path: Path,
) -> None:
    builder, _build_fn = _load_unified_builder()
    module, _script = _load_public_bootstrap(builder, tmp_path)
    payload = b"manifest fixture\n"
    target = tmp_path / "downloaded.bin"

    class _Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, size: int = -1):
            if self._read:
                return b""
            self._read = True
            return payload

        _read = False

    original_urlopen = module.urllib.request.urlopen
    module.urllib.request.urlopen = lambda *_args, **_kwargs: _Response()
    try:
        module._download_verified(
            "https://payloads.example.test/fixture.bin",
            target,
            expected_size=len(payload),
            expected_sha256=hashlib.sha256(payload).hexdigest(),
            max_bytes=1024,
        )
    finally:
        module.urllib.request.urlopen = original_urlopen
    assert target.read_bytes() == payload

    wrong_hash_target = tmp_path / "wrong-hash.bin"
    original_urlopen = module.urllib.request.urlopen
    module.urllib.request.urlopen = lambda *_args, **_kwargs: _Response()
    try:
        with pytest.raises(module.BootstrapError, match="SHA-256") as failure:
            module._download_verified(
                "https://payloads.example.test/fixture.bin",
                wrong_hash_target,
                expected_size=len(payload),
                expected_sha256="0" * 64,
                max_bytes=1024,
            )
    finally:
        module.urllib.request.urlopen = original_urlopen
    assert failure.value.code == "download_sha256_mismatch"
    assert not wrong_hash_target.exists()

    malicious = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(malicious, "w") as archive:
        archive.writestr("../escape", b"unsafe")
    with pytest.raises(module.BootstrapError, match="Unsafe ZIP member") as failure:
        module._safe_extract_payload(malicious, tmp_path / "extract", VERSION)
    assert failure.value.code == "unsafe_payload_member"

    safe = tmp_path / "safe.zip"
    prefix = f"image-pptgen-{VERSION}"
    with zipfile.ZipFile(safe, "w") as archive:
        for relative in module._REQUIRED_PAYLOAD_PATHS:
            archive.writestr(f"{prefix}/{relative}", b"fixture")
        archive.writestr(f"{prefix}/wheelhouse/fixture.whl", b"wheel")
    extracted = module._safe_extract_payload(safe, tmp_path / "safe-extract", VERSION)
    assert extracted.joinpath("windows", "windows_installer.py").read_bytes() == b"fixture"


def test_public_windows_bootstrap_manifest_and_payload_use_product_user_agent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    builder, _build_fn = _load_unified_builder()
    module, _script = _load_public_bootstrap(builder, tmp_path)
    payload = b"download fixture\n"
    requests: list[tuple[str, str | None]] = []

    class _Response:
        status = 200

        def __init__(self) -> None:
            self._read = False

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, size: int = -1):
            if self._read:
                return b""
            self._read = True
            return payload

    def _urlopen(request, **_kwargs):
        requests.append((request.full_url, request.get_header("User-agent")))
        return _Response()

    monkeypatch.setattr(module.urllib.request, "urlopen", _urlopen)
    for name in ("manifest.json", "image-pptgen-contract.zip"):
        module._download_verified(
            f"https://payloads.example.test/{name}",
            tmp_path / name,
            expected_size=len(payload),
            expected_sha256=hashlib.sha256(payload).hexdigest(),
            max_bytes=1024,
        )

    assert [url for url, _user_agent in requests] == [
        "https://payloads.example.test/manifest.json",
        "https://payloads.example.test/image-pptgen-contract.zip",
    ]
    assert [user_agent for _url, user_agent in requests] == [
        f"ImagePPTGen-Installer/{VERSION}",
        f"ImagePPTGen-Installer/{VERSION}",
    ]


def test_toolkit_wheel_contains_only_python_sources_and_is_deterministic(
    tmp_path: Path,
) -> None:
    builder, _build_fn = _load_unified_builder()
    app_root = tmp_path / "app"
    source = app_root / "packages" / "pptgen_toolkit" / "src" / "pptgen_toolkit"
    (source / "__pycache__").mkdir(parents=True)
    (source / "nested" / "__pycache__").mkdir(parents=True)
    (source / "cli.py").write_text("def main():\n    return 0\n", encoding="utf-8")
    (source / "nested" / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    (source / "README.md").write_text("not a package source\n", encoding="utf-8")
    (source / "ignored.pyc").write_bytes(b"bytecode")
    (source / "__pycache__" / "cached.py").write_text(
        "should not ship\n", encoding="utf-8"
    )
    (source / "nested" / "__pycache__" / "module.cpython-314.pyc").write_bytes(
        b"bytecode"
    )

    first = builder._build_image_toolkit_wheel(app_root)
    first_bytes = first.read_bytes()
    with zipfile.ZipFile(first) as archive:
        names = archive.namelist()
        entry_points = archive.read(
            "image_pptgen_toolkit-0.1.0.dist-info/entry_points.txt"
        ).decode("utf-8")
    assert names == sorted(names)
    assert "pptgen_toolkit/cli.py" in names
    assert "pptgen_toolkit/nested/module.py" in names
    assert entry_points == (
        "[console_scripts]\n"
        "image-pptgen = pptgen_toolkit.image_cli:main\n"
    )
    assert all(not name.endswith((".pyc", ".md")) for name in names)
    assert all("__pycache__" not in name for name in names)

    second = builder._build_image_toolkit_wheel(app_root)
    assert second == first
    assert second.read_bytes() == first_bytes


def _stored_zip(path: Path, members: dict[str, bytes]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, payload in sorted(members.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, payload)


def _write_fake_macos_plutil(bin_dir: Path) -> None:
    """Emulate the narrow macOS ``plutil -extract … raw`` contract in Linux tests."""
    script = bin_dir / "plutil"
    script.write_text(
        f"#!{sys.executable}\n"
        "import json, sys\n"
        "args = sys.argv[1:]\n"
        "key = args[args.index('-extract') + 1]\n"
        "path = args[-1]\n"
        "with open(path, encoding='utf-8') as handle: value = json.load(handle)\n"
        "for part in key.split('.'):\n"
        "    if not isinstance(value, dict) or part not in value: raise SystemExit(1)\n"
        "    value = value[part]\n"
        "if isinstance(value, bool): print('true' if value else 'false')\n"
        "elif value is None: raise SystemExit(1)\n"
        "else: print(value)\n",
        encoding="utf-8",
    )
    script.chmod(0o755)


def _run_macos_native_case_collision(tmp_path: Path, *, collision: str):
    """Run only the Python-free macOS bootstrap phase against a bad archive."""
    builder, _build_fn = _load_unified_builder()
    version = f"1.2.3-macos-case-{collision}"
    web_root = tmp_path / "web"
    web_root.mkdir()
    bundle = tmp_path / f"image-pptgen-{version}"
    (bundle / "macos").mkdir(parents=True)
    (bundle / "wheelhouse").mkdir()
    license_zip = tmp_path / "licenses.zip"
    license_members = {"LICENSE.txt": b"license\n"}
    if collision == "zip":
        license_members["license.txt"] = b"case collision\n"
    _stored_zip(license_zip, license_members)
    license_bytes = license_zip.read_bytes()
    fallback_name = "cpython-fallback.tar.gz"
    fallback_bytes = b"fallback must never be downloaded by this rejection test\n"
    runtime_sha = hashlib.sha256(fallback_bytes).hexdigest()
    lock = {
        "schema_version": 1,
        "freeze_id": "fixture-case-fold-v1",
        "platforms": {
            "macos-arm64": {
                "runtime_asset": {
                    "filename": fallback_name,
                    "bytes": len(fallback_bytes),
                    "sha256": runtime_sha,
                },
                "python_json": {"python_exe": "python/install/bin/python3.11"},
                "license_bundle": {
                    "filename": license_zip.name,
                    "bytes": len(license_bytes),
                    "sha256": hashlib.sha256(license_bytes).hexdigest(),
                },
            }
        },
    }
    (bundle / "macos" / "fallback-lock.json").write_text(json.dumps(lock), encoding="utf-8")
    (bundle / "macos" / "installer.py").write_text("# unreachable\n", encoding="utf-8")
    (bundle / "licenses").mkdir()
    (bundle / "licenses" / license_zip.name).write_bytes(license_bytes)
    if collision == "tar":
        collision_root = bundle / "case-collision"
        collision_root.mkdir()
        (collision_root / "A.txt").write_text("upper\n", encoding="utf-8")
        (collision_root / "a.txt").write_text("lower\n", encoding="utf-8")

    archive_name = f"image-pptgen-{version}-macos-arm64.tar.gz"
    archive_path = web_root / archive_name
    with tarfile.open(archive_path, "w:gz") as archive:
        for path in sorted(bundle.rglob("*")):
            archive.add(path, arcname=path.relative_to(tmp_path).as_posix(), recursive=False)
    archive_bytes = archive_path.read_bytes()
    requests: list[str] = []

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(web_root), **kwargs)

        def do_GET(self):  # noqa: N802 - stdlib handler API
            requests.append(self.path)
            return super().do_GET()

        def log_message(self, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    manifest = {
        "schema_version": 2,
        "product": "image-pptgen",
        "version": version,
        "platforms": {
            "macos-arm64": {
                "archive": {
                    "name": archive_name,
                    "path": archive_name,
                    "url": f"{base_url}/{archive_name}",
                    "size": len(archive_bytes),
                    "sha256": hashlib.sha256(archive_bytes).hexdigest(),
                },
                "fallback_runtime": {
                    "name": fallback_name,
                    "path": fallback_name,
                    "url": f"{base_url}/{fallback_name}",
                    "size": len(fallback_bytes),
                    "sha256": runtime_sha,
                    "freeze_id": lock["freeze_id"],
                },
            }
        },
    }
    (web_root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    shell = tmp_path / "install.sh"
    shell.write_text(
        builder._render_shell_bootstrap(
            version=version,
            dist_base_url=base_url,
            manifest_url=f"{base_url}/manifest.json",
            r2_base_url=base_url,
        ),
        encoding="utf-8",
    )
    shell.chmod(0o755)
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    _write_fake_macos_plutil(fake_bin)
    (fake_bin / "uname").write_text(
        "#!/usr/bin/env bash\n"
        "if [ \"$1\" = '-s' ]; then printf 'Darwin\\n'; else printf 'arm64\\n'; fi\n",
        encoding="utf-8",
    )
    (fake_bin / "uname").chmod(0o755)
    environment = os.environ.copy()
    environment.update(
        {
            "HOME": str(tmp_path / "home"),
            "PATH": f"{fake_bin}:{environment['PATH']}",
            "CODEX_WORKSPACE_PYTHON": "",
        }
    )
    try:
        completed = subprocess.run(
            ["bash", str(shell)], env=environment, text=True, capture_output=True, check=False
        )
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()
    return completed, requests, fallback_name


def _fake_fallback_authority(tmp_path: Path, module) -> Path:
    assets = tmp_path / "fallback-assets"
    assets.mkdir(parents=True, exist_ok=True)
    platforms: dict[str, object] = {}
    for platform_id in ("windows-amd64", "macos-arm64"):
        wheel_name = f"image_contract_fixture-1.0-py3-none-any-{platform_id}.whl"
        wheel_payload = f"fixture wheel for {platform_id}\n".encode()
        wheelhouse_name = f"{platform_id}-wheelhouse.zip"
        license_name = f"{platform_id}-licenses.zip"
        runtime_name = f"{platform_id}-runtime.tar.gz"
        _stored_zip(assets / wheelhouse_name, {wheel_name: wheel_payload})
        _stored_zip(assets / license_name, {"LICENSE.txt": b"fixture license\n"})
        (assets / runtime_name).write_bytes(f"fixture runtime for {platform_id}\n".encode())

        def file_spec(name: str) -> dict[str, object]:
            path = assets / name
            return {
                "filename": name,
                "bytes": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }

        platforms[platform_id] = {
            "runtime_asset": file_spec(runtime_name),
            "wheelhouse_bundle": {**file_spec(wheelhouse_name), "count": 1},
            "license_bundle": {**file_spec(license_name), "member_count": 1},
            "wheels": [
                {
                    "filename": wheel_name,
                    "bytes": len(wheel_payload),
                    "sha256": hashlib.sha256(wheel_payload).hexdigest(),
                }
            ],
        }
    lock_path = tmp_path / "fallback-lock.json"
    lock_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "freeze_id": "test-multiplatform-fixture-v1",
                "platforms": platforms,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    module.FALLBACK_LOCK_PATH = lock_path
    return assets


def _build(tmp_path: Path, *, version: str = VERSION):
    module, build_multiplatform_release = _load_unified_builder()
    output_root = tmp_path / "pages-output"
    r2_root = tmp_path / "r2-payloads"
    ledger_path = tmp_path / "ledgers" / "payloads.json"
    fallback_assets_root = _fake_fallback_authority(tmp_path, module)
    try:
        result = build_multiplatform_release(
            ROOT,
            output_root,
            version=version,
            dist_base_url=DIST_BASE_URL,
            manifest_url=MANIFEST_URL,
            r2_root=r2_root,
            r2_prefix=R2_PREFIX,
            r2_base_url=R2_BASE_URL,
            r2_ledger_path=ledger_path,
            fallback_assets_root=fallback_assets_root,
        )
    except TypeError as exc:
        pytest.fail(
            "build_multiplatform_release must accept the parameterized "
            "distribution/R2 contract (dist_base_url, manifest_url, r2_root, "
            f"r2_prefix, r2_base_url, r2_ledger_path, fallback_assets_root): {exc}"
        )
    return result, output_root, r2_root, ledger_path


def _path_from_result(result, *names: str) -> Path | None:
    if isinstance(result, dict):
        values = result
    else:
        values = {name: getattr(result, name, None) for name in names}
    for name in names:
        value = values.get(name)
        if value:
            path = Path(value)
            if path.exists():
                return path
    return None


def _pages_root(result, output_root: Path) -> Path:
    from_result = _path_from_result(result, "pages_root", "pages_dir", "dist_dir")
    if from_result is not None and from_result.is_dir():
        return from_result
    for candidate in (output_root / "pages", output_root / "pages-dist"):
        if candidate.is_dir():
            return candidate
    pytest.fail(
        "unified builder must expose or create a Pages-only tree at "
        "result.pages_root/pages_dir or output_root/pages(-dist)"
    )


def _manifest_path(result, pages_root: Path, version: str) -> Path:
    from_result = _path_from_result(result, "manifest_path")
    if from_result is not None:
        return from_result
    candidates = sorted(
        path
        for path in pages_root.rglob("manifest.json")
        if path.is_file() and "reports" not in path.parts
    )
    candidates = [
        path
        for path in candidates
        if version in path.parts or path.parent.name == version
    ] or candidates
    if len(candidates) != 1:
        pytest.fail(
            "unified builder must produce one aggregate release manifest.json "
            f"under the Pages tree; found {candidates}"
        )
    return candidates[0]


def _manifest_and_context(tmp_path: Path, *, version: str = VERSION):
    result, output_root, r2_root, ledger_path = _build(tmp_path, version=version)
    pages_root = _pages_root(result, output_root)
    manifest_path = _manifest_path(result, pages_root, version)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        pytest.fail(f"aggregate release manifest is unreadable: {exc}")
    return result, output_root, pages_root, manifest_path, manifest, r2_root, ledger_path


def _platform_entries(manifest: dict[str, object]) -> dict[str, dict[str, object]]:
    entries = manifest.get("platforms")
    assert isinstance(entries, dict), (
        "aggregate manifest must expose a 'platforms' object keyed by the "
        "three canonical platform IDs"
    )
    return entries  # type: ignore[return-value]


def _archive_path(r2_root: Path, entry: dict[str, object]) -> Path:
    archive = entry.get("archive")
    assert isinstance(archive, dict), "manifest platform entry must contain archive metadata"
    name = archive.get("name")
    assert isinstance(name, str) and name, "manifest archive.name must be non-empty"
    candidates = sorted(path for path in r2_root.rglob(name) if path.is_file())
    assert len(candidates) == 1, (
        f"R2 payload must contain exactly one archive named {name!r}; "
        f"found {candidates}"
    )
    return candidates[0]


def _archive_members(path: Path) -> list[str]:
    if path.name.endswith(".zip"):
        with zipfile.ZipFile(path) as archive:
            return archive.namelist()
    if path.name.endswith(".tar.gz"):
        with tarfile.open(path, "r:gz") as archive:
            return archive.getnames()
    pytest.fail(f"unsupported platform archive format: {path.name}")


def _archive_file(path: Path, member_name: str) -> bytes:
    if path.name.endswith(".zip"):
        with zipfile.ZipFile(path) as archive:
            return archive.read(member_name)
    with tarfile.open(path, "r:gz") as archive:
        member = archive.extractfile(member_name)
        assert member is not None, f"archive member is not readable: {member_name}"
        return member.read()


def _archive_files(path: Path):
    if path.name.endswith(".zip"):
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                if not info.is_dir():
                    yield info.filename, archive.read(info)
        return
    with tarfile.open(path, "r:gz") as archive:
        for member in archive.getmembers():
            if not member.isfile():
                continue
            source = archive.extractfile(member)
            assert source is not None, f"archive member is not readable: {member.name}"
            yield member.name, source.read()


def _identity(entry: dict[str, object]) -> dict[str, object]:
    identity = entry.get("identity")
    assert isinstance(identity, dict), "manifest platform entry must contain identity"
    assert entry.get("build_id") == identity.get("build_id"), (
        "manifest platform entry must expose build_id bound to its release identity"
    )
    return identity  # type: ignore[return-value]


def _entry_archive_metadata(entry: dict[str, object]) -> dict[str, object]:
    archive = entry.get("archive")
    assert isinstance(archive, dict), "manifest platform entry must contain archive"
    for field in ("name", "path", "sha256", "size"):
        assert field in archive, f"manifest archive metadata is missing {field!r}"
    return archive  # type: ignore[return-value]


def _text_files(root: Path):
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        try:
            yield path, path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _ledger_entries(ledger: dict[str, object]) -> list[dict[str, object]]:
    entries = ledger.get("entries")
    assert isinstance(entries, list), "R2 payload ledger must expose an entries list"
    assert all(isinstance(item, dict) for item in entries), (
        "R2 payload ledger entries must be objects"
    )
    return entries  # type: ignore[return-value]


def test_unified_manifest_has_exact_three_platform_targets(tmp_path: Path) -> None:
    _result, _output, _pages, _manifest_path, manifest, _r2, _ledger = _manifest_and_context(
        tmp_path
    )

    entries = _platform_entries(manifest)
    assert tuple(entries) == TARGET_PLATFORMS
    assert set(entries) == set(TARGET_PLATFORMS)
    assert manifest["version"] == VERSION


def test_auto_select_maps_only_supported_targets_and_fails_closed_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    builder, _build_fn = _load_unified_builder()
    selector = getattr(builder, "select_platform", None)
    assert callable(selector), (
        "unified builder must expose select_platform(system, machine, "
        "user_platform=None) for non-interactive auto-selection"
    )

    assert selector(system="Linux", machine="x86_64") == "linux-x86_64"
    assert selector(system="Windows", machine="AMD64") == "windows-amd64"
    assert selector(system="Darwin", machine="arm64") == "macos-arm64"

    monkeypatch.setattr(
        builtins,
        "input",
        lambda *_args, **_kwargs: pytest.fail(
            "unknown platform must fail closed without asking the user to choose"
        ),
    )
    with pytest.raises(Exception, match=r"(?i)(unsupported|unknown|platform|arch)"):
        selector(system="Solaris", machine="sparc")


def test_each_platform_archive_is_single_root_identity_bound_and_contract_complete(
    tmp_path: Path,
) -> None:
    _result, _output, _pages, _manifest_path, manifest, r2_root, _ledger = _manifest_and_context(
        tmp_path
    )
    entries = _platform_entries(manifest)
    current_cli = (
        ROOT / "packages" / "pptgen_toolkit" / "src" / "pptgen_toolkit" / "image_cli.py"
    ).read_bytes()
    current_bundle = (
        ROOT
        / "packages"
        / "pptgen_toolkit"
        / "src"
        / "pptgen_toolkit"
        / "static_preview_bundle.py"
    ).read_bytes()
    current_skill = (
        ROOT / "skills" / "generate-image-presentation" / "SKILL.md"
    ).read_bytes()

    common_members = {
        "app/runtime_manager.py",
        "app/image-launcher.py",
        "app/release-identity.json",
        "app/skills/generate-image-presentation/SKILL.md",
        "app/skills/generate-image-presentation/scripts/image-pptgen-dispatch",
        "app/skills/generate-image-presentation/scripts/image-pptgen-dispatch.ps1",
    }
    platform_members = {
        "linux-x86_64": {
            "linux/requirements.lock",
            "app/image-pptgen-wrapper.sh",
            "app/image-pptgen-server-wrapper.sh",
        },
        "windows-amd64": {
            "windows/requirements.lock",
            "windows/fallback-lock.json",
            "windows/install.ps1",
            "windows/windows_installer.py",
            "licenses/windows-amd64-licenses.zip",
            "app/packages/pptgen_toolkit/dist/image_pptgen_toolkit-0.1.0-py3-none-any.whl",
        },
        "macos-arm64": {
            "macos/requirements.lock",
            "macos/installer.py",
            "macos/image-pptgen-wrapper.sh",
            "macos/image-pptgen-server-wrapper.sh",
            "macos/image-pptgen-held-command.sh",
            "app/packages/pptgen_toolkit/dist/image_pptgen_toolkit-0.1.0-py3-none-any.whl",
        },
    }

    for platform_id in TARGET_PLATFORMS:
        entry = entries[platform_id]
        metadata = _entry_archive_metadata(entry)
        archive_path = _archive_path(r2_root, entry)
        names = _archive_members(archive_path)
        assert names == sorted(names), f"archive members are not deterministically sorted: {platform_id}"
        roots = {PurePosixPath(name).parts[0] for name in names if name}
        assert roots == {f"image-pptgen-{VERSION}"}

        root = f"image-pptgen-{VERSION}/"
        relative_members = {
            name[len(root) :]
            for name in names
            if name.startswith(root) and name != root.rstrip("/")
        }
        assert common_members | platform_members[platform_id] <= relative_members
        packaged_skill = _archive_file(
            archive_path, f"{root}app/skills/generate-image-presentation/SKILL.md"
        )
        assert packaged_skill == current_skill, f"Skill bytes drifted: {platform_id}"
        skill_text = packaged_skill.decode("utf-8")
        assert "On Linux and Windows, run:" not in skill_text
        assert "<dispatcher> result --run-id <run_id> --json" in skill_text
        assert "file:" in skill_text
        assert "do not present a loopback Preview or download URL" in skill_text
        if platform_id == "linux-x86_64":
            packaged_cli = _archive_file(
                archive_path,
                f"{root}app/packages/pptgen_toolkit/src/pptgen_toolkit/image_cli.py",
            )
            packaged_bundle = _archive_file(
                archive_path,
                f"{root}app/packages/pptgen_toolkit/src/pptgen_toolkit/static_preview_bundle.py",
            )
            assert packaged_cli == current_cli
            assert packaged_bundle == current_bundle
        if platform_id == "windows-amd64":
            assert any(
                name.startswith(f"{root}wheelhouse/") and name.endswith(".whl")
                for name in names
            ), "Windows payload must include at least one locked wheelhouse wheel"
        if platform_id in {"windows-amd64", "macos-arm64"}:
            toolkit_wheels = [
                name
                for name in names
                if name.startswith(
                    f"{root}app/packages/pptgen_toolkit/dist/"
                )
                and name.endswith(".whl")
            ]
            assert toolkit_wheels == [
                f"{root}app/packages/pptgen_toolkit/dist/"
                "image_pptgen_toolkit-0.1.0-py3-none-any.whl"
            ]
            with zipfile.ZipFile(io.BytesIO(_archive_file(archive_path, toolkit_wheels[0]))) as wheel:
                assert wheel.namelist() == sorted(wheel.namelist())
                assert (
                    "image_pptgen_toolkit-0.1.0.dist-info/RECORD" in wheel.namelist()
                )
                assert wheel.read("pptgen_toolkit/image_cli.py") == current_cli
                assert (
                    wheel.read("pptgen_toolkit/static_preview_bundle.py") == current_bundle
                )

        identity = json.loads(
            _archive_file(archive_path, f"image-pptgen-{VERSION}/app/release-identity.json")
        )
        assert identity["platform"] == platform_id
        assert identity["version"] == VERSION
        assert identity["build_id"] == _identity(entry)["build_id"]

        archive_bytes = archive_path.read_bytes()
        assert metadata["name"] == archive_path.name
        assert metadata["sha256"] == hashlib.sha256(archive_bytes).hexdigest()
        assert metadata["size"] == len(archive_bytes)
        assert metadata["url"] == (
            f"{R2_BASE_URL}/{metadata['path']}"
        )
        assert _identity(entry)["version"] == VERSION


def test_multiplatform_archives_preserve_posix_dispatcher_executable_metadata(
    tmp_path: Path,
) -> None:
    _result, _output, _pages, _manifest_path, manifest, r2_root, _ledger = _manifest_and_context(
        tmp_path
    )
    member_name = (
        f"image-pptgen-{VERSION}/app/skills/"
        "generate-image-presentation/scripts/image-pptgen-dispatch"
    )
    entries = _platform_entries(manifest)

    for platform_id in ("linux-x86_64", "macos-arm64"):
        archive_path = _archive_path(r2_root, entries[platform_id])
        with tarfile.open(archive_path, "r:gz") as archive:
            member = archive.getmember(member_name)
        assert member.isfile()
        assert stat.S_IMODE(member.mode) == 0o755

    windows_archive = _archive_path(r2_root, entries["windows-amd64"])
    with zipfile.ZipFile(windows_archive) as archive:
        info = archive.getinfo(member_name)
    posix_mode = (info.external_attr >> 16) & 0xFFFF
    assert stat.S_ISREG(posix_mode)
    assert stat.S_IMODE(posix_mode) == 0o755


def test_generated_platform_archives_pass_their_native_payload_validators(tmp_path: Path) -> None:
    _result, _output, _pages, _manifest_path, manifest, r2_root, _ledger = _manifest_and_context(
        tmp_path
    )
    entries = _platform_entries(manifest)

    windows = _load_module(
        ROOT / "packaging" / "image" / "platform" / "windows" / "windows_installer.py",
        "image_windows_installer_payload_contract",
    )
    windows_entry = entries["windows-amd64"]
    windows_meta = _entry_archive_metadata(windows_entry)
    windows_archive = _archive_path(r2_root, windows_entry)
    windows_contract = windows.validate_payload(
        windows_archive,
        expected_size=windows_meta["size"],
        expected_sha256=windows_meta["sha256"],
        version=VERSION,
    )
    with tempfile.TemporaryDirectory(prefix="image-windows-payload-contract-") as temporary:
        extracted = windows._extract_payload(
            windows_archive, Path(temporary) / "extract", windows_contract
        )
        assert (extracted / "app" / "release-identity.json").is_file()

    macos = _load_module(
        ROOT / "packaging" / "image" / "platform" / "macos" / "installer.py",
        "image_macos_installer_payload_contract",
    )
    macos_entry = entries["macos-arm64"]
    macos_meta = _entry_archive_metadata(macos_entry)
    macos_archive = _archive_path(r2_root, macos_entry)
    macos_manifest = macos.ReleaseManifest(
        VERSION,
        "macos-arm64",
        macos.ArchiveSpec(
            macos_meta["name"], macos_meta["sha256"], macos_meta["size"]
        ),
    )
    macos.verify_archive(macos_manifest, macos_archive)
    with tempfile.TemporaryDirectory(prefix="image-macos-payload-contract-") as temporary:
        extracted = macos.safe_extract_archive(macos_archive, Path(temporary) / "extract")
        assert (extracted / "app" / "release-identity.json").is_file()


def test_platform_payloads_have_no_stale_distribution_template(tmp_path: Path) -> None:
    _result, _output, _pages, _manifest_path, manifest, r2_root, _ledger = _manifest_and_context(
        tmp_path
    )
    forbidden = (*OLD_DISTRIBUTION_URLS, "__VERSION__")
    for platform_id, entry in _platform_entries(manifest).items():
        archive_path = _archive_path(r2_root, entry)
        for member_name, payload in _archive_files(archive_path):
            try:
                text = payload.decode("utf-8")
            except UnicodeDecodeError:
                continue
            for token in forbidden:
                assert token not in text, (
                    f"{platform_id} payload retains stale installer token {token!r}: "
                    f"{member_name}"
                )


def test_manifest_metadata_is_unique_and_same_version_is_immutable(tmp_path: Path) -> None:
    _result, output_root, _pages, _manifest_path, manifest, r2_root, _ledger = _manifest_and_context(
        tmp_path
    )
    entries = _platform_entries(manifest)
    metadata_tuples = []
    archive_paths = []
    for platform_id in TARGET_PLATFORMS:
        entry = entries[platform_id]
        metadata = _entry_archive_metadata(entry)
        identity = _identity(entry)
        metadata_tuples.append(
            (
                metadata["name"],
                metadata["sha256"],
                metadata["size"],
                identity["version"],
                identity["build_id"],
            )
        )
        archive_paths.append(_archive_path(r2_root, entry))
    assert len(set(metadata_tuples)) == len(TARGET_PLATFORMS)
    assert len({item[0] for item in metadata_tuples}) == len(TARGET_PLATFORMS)
    assert len({item[1] for item in metadata_tuples}) == len(TARGET_PLATFORMS)
    assert len({item[4] for item in metadata_tuples}) == len(TARGET_PLATFORMS)

    tampered = archive_paths[0]
    original = tampered.read_bytes()
    tampered.write_bytes(original + b"\ncontract tamper\n")
    with pytest.raises(
        Exception,
        match=r"(?i)(immutable|conflict|overwrite|already|existing|sha.?256|manifest|version)",
    ):
        _build(tmp_path, version=VERSION)
    assert tampered.read_bytes() == original + b"\ncontract tamper\n"
    assert output_root.exists()


def test_pages_are_lightweight_and_r2_paths_ledger_are_parameterized(tmp_path: Path) -> None:
    _result, _output, pages_root, _manifest_path, manifest, r2_root, ledger_path = _manifest_and_context(
        tmp_path
    )
    assert r2_root == tmp_path / "r2-payloads"
    assert ledger_path == tmp_path / "ledgers" / "payloads.json"
    assert ledger_path.is_file()

    for path in pages_root.rglob("*"):
        if path.is_file():
            assert path.stat().st_size < PAGES_FILE_LIMIT, path

    for path, _text in _text_files(pages_root):
        assert path.suffix.lower() not in {".whl", ".zip", ".gz", ".tar"}
        assert not any(
            token in path.name.casefold()
            for token in ("runtime", "wheelhouse", "license")
        )
    page_paths = {
        path.relative_to(pages_root).as_posix() for path in pages_root.rglob("*") if path.is_file()
    }
    assert not any(path.endswith((".zip", ".tar.gz", ".whl")) for path in page_paths)
    assert not any(
        token in path.casefold() for path in page_paths for token in ("wheelhouse", "license")
    )
    preferred = pages_root / "install.py"
    versioned = pages_root / "releases" / VERSION / "install.py"
    assert preferred.is_file()
    assert versioned.is_file()
    assert preferred.read_bytes() == versioned.read_bytes()
    assert "--bootstrap-python" in preferred.read_text(encoding="utf-8")
    assert "--managed-python" not in preferred.read_text(encoding="utf-8")

    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    ledger_entries = _ledger_entries(ledger)
    ledger_paths = [entry.get("path") for entry in ledger_entries]
    assert ledger_paths == sorted(ledger_paths)
    assert all(isinstance(path, str) and path.startswith(f"{R2_PREFIX}/") for path in ledger_paths)
    manifest_paths = {
        _entry_archive_metadata(entry).get("path")
        for entry in _platform_entries(manifest).values()
    }
    assert manifest_paths <= set(ledger_paths)


def test_two_builds_are_byte_identical_and_manifest_order_is_stable(tmp_path: Path) -> None:
    first, first_output, first_pages, first_manifest_path, first_manifest, first_r2, first_ledger = (
        _manifest_and_context(tmp_path / "first")
    )
    second, second_output, second_pages, second_manifest_path, second_manifest, second_r2, second_ledger = (
        _manifest_and_context(tmp_path / "second")
    )

    assert first_manifest_path.read_bytes() == second_manifest_path.read_bytes()
    assert first_manifest == second_manifest
    assert _snapshot(first_pages) == _snapshot(second_pages)
    assert _snapshot(first_r2) == _snapshot(second_r2)
    assert first_ledger.read_bytes() == second_ledger.read_bytes()

    assert tuple(_platform_entries(first_manifest)) == TARGET_PLATFORMS
    assert list(_platform_entries(first_manifest)) == sorted(_platform_entries(first_manifest))
    for platform_id, entry in _platform_entries(first_manifest).items():
        archive_name = _entry_archive_metadata(entry)["name"]
        assert archive_name == _entry_archive_metadata(_platform_entries(second_manifest)[platform_id])["name"]


def test_custom_distribution_and_manifest_urls_are_baked_without_old_pages_fallback(
    tmp_path: Path,
) -> None:
    _result, _output, pages_root, manifest_path, manifest, r2_root, _ledger = _manifest_and_context(
        tmp_path
    )
    generated_text = []
    for path, text in _text_files(pages_root):
        generated_text.append((path, text))
    for path in sorted(r2_root.rglob("*")):
        if not path.is_file() or path.suffix.lower() in {".zip", ".gz", ".whl"}:
            continue
        try:
            generated_text.append((path, path.read_text(encoding="utf-8")))
        except UnicodeDecodeError:
            continue

    joined = "\n".join(text for _path, text in generated_text)
    assert DIST_BASE_URL in joined
    assert MANIFEST_URL in joined
    assert R2_BASE_URL in joined
    installer_text = "\n".join(
        text
        for path, text in generated_text
        if any(token in path.name.casefold() for token in ("install", "bootstrap"))
    )
    assert installer_text, "generated output must contain a rendered installer/bootstrap"
    assert DIST_BASE_URL in installer_text
    assert MANIFEST_URL in installer_text
    assert R2_BASE_URL in installer_text
    for old_url in OLD_DISTRIBUTION_URLS:
        assert old_url not in joined, f"generated installer fell back to old fixed URL: {old_url}"
    assert manifest.get("manifest_url") == MANIFEST_URL
    assert manifest_path.is_file()


def test_rendered_bootstraps_quote_distribution_urls_without_command_substitution(
    tmp_path: Path,
) -> None:
    builder, build_multiplatform_release = _load_unified_builder()
    output_root = tmp_path / "pages-output"
    marker = tmp_path / "url-command-substitution-must-not-run"
    hostile_dist = f"https://dist.example.test/$(touch$IFS{marker.as_posix()})"
    assets = _fake_fallback_authority(tmp_path, builder)
    result = build_multiplatform_release(
        ROOT,
        output_root,
        version=VERSION,
        dist_base_url=hostile_dist,
        manifest_url=MANIFEST_URL,
        r2_root=tmp_path / "r2",
        r2_prefix=R2_PREFIX,
        r2_base_url=R2_BASE_URL,
        r2_ledger_path=tmp_path / "ledger.json",
        fallback_assets_root=assets,
    )
    pages_root = _pages_root(result, output_root)
    shell = pages_root / "install.sh"
    completed = subprocess.run(
        ["bash", str(shell)], capture_output=True, text=True, check=False
    )
    assert completed.returncode != 0
    assert hostile_dist in shell.read_text(encoding="utf-8")
    assert not marker.exists(), "rendered shell URL executed command substitution"

    powershell = (pages_root / "install.ps1").read_text(encoding="utf-8")
    assert f"$DistBaseUrl = '{hostile_dist}'" in powershell


def test_linux_bootstrap_downloads_manifest_and_archive_over_local_http(
    tmp_path: Path,
) -> None:
    """Exercise the generated bootstrap's real HTTP, hash, and install chain."""

    builder, _build_fn = _load_unified_builder()
    version = "1.2.3-http"
    web_root = tmp_path / "web"
    web_root.mkdir()
    archive_name = f"image-pptgen-{version}-linux-x86_64.tar.gz"
    bundle = tmp_path / f"image-pptgen-{version}"
    identity = {
        "build_id": "fixture-build-http",
        "version": version,
        "platform": "linux-x86_64",
    }
    (bundle / "app" / "skills" / "generate-image-presentation").mkdir(parents=True)
    (bundle / "app" / "packages" / "pptgen_toolkit").mkdir(parents=True)
    (bundle / "app" / "requirements.txt").write_text("", encoding="utf-8")
    (bundle / "app" / "image-pptgen-wrapper.sh").write_text(
        "#!/usr/bin/env bash\nexit 0\n", encoding="utf-8"
    )
    (bundle / "app" / "image-pptgen-server-wrapper.sh").write_text(
        "#!/usr/bin/env bash\nexit 0\n", encoding="utf-8"
    )
    (bundle / "app" / "runtime_manager.py").write_text(
        "import json,sys\n"
        "if sys.argv[1:2] == ['ensure-ready']:\n"
        " print(json.dumps({'ok': True, 'base_url': 'http://127.0.0.1:3130', 'version': '1.2.3-http', 'build_id': 'fixture-build-http', 'instance_id': 'fixture'}))\n",
        encoding="utf-8",
    )
    (bundle / "app" / "release-identity.json").write_text(
        json.dumps(identity), encoding="utf-8"
    )
    (bundle / "app" / "skills" / "generate-image-presentation" / "SKILL.md").write_text(
        "# fixture\n", encoding="utf-8"
    )
    with tarfile.open(web_root / archive_name, "w:gz") as archive:
        for path in sorted(bundle.rglob("*")):
            archive.add(
                path,
                arcname=path.relative_to(tmp_path).as_posix(),
                recursive=False,
            )
    archive_path = web_root / archive_name
    archive_bytes = archive_path.read_bytes()
    manifest = {
        "schema_version": 2,
        "product": "image-pptgen",
        "version": version,
        "platforms": {
            "linux-x86_64": {
                "archive": {
                    "name": archive_name,
                    "path": archive_name,
                    "url": "__ARCHIVE_URL__",
                    "size": len(archive_bytes),
                    "sha256": hashlib.sha256(archive_bytes).hexdigest(),
                }
            }
        },
    }

    requests: list[str] = []

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(web_root), **kwargs)

        def do_GET(self):  # noqa: N802 - stdlib handler API
            requests.append(self.path)
            return super().do_GET()

        def log_message(self, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    manifest["platforms"]["linux-x86_64"]["archive"]["url"] = f"{base_url}/{archive_name}"
    (web_root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    output = tmp_path / "pages"
    pages = output / "pages-dist"
    pages.mkdir(parents=True)
    shell = pages / "install.sh"
    shell.write_text(
        builder._render_shell_bootstrap(
            version=version,
            dist_base_url=base_url,
            manifest_url=f"{base_url}/manifest.json",
            r2_base_url=base_url,
        ),
        encoding="utf-8",
    )
    shell.chmod(0o755)
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    (fake_bin / "codex").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    (fake_bin / "fc-list").write_text("#!/usr/bin/env bash\nprintf 'Noto Sans CJK\n'\n", encoding="utf-8")
    for path in (fake_bin / "codex", fake_bin / "fc-list"):
        path.chmod(0o755)
    home = tmp_path / "home"
    data_root = home / ".local" / "share"
    install_id = f"{version}-{hashlib.sha256(archive_bytes).hexdigest()[:12]}"
    venv_bin = data_root / "image-pptgen" / "venvs" / install_id / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "image-pptgen").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    (venv_bin / "python").write_text(
        "#!/usr/bin/env python3\n"
        "import json\n"
        "print(json.dumps({'ok': True, 'base_url': 'http://127.0.0.1:3130', 'version': '1.2.3-http', 'build_id': 'fixture-build-http', 'instance_id': 'fixture'}))\n",
        encoding="utf-8",
    )
    (venv_bin / "image-pptgen").chmod(0o755)
    (venv_bin / "python").chmod(0o755)
    environment = os.environ.copy()
    environment.update(
        {
            "HOME": str(home),
            "XDG_DATA_HOME": str(data_root),
            "XDG_CONFIG_HOME": str(home / ".config"),
            "PATH": f"{fake_bin}:{environment['PATH']}",
            "CODEX_WORKSPACE_PYTHON": sys.executable,
        }
    )
    try:
        completed = subprocess.run(
            ["bash", str(shell)],
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()
    assert completed.returncode == 0, completed.stderr
    assert requests == ["/manifest.json", f"/{archive_name}"]
    assert not any("fallback" in path for path in requests)
    assert (data_root / "image-pptgen" / "current" / "app" / "release-identity.json").is_file()


def test_rendered_shell_bootstrap_fails_closed_for_unknown_platform(tmp_path: Path) -> None:
    builder, _build_fn = _load_unified_builder()
    shell = tmp_path / "install.sh"
    shell.write_text(
        builder._render_shell_bootstrap(
            version=VERSION,
            dist_base_url="http://127.0.0.1:1",
            manifest_url="http://127.0.0.1:1/manifest.json",
            r2_base_url="http://127.0.0.1:1",
        ),
        encoding="utf-8",
    )
    shell.chmod(0o755)
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    (fake_bin / "uname").write_text(
        "#!/usr/bin/env bash\n"
        "if [ \"$1\" = '-s' ]; then printf 'Solaris\\n'; else printf 'sparc\\n'; fi\n",
        encoding="utf-8",
    )
    (fake_bin / "uname").chmod(0o755)
    environment = os.environ.copy()
    environment.update(
        {
            "PATH": f"{fake_bin}:{environment['PATH']}",
            "CODEX_WORKSPACE_PYTHON": sys.executable,
        }
    )
    completed = subprocess.run(
        ["bash", str(shell)], env=environment, text=True, capture_output=True, check=False
    )
    assert completed.returncode != 0
    assert "Unsupported Image PPTGen platform" in completed.stderr


@pytest.mark.parametrize("installer_exit", [0, 23])
def test_macos_dynamic_failure_then_known_managed_installer_result_is_preserved(
    tmp_path: Path, installer_exit: int
) -> None:
    builder, _build_fn = _load_unified_builder()
    version = "1.2.3-macos-bootstrap"
    web_root = tmp_path / "web"
    web_root.mkdir()
    bundle = tmp_path / f"image-pptgen-{version}"
    (bundle / "app" / "skills" / "generate-image-presentation").mkdir(parents=True)
    (bundle / "wheelhouse").mkdir()
    (bundle / "macos").mkdir()
    license_payload = b"license extracted by bootstrap\n"
    license_zip = tmp_path / "macos-arm64-licenses.zip"
    _stored_zip(license_zip, {"LICENSE.txt": license_payload})
    license_bytes = license_zip.read_bytes()
    fallback_bytes = b"fallback runtime must not be downloaded on official success\n"
    fallback_name = "cpython-fixture.tar.gz"
    runtime_sha = hashlib.sha256(fallback_bytes).hexdigest()
    lock = {
        "schema_version": 1,
        "freeze_id": "fixture-freeze-v1",
        "platforms": {
            "macos-arm64": {
                "runtime_asset": {
                    "filename": fallback_name,
                    "bytes": len(fallback_bytes),
                    "sha256": runtime_sha,
                },
                "python_json": {"python_exe": "python/install/bin/python3.11"},
                "license_bundle": {
                    "filename": license_zip.name,
                    "bytes": len(license_bytes),
                    "sha256": hashlib.sha256(license_bytes).hexdigest(),
                },
            }
        },
    }
    (bundle / "macos" / "fallback-lock.json").write_text(
        json.dumps(lock), encoding="utf-8"
    )
    (bundle / "licenses").mkdir()
    (bundle / "licenses" / license_zip.name).write_bytes(license_bytes)
    (bundle / "macos" / "installer.py").write_text("# fake installer\n", encoding="utf-8")
    archive_name = f"image-pptgen-{version}-macos-arm64.tar.gz"
    archive_path = web_root / archive_name
    with tarfile.open(archive_path, "w:gz") as archive:
        for path in sorted(bundle.rglob("*")):
            archive.add(
                path,
                arcname=path.relative_to(tmp_path).as_posix(),
                recursive=False,
            )
    archive_bytes = archive_path.read_bytes()
    fallback_path = web_root / fallback_name
    fallback_path.write_bytes(fallback_bytes)
    requests: list[str] = []

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(web_root), **kwargs)

        def do_GET(self):  # noqa: N802 - stdlib handler API
            requests.append(self.path)
            return super().do_GET()

        def log_message(self, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    manifest = {
        "schema_version": 2,
        "product": "image-pptgen",
        "version": version,
        "platforms": {
            "macos-arm64": {
                "archive": {
                    "name": archive_name,
                    "path": archive_name,
                    "url": f"{base_url}/{archive_name}",
                    "size": len(archive_bytes),
                    "sha256": hashlib.sha256(archive_bytes).hexdigest(),
                },
                "fallback_runtime": {
                    "name": fallback_name,
                    "path": fallback_name,
                    "url": f"{base_url}/{fallback_name}",
                    "size": len(fallback_bytes),
                    "sha256": runtime_sha,
                    "freeze_id": lock["freeze_id"],
                },
            }
        },
    }
    (web_root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    shell = tmp_path / "install.sh"
    shell.write_text(
        builder._render_shell_bootstrap(
            version=version,
            dist_base_url=base_url,
            manifest_url=f"{base_url}/manifest.json",
            r2_base_url=base_url,
        ),
        encoding="utf-8",
    )
    shell.chmod(0o755)
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    _write_fake_macos_plutil(fake_bin)
    (fake_bin / "uname").write_text(
        "#!/usr/bin/env bash\n"
        "if [ \"$1\" = '-s' ]; then printf 'Darwin\\n'; else printf 'arm64\\n'; fi\n",
        encoding="utf-8",
    )
    fake_python = fake_bin / "official-python"
    fake_python.write_text(
        "#!/usr/bin/env python3\n"
        "import os, subprocess, sys\n"
        "real = os.environ['REAL_PYTHON']\n"
        "if len(sys.argv) >= 3 and sys.argv[1] == '-I' and sys.argv[2] == '-c' and 'platform.machine' in sys.argv[3]:\n"
        "    raise SystemExit(0)\n"
        "if len(sys.argv) >= 4 and sys.argv[1] == '-I' and sys.argv[2] == '-m' and sys.argv[3] == 'venv':\n"
        "    raise SystemExit(1)\n"
        "raise SystemExit(subprocess.call([real, *sys.argv[1:]]))\n",
        encoding="utf-8",
    )
    known_python = (
        tmp_path
        / "home"
        / ".cache"
        / "codex-runtimes"
        / "codex-primary-runtime"
        / "dependencies"
        / "python"
        / "bin"
        / "python3"
    )
    known_python.parent.mkdir(parents=True)
    known_python.write_text(
        f"#!{sys.executable}\n"
        "import os, subprocess, sys\n"
        "real = os.environ['REAL_PYTHON']\n"
        "if len(sys.argv) >= 3 and sys.argv[1] == '-I' and sys.argv[2] == '-c' and 'platform.machine' in sys.argv[3]:\n"
        "    raise SystemExit(0)\n"
        "if len(sys.argv) >= 4 and sys.argv[1] == '-I' and sys.argv[2] == '-m':\n"
        "    raise SystemExit(0)\n"
        "for index, value in enumerate(sys.argv[1:]):\n"
        "    if value.endswith('/macos/installer.py'):\n"
        "        args = sys.argv[index + 2:]\n"
        "        license_dir = args[args.index('--license-dir') + 1]\n"
        "        with open(os.environ['FAKE_INSTALL_RECORD'], 'w', encoding='utf-8') as output:\n"
        "            output.write(open(os.path.join(license_dir, 'LICENSE.txt'), encoding='utf-8').read())\n"
        "        with open(os.environ['FAKE_INSTALL_ARGS'], 'w', encoding='utf-8') as output:\n"
        "            output.write('\\n'.join(args))\n"
        f"        if {installer_exit}:\n"
        "            sys.stderr.write('inner installer failure\\n')\n"
        f"            raise SystemExit({installer_exit})\n"
        "        raise SystemExit(0)\n"
        "raise SystemExit(subprocess.call([real, *sys.argv[1:]]))\n",
        encoding="utf-8",
    )
    fake_bin.joinpath("uname").chmod(0o755)
    fake_python.chmod(0o755)
    known_python.chmod(0o755)
    record = tmp_path / "license-record.txt"
    install_args = tmp_path / "installer-args.txt"
    environment = os.environ.copy()
    environment.update(
        {
            "HOME": str(tmp_path / "home"),
            "PATH": f"{fake_bin}:{environment['PATH']}",
            "CODEX_WORKSPACE_PYTHON": str(fake_python),
            "REAL_PYTHON": sys.executable,
            "FAKE_INSTALL_RECORD": str(record),
            "FAKE_INSTALL_ARGS": str(install_args),
        }
    )
    try:
        completed = subprocess.run(
            ["bash", str(shell)], env=environment, text=True, capture_output=True, check=False
        )
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()
    if installer_exit == 0:
        assert completed.returncode == 0, completed.stderr
    else:
        assert completed.returncode == installer_exit
        assert "inner installer failure" in completed.stderr
        assert (
            "Official Runtime passed its probe but the platform installer failed"
            in completed.stderr
        )
    assert requests == ["/manifest.json", f"/{archive_name}"]
    assert record.read_text(encoding="utf-8") == license_payload.decode()
    args = install_args.read_text(encoding="utf-8").splitlines()
    assert args[args.index("--install-root") + 1] == str(
        tmp_path / "home" / ".codex" / "image-pptgen"
    )
    assert args[args.index("--bin-home") + 1] == str(tmp_path / "home" / ".codex" / "bin")
    assert args[args.index("--skill-home") + 1] == str(
        tmp_path / "home" / ".codex" / "skills"
    )


def test_macos_bootstrap_requires_two_official_failures_before_fallback(
    tmp_path: Path,
) -> None:
    builder, _build_fn = _load_unified_builder()
    version = "1.2.3-macos-fallback"
    web_root = tmp_path / "web"
    web_root.mkdir()
    bundle = tmp_path / f"image-pptgen-{version}"
    (bundle / "macos").mkdir(parents=True)
    (bundle / "wheelhouse").mkdir()
    license_payload = b"fallback license\n"
    license_zip = tmp_path / "licenses.zip"
    _stored_zip(license_zip, {"LICENSE.txt": license_payload})
    license_bytes = license_zip.read_bytes()
    fallback_python_payload = (
        f"#!{sys.executable}\n"
        "import json, os, subprocess, sys\n"
        "from pathlib import Path\n"
        "real = os.environ['REAL_PYTHON']\n"
        "for index, value in enumerate(sys.argv[1:]):\n"
        "    if value.endswith('/macos/installer.py'):\n"
        "        args = sys.argv[index + 2:]\n"
        "        receipt = Path(args[args.index('--fallback-authorization-receipt') + 1])\n"
        "        payload = json.loads(receipt.read_text(encoding='utf-8'))\n"
        "        assert payload['decision'] == 'fallback_authorized'\n"
        "        assert [item['result'] for item in payload['official_attempts']] == ['failed', 'failed']\n"
        "        Path(os.environ['FAKE_INSTALL_RECORD']).write_text('fallback-installed', encoding='utf-8')\n"
        "        raise SystemExit(0)\n"
        "raise SystemExit(subprocess.call([real, *sys.argv[1:]]))\n"
    ).encode()
    fallback_root = tmp_path / "fallback"
    (fallback_root / "python" / "install" / "bin").mkdir(parents=True)
    fallback_python = fallback_root / "python" / "install" / "bin" / "python3.11"
    fallback_python.write_bytes(fallback_python_payload)
    fallback_python.chmod(0o755)
    fallback_name = "cpython-fallback.tar.gz"
    fallback_path = web_root / fallback_name
    with tarfile.open(fallback_path, "w:gz") as archive:
        archive.add(fallback_root / "python", arcname="python")
    fallback_bytes = fallback_path.read_bytes()
    runtime_sha = hashlib.sha256(fallback_bytes).hexdigest()
    lock = {
        "schema_version": 1,
        "freeze_id": "fixture-fallback-v1",
        "platforms": {
            "macos-arm64": {
                "runtime_asset": {
                    "filename": fallback_name,
                    "bytes": len(fallback_bytes),
                    "sha256": runtime_sha,
                },
                "python_json": {"python_exe": "python/install/bin/python3.11"},
                "license_bundle": {
                    "filename": license_zip.name,
                    "bytes": len(license_bytes),
                    "sha256": hashlib.sha256(license_bytes).hexdigest(),
                },
            }
        },
    }
    (bundle / "macos" / "fallback-lock.json").write_text(json.dumps(lock), encoding="utf-8")
    (bundle / "licenses").mkdir()
    (bundle / "licenses" / license_zip.name).write_bytes(license_bytes)
    (bundle / "macos" / "installer.py").write_text("# fake installer\n", encoding="utf-8")
    archive_name = f"image-pptgen-{version}-macos-arm64.tar.gz"
    archive_path = web_root / archive_name
    with tarfile.open(archive_path, "w:gz") as archive:
        for path in sorted(bundle.rglob("*")):
            archive.add(path, arcname=path.relative_to(tmp_path).as_posix(), recursive=False)
    archive_bytes = archive_path.read_bytes()
    requests: list[str] = []

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(web_root), **kwargs)

        def do_GET(self):  # noqa: N802 - stdlib handler API
            requests.append(self.path)
            return super().do_GET()

        def log_message(self, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    manifest = {
        "schema_version": 2,
        "product": "image-pptgen",
        "version": version,
        "platforms": {
            "macos-arm64": {
                "archive": {
                    "name": archive_name,
                    "path": archive_name,
                    "url": f"{base_url}/{archive_name}",
                    "size": len(archive_bytes),
                    "sha256": hashlib.sha256(archive_bytes).hexdigest(),
                },
                "fallback_runtime": {
                    "name": fallback_name,
                    "path": fallback_name,
                    "url": f"{base_url}/{fallback_name}",
                    "size": len(fallback_bytes),
                    "sha256": runtime_sha,
                    "freeze_id": lock["freeze_id"],
                },
            }
        },
    }
    (web_root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    shell = tmp_path / "install.sh"
    shell.write_text(
        builder._render_shell_bootstrap(
            version=version,
            dist_base_url=base_url,
            manifest_url=f"{base_url}/manifest.json",
            r2_base_url=base_url,
        ),
        encoding="utf-8",
    )
    shell.chmod(0o755)
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    _write_fake_macos_plutil(fake_bin)
    (fake_bin / "uname").write_text(
        "#!/usr/bin/env bash\n"
        "if [ \"$1\" = '-s' ]; then printf 'Darwin\\n'; else printf 'arm64\\n'; fi\n",
        encoding="utf-8",
    )
    for command in ("python", "python3"):
        (fake_bin / command).write_text("#!/usr/bin/env bash\nexit 97\n", encoding="utf-8")
        (fake_bin / command).chmod(0o755)
    fake_bin.joinpath("uname").chmod(0o755)
    record = tmp_path / "install-record.txt"
    environment = os.environ.copy()
    environment.update(
        {
            "HOME": str(tmp_path / "home"),
            "PATH": f"{fake_bin}:{environment['PATH']}",
            "REAL_PYTHON": sys.executable,
            "FAKE_INSTALL_RECORD": str(record),
        }
    )
    try:
        completed = subprocess.run(
            ["bash", str(shell)], env=environment, text=True, capture_output=True, check=False
        )
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()
    assert completed.returncode == 0, completed.stderr
    assert requests == ["/manifest.json", f"/{archive_name}", f"/{fallback_name}"]
    assert record.read_text(encoding="utf-8") == "fallback-installed"


@pytest.mark.parametrize(
    ("collision", "failure"),
    (("tar", "Platform archive safety validation failed"), ("zip", "macOS license bundle safety validation failed")),
)
def test_macos_native_bootstrap_rejects_casefold_archive_member_collisions(
    tmp_path: Path, collision: str, failure: str
) -> None:
    completed, requests, fallback_name = _run_macos_native_case_collision(
        tmp_path, collision=collision
    )
    assert completed.returncode != 0
    assert failure in completed.stderr
    assert f"/{fallback_name}" not in requests


def test_powershell_bootstrap_uses_dynamic_then_bounded_known_runtime_discovery() -> None:
    builder, _build_fn = _load_unified_builder()
    script = builder._render_powershell_bootstrap(
        version=VERSION,
        dist_base_url=DIST_BASE_URL,
        manifest_url=MANIFEST_URL,
        r2_base_url=R2_BASE_URL,
    )
    discovery = script[
        script.index("function Resolve-ManagedPythonCandidate") : script.index("$workRoot =")
    ]
    dynamic_attempt = "Test-OfficialApproach $dynamicOfficial 'venv-ensurepip'"
    known_attempt = "Test-OfficialApproach $knownManagedOfficial 'venv-explicit-ensurepip'"
    fallback_gate = "if (-not $officialSuccess)"
    assert "function Find-DynamicOfficialPython" in discovery
    assert "function Find-KnownManagedPrimaryPython" in discovery
    assert "Get-ChildItem" not in discovery
    assert dynamic_attempt in script and known_attempt in script
    assert script.index(dynamic_attempt) < script.index(known_attempt) < script.index(fallback_gate)
    assert "function Resolve-ManifestObjectUrl" in script
    assert "Assert-SafeMemberName $path" in script
    assert "$archiveUrl = Resolve-ManifestObjectUrl $archive 'archive'" in script
    assert "$fallbackUrl = Resolve-ManifestObjectUrl $fallback 'fallback Runtime'" in script


def _native_runner_fixture(
    *, stage: str, exit_code: int, stdout: str = "", stderr: str = ""
) -> dict[str, object]:
    """Model the generated runner's observable contract on a non-Windows host."""
    if exit_code == 0:
        return {
            "ok": True,
            "stage": stage,
            "exit_code": exit_code,
            "stdout": stdout,
            "stderr": stderr,
        }
    tail = stderr[-12000:] if stderr else stdout[-12000:]
    return {
        "ok": False,
        "stage": stage,
        "exit_code": exit_code,
        "message": f"native stage {stage} failed (exit {exit_code}):\nstderr: {tail}",
    }


def test_generated_windows_bootstrap_native_runner_contract_is_stage_aware() -> None:
    builder, _build_fn = _load_unified_builder()
    script = builder._render_powershell_bootstrap(
        version=VERSION,
        dist_base_url=DIST_BASE_URL,
        manifest_url=MANIFEST_URL,
        r2_base_url=R2_BASE_URL,
    )
    runner = script[script.index("function Invoke-NativeStage") : script.index("function Resolve-ManifestObjectUrl")]
    assert "$ErrorActionPreference = 'Continue'" in runner
    assert "1> $stdoutPath 2> $stderrPath" in runner
    assert "$exitCode = if ($null -eq $LASTEXITCODE)" in runner
    assert runner.count("& $FilePath @ArgumentList") == 1
    assert "*> $null" not in script
    assert "& $Candidate" not in script
    assert "& $probePython" not in script
    assert "& $tar.Source" not in script
    assert "& powershell.exe" not in script
    assert script.count("$LASTEXITCODE") == 2
    assert script.count("$exitCode = if ($null -eq $LASTEXITCODE)") == 1
    assert "Format-NativeFailure" in script
    assert "platform-installer:official" in script
    assert "fallback was not downloaded" in script

    noisy_success = _native_runner_fixture(
        stage="official-probe:venv-ensurepip:pip",
        exit_code=0,
        stderr="warning from native stderr\n",
    )
    assert noisy_success["ok"] is True
    assert noisy_success["stderr"]

    failure = _native_runner_fixture(
        stage="official-probe:venv-ensurepip:venv",
        exit_code=17,
        stderr="diagnostic head\n" + ("x" * 12050) + "\nERROR_TAIL",
    )
    assert failure["ok"] is False
    assert failure["stage"] == "official-probe:venv-ensurepip:venv"
    assert failure["exit_code"] == 17
    assert "ERROR_TAIL" in str(failure["message"])
    assert "diagnostic head" not in str(failure["message"])


def test_generated_windows_bootstrap_preserves_probe_approaches_and_installer_no_fallback() -> None:
    builder, _build_fn = _load_unified_builder()
    script = builder._render_powershell_bootstrap(
        version=VERSION,
        dist_base_url=DIST_BASE_URL,
        manifest_url=MANIFEST_URL,
        r2_base_url=R2_BASE_URL,
    )
    dynamic = "Test-OfficialApproach $dynamicOfficial 'venv-ensurepip'"
    known = "Test-OfficialApproach $knownManagedOfficial 'venv-explicit-ensurepip'"
    fallback_gate = "if (-not $officialSuccess)"
    assert script.index(dynamic) < script.index(known) < script.index(fallback_gate)

    attempts = [("venv-ensurepip", 1), ("venv-explicit-ensurepip", 0)]
    selected = next((name for name, exit_code in attempts if exit_code == 0), None)
    assert selected == "venv-explicit-ensurepip"
    installer_failure = _native_runner_fixture(
        stage="platform-installer:official",
        exit_code=23,
        stderr=("installer diagnostic\n" * 800) + "PLATFORM_TAIL",
    )
    assert installer_failure["ok"] is False
    assert "platform-installer:official" in str(installer_failure["message"])
    assert "PLATFORM_TAIL" in str(installer_failure["message"])
    assert "official Runtime platform installer failed; fallback was not downloaded" in script
    official_call = script.index("-Stage 'platform-installer:official'")
    fallback_call = script.index("if (-not $officialSuccess)")
    assert fallback_call < official_call


def _official_probe_fixture(dynamic_exit: int, known_ensurepip_exit: int) -> dict[str, object]:
    """Model the generated bootstrap's two official attempts off Windows."""

    attempts = [
        ("venv-ensurepip", dynamic_exit),
        ("venv-explicit-ensurepip", known_ensurepip_exit),
    ]
    selected = next((name for name, exit_code in attempts if exit_code == 0), None)
    return {
        "official": selected,
        "fallback_authorized": selected is None,
        "official_attempts": [
            {"approach": name, "result": "succeeded" if exit_code == 0 else "failed"}
            for name, exit_code in attempts
        ],
    }


def test_generated_windows_bootstrap_known_managed_probe_uses_probe_ensurepip_not_base_pip() -> None:
    builder, _build_fn = _load_unified_builder()
    script = builder._render_powershell_bootstrap(
        version=VERSION,
        dist_base_url=DIST_BASE_URL,
        manifest_url=MANIFEST_URL,
        r2_base_url=R2_BASE_URL,
    )
    explicit_branch = script[
        script.rindex(
            "$venvResult = Invoke-NativeStage",
            0,
            script.index("function Resolve-ManagedPythonCandidate"),
        ) : script.index("function Resolve-ManagedPythonCandidate")
    ]
    assert "'venv-explicit-ensurepip'" in script
    assert "probe-explicit-ensurepip" in script
    assert "'-m', 'venv', '--copies', '--without-pip'" in explicit_branch
    assert "-FilePath $probePython -ArgumentList @('-I', '-m', 'ensurepip')" in explicit_branch
    assert "-FilePath $probePython -ArgumentList @('-I', '-m', 'pip', '--version')" in explicit_branch
    assert "-FilePath $Candidate -ArgumentList @('-I', '-m', 'pip'" not in explicit_branch
    assert "'venv-host-pip'" not in script

    # Dynamic discovery is absent, but the known managed interpreter's base
    # pip is irrelevant once the disposable probe's ensurepip succeeds.
    known_managed_success = _official_probe_fixture(dynamic_exit=1, known_ensurepip_exit=0)
    assert known_managed_success["official"] == "venv-explicit-ensurepip"
    assert known_managed_success["fallback_authorized"] is False
    assert known_managed_success["official_attempts"] == [
        {"approach": "venv-ensurepip", "result": "failed"},
        {"approach": "venv-explicit-ensurepip", "result": "succeeded"},
    ]

    explicit_ensurepip_failure = _official_probe_fixture(dynamic_exit=1, known_ensurepip_exit=1)
    assert explicit_ensurepip_failure["official"] is None
    assert explicit_ensurepip_failure["fallback_authorized"] is True
    assert len({item["approach"] for item in explicit_ensurepip_failure["official_attempts"]}) == 2
    assert all(item["result"] == "failed" for item in explicit_ensurepip_failure["official_attempts"])


def test_r30_windows_bootstrap_avoids_ps51_nested_identity_quotes_and_bom_receipts() -> None:
    """Keep the two R29 Windows failures out of the generated PS5.1 path."""
    builder, _build_fn = _load_unified_builder()
    script = builder._render_powershell_bootstrap(
        version=VERSION,
        dist_base_url=DIST_BASE_URL,
        manifest_url=MANIFEST_URL,
        r2_base_url=R2_BASE_URL,
    )
    identity = script[
        script.index("function Test-OfficialApproach") : script.index(
            "function Resolve-ManagedPythonCandidate"
        )
    ]

    # Windows PowerShell 5.1 must not rebuild an inline Python expression with
    # nested tuple/list quotes.  The identity probe is a disposable script
    # file and passes only simple path argv values through Invoke-NativeStage.
    assert "'-c'" not in identity
    assert "$identityProbePath = Join-Path $script:NativeCaptureRoot" in identity
    assert "Write-Utf8NoBom $identityProbePath" in identity
    assert (
        "-FilePath $Candidate -ArgumentList @('-I', $identityProbePath, $Candidate)"
        in identity
    )
    assert 'platform.machine().lower() in {"amd64", "x86_64"}' in identity

    # Both bootstrap receipts are strict-UTF-8 compatible: the writer is
    # explicit, and the old PS5.1 Set-Content UTF8/BOM path is absent.
    assert "function Write-Utf8NoBom" in script
    assert "[System.Text.UTF8Encoding]::new($false)" in script
    assert script.count("Write-Utf8NoBom $receiptPath") == 2
    assert "Set-Content -LiteralPath $receiptPath -Encoding UTF8" not in script


def test_generated_windows_bootstrap_fallback_omits_empty_official_arg_and_records_attempt_diagnostics() -> None:
    builder, _build_fn = _load_unified_builder()
    script = builder._render_powershell_bootstrap(
        version=VERSION,
        dist_base_url=DIST_BASE_URL,
        manifest_url=MANIFEST_URL,
        r2_base_url=R2_BASE_URL,
    )
    fallback_start = script.index("if (-not $officialSuccess)")
    official_start = script.index("if ([string]::IsNullOrWhiteSpace($official))")
    fallback_branch = script[fallback_start:official_start]
    official_branch = script[official_start:]

    # PowerShell 5.1 can drop an empty native argv.  The fallback branch must
    # omit the optional parameter altogether, while the official branch still
    # binds the selected managed executable.
    assert "'-OfficialPython', ''" not in script
    assert "if (-not [string]::IsNullOrWhiteSpace($official))" in fallback_branch
    assert "'-OfficialPython', $official" in official_branch
    assert "'-FallbackPythonRoot', $fallbackRoot" in fallback_branch

    # Receipt and final error use the same bounded, redacted attempt records;
    # the explicit ensurepip failure is not collapsed to a last-line Traceback.
    assert "official_attempts = @($script:OfficialAttemptDetails)" in fallback_branch
    assert "function New-OfficialAttemptRecord" in script
    assert "function Format-OfficialAttemptDiagnostics" in script
    assert "function Redact-NativeOutput" in script
    assert "official-probe:' + $Approach + ':ensurepip'" in script
    assert "exit_code = [int]$Probe.ExitCode" in script
    assert "stderr = Redact-NativeOutput" in script
    assert "stdout = Redact-NativeOutput" in script
    assert "exception = Redact-NativeOutput" in script
    assert "New-OfficialProbeFailure ('official-probe:' + $Approach + ':candidate') 127" in script
    assert "New-OfficialProbeFailure ('official-probe:' + $Approach + ':venv-output') 126" in script
    assert script.count("-Succeeded $false -Probe $script:LastOfficialProbe") == 2
    assert script.count("-Succeeded $true -Probe $script:LastOfficialProbe") == 2
    assert "$attemptDiagnostics = Format-OfficialAttemptDiagnostics $script:OfficialAttemptDetails" in script
    assert "$message += [Environment]::NewLine + $attemptDiagnostics" in script

    redactor = script[
        script.index("function Redact-NativeOutput") : script.index("function New-OfficialProbeFailure")
    ]
    assert "$text = Get-NativeOutputTail $Value" in redactor
    assert "api[_-]?key|access[_-]?token|token|authorization|password|secret|cookie" in redactor
    assert "Bearer [REDACTED]" in redactor
    assert "<redacted-path>" in redactor

    # Both failures append the current native stage record before fallback is
    # authorized.  A later success instead leaves a visible selection proof.
    dynamic_start = script.index("if (Test-OfficialApproach $dynamicOfficial")
    dynamic_success_branch = script[dynamic_start : script.index("} else {", dynamic_start)]
    dynamic_failure_start = script.index("} else {", dynamic_start)
    known_start = script.index("if (Test-OfficialApproach $knownManagedOfficial")
    known_success_branch = script[
        known_start:official_start
    ]
    dynamic_failure_branch = script[dynamic_failure_start:known_start]
    known_failure_branch = script[script.index("} else {", known_start):official_start]
    assert "-Approach 'venv-ensurepip' -Succeeded $false -Probe $script:LastOfficialProbe" in dynamic_failure_branch
    assert "-Approach 'venv-explicit-ensurepip' -Succeeded $false -Probe $script:LastOfficialProbe" in known_failure_branch
    assert "$selectedOfficialApproach = 'venv-ensurepip'" in dynamic_success_branch
    assert "$selectedOfficialApproach = 'venv-explicit-ensurepip'" in known_success_branch
    assert "Write-Output ('Image PPTGen bootstrap selected official Runtime approach: ' + $selectedOfficialApproach)" in official_branch
    assert "official Runtime probe returned success without an approach" in official_branch
    assert "'-RuntimeSelectionReceipt', $receiptPath" in fallback_branch
    assert "'-RuntimeSelectionReceipt', $receiptPath" in official_branch
    assert "'official-runtime-selection.json'" in official_branch
    assert "decision = 'official_selected'" in official_branch
    assert "selected_approach = $selectedOfficialApproach" in official_branch
    assert "fallback_runtime" not in official_branch


def test_generated_windows_bootstrap_fallback_receipt_uses_extracted_archive_root(
    tmp_path: Path,
) -> None:
    builder, _build_fn = _load_unified_builder()
    lock = json.loads(
        (ROOT / "packaging" / "image" / "fallback" / "fallback-lock.json").read_text(
            encoding="utf-8"
        )
    )
    layout = lock["platforms"]["windows-amd64"]["runtime_archive_layout"]
    assert layout == {"member_root": "python", "python_exe": "python.exe"}

    lock_relative_python = PurePosixPath(layout["python_exe"])
    extracted_root = (tmp_path / "fallback-stage" / "python").resolve()
    extracted_root.mkdir(parents=True)
    receipt_python = extracted_root.joinpath(*lock_relative_python.parts).resolve()
    receipt_python.write_bytes(b"path-contract-fixture")
    installer_expected = extracted_root / "python.exe"
    assert receipt_python.is_file()
    assert receipt_python == installer_expected.resolve()
    receipt = {
        "extracted_root": str(extracted_root),
        "python_path": str(receipt_python),
    }
    assert Path(receipt["extracted_root"]).joinpath(*lock_relative_python.parts) == Path(
        receipt["python_path"]
    )

    script = builder._render_powershell_bootstrap(
        version=VERSION,
        dist_base_url=DIST_BASE_URL,
        manifest_url=MANIFEST_URL,
        r2_base_url=R2_BASE_URL,
    )
    assert "$fallbackStage = Join-Path $workRoot 'fallback-runtime'" in script
    assert "$runtimeLayout = $lockPlatform.runtime_archive_layout" in script
    assert "$runtimeRoot = [string]$runtimeLayout.member_root" in script
    assert "$runtimeRel = [string]$runtimeLayout.python_exe" in script
    assert "Expand-SafeTarGz $fallbackArchive $fallbackStage $runtimeRoot" in script
    assert "$fallbackRoot = Join-Path $fallbackStage $runtimeRoot" in script
    assert "$fallbackPython = [IO.Path]::GetFullPath((Join-Path $fallbackRoot" in script
    assert "'-FallbackPythonRoot', $fallbackRoot" in script


def test_pages_and_r2_roots_must_not_overlap_or_contain_binary_residue(
    tmp_path: Path,
) -> None:
    builder, build_multiplatform_release = _load_unified_builder()
    assets = _fake_fallback_authority(tmp_path, builder)
    output_root = tmp_path / "overlap-output"
    with pytest.raises(Exception, match=r"(?i)(Pages|R2|separate|overlap)"):
        build_multiplatform_release(
            ROOT,
            output_root,
            version=VERSION,
            dist_base_url=DIST_BASE_URL,
            manifest_url=MANIFEST_URL,
            r2_root=output_root / "pages-dist" / "payloads",
            r2_prefix=R2_PREFIX,
            r2_base_url=R2_BASE_URL,
            r2_ledger_path=tmp_path / "overlap-ledger.json",
            fallback_assets_root=assets,
        )

    binary_output = tmp_path / "binary-output"
    pages_root = binary_output / "pages-dist"
    pages_root.mkdir(parents=True)
    (pages_root / "unowned-runtime.zip").write_bytes(b"\x00\xff\x00\xff")
    with pytest.raises(Exception, match=r"(?i)(Pages|non-text|asset)"):
        build_multiplatform_release(
            ROOT,
            binary_output,
            version=VERSION,
            dist_base_url=DIST_BASE_URL,
            manifest_url=MANIFEST_URL,
            r2_root=tmp_path / "binary-r2",
            r2_prefix=R2_PREFIX,
            r2_base_url=R2_BASE_URL,
            r2_ledger_path=tmp_path / "binary-ledger.json",
            fallback_assets_root=assets,
        )


def test_fallback_assets_and_release_sources_reject_symbolic_links(tmp_path: Path) -> None:
    builder, build_multiplatform_release = _load_unified_builder()
    assets = _fake_fallback_authority(tmp_path, builder)
    runtime = next(assets.glob("*-runtime.tar.gz"))
    external = tmp_path / "outside-fallback-assets.bin"
    external.write_bytes(runtime.read_bytes())
    runtime.unlink()
    runtime.symlink_to(external)
    with pytest.raises(Exception, match=r"(?i)(symbolic|unsafe|asset)"):
        build_multiplatform_release(
            ROOT,
            tmp_path / "symlink-output",
            version=VERSION,
            dist_base_url=DIST_BASE_URL,
            manifest_url=MANIFEST_URL,
            r2_root=tmp_path / "symlink-r2",
            r2_prefix=R2_PREFIX,
            r2_base_url=R2_BASE_URL,
            r2_ledger_path=tmp_path / "symlink-ledger.json",
            fallback_assets_root=assets,
        )

    legacy = _load_module(LEGACY_BUILDER_PATH, "legacy_image_builder_symlink_contract")
    source = tmp_path / "outside-release-source.txt"
    source.write_text("outside\n", encoding="utf-8")
    link = tmp_path / "release-source-link.txt"
    link.symlink_to(source)
    with pytest.raises(FileNotFoundError):
        legacy._copy_file(link, tmp_path / "copied.txt")


def test_immutable_metadata_publish_never_overwrites_a_concurrent_winner(
    tmp_path: Path,
) -> None:
    builder, _build_fn = _load_unified_builder()
    target = tmp_path / "immutable" / "manifest.json"
    payloads = (b'{"winner":"a"}\n', b'{"winner":"b"}\n')
    barrier = threading.Barrier(4)

    def publish(payload: bytes):
        barrier.wait()
        try:
            builder._write_text_immutable(target, payload)
        except Exception as exc:  # the losing, different payload must fail closed
            return type(exc).__name__
        return "ok"

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(publish, (*payloads, *payloads)))
    assert target.read_bytes() in payloads
    assert "ok" in results
    assert any(result != "ok" for result in results)


def test_legacy_linux_builder_api_remains_callable(tmp_path: Path) -> None:
    legacy = _load_module(LEGACY_BUILDER_PATH, "legacy_image_build_release")
    signature = inspect.signature(legacy.build_release)
    assert {"repo_root", "output_root", "version"} <= set(signature.parameters)

    result = legacy.build_release(ROOT, tmp_path / "legacy", version="0.1.0")
    assert result.archive_path.name == "image-pptgen-0.1.0-linux-x86_64.tar.gz"
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["platform"] == "linux-x86_64"
    assert manifest["archive"]["sha256"] == legacy.sha256_file(result.archive_path)
