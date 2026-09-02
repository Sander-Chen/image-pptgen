from __future__ import annotations

import importlib.util
import hashlib
import json
import os
import stat
import subprocess
import sys
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
BUILDER_MODULE = ROOT / "packaging" / "image_build_release.py"
_IMAGE_LAUNCHER_ENVIRONMENT_KEYS = (
    "IMAGE_PPTGEN_DATA_ROOT",
    "PPTGEN_BASE_URL",
    "PPTGEN_DATA_ROOT",
    "PPTGEN_INSTANCE_ID_PATH",
    "PPTGEN_RELEASE_IDENTITY_PATH",
    "PPTGEN_RELEASE_ROOT",
    "PPTGEN_PUBLIC_DATA_DIR",
    "PPTGEN_HISTORICAL_DATA_DIR",
    "PPTGEN_IMAGE_RUNTIME_MODE",
    "PPT_DB_PATH",
    "PPT_ARTIFACTS_DIR",
    "PORT",
)


@pytest.fixture(autouse=True)
def _restore_image_launcher_environment():
    """Keep launcher-owned process state inside each Image release test."""
    previous = {key: os.environ.get(key) for key in _IMAGE_LAUNCHER_ENVIRONMENT_KEYS}
    yield
    for key, value in previous.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def _load_builder():
    spec = importlib.util.spec_from_file_location("image_build_release", BUILDER_MODULE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _members(path: Path) -> list[str]:
    with tarfile.open(path, "r:gz") as archive:
        return archive.getnames()


def test_image_release_contains_only_image_runtime_surfaces(tmp_path):
    builder = _load_builder()
    result = builder.build_release(ROOT, tmp_path / "build", version="0.1.0")

    builder.validate_archive(result.archive_path)
    names = set(_members(result.archive_path))
    prefix = "image-pptgen-0.1.0"
    assert f"{prefix}/app/public_server.py" in names
    assert f"{prefix}/app/image-launcher.py" in names
    assert f"{prefix}/app/image-pptgen-wrapper.sh" in names
    assert f"{prefix}/app/image-pptgen-server-wrapper.sh" in names
    assert f"{prefix}/app/skills/generate-image-presentation/SKILL.md" in names
    assert f"{prefix}/app/skills/generate-image-presentation/scripts/image-pptgen-dispatch" in names
    assert f"{prefix}/app/skills/generate-image-presentation/scripts/image-pptgen-dispatch.ps1" in names
    assert f"{prefix}/app/packages/pptgen_toolkit/pyproject.toml" in names
    assert f"{prefix}/app/backend/services/platform_runtime.py" in names
    assert f"{prefix}/app/release-identity.json" in names
    assert f"{prefix}/app/frontend/dist/index.html" in names
    assert not any(Path(name).name in {"generate-presentation", "pptgen-wrapper.sh", "pptgen-server-wrapper.sh"} for name in names)
    assert not any(f"{prefix}/app/frontend/src" in name for name in names)
    assert all(
        "frontend" not in Path(name).parts
        or Path(name).parts[:3] != (prefix, "app", "frontend")
        or len(Path(name).parts) == 3
        or Path(name).parts[3] == "dist"
        for name in names
    )
    with tarfile.open(result.archive_path, "r:gz") as archive:
        identity = json.loads(
            archive.extractfile(f"{prefix}/app/release-identity.json").read()
        )
    assert identity["product"] == "image-pptgen"
    assert identity["service"] == "image-pptgen-server"
    assert identity["surface"] == "public_image_3_0"
    assert identity["command"] == "image-pptgen"
    assert identity["service_command"] == "image-pptgen-server"
    assert identity["skill"] == "generate-image-presentation"
    assert identity["base_url"] == "http://127.0.0.1:3130"
    assert identity["data_root"] == "~/.local/share/image-pptgen"
    assert identity["config_root"] == "~/.config/image-pptgen"
    assert len(identity["runtime_content_sha256"]) == 64
    assert len(identity["skill_sha256"]) == 64


def test_linux_release_archive_contains_static_preview_bundle_sources(tmp_path):
    builder = _load_builder()
    result = builder.build_release(ROOT, tmp_path / "build", version="0.1.0")
    prefix = "image-pptgen-0.1.0"
    names = set(_members(result.archive_path))
    cli_member = f"{prefix}/app/packages/pptgen_toolkit/src/pptgen_toolkit/image_cli.py"
    bundle_member = (
        f"{prefix}/app/packages/pptgen_toolkit/src/pptgen_toolkit/static_preview_bundle.py"
    )
    assert cli_member in names
    assert bundle_member in names
    with tarfile.open(result.archive_path, "r:gz") as archive:
        packaged_cli = archive.extractfile(cli_member).read()
        packaged_bundle = archive.extractfile(bundle_member).read()
    assert packaged_cli == (
        ROOT / "packages" / "pptgen_toolkit" / "src" / "pptgen_toolkit" / "image_cli.py"
    ).read_bytes()
    assert packaged_bundle == (
        ROOT
        / "packages"
        / "pptgen_toolkit"
        / "src"
        / "pptgen_toolkit"
        / "static_preview_bundle.py"
    ).read_bytes()


def test_image_skill_dispatcher_is_executable_in_staging_and_linux_tar(tmp_path):
    builder = _load_builder()
    app_root = tmp_path / "staging" / "app"
    builder._populate_runtime(ROOT, app_root)

    dispatcher = app_root / "skills" / "generate-image-presentation" / "scripts" / "image-pptgen-dispatch"
    mode = dispatcher.lstat().st_mode
    assert stat.S_ISREG(mode)
    assert not stat.S_ISLNK(mode)
    assert stat.S_IMODE(mode) == 0o755

    result = builder.build_release(ROOT, tmp_path / "build", version="0.1.0-dispatch-mode")
    member_name = (
        "image-pptgen-0.1.0-dispatch-mode/app/skills/"
        "generate-image-presentation/scripts/image-pptgen-dispatch"
    )
    with tarfile.open(result.archive_path, "r:gz") as archive:
        member = archive.getmember(member_name)
    assert member.isfile()
    assert stat.S_IMODE(member.mode) == 0o755


@pytest.mark.parametrize("shape", ["missing", "directory", "symlink"])
def test_image_skill_dispatcher_contract_fails_closed(tmp_path, shape):
    builder = _load_builder()
    dispatcher = (
        tmp_path
        / "app"
        / "skills"
        / "generate-image-presentation"
        / "scripts"
        / "image-pptgen-dispatch"
    )
    dispatcher.parent.mkdir(parents=True)
    if shape == "directory":
        dispatcher.mkdir()
    elif shape == "symlink":
        target = tmp_path / "outside-dispatch"
        target.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
        dispatcher.symlink_to(target)

    with pytest.raises((FileNotFoundError, ValueError)):
        builder._ensure_skill_dispatcher_executable(tmp_path / "app")


def test_private_013_candidate_archives_runtime_manager_and_identity(tmp_path):
    builder = _load_builder()
    result = builder.build_release(ROOT, tmp_path / "build", version="0.1.3")

    prefix = "image-pptgen-0.1.3"
    names = set(_members(result.archive_path))
    assert f"{prefix}/app/runtime_manager.py" in names

    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["version"] == "0.1.3"
    assert manifest["archive"]["sha256"] == builder.sha256_file(result.archive_path)
    assert manifest["identity"]["version"] == "0.1.3"
    assert manifest["identity"]["source_commit"] == builder._source_commit(ROOT)
    assert len(manifest["identity"]["runtime_content_sha256"]) == 64
    assert len(manifest["identity"]["skill_sha256"]) == 64

    with tarfile.open(result.archive_path, "r:gz") as archive:
        runtime_manager = archive.extractfile(f"{prefix}/app/runtime_manager.py")
        assert runtime_manager is not None
        assert runtime_manager.read() == (
            ROOT / "packaging" / "image" / "runtime_manager.py"
        ).read_bytes()


def test_extracted_runtime_manager_imports_shared_core_outside_repo(tmp_path):
    builder = _load_builder()
    result = builder.build_release(ROOT, tmp_path / "build", version="0.1.3")
    extract_root = tmp_path / "extract"
    with tarfile.open(result.archive_path, "r:gz") as archive:
        archive.extractall(extract_root)
    app_root = extract_root / "image-pptgen-0.1.3" / "app"
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)

    completed = subprocess.run(
        [sys.executable, str(app_root / "runtime_manager.py"), "--help"],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "ensure-ready" in completed.stdout
    assert "stop" in completed.stdout


def test_image_install_docs_offer_agent_sentence_and_keep_server_diagnostic_only(tmp_path):
    builder = _load_builder()
    docs_dir = tmp_path / "docs"
    builder._render_docs(
        ROOT,
        docs_dir,
        version="0.1.3",
        dist_base_url="https://image-pptgen-dist.pages.dev",
    )
    docs = (docs_dir / "install.md").read_text(encoding="utf-8")
    metadata = json.loads((docs_dir / "install.json").read_text(encoding="utf-8"))

    assert "Codex" in docs and "Claude Code" in docs
    assert "Install Image PPTGen" in docs
    assert "$generate-image-presentation" in docs
    assert "curl -fsSL https://image-pptgen-dist.pages.dev/install.sh" in docs
    assert "image-pptgen-server" in docs
    diagnostic_heading = "## Advanced diagnostics"
    assert diagnostic_heading in docs
    primary_flow = docs.split(diagnostic_heading, 1)[0]
    assert "image-pptgen-server" not in primary_flow

    assert metadata["version"] == "0.1.3"
    assert metadata["install_sentence"]
    assert metadata["codex_install_sentence"] == metadata["install_sentence"]
    assert metadata["claude_code_install_sentence"] == metadata["install_sentence"]
    assert metadata["professional_install_command"].startswith("curl -fsSL")
    assert metadata["next_task_skill"] == "$generate-image-presentation"
    assert metadata["diagnostic_service_command"] == "image-pptgen-server"


def test_image_release_is_deterministic_across_two_builds(tmp_path):
    builder = _load_builder()
    first = builder.build_release(ROOT, tmp_path / "first", version="0.1.1")
    second = builder.build_release(ROOT, tmp_path / "second", version="0.1.1")

    assert builder.sha256_file(first.archive_path) == builder.sha256_file(second.archive_path)
    assert first.archive_path.read_bytes() == second.archive_path.read_bytes()


def test_image_server_wrapper_defaults_to_codex_user_config_inheritance(tmp_path: Path) -> None:
    data_home = tmp_path / "xdg-data"
    capture_path = tmp_path / "captured-inheritance.txt"
    fake_python = data_home / "image-pptgen" / "current-venv" / "bin" / "python"
    fake_python.parent.mkdir(parents=True)
    fake_python.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s' \"${PPTGEN_CODEX_INHERIT_USER_CONFIG-}\" > \"$IMAGE_PPTGEN_CAPTURE_PATH\"\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    environment = os.environ.copy()
    environment.update(
        {
            "XDG_DATA_HOME": str(data_home),
            "XDG_CONFIG_HOME": str(tmp_path / "xdg-config"),
            "IMAGE_PPTGEN_CAPTURE_PATH": str(capture_path),
        }
    )
    environment.pop("PPTGEN_CODEX_INHERIT_USER_CONFIG", None)

    completed = subprocess.run(
        ["bash", ROOT / "packaging" / "image" / "image-pptgen-server-wrapper.sh"],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert completed.returncode == 0, completed.stderr
    assert capture_path.read_text(encoding="utf-8") == "1"


def test_image_archive_validation_rejects_links_and_unsafe_paths(tmp_path):
    builder = _load_builder()
    for arcname, link in (("link", True), ("../outside", False)):
        archive = tmp_path / f"{arcname.replace('/', '_')}.tar.gz"
        with tarfile.open(archive, "w:gz") as handle:
            info = tarfile.TarInfo(arcname)
            if link:
                info.type = tarfile.SYMTYPE
                info.linkname = "target"
            else:
                info.size = 0
            handle.addfile(info)
        with pytest.raises(ValueError, match="unsafe archive member"):
            builder.validate_archive(archive)


def test_image_installer_is_namespace_scoped_and_has_no_html_replacement(tmp_path):
    builder = _load_builder()
    result = builder.build_release(ROOT, tmp_path / "build", version="0.1.2")
    installer = result.bootstrap_path.read_text(encoding="utf-8")

    assert 'INSTALL_ROOT="$DATA_HOME/image-pptgen"' in installer
    assert 'CONFIG_HOME/image-pptgen' in installer
    assert 'generate-image-presentation' in installer
    assert 'image-pptgen-server' in installer
    assert 'image-pptgen doctor --json' in installer
    assert '127.0.0.1:3130' in installer
    assert 'generate-presentation' not in installer
    assert 'pptgen-server' not in installer.replace('image-pptgen-server', '')
    assert 'rm -rf "$HOME/.agents/skills/generate-presentation"' not in installer


def test_image_package_excludes_browser_runtime_prerequisites_but_preserves_shared_qa(tmp_path):
    builder = _load_builder()
    result = builder.build_release(ROOT, tmp_path / "build", version="0.1.2")
    installer = result.bootstrap_path.read_text(encoding="utf-8")

    with tarfile.open(result.archive_path, "r:gz") as archive:
        requirements = archive.extractfile(
            "image-pptgen-0.1.2/app/requirements.txt"
        )
        assert requirements is not None
        requirement_text = requirements.read().decode("utf-8")

    assert "playwright" not in requirement_text.lower()
    assert "playwright" not in installer.lower()
    assert "google-chrome" not in installer.lower()
    assert "google-chrome-stable" not in installer.lower()

    # The subtraction is scoped to the public Image installer. Shared HTML
    # generation and the acceptance harness still own browser screenshot work.
    assert "def screenshot_html_file" in (ROOT / "pipeline.py").read_text(encoding="utf-8")
    assert "from playwright.sync_api" in (
        ROOT / "qa" / "image-skill-docker" / "harness.py"
    ).read_text(encoding="utf-8")


def test_image_launcher_identity_is_distinct_and_stable(tmp_path, monkeypatch):
    launcher_path = ROOT / "packaging" / "image" / "launcher.py"
    spec = importlib.util.spec_from_file_location("image_release_launcher", launcher_path)
    assert spec and spec.loader
    launcher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(launcher)

    app_root = tmp_path / "release" / "app"
    app_root.mkdir(parents=True)
    (app_root / "release-identity.json").write_text(
        json.dumps(
            {
                "build_id": "image-build-001",
                "product": "image-pptgen",
                "service": "image-pptgen-server",
                "skill": "generate-image-presentation",
                "skill_sha256": "a" * 64,
                "source_commit": "b" * 40,
                "runtime_content_sha256": "c" * 64,
                "surface": "public_image_3_0",
                "version": "0.1.0",
            }
        ),
        encoding="utf-8",
    )
    xdg_data = tmp_path / "xdg-data"
    xdg_config = tmp_path / "xdg-config"
    monkeypatch.setenv("XDG_DATA_HOME", str(xdg_data))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_config))

    first = launcher.prepare_runtime_identity(app_root)
    second = launcher.prepare_runtime_identity(app_root)

    assert first == second
    assert first["product"] == "image-pptgen"
    assert first["service"] == "image-pptgen-server"
    assert first["base_url"] == "http://127.0.0.1:3130"
    assert first["data_root"].endswith("/image-pptgen")
    assert first["config_root"].endswith("/image-pptgen")
    assert first["skill"] == "generate-image-presentation"
    assert (xdg_data / "image-pptgen" / "state" / "runtime-instance.json").exists()
    assert not (xdg_data / "pptgen").exists()
    assert not (xdg_config / "pptgen").exists()


def test_image_launcher_uses_explicit_then_active_then_windows_data_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    launcher_path = ROOT / "packaging" / "image" / "launcher.py"
    spec = importlib.util.spec_from_file_location("image_release_launcher_roots", launcher_path)
    assert spec and spec.loader
    launcher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(launcher)

    explicit_root = tmp_path / "explicit"
    active_root = tmp_path / "active"
    local_app_data = tmp_path / "local-app-data"
    monkeypatch.setenv("IMAGE_PPTGEN_DATA_ROOT", str(explicit_root))
    monkeypatch.setenv("PPTGEN_DATA_ROOT", str(active_root))
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))

    assert launcher._data_root() == explicit_root.resolve()

    monkeypatch.delenv("IMAGE_PPTGEN_DATA_ROOT")
    assert launcher._data_root() == active_root.resolve()

    monkeypatch.delenv("PPTGEN_DATA_ROOT")
    monkeypatch.setattr(
        launcher,
        "os",
        SimpleNamespace(name="nt", environ=os.environ),
    )
    assert launcher._data_root() == (local_app_data / "ImagePPTGen").resolve()


def test_candidate_image_doctor_joins_release_instance_and_safe_roots(tmp_path: Path) -> None:
    builder = _load_builder()
    result = builder.build_release(ROOT, tmp_path / "build", version="0.1.3")
    extract_root = tmp_path / "extract"
    with tarfile.open(result.archive_path, "r:gz") as archive:
        archive.extractall(extract_root)

    app_root = extract_root / "image-pptgen-0.1.3" / "app"
    release = json.loads((app_root / "release-identity.json").read_text(encoding="utf-8"))
    expected_preview_sha256 = hashlib.sha256(
        (ROOT / "frontend" / "dist" / "index.html").read_bytes()
    ).hexdigest()
    script = """
import importlib.util
import json
import sys
from pathlib import Path

app_root = Path(sys.argv[1])
launcher_spec = importlib.util.spec_from_file_location("candidate_image_launcher", app_root / "image-launcher.py")
launcher = importlib.util.module_from_spec(launcher_spec)
launcher_spec.loader.exec_module(launcher)
launcher.prepare_runtime_identity(app_root)
sys.path.insert(0, str(app_root))
import public_server
response = public_server.app.test_client().get(
    "/api/runtime-identity?product=caller-override&secret=do-not-return"
)
preview = public_server.app.test_client().get("/history/run/123/preview")
print(json.dumps({
    "status": response.status_code,
    "identity": response.get_json(),
    "preview_status": preview.status_code,
    "preview_content_type": preview.content_type,
    "preview_sha256": __import__("hashlib").sha256(preview.data).hexdigest(),
}))
"""
    environment = os.environ.copy()
    environment.update(
        {
            "PPTGEN_IMAGE_RUNTIME_IDENTITY_JSON": '{"product":"caller-override","secret":"do-not-return"}',
            "XDG_CONFIG_HOME": str(tmp_path / "xdg-config"),
            "XDG_DATA_HOME": str(tmp_path / "xdg-data"),
        }
    )
    completed = subprocess.run(
        [sys.executable, "-c", script, str(app_root)],
        cwd=app_root,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    observed = json.loads(completed.stdout)

    assert observed["status"] == 200
    assert observed["preview_status"] == 200
    assert observed["preview_content_type"] == "text/html; charset=utf-8"
    assert observed["preview_sha256"] == expected_preview_sha256
    assert observed["identity"] == {
        "artifacts_root": "image-pptgen/state/data/artifacts",
        "base_url": "http://127.0.0.1:3130",
        "build_id": release["build_id"],
        "data_root": "image-pptgen/state/data",
        "instance_id": observed["identity"]["instance_id"],
        "product": "image-pptgen",
        "service": "image-pptgen-server",
        "skill_sha256": release["skill_sha256"],
        "source_commit": release["source_commit"],
        "surface": "public_image_3_0",
        "runtime_content_sha256": release["runtime_content_sha256"],
        "version": "0.1.3",
    }
    assert observed["identity"]["instance_id"]
