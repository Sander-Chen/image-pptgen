from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tarfile

import pytest


ROOT = Path(__file__).resolve().parents[1]
INSTALLER_PATH = (
    ROOT / "packaging" / "image" / "platform" / "macos" / "installer.py"
)
CONTRACT_PATH = INSTALLER_PATH.with_name("contract.json")
FALLBACK_LOCK_PATH = ROOT / "packaging" / "image" / "fallback" / "fallback-lock.json"


def _load_installer():
    spec = importlib.util.spec_from_file_location("image_macos_installer", INSTALLER_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_archive(
    path: Path,
    *,
    version: str,
    unsafe_name: str | None = None,
    link: bool = False,
) -> None:
    prefix = f"image-pptgen-{version}"
    members = {
        f"{prefix}/app/runtime_manager.py": b"# runtime manager\n",
        f"{prefix}/app/image-launcher.py": b"# launcher\n",
        f"{prefix}/app/requirements.txt": b"flask\n",
        f"{prefix}/app/packages/pptgen_toolkit/pyproject.toml": b"[project]\nname='pptgen-toolkit'\nversion='0.0.0'\n",
        f"{prefix}/app/skills/generate-image-presentation/SKILL.md": (
            f"# Image skill {version}\n".encode("utf-8")
        ),
        f"{prefix}/macos/requirements.lock": (
            b"Flask==3.1.3 --hash=sha256:"
            + b"d" * 64
            + b"\n"
        ),
    }
    with tarfile.open(path, "w:gz") as archive:
        for name, payload in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(payload))
        if unsafe_name is not None:
            info = tarfile.TarInfo(unsafe_name)
            if link:
                info.type = tarfile.SYMTYPE
                info.linkname = "target"
            archive.addfile(info, io.BytesIO(b"") if not link else None)


def _write_manifest(path: Path, archive: Path, *, version: str) -> None:
    payload = archive.read_bytes()
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "version": version,
                "platform": "macos-arm64",
                "archive": {
                    "name": archive.name,
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "size": len(payload),
                },
            }
        ),
        encoding="utf-8",
    )


def _payload(tmp_path: Path, version: str = "1.2.3") -> tuple[Path, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    archive = tmp_path / f"image-pptgen-{version}-macos-arm64.tar.gz"
    manifest = tmp_path / "manifest.json"
    _write_archive(archive, version=version)
    _write_manifest(manifest, archive, version=version)
    return manifest, archive


def test_default_skill_home_follows_codex_home_without_creating_agents_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installer = _load_installer()
    home = tmp_path / "home"

    monkeypatch.delenv("CODEX_HOME", raising=False)
    default_layout = installer.InstallLayout.for_home(home)
    assert default_layout.root == (home / ".codex" / "image-pptgen").resolve()
    assert default_layout.bin_home == (home / ".codex" / "bin").resolve()
    assert default_layout.skill_home == (home / ".codex" / "skills").resolve()
    assert default_layout.skill_home != (home / ".agents" / "skills").resolve()

    codex_home = home / ".codex-managed"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    managed_layout = installer.InstallLayout.for_home(home)
    assert managed_layout.skill_home == (codex_home / "skills").resolve()

    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "other-user-codex"))
    inherited_layout = installer.InstallLayout.for_home(home)
    assert inherited_layout.skill_home == (home / ".codex" / "skills").resolve()

    explicit = home / "explicit-skills"
    overridden_layout = installer.InstallLayout.for_home(home, skill_home=explicit)
    assert overridden_layout.skill_home == explicit.resolve()


def _write_fallback_receipt(
    path: Path,
    fallback_root: Path,
    *,
    archive_sha256: str = "b21dbc3f3e01932fcc3f0f4c51e5a7ef61888cb454d23eee6e8207c6f52d0b04",
    archive_bytes: int = 27115492,
    attempts: list[dict[str, str]] | None = None,
) -> None:
    fallback_python = fallback_root / "python" / "install" / "bin" / "python3.11"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "platform": "macos-arm64",
                "freeze_id": "pbs-20260718-cp311-plus-cp312-v4",
                "decision": "fallback_authorized",
                "official_attempts": attempts
                or [
                    {"approach": "venv-ensurepip", "result": "failed"},
                    {"approach": "venv-host-pip", "result": "failed"},
                ],
                "fallback_runtime": {
                    "archive_sha256": archive_sha256,
                    "archive_bytes": archive_bytes,
                    "extracted_root": str(fallback_root.resolve()),
                    "python_path": str(fallback_python.resolve()),
                },
            }
        ),
        encoding="utf-8",
    )


def _prepare_install(layout, install_id: str, skill_text: str) -> Path:
    release = layout.releases / install_id
    venv = layout.venvs / install_id
    (release / "app" / "skills" / "generate-image-presentation").mkdir(
        parents=True
    )
    (venv / "bin").mkdir(parents=True)
    (release / "app" / "runtime_manager.py").write_text("# manager\n")
    (release / "app" / "image-launcher.py").write_text("# launcher\n")
    (release / "app" / "skills" / "generate-image-presentation" / "SKILL.md").write_text(
        skill_text,
        encoding="utf-8",
    )
    for name in ("python", "image-pptgen"):
        executable = venv / "bin" / name
        executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        executable.chmod(0o755)
    return release


def _marker(install_id: str, *, previous_install_id: str | None = None) -> dict[str, object]:
    return {
        "schema_version": 1,
        "install_id": install_id,
        "version": install_id.split("-", 1)[0],
        "platform": "macos-arm64",
        "archive_sha256": "a" * 64,
        "archive_size": 1,
        "runtime_source": "official",
        "python_version": "3.12.1",
        "previous_install_id": previous_install_id,
    }


def test_manifest_requires_exact_platform_size_and_sha256(tmp_path: Path) -> None:
    installer = _load_installer()
    manifest_path, archive = _payload(tmp_path)

    manifest = installer.load_manifest(manifest_path)
    installer.verify_archive(manifest, archive)

    archive.write_bytes(archive.read_bytes() + b"tamper")
    with pytest.raises(installer.InstallerError) as failure:
        installer.verify_archive(manifest, archive)
    assert failure.value.code == "archive_size_mismatch"

    manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_data["platform"] = "windows-amd64"
    manifest_path.write_text(json.dumps(manifest_data), encoding="utf-8")
    with pytest.raises(installer.InstallerError) as failure:
        installer.load_manifest(manifest_path)
    assert failure.value.code == "manifest_platform_mismatch"


@pytest.mark.parametrize(
    ("unsafe_name", "link"),
    [("../outside", False), ("/absolute", False), ("payload-link", True)],
)
def test_archive_extraction_rejects_unsafe_members(
    tmp_path: Path, unsafe_name: str, link: bool
) -> None:
    installer = _load_installer()
    archive = tmp_path / "unsafe.tar.gz"
    destination = tmp_path / "extract"
    _write_archive(
        archive,
        version="1.2.3",
        unsafe_name=unsafe_name,
        link=link,
    )

    with pytest.raises(installer.InstallerError) as failure:
        installer.safe_extract_archive(archive, destination)
    assert failure.value.code == "unsafe_archive_member"
    assert not (tmp_path / "outside").exists()


def test_runtime_selection_prefers_official_and_never_probes_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installer = _load_installer()
    official = tmp_path / "Codex.app" / "Contents" / "Resources" / "python"
    fallback = tmp_path / "fallback"
    official.parent.mkdir(parents=True)
    official.write_text("official", encoding="utf-8")
    official.chmod(0o755)
    (fallback / "bin").mkdir(parents=True)
    (fallback / "bin" / "python3").write_text("fallback", encoding="utf-8")
    (fallback / "bin" / "python3").chmod(0o755)
    probes: list[Path] = []

    def probe(candidate: Path) -> tuple[int, int, int]:
        probes.append(candidate)
        return (3, 12, 1)

    monkeypatch.setattr(installer, "_probe_python", probe)
    choice = installer.select_runtime(official, fallback)

    assert choice.source == "official"
    assert choice.executable == official.resolve()
    assert probes == [official.resolve()]


def test_runtime_probe_failure_can_use_receipted_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installer = _load_installer()
    official = tmp_path / "official-python"
    fallback_root = tmp_path / "fallback"
    fallback_python = fallback_root / "python" / "install" / "bin" / "python3.11"
    official.write_text("official", encoding="utf-8")
    official.chmod(0o755)
    fallback_python.parent.mkdir(parents=True)
    fallback_python.write_text("fallback", encoding="utf-8")
    fallback_python.chmod(0o755)
    receipt = tmp_path / "fallback-authorization.json"
    _write_fallback_receipt(receipt, fallback_root)
    probes: list[Path] = []

    def probe(candidate: Path) -> tuple[int, int, int]:
        probes.append(candidate)
        if candidate == official.resolve():
            raise installer.InstallerError("python_probe_failed", "official unavailable")
        return (3, 11, 15)

    monkeypatch.setattr(installer, "_probe_python", probe)

    choice = installer.select_runtime(
        official,
        fallback_root,
        fallback_authorization_receipt=receipt,
    )

    assert choice.source == "fallback"
    assert choice.executable == fallback_python.resolve()
    assert probes == [official.resolve(), fallback_python.resolve()]


@pytest.mark.parametrize(
    ("mutate", "expected_code"),
    [
        (lambda payload: payload.pop("fallback_runtime"), "fallback_not_authorized"),
        (
            lambda payload: payload["fallback_runtime"].__setitem__(
                "archive_sha256", "0" * 64
            ),
            "fallback_not_authorized",
        ),
        (
            lambda payload: payload.__setitem__(
                "official_attempts",
                [
                    {"approach": "venv-ensurepip", "result": "failed"},
                    {"approach": "venv-ensurepip", "result": "failed"},
                ],
            ),
            "fallback_not_authorized",
        ),
    ],
)
def test_runtime_probe_failure_rejects_unbound_fallback_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutate,
    expected_code: str,
) -> None:
    installer = _load_installer()
    official = tmp_path / "official-python"
    fallback_root = tmp_path / "fallback"
    fallback_python = fallback_root / "python" / "install" / "bin" / "python3.11"
    official.write_text("official", encoding="utf-8")
    official.chmod(0o755)
    fallback_python.parent.mkdir(parents=True)
    fallback_python.write_text("fallback", encoding="utf-8")
    fallback_python.chmod(0o755)
    receipt = tmp_path / "fallback-authorization.json"
    _write_fallback_receipt(receipt, fallback_root)
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    mutate(payload)
    receipt.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(
        installer,
        "_probe_python",
        lambda _candidate: (_ for _ in ()).throw(
            installer.InstallerError("python_probe_failed", "official unavailable")
        ),
    )

    with pytest.raises(installer.InstallerError) as failure:
        installer.select_runtime(
            official,
            fallback_root,
            fallback_authorization_receipt=receipt,
        )

    assert failure.value.code == expected_code


def test_runtime_selection_fails_closed_without_explicit_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installer = _load_installer()
    official = tmp_path / "official-python"
    official.write_text("official", encoding="utf-8")
    official.chmod(0o755)
    monkeypatch.setattr(
        installer,
        "_probe_python",
        lambda _candidate: (_ for _ in ()).throw(
            installer.InstallerError("python_probe_failed", "no runtime")
        ),
    )

    with pytest.raises(installer.InstallerError) as failure:
        installer.select_runtime(official, None)
    assert failure.value.code == "runtime_unavailable"


def test_frozen_fallback_directory_resolves_exact_locked_python_member(
    tmp_path: Path,
) -> None:
    installer = _load_installer()
    fallback_root = tmp_path / "fallback"
    python = fallback_root / "python" / "install" / "bin" / "python3.11"
    python.parent.mkdir(parents=True)
    python.write_text("fallback", encoding="utf-8")
    python.chmod(0o755)

    assert installer._fallback_executable(fallback_root) == python.resolve()


def test_fallback_directory_rejects_lookalike_python_paths(tmp_path: Path) -> None:
    installer = _load_installer()
    fallback_root = tmp_path / "fallback"
    lookalike = fallback_root / "bin" / "python3"
    lookalike.parent.mkdir(parents=True)
    lookalike.write_text("fallback", encoding="utf-8")
    lookalike.chmod(0o755)

    with pytest.raises(installer.InstallerError) as failure:
        installer._fallback_executable(fallback_root)

    assert failure.value.code == "fallback_unavailable"


def _native_wheelhouse(wheelhouse: Path, cpython_tag: str) -> None:
    wheelhouse.mkdir(parents=True)
    for distribution in ("charset_normalizer", "markupsafe", "pillow"):
        (wheelhouse / f"{distribution}-0-cpython-{cpython_tag}-{cpython_tag}-macosx_11_0_arm64.whl").touch()


def test_official_provisioning_approaches_use_different_pip_bootstraps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installer = _load_installer()
    app_root = tmp_path / "app"
    wheelhouse = tmp_path / "wheelhouse"
    (app_root / "packages" / "pptgen_toolkit").mkdir(parents=True)
    (app_root.parent / "macos").mkdir()
    (app_root.parent / "macos" / "requirements.lock").write_text(
        "flask==3.1.3 --hash=sha256:" + "d" * 64 + "\n",
        encoding="utf-8",
    )
    _native_wheelhouse(wheelhouse, "cp312")
    choice = installer.RuntimeChoice(
        "official", tmp_path / "official-python", "3.12.1"
    )
    commands: list[list[str]] = []
    monkeypatch.setattr(
        installer,
        "_run_checked",
        lambda command, **_kwargs: commands.append(command),
    )

    first_venv = tmp_path / "first-venv"
    installer._provision_venv(
        choice,
        first_venv,
        app_root,
        wheelhouse,
        approach="venv-ensurepip",
    )
    first_commands = list(commands)
    commands.clear()
    second_venv = tmp_path / "second-venv"
    installer._provision_venv(
        choice,
        second_venv,
        app_root,
        wheelhouse,
        approach="venv-host-pip",
    )
    second_commands = list(commands)

    assert "--without-pip" not in first_commands[0]
    assert "--without-pip" in second_commands[0]
    assert first_commands[1][:4] == [
        str(first_venv / "bin" / "python"),
        "-I",
        "-m",
        "pip",
    ]
    assert second_commands[1][:6] == [
        str(choice.executable),
        "-I",
        "-m",
        "pip",
        "--python",
        str(second_venv / "bin" / "python"),
    ]
    for commands in (first_commands, second_commands):
        dependency_install = commands[1]
        assert "--require-hashes" in dependency_install
        assert dependency_install[-2:] == [
            "-r", str(app_root.parent / "macos" / "requirements.lock")
        ]


def test_official_provisioning_prefers_prebuilt_toolkit_wheel_without_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installer = _load_installer()
    app_root = tmp_path / "app"
    toolkit = app_root / "packages" / "pptgen_toolkit"
    (toolkit / "dist").mkdir(parents=True)
    wheel = toolkit / "dist" / "image_pptgen_toolkit-0.1.0-py3-none-any.whl"
    wheel.write_bytes(b"prebuilt wheel")
    (app_root.parent / "macos").mkdir()
    (app_root.parent / "macos" / "requirements.lock").write_text(
        "flask==3.1.3 --hash=sha256:" + "d" * 64 + "\n",
        encoding="utf-8",
    )
    wheelhouse = tmp_path / "wheelhouse"
    _native_wheelhouse(wheelhouse, "cp312")
    choice = installer.RuntimeChoice(
        "official", tmp_path / "official-python", "3.12.1"
    )
    commands: list[list[str]] = []
    monkeypatch.setattr(
        installer,
        "_run_checked",
        lambda command, **_kwargs: commands.append(command),
    )

    installer._provision_venv(
        choice,
        tmp_path / "venv",
        app_root,
        wheelhouse,
        approach="venv-host-pip",
    )

    toolkit_install = commands[2]
    assert toolkit_install[-1] == str(wheel)
    assert "--no-build-isolation" not in toolkit_install
    assert "--no-deps" in toolkit_install


def test_wheelhouse_rejects_cp311_only_native_wheels_for_official_cp312(
    tmp_path: Path,
) -> None:
    installer = _load_installer()
    wheelhouse = tmp_path / "wheelhouse"
    _native_wheelhouse(wheelhouse, "cp311")

    with pytest.raises(installer.InstallerError) as failure:
        installer._assert_wheelhouse_supports_runtime(wheelhouse, "3.12.13")

    assert failure.value.code == "wheelhouse_python_abi_incompatible"
    assert failure.value.details == {
        "stage": "wheelhouse-abi-preflight",
        "runtime_version": "3.12.13",
        "expected_cpython_tag": "cp312",
        "missing_distributions": ["charset_normalizer", "markupsafe", "pillow"],
    }


def test_wheelhouse_accepts_cp311_fallback_and_cp312_official_runtime(
    tmp_path: Path,
) -> None:
    installer = _load_installer()
    wheelhouse = tmp_path / "wheelhouse"
    _native_wheelhouse(wheelhouse, "cp311")
    for distribution in ("charset_normalizer", "markupsafe", "pillow"):
        (wheelhouse / f"{distribution}-0-cpython-cp312-cp312-macosx_11_0_arm64.whl").touch()

    installer._assert_wheelhouse_supports_runtime(wheelhouse, "3.11.15")
    installer._assert_wheelhouse_supports_runtime(wheelhouse, "3.12.13")


def test_runtime_provision_failure_preserves_the_command_stage_and_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installer = _load_installer()
    runtime = installer.RuntimeChoice(
        "official", tmp_path / "official-python", "3.12.1"
    )

    def fail_provision(*_args, approach: str, **_kwargs) -> None:
        raise installer.InstallerError(
            "command_failed",
            "installer command failed",
            command="python",
            stage=f"{approach}:dependency-install",
            stderr="sandbox denied executable",
        )

    monkeypatch.setattr(installer, "_provision_venv", fail_provision)

    with pytest.raises(installer.InstallerError) as failure:
        installer._provision_with_fallback(
            runtime,
            fallback_python_dir=None,
            fallback_authorization_receipt=None,
            license_dir=None,
            venv_root=tmp_path / "venv",
            app_root=tmp_path / "app",
            wheelhouse=tmp_path / "wheelhouse",
        )

    assert failure.value.code == "runtime_provision_failed"
    assert failure.value.details == {
        "runtime_source": "official",
        "cause_code": "command_failed",
        "cause_details": {
            "command": "python",
            "stage": "venv-host-pip:dependency-install",
            "stderr": "sandbox denied executable",
        },
    }


@pytest.mark.parametrize("with_existing_activation", [False, True])
def test_activate_install_restores_links_and_marker_after_marker_write_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    with_existing_activation: bool,
) -> None:
    installer = _load_installer()
    layout = installer.InstallLayout.for_home(tmp_path / "home")
    installer._ensure_layout(layout)
    old_id = "1.0.0-deadbeef0001"
    new_id = "2.0.0-deadbeef0002"
    _prepare_install(layout, old_id, "old skill\n")
    _prepare_install(layout, new_id, "new skill\n")
    old_marker = _marker(old_id)
    if with_existing_activation:
        installer._activate_install(layout, old_marker)
        marker_before = layout.active_marker.read_bytes()
        release_before = layout.current_release.resolve()
        venv_before = layout.current_venv.resolve()
    else:
        marker_before = None
        release_before = None
        venv_before = None

    original_atomic_json = installer._atomic_json

    def fail_active_marker(path: Path, payload) -> None:
        if path == layout.active_marker:
            raise installer.InstallerError("marker_write_failed", "injected")
        original_atomic_json(path, payload)

    monkeypatch.setattr(installer, "_atomic_json", fail_active_marker)
    with pytest.raises(installer.InstallerError) as failure:
        installer._activate_install(layout, _marker(new_id, previous_install_id=old_id))

    assert failure.value.code == "marker_write_failed"
    if with_existing_activation:
        assert layout.active_marker.read_bytes() == marker_before
        assert layout.current_release.resolve() == release_before
        assert layout.current_venv.resolve() == venv_before
    else:
        assert not layout.active_marker.exists()
        assert not layout.current_release.exists()
        assert not layout.current_release.is_symlink()
        assert not layout.current_venv.exists()
        assert not layout.current_venv.is_symlink()


def test_install_is_user_scoped_atomic_and_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installer = _load_installer()
    manifest_path, archive = _payload(tmp_path)
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    home = tmp_path / "home"
    layout = installer.InstallLayout.for_home(home)
    runtime = installer.RuntimeChoice(
        source="official", executable=tmp_path / "official-python", version="3.12.1"
    )
    provisions: list[Path] = []

    def provision(
        _choice, venv_root: Path, _app_root: Path, _wheelhouse: Path, **_kwargs
    ) -> None:
        provisions.append(venv_root)
        (venv_root / "bin").mkdir(parents=True)
        for name in ("python", "image-pptgen"):
            path = venv_root / "bin" / name
            path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            path.chmod(0o755)

    monkeypatch.setattr(installer, "select_runtime", lambda *_args, **_kwargs: runtime)
    monkeypatch.setattr(installer, "_provision_venv", provision)
    monkeypatch.setattr(
        installer,
        "_command_scoped_cli_json",
        lambda *_args, **_kwargs: {"ok": True, "version": "1.2.3"},
    )

    first = installer.install_release(
        manifest_path=manifest_path,
        archive_path=archive,
        wheelhouse=wheelhouse,
        official_python=runtime.executable,
        fallback_python_dir=None,
        layout=layout,
    )
    second = installer.install_release(
        manifest_path=manifest_path,
        archive_path=archive,
        wheelhouse=wheelhouse,
        official_python=runtime.executable,
        fallback_python_dir=None,
        layout=layout,
    )

    assert first["ok"] is True and first["reused"] is False
    assert second["ok"] is True and second["reused"] is True
    assert len(provisions) == 1
    active = json.loads(layout.active_marker.read_text(encoding="utf-8"))
    assert active["install_id"] == "1.2.3-" + first["archive_sha256"][:12]
    assert active["runtime_source"] == "official"
    assert provisions[0] == layout.venvs / active["install_id"]
    assert layout.current_release.resolve() == (
        layout.releases / active["install_id"]
    ).resolve()
    assert layout.current_venv.resolve() == (
        layout.venvs / active["install_id"]
    ).resolve()
    assert (layout.bin_home / "image-pptgen").is_file()
    assert (layout.skill_home / "generate-image-presentation" / "SKILL.md").is_file()
    assert not list(layout.staging.glob("*"))


@pytest.mark.parametrize(
    "failure_stage", ("environment", "wrappers", "skill", "readiness")
)
def test_install_failure_after_activation_restores_previous_install_and_skill(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_stage: str,
) -> None:
    installer = _load_installer()
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    layout = installer.InstallLayout.for_home(tmp_path / "home")
    runtime = installer.RuntimeChoice(
        source="official", executable=tmp_path / "official-python", version="3.12.1"
    )

    def provision(
        _choice, venv_root: Path, _app_root: Path, _wheelhouse: Path, **_kwargs
    ) -> None:
        (venv_root / "bin").mkdir(parents=True)
        for name in ("python", "image-pptgen"):
            path = venv_root / "bin" / name
            path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            path.chmod(0o755)

    monkeypatch.setattr(installer, "select_runtime", lambda *_args, **_kwargs: runtime)
    monkeypatch.setattr(installer, "_provision_venv", provision)
    monkeypatch.setattr(
        installer,
        "_command_scoped_cli_json",
        lambda *_args, **_kwargs: {"ok": True},
    )

    first_manifest, first_archive = _payload(tmp_path / "v1", "1.0.0")
    installer.install_release(
        manifest_path=first_manifest,
        archive_path=first_archive,
        wheelhouse=wheelhouse,
        official_python=runtime.executable,
        fallback_python_dir=None,
        layout=layout,
    )
    previous_marker = layout.active_marker.read_bytes()
    previous_id = json.loads(previous_marker)["install_id"]
    previous_skill = (
        layout.skill_home / "generate-image-presentation" / "SKILL.md"
    ).read_bytes()

    if failure_stage == "environment":
        monkeypatch.setattr(
            installer,
            "_write_environment",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                installer.InstallerError("environment_failed", "injected")
            ),
        )
    elif failure_stage == "wrappers":
        monkeypatch.setattr(
            installer,
            "_install_wrappers",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                installer.InstallerError("wrappers_failed", "injected")
            ),
        )
    elif failure_stage == "skill":
        monkeypatch.setattr(
            installer,
            "_install_skill",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                installer.InstallerError("skill_failed", "injected")
            ),
        )
    else:
        monkeypatch.setattr(
            installer,
            "_command_scoped_cli_json",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                installer.InstallerError("readiness_failed", "injected")
            ),
        )

    second_manifest, second_archive = _payload(tmp_path / "v2", "2.0.0")
    with pytest.raises(installer.InstallerError) as failure:
        installer.install_release(
            manifest_path=second_manifest,
            archive_path=second_archive,
            wheelhouse=wheelhouse,
            official_python=runtime.executable,
            fallback_python_dir=None,
            layout=layout,
        )

    assert failure.value.code == f"{failure_stage}_failed"
    assert layout.active_marker.read_bytes() == previous_marker
    assert layout.current_release.resolve() == (layout.releases / previous_id).resolve()
    assert layout.current_venv.resolve() == (layout.venvs / previous_id).resolve()
    assert (
        layout.skill_home / "generate-image-presentation" / "SKILL.md"
    ).read_bytes() == previous_skill


def test_install_retries_official_provision_then_automatically_uses_local_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installer = _load_installer()
    manifest_path, archive = _payload(tmp_path)
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    fallback_root = tmp_path / "fallback"
    fallback_python = fallback_root / "python" / "install" / "bin" / "python3.11"
    fallback_python.parent.mkdir(parents=True)
    fallback_python.write_text("fallback", encoding="utf-8")
    fallback_python.chmod(0o755)
    receipt = tmp_path / "fallback-authorization.json"
    _write_fallback_receipt(receipt, fallback_root)
    official = installer.RuntimeChoice(
        source="official", executable=tmp_path / "official-python", version="3.12.1"
    )
    fallback = installer.RuntimeChoice(
        source="fallback", executable=fallback_python, version="3.11.9"
    )
    attempts: list[tuple[str, str]] = []
    licenses = tmp_path / "licenses"
    licenses.mkdir()
    (licenses / "NOTICE.txt").write_text("frozen licenses\n", encoding="utf-8")

    def provision(
        choice,
        venv_root: Path,
        _app_root: Path,
        _wheelhouse: Path,
        *,
        approach: str,
    ) -> None:
        attempts.append((choice.source, approach))
        if choice.source == "official":
            raise installer.InstallerError("command_failed", "official venv failed")
        (venv_root / "bin").mkdir(parents=True)
        for name in ("python", "image-pptgen"):
            path = venv_root / "bin" / name
            path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            path.chmod(0o755)

    monkeypatch.setattr(installer, "select_runtime", lambda *_args, **_kwargs: official)
    monkeypatch.setattr(installer, "_select_fallback_runtime", lambda _root: fallback)
    monkeypatch.setattr(installer, "_provision_venv", provision)
    monkeypatch.setattr(
        installer,
        "_command_scoped_cli_json",
        lambda *_args, **_kwargs: {"ok": True},
    )

    layout = installer.InstallLayout.for_home(tmp_path / "home")
    result = installer.install_release(
        manifest_path=manifest_path,
        archive_path=archive,
        wheelhouse=wheelhouse,
        official_python=official.executable,
        fallback_python_dir=fallback_root,
        layout=layout,
        fallback_freeze_id="pbs-20260718-cp311-plus-cp312-v4",
        license_dir=licenses,
        fallback_authorization_receipt=receipt,
    )

    assert attempts == [
        ("official", "venv-ensurepip"),
        ("official", "venv-host-pip"),
        ("fallback", "venv-ensurepip"),
    ]
    assert result["runtime_source"] == "fallback"
    assert result["python_version"] == "3.11.9"
    assert result["fallback_freeze_id"] == "pbs-20260718-cp311-plus-cp312-v4"
    assert [attempt["result"] for attempt in result["official_attempts"]] == [
        "failed",
        "failed",
    ]
    assert (layout.licenses / result["install_id"] / "NOTICE.txt").is_file()

    monkeypatch.setattr(
        installer,
        "select_runtime",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("reinstall must reuse the frozen environment")
        ),
    )
    with pytest.raises(installer.InstallerError) as failure:
        installer.install_release(
            manifest_path=manifest_path,
            archive_path=archive,
            wheelhouse=wheelhouse,
            official_python=tmp_path / "official-no-longer-available",
            fallback_python_dir=None,
            layout=layout,
        )
    assert failure.value.code == "fallback_not_authorized"

    repeated = installer.install_release(
        manifest_path=manifest_path,
        archive_path=archive,
        wheelhouse=wheelhouse,
        official_python=tmp_path / "official-no-longer-available",
        fallback_python_dir=fallback_root,
        layout=layout,
        fallback_freeze_id="pbs-20260718-cp311-plus-cp312-v4",
        license_dir=licenses,
        fallback_authorization_receipt=receipt,
    )
    assert repeated["reused"] is True
    assert repeated["runtime_source"] == "fallback"
    assert repeated["fallback_freeze_id"] == "pbs-20260718-cp311-plus-cp312-v4"


def test_install_two_versions_then_rollback_restores_previous_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installer = _load_installer()
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    layout = installer.InstallLayout.for_home(tmp_path / "home")
    runtime = installer.RuntimeChoice(
        source="official", executable=tmp_path / "official-python", version="3.12.1"
    )

    def provision(
        _choice, venv_root: Path, _app_root: Path, _wheelhouse: Path, **_kwargs
    ) -> None:
        (venv_root / "bin").mkdir(parents=True)
        for name in ("python", "image-pptgen"):
            path = venv_root / "bin" / name
            path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            path.chmod(0o755)

    calls: list[tuple[str, str | None]] = []

    def runtime_json(_layout, command: str, *, install_id=None, **_kwargs):
        calls.append((command, install_id))
        return {"ok": True, "command": command}

    def command_scoped_json(_layout, *arguments: str, install_id=None, **_kwargs):
        calls.append(("command-scoped:" + " ".join(arguments), install_id))
        return {"ok": True, "service": "image-pptgen"}

    monkeypatch.setattr(installer, "select_runtime", lambda *_args, **_kwargs: runtime)
    monkeypatch.setattr(installer, "_provision_venv", provision)
    monkeypatch.setattr(installer, "_runtime_manager_json", runtime_json)
    monkeypatch.setattr(installer, "_command_scoped_cli_json", command_scoped_json)

    first_manifest, first_archive = _payload(tmp_path / "v1", "1.0.0")
    second_manifest, second_archive = _payload(tmp_path / "v2", "2.0.0")
    installer.install_release(
        manifest_path=first_manifest,
        archive_path=first_archive,
        wheelhouse=wheelhouse,
        official_python=runtime.executable,
        fallback_python_dir=None,
        layout=layout,
    )
    first_id = json.loads(layout.active_marker.read_text())["install_id"]
    installer.install_release(
        manifest_path=second_manifest,
        archive_path=second_archive,
        wheelhouse=wheelhouse,
        official_python=runtime.executable,
        fallback_python_dir=None,
        layout=layout,
    )
    second_id = json.loads(layout.active_marker.read_text())["install_id"]

    result = installer.rollback(layout)
    active = json.loads(layout.active_marker.read_text(encoding="utf-8"))

    assert result["ok"] is True
    assert result["from_install_id"] == second_id
    assert result["install_id"] == first_id
    assert active["install_id"] == first_id
    assert active["previous_install_id"] == second_id
    assert layout.current_release.resolve() == (layout.releases / first_id).resolve()
    assert (
        layout.skill_home / "generate-image-presentation" / "SKILL.md"
    ).read_text(encoding="utf-8") == "# Image skill 1.0.0\n"
    assert ("stop", second_id) in calls
    assert ("command-scoped:doctor --json", first_id) in calls


@pytest.mark.parametrize("failure_stage", ["activation", "readiness", "skill"])
def test_rollback_failure_restores_original_activation_and_skill(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_stage: str,
) -> None:
    installer = _load_installer()
    layout = installer.InstallLayout.for_home(tmp_path / "home")
    installer._ensure_layout(layout)
    previous_id = "1.0.0-deadbeef0001"
    current_id = "2.0.0-deadbeef0002"
    previous_release = _prepare_install(layout, previous_id, "previous skill\n")
    _prepare_install(layout, current_id, "current skill\n")
    installer._atomic_json(
        previous_release / ".image-pptgen-install.json", _marker(previous_id)
    )
    current_marker = _marker(current_id, previous_install_id=previous_id)
    installer._activate_install(layout, current_marker)
    installer._install_skill(layout, layout.releases / current_id, current_id)

    original_activate = installer._activate_install
    original_install_skill = installer._install_skill

    if failure_stage == "activation":
        def fail_previous_activation(target_layout, marker) -> None:
            if marker["install_id"] == previous_id:
                raise installer.InstallerError("activation_failed", "injected")
            original_activate(target_layout, marker)

        monkeypatch.setattr(installer, "_activate_install", fail_previous_activation)
    elif failure_stage == "skill":
        def fail_previous_skill(target_layout, release, install_id):
            if install_id == previous_id:
                raise installer.InstallerError("skill_failed", "injected")
            return original_install_skill(target_layout, release, install_id)

        monkeypatch.setattr(installer, "_install_skill", fail_previous_skill)

    def runtime_json(_layout, command: str, *, install_id=None, **_kwargs):
        return {"ok": True, "command": command}

    def command_scoped_json(_layout, *_arguments: str, install_id=None, **_kwargs):
        if failure_stage == "readiness" and install_id == previous_id:
            raise installer.InstallerError("readiness_failed", "injected")
        return {"ok": True, "service": "image-pptgen"}

    monkeypatch.setattr(installer, "_runtime_manager_json", runtime_json)
    monkeypatch.setattr(installer, "_command_scoped_cli_json", command_scoped_json)

    with pytest.raises(installer.InstallerError) as failure:
        installer.rollback(layout)

    assert failure.value.code == f"{failure_stage}_failed"
    active = json.loads(layout.active_marker.read_text(encoding="utf-8"))
    assert active["install_id"] == current_id
    assert layout.current_release.resolve() == (layout.releases / current_id).resolve()
    assert layout.current_venv.resolve() == (layout.venvs / current_id).resolve()
    assert (
        layout.skill_home / "generate-image-presentation" / "SKILL.md"
    ).read_text(encoding="utf-8") == "current skill\n"


def test_doctor_and_stop_use_active_venv_python_and_runtime_manager(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installer = _load_installer()
    layout = installer.InstallLayout.for_home(tmp_path / "home")
    install_id = "1.2.3-deadbeef1234"
    release = layout.releases / install_id
    venv = layout.venvs / install_id
    (release / "app").mkdir(parents=True)
    (venv / "bin").mkdir(parents=True)
    (release / "app" / "runtime_manager.py").write_text("# manager\n")
    (release / "app" / "image-launcher.py").write_text("# launcher\n")
    (venv / "bin" / "python").write_text("python\n")
    (venv / "bin" / "image-pptgen").write_text("cli\n")
    for path in (venv / "bin" / "python", venv / "bin" / "image-pptgen"):
        path.chmod(0o755)
    installer._activate_install(
        layout,
        {
            "schema_version": 1,
            "install_id": install_id,
            "version": "1.2.3",
            "archive_sha256": "d" * 64,
            "archive_size": 42,
            "runtime_source": "official",
            "python_version": "3.12.1",
            "previous_install_id": None,
        },
    )
    observed: list[tuple[str, str | None]] = []

    def runtime_json(_layout, command: str, *, install_id=None, **_kwargs):
        observed.append((command, install_id))
        return {"ok": True, "command": command}

    monkeypatch.setattr(installer, "_runtime_manager_json", runtime_json)
    monkeypatch.setattr(
        installer,
        "_command_scoped_cli_json",
        lambda *_args, **_kwargs: {"ok": True, "service": "image-pptgen"},
    )

    doctor = installer.doctor(layout)
    stopped = installer.stop(layout)

    assert doctor["ok"] is True
    assert doctor["install_id"] == install_id
    assert doctor["cli"]["service"] == "image-pptgen"
    assert stopped["ok"] is True
    assert doctor["runtime"] == {"ok": True, "mode": "command_scoped"}
    assert observed == [("stop", install_id)]


def test_command_scoped_doctor_uses_active_release_and_explicit_install_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installer = _load_installer()
    layout = installer.InstallLayout.for_home(tmp_path / "home")
    install_id = "1.2.3-deadbeef1234"
    _prepare_install(layout, install_id, "skill\n")
    helper = layout.releases / install_id / "macos" / "image-pptgen-held-command.sh"
    helper.parent.mkdir(parents=True)
    observed = tmp_path / "observed.txt"
    helper.write_text(
        "#!/bin/sh\n"
        'printf "%s|%s\\n" "$IMAGE_PPTGEN_INSTALL_ROOT" "$*" > "$OBSERVED"\n'
        "printf '%s\\n' '{\"ok\":true,\"service\":\"image-pptgen\"}'\n",
        encoding="utf-8",
    )
    helper.chmod(0o755)
    monkeypatch.setenv("OBSERVED", str(observed))

    result = installer._command_scoped_cli_json(
        layout,
        "doctor",
        "--json",
        install_id=install_id,
    )

    assert result == {"ok": True, "service": "image-pptgen"}
    assert observed.read_text(encoding="utf-8") == f"{layout.root}|doctor --json\n"


def test_failed_activation_can_restore_preexisting_skill(
    tmp_path: Path,
) -> None:
    installer = _load_installer()
    layout = installer.InstallLayout.for_home(tmp_path / "home")
    installer._ensure_layout(layout)
    target = layout.skill_home / "generate-image-presentation"
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text("old skill\n", encoding="utf-8")
    release = layout.releases / "2.0.0-deadbeef1234"
    source = release / "app" / "skills" / "generate-image-presentation"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text("new skill\n", encoding="utf-8")

    activation = installer._install_skill(
        layout, release, "2.0.0-deadbeef1234"
    )
    assert (target / "SKILL.md").read_text(encoding="utf-8") == "new skill\n"

    installer._restore_skill(activation, layout)

    assert (target / "SKILL.md").read_text(encoding="utf-8") == "old skill\n"


def test_macos_contract_matches_frozen_fallback_authority_exactly() -> None:
    installer = _load_installer()
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    fallback_lock = json.loads(FALLBACK_LOCK_PATH.read_text(encoding="utf-8"))
    frozen = contract["runtime_selection"]["frozen_inputs"]
    authority = fallback_lock["platforms"]["macos-arm64"]

    assert installer.FALLBACK_FREEZE_ID == fallback_lock["freeze_id"]
    assert contract["runtime_selection"]["official_approaches"] == [
        "venv-ensurepip",
        "venv-host-pip",
    ]
    assert contract["runtime_selection"]["fallback_authorization_argument"] == (
        "--fallback-authorization-receipt"
    )
    assert contract["install"] == {
        "default_root": "~/.codex/image-pptgen",
        "default_bin_root": "~/.codex/bin",
        "default_skill_root": "~/.codex/skills",
        "requires_administrator": False,
        "active_marker": "active.json",
        "release_directory": "releases/{install_id}",
        "venv_directory": "venvs/{install_id}",
        "python": "venvs/{install_id}/bin/python",
        "runtime_manager": "releases/{install_id}/app/runtime_manager.py",
        "requirements_lock": "macos/requirements.lock",
    }
    assert contract["runtime_selection"]["fallback_authorization"] == {
        "schema_version": 1,
        "freeze_id": fallback_lock["freeze_id"],
        "platform": "macos-arm64",
        "decision": "fallback_authorized",
        "official_attempt_count": 2,
        "approaches_must_differ": True,
        "results_must_be": "failed",
        "runtime_receipt_fields": [
            "archive_sha256",
            "archive_bytes",
            "extracted_root",
            "python_path",
        ],
    }
    for contract_prefix, authority_key in (
        ("runtime_archive", "runtime_asset"),
        ("wheelhouse_bundle", "wheelhouse_bundle"),
        ("license_bundle", "license_bundle"),
    ):
        expected = authority[authority_key]
        assert frozen[contract_prefix] == expected["filename"]
        assert frozen[f"{contract_prefix}_sha256"] == expected["sha256"]
        assert frozen[f"{contract_prefix}_bytes"] == expected["bytes"]
    assert frozen["runtime_extracted_python"] == (
        "python/" + authority["python_json"]["python_exe"]
    )
    assert contract["payload"]["required_members"] == [
        "app/runtime_manager.py",
        "app/image-launcher.py",
        "app/skills/generate-image-presentation/SKILL.md",
        "app/packages/pptgen_toolkit/pyproject.toml",
        "app/packages/pptgen_toolkit/dist/image_pptgen_toolkit-0.1.0-py3-none-any.whl",
        "macos/requirements.lock",
    ]
    assert contract["install"]["requirements_lock"] == "macos/requirements.lock"
    assert fallback_lock["policy"]["network_during_install"] is False
    assert contract["runtime_selection"]["network_discovery"] is False
    expected_first_download = (
        fallback_lock["application_payload_reference"]["bytes"]
        + authority["runtime_asset"]["bytes"]
        + authority["wheelhouse_bundle"]["bytes"]
        + authority["license_bundle"]["bytes"]
    )
    assert authority["budget"]["first_download_bytes"] == expected_first_download
    assert expected_first_download < fallback_lock["policy"]["target_first_download_bytes"]


def test_macos_requirements_lock_permits_each_frozen_native_wheel_abi() -> None:
    fallback_lock = json.loads(FALLBACK_LOCK_PATH.read_text(encoding="utf-8"))
    requirement_lines = {
        line.split("==", 1)[0].casefold(): line
        for line in (INSTALLER_PATH.parent / "requirements.lock").read_text(encoding="utf-8").splitlines()
        if line
    }
    distribution_to_requirement = {
        "charset_normalizer": "charset-normalizer",
        "markupsafe": "markupsafe",
        "pillow": "pillow",
    }
    for wheel in fallback_lock["platforms"]["macos-arm64"]["wheels"]:
        filename = wheel["filename"]
        for distribution, requirement in distribution_to_requirement.items():
            if filename.startswith(f"{distribution}-"):
                assert f"--hash=sha256:{wheel['sha256']}" in requirement_lines[requirement]
