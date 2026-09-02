from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import zipfile

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = (
    ROOT
    / "packaging"
    / "image"
    / "platform"
    / "windows"
    / "windows_installer.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("image_windows_installer", MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _payload(tmp_path: Path, version: str) -> Path:
    archive = tmp_path / f"image-pptgen-{version}-windows-amd64.zip"
    prefix = f"image-pptgen-{version}"
    release_identity = {
        "build_id": f"windows-build-{version}",
        "product": "image-pptgen",
        "service": "image-pptgen-server",
        "skill": "generate-image-presentation",
        "skill_sha256": "a" * 64,
        "source_commit": "b" * 40,
        "runtime_content_sha256": "c" * 64,
        "surface": "public_image_3_0",
        "version": version,
    }
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as handle:
        handle.writestr(f"{prefix}/app/runtime_manager.py", "print('manager')\n")
        handle.writestr(f"{prefix}/app/image-launcher.py", "print('launcher')\n")
        handle.writestr(
            f"{prefix}/app/release-identity.json",
            json.dumps(release_identity, sort_keys=True),
        )
        handle.writestr(
            f"{prefix}/app/skills/generate-image-presentation/SKILL.md",
            f"# Windows skill {version}\n",
        )
        handle.writestr(
            f"{prefix}/windows/requirements.lock",
            "Flask==3.1.3 --hash=sha256:" + "d" * 64 + "\n",
        )
        handle.writestr(
            f"{prefix}/windows/fallback-lock.json",
            json.dumps({"freeze_id": "pbs-20260718-cp311-plus-cp312-v4"}),
        )
        handle.writestr(
            f"{prefix}/licenses/windows-amd64-licenses.zip", b"licenses"
        )
        handle.writestr(f"{prefix}/wheelhouse/example.whl", b"wheel")
    return archive


def _request(
    module,
    tmp_path: Path,
    archive: Path,
    version: str,
    source: str = "official",
    runtime_selection_receipt: Path | None = None,
):
    base_python = tmp_path / "codex-runtime" / "python.exe"
    base_python.parent.mkdir(parents=True, exist_ok=True)
    base_python.write_bytes(b"python")
    return module.InstallRequest(
        payload=archive,
        payload_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
        payload_size=archive.stat().st_size,
        version=version,
        install_root=tmp_path / "install",
        skill_root=tmp_path / "skills",
        base_python=base_python,
        runtime_source=source,
        platform_root=MODULE_PATH.parent,
        runtime_selection_receipt=runtime_selection_receipt,
    )


def _fake_venv(_base_python: Path, venv_root: Path, _release_root: Path) -> None:
    scripts = venv_root / "Scripts"
    scripts.mkdir(parents=True)
    (scripts / "python.exe").write_bytes(b"python")
    (scripts / "image-pptgen.exe").write_bytes(b"cli")


def _fake_final_launcher_rebind(venv_root: Path, _release_root: Path) -> None:
    assert venv_root.name.startswith("staging-") is False
    assert (venv_root / "Scripts" / "python.exe").is_file()
    assert (venv_root / "Scripts" / "image-pptgen.exe").is_file()


def _patch_fake_venv_transaction(monkeypatch: pytest.MonkeyPatch, module) -> None:
    monkeypatch.setattr(module, "_create_venv", _fake_venv)
    monkeypatch.setattr(module, "_rebind_final_console_launcher", _fake_final_launcher_rebind)


def _attempt(
    approach: str,
    result: str,
    *,
    stage: str | None = None,
    stderr: str = "",
) -> dict[str, object]:
    return {
        "approach": approach,
        "result": result,
        "stage": stage or f"official-probe:{approach}:pip",
        "exit_code": 0 if result == "succeeded" else 127,
        "stderr": stderr,
        "stdout": "",
        "exception": "",
    }


def _runtime_selection_receipt(
    module,
    path: Path,
    *,
    source: str,
    base_python: Path,
    selected_approach: str = "venv-explicit-ensurepip",
) -> Path:
    if source == "official":
        attempts = (
            [_attempt("venv-ensurepip", "succeeded")]
            if selected_approach == "venv-ensurepip"
            else [
                _attempt("venv-ensurepip", "failed"),
                _attempt("venv-explicit-ensurepip", "succeeded"),
            ]
        )
        receipt: dict[str, object] = {
            "schema_version": 1,
            "platform": module.PLATFORM,
            "decision": "official_selected",
            "selected_approach": selected_approach,
            "official_attempts": attempts,
        }
    else:
        receipt = {
            "schema_version": 1,
            "platform": module.PLATFORM,
            "freeze_id": module._FALLBACK_FREEZE_ID,
            "decision": "fallback_authorized",
            "official_attempts": [
                _attempt("venv-ensurepip", "failed", stderr="token=should-redact"),
                _attempt("venv-explicit-ensurepip", "failed"),
            ],
            "fallback_runtime": {
                "archive_sha256": module._FALLBACK_ARCHIVE_SHA256,
                "archive_bytes": module._FALLBACK_ARCHIVE_BYTES,
                "extracted_root": str(base_python.parent.resolve()),
                "python_path": str(base_python.resolve()),
            },
        }
    path.write_text(json.dumps(receipt), encoding="utf-8")
    return path


def test_offline_venv_prefers_prebuilt_toolkit_wheel_without_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_module()
    base_python = tmp_path / "codex-runtime" / "python.exe"
    base_python.parent.mkdir(parents=True)
    base_python.write_bytes(b"python")
    release_root = tmp_path / "release"
    (release_root / "windows").mkdir(parents=True)
    (release_root / "windows" / "requirements.lock").write_text(
        "Flask==3.1.3 --hash=sha256:" + "d" * 64 + "\n",
        encoding="utf-8",
    )
    (release_root / "wheelhouse").mkdir()
    toolkit = release_root / "app" / "packages" / "pptgen_toolkit"
    (toolkit / "dist").mkdir(parents=True)
    wheel = toolkit / "dist" / "image_pptgen_toolkit-0.1.0-py3-none-any.whl"
    wheel.write_bytes(b"prebuilt wheel")
    venv_root = tmp_path / "venv"
    commands: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs) -> None:
        commands.append(command)
        if command[1:3] == ["-m", "venv"]:
            scripts = venv_root / "Scripts"
            scripts.mkdir(parents=True)
            (scripts / "python.exe").write_bytes(b"python")
            (scripts / "image-pptgen.exe").write_bytes(b"cli")

    monkeypatch.setattr(module, "_run_checked", fake_run)
    module._create_venv(base_python, venv_root, release_root)

    toolkit_install = next(command for command in commands if command[-1] == str(wheel))
    assert "--no-deps" in toolkit_install
    assert "--no-build-isolation" not in toolkit_install
    assert str(toolkit) not in toolkit_install


def test_payload_contract_checks_size_hash_and_expected_root(tmp_path: Path) -> None:
    module = _load_module()
    archive = _payload(tmp_path, "1.2.3")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()

    contract = module.validate_payload(
        archive,
        expected_size=archive.stat().st_size,
        expected_sha256=digest,
        version="1.2.3",
    )

    assert contract.root_name == "image-pptgen-1.2.3"
    assert contract.sha256 == digest
    with pytest.raises(module.InstallerError) as wrong_size:
        module.validate_payload(
            archive,
            expected_size=archive.stat().st_size + 1,
            expected_sha256=digest,
            version="1.2.3",
        )
    assert wrong_size.value.code == "payload_size_mismatch"
    with pytest.raises(module.InstallerError) as wrong_hash:
        module.validate_payload(
            archive,
            expected_size=archive.stat().st_size,
            expected_sha256="0" * 64,
            version="1.2.3",
        )
    assert wrong_hash.value.code == "payload_sha256_mismatch"


@pytest.mark.parametrize(
    "member_name,external_attr",
    [
        ("../outside", 0),
        ("/absolute", 0),
        ("C:/drive", 0),
        ("root\\escape", 0),
        ("root/CON.txt", 0),
        ("root/file.txt:stream", 0),
        ("root/link", (stat.S_IFLNK | 0o777) << 16),
    ],
)
def test_payload_contract_rejects_windows_unsafe_members(
    tmp_path: Path, member_name: str, external_attr: int
) -> None:
    module = _load_module()
    archive = tmp_path / "unsafe.zip"
    info = zipfile.ZipInfo(member_name)
    info.external_attr = external_attr
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr(info, b"unsafe")

    with pytest.raises(module.InstallerError) as failure:
        module.validate_payload(
            archive,
            expected_size=archive.stat().st_size,
            expected_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
            version="1.0.0",
        )

    assert failure.value.code == "unsafe_payload_member"


def test_payload_contract_rejects_case_insensitive_duplicate(tmp_path: Path) -> None:
    module = _load_module()
    archive = tmp_path / "duplicate.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("image-pptgen-1.0.0/App/file.txt", b"first")
        handle.writestr("image-pptgen-1.0.0/app/FILE.txt", b"second")

    with pytest.raises(module.InstallerError) as failure:
        module.validate_payload(
            archive,
            expected_size=archive.stat().st_size,
            expected_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
            version="1.0.0",
        )

    assert failure.value.code == "unsafe_payload_member"


def test_install_repeat_upgrade_and_manual_rollback_are_atomic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_module()
    _patch_fake_venv_transaction(monkeypatch, module)
    first_archive = _payload(tmp_path, "1.0.0")
    first_request = _request(module, tmp_path, first_archive, "1.0.0")

    first = module.install_release(first_request)
    repeated = module.install_release(first_request)

    assert first["ok"] is True and first["reused"] is False
    assert repeated["ok"] is True and repeated["reused"] is True
    state_path = first_request.install_root / "state" / "windows-install-state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["active"]["runtime_source"] == "official"
    assert Path(state["active"]["venv_root"]).joinpath(
        "Scripts", "python.exe"
    ).is_file()
    assert Path(state["active"]["release_root"]).joinpath(
        "app", "runtime_manager.py"
    ).is_file()

    second_archive = _payload(tmp_path, "1.1.0")
    second_request = _request(
        module, tmp_path, second_archive, "1.1.0", source="fallback"
    )
    upgraded = module.install_release(second_request)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert upgraded["reused"] is False
    assert state["active"]["version"] == "1.1.0"
    assert state["active"]["runtime_source"] == "fallback"
    assert state["previous"]["version"] == "1.0.0"
    assert state["active"]["install_id"] != state["previous"]["install_id"]
    old_release = Path(state["previous"]["release_root"])
    assert old_release.joinpath("app", "runtime_manager.py").is_file()
    assert (tmp_path / "skills" / "generate-image-presentation" / "SKILL.md").read_text(
        encoding="utf-8"
    ) == "# Windows skill 1.1.0\n"

    rolled_back = module.rollback(first_request.install_root, stop_active=False)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert rolled_back["ok"] is True
    assert state["active"]["version"] == "1.0.0"
    assert state["previous"]["version"] == "1.1.0"
    assert (tmp_path / "skills" / "generate-image-presentation" / "SKILL.md").read_text(
        encoding="utf-8"
    ) == "# Windows skill 1.0.0\n"


def test_final_path_rebind_runs_after_promotion_and_before_state_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_module()
    archive = _payload(tmp_path, "1.0.0")
    request = _request(module, tmp_path, archive, "1.0.0")
    observed: dict[str, Path] = {}

    def fake_venv(_base_python: Path, venv_root: Path, _release_root: Path) -> None:
        observed["staging"] = venv_root
        _fake_venv(_base_python, venv_root, _release_root)

    def final_rebind(venv_root: Path, release_root: Path) -> None:
        observed["final_venv"] = venv_root
        observed["final_release"] = release_root
        assert not venv_root.name.startswith("staging-")
        assert not release_root.name.startswith("staging-")
        assert not observed["staging"].exists()
        assert not (request.install_root / "state" / "windows-install-state.json").exists()
        assert (venv_root / "Scripts" / "python.exe").is_file()
        assert (venv_root / "Scripts" / "image-pptgen.exe").is_file()

    monkeypatch.setattr(module, "_create_venv", fake_venv)
    monkeypatch.setattr(module, "_rebind_final_console_launcher", final_rebind)

    result = module.install_release(request)

    assert result["ok"] is True
    assert observed["final_venv"].name == result["active"]["install_id"]
    assert observed["final_release"].name == result["active"]["install_id"]
    assert not observed["staging"].exists()


def test_final_rebind_uses_final_python_offline_toolkit_and_probes_launcher(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_module()
    release_root = tmp_path / "release"
    wheelhouse = release_root / "wheelhouse"
    wheelhouse.mkdir(parents=True)
    toolkit = release_root / "app" / "packages" / "pptgen_toolkit" / "dist"
    toolkit.mkdir(parents=True)
    wheel = toolkit / "image_pptgen_toolkit-0.1.0-py3-none-any.whl"
    wheel.write_bytes(b"prebuilt wheel")
    venv_root = tmp_path / "venvs" / "final-release"
    scripts = venv_root / "Scripts"
    scripts.mkdir(parents=True)
    python = scripts / "python.exe"
    python.write_bytes(b"python")
    cli = scripts / "image-pptgen.exe"
    commands: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs) -> None:
        commands.append(command)
        if command[-1] == str(wheel):
            cli.write_bytes(b"final-cli")

    monkeypatch.setattr(module, "_run_checked", fake_run)

    module._rebind_final_console_launcher(venv_root, release_root)

    toolkit_install = next(command for command in commands if command[-1] == str(wheel))
    assert toolkit_install[0] == str(python)
    assert "--no-index" in toolkit_install
    assert "--force-reinstall" in toolkit_install
    assert "--no-deps" in toolkit_install
    assert commands[-1] == [str(cli), "--help"]


def test_failed_final_rebind_preserves_previous_active_release_and_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_module()
    _patch_fake_venv_transaction(monkeypatch, module)
    first_archive = _payload(tmp_path, "1.0.0")
    first_request = _request(module, tmp_path, first_archive, "1.0.0")
    module.install_release(first_request)
    state_path = first_request.install_root / "state" / "windows-install-state.json"
    old_state = state_path.read_bytes()

    def fail_rebind(_venv_root: Path, _release_root: Path) -> None:
        raise module.InstallerError("runtime_probe_failed", "injected final launcher failure")

    monkeypatch.setattr(module, "_rebind_final_console_launcher", fail_rebind)
    second_archive = _payload(tmp_path, "1.1.0")
    second_request = _request(module, tmp_path, second_archive, "1.1.0")
    with pytest.raises(module.InstallerError, match="injected final launcher failure"):
        module.install_release(second_request)

    assert state_path.read_bytes() == old_state
    state = json.loads(old_state)
    assert Path(state["active"]["release_root"]).is_dir()
    assert Path(state["active"]["venv_root"]).is_dir()
    assert not any(
        path.name.startswith("1.1.0-")
        for path in (first_request.install_root / "releases").iterdir()
    )
    assert not any(
        path.name.startswith("1.1.0-")
        for path in (first_request.install_root / "venvs").iterdir()
    )


def test_run_json_failure_includes_bounded_child_returncode_and_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_module()
    output = "x" * 3000
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=["child"], returncode=23, stdout="", stderr=output
        ),
    )

    with pytest.raises(module.InstallerError) as failure:
        module._run_json(["child"], env={}, code="doctor_product_failed")

    assert failure.value.details["returncode"] == 23
    assert failure.value.details["output"] == output[-2000:]


def test_failed_upgrade_preserves_old_state_and_removes_owned_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_module()
    _patch_fake_venv_transaction(monkeypatch, module)
    first_archive = _payload(tmp_path, "1.0.0")
    first_request = _request(module, tmp_path, first_archive, "1.0.0")
    module.install_release(first_request)
    state_path = first_request.install_root / "state" / "windows-install-state.json"
    old_state = state_path.read_bytes()

    def fail_venv(_base_python: Path, _venv_root: Path, _release_root: Path) -> None:
        raise module.InstallerError("venv_failed", "injected failure")

    monkeypatch.setattr(module, "_create_venv", fail_venv)
    second_archive = _payload(tmp_path, "1.1.0")
    second_request = _request(module, tmp_path, second_archive, "1.1.0")
    with pytest.raises(module.InstallerError) as failure:
        module.install_release(second_request)

    assert failure.value.code == "venv_failed"
    assert state_path.read_bytes() == old_state
    assert not list((first_request.install_root / "releases").glob("*.staging-*"))
    assert not list((first_request.install_root / "venvs").glob("*.staging-*"))
    assert not any(
        path.name.startswith("1.1.0-")
        for path in (first_request.install_root / "releases").iterdir()
    )


def test_long_release_version_uses_version_free_transaction_paths_and_cleans_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_module()
    _patch_fake_venv_transaction(monkeypatch, module)
    first_archive = _payload(tmp_path, "1.0.0")
    first_request = _request(module, tmp_path, first_archive, "1.0.0")
    module.install_release(first_request)
    state_path = first_request.install_root / "state" / "windows-install-state.json"
    old_state = state_path.read_bytes()

    version = "0.0.0-desktop-cloudflare-r8"
    second_archive = _payload(tmp_path, version)
    second_request = _request(module, tmp_path, second_archive, version)
    observed: dict[str, Path] = {}
    real_extract = module._extract_payload

    def capture_extract(payload: Path, destination: Path, contract):
        observed["payload_stage"] = payload
        observed["extract_stage"] = destination
        return real_extract(payload, destination, contract)

    def fail_venv(_base_python: Path, venv_root: Path, release_root: Path) -> None:
        observed["venv_stage"] = venv_root
        observed["release_stage"] = release_root
        raise module.InstallerError("venv_failed", "injected long-version failure")

    monkeypatch.setattr(module, "_extract_payload", capture_extract)
    monkeypatch.setattr(module, "_create_venv", fail_venv)
    with pytest.raises(module.InstallerError, match="injected long-version failure"):
        module.install_release(second_request)

    assert set(observed) == {
        "payload_stage",
        "extract_stage",
        "release_stage",
        "venv_stage",
    }
    for path in observed.values():
        assert version not in path.name
        assert not path.name.startswith(".0.0.0")
    assert state_path.read_bytes() == old_state
    assert not any(
        path.name.startswith((".staging-", ".payload-", "staging-"))
        for path in (second_request.install_root / "releases").iterdir()
    )
    assert not any(
        path.name.startswith("staging-")
        for path in (second_request.install_root / "venvs").iterdir()
    )


def test_installer_never_guesses_or_downloads_a_python_runtime() -> None:
    module = _load_module()
    powershell = (MODULE_PATH.parent / "install.ps1").read_text(encoding="utf-8")
    source = MODULE_PATH.read_text(encoding="utf-8")

    assert "OfficialPython" in powershell
    assert "FallbackPythonRoot" in powershell
    assert powershell.index("OfficialPython") < powershell.index("FallbackPythonRoot")
    assert "python.exe" in powershell
    assert "install\\python.exe" not in powershell
    assert "FallbackAuthorizationFile" in powershell
    assert "RuntimeSelectionReceipt" in powershell
    assert "--runtime-selection-receipt $RuntimeSelectionReceipt" in powershell
    assert "pbs-20260718-cp311-plus-cp312-v4" in powershell
    assert "$Attempts.Count -ne 2" in powershell
    assert "$Approaches.Count -ne 2" in powershell
    assert "a48c2dbe832319f61aa8557c9900caec70f7fed0cbee391a4c9ff9f98b50222d" in powershell
    assert "$ReceiptRoot -ne $FallbackRoot" in powershell
    assert "$ReceiptPython -ne $ExpectedFallbackPython" in powershell
    assert "Invoke-WebRequest" not in powershell
    assert "Start-BitsTransfer" not in powershell
    assert "winget" not in powershell.lower()
    assert "https://" not in powershell
    assert "urlopen" not in source
    assert "requests" not in source
    assert "shell=True" not in source
    assert source.count("shell=False") >= 2


def test_installer_fallback_receipt_path_is_relative_to_extracted_python_root(
    tmp_path: Path,
) -> None:
    lock = json.loads(
        (ROOT / "packaging" / "image" / "fallback" / "fallback-lock.json").read_text(
            encoding="utf-8"
        )
    )
    relative_python = lock["platforms"]["windows-amd64"]["runtime_archive_layout"][
        "python_exe"
    ]
    assert relative_python == "python.exe"
    extracted_root = tmp_path / "fallback-runtime" / "python"
    expected_python = extracted_root / "python.exe"
    expected_python.parent.mkdir(parents=True)
    expected_python.write_bytes(b"fixture-python")
    receipt = {
        "extracted_root": str(extracted_root.resolve()),
        "python_path": str(expected_python.resolve()),
    }
    assert Path(receipt["extracted_root"]).joinpath(*relative_python.split("/")) == Path(
        receipt["python_path"]
    )

    powershell = (MODULE_PATH.parent / "install.ps1").read_text(encoding="utf-8")
    assert "Join-Path $FallbackRoot 'python.exe'" in powershell
    assert "Join-Path $FallbackRoot 'install\\python.exe'" not in powershell


def test_state_commit_failure_restores_old_release_and_skill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_module()
    _patch_fake_venv_transaction(monkeypatch, module)
    first_archive = _payload(tmp_path, "1.0.0")
    first_request = _request(module, tmp_path, first_archive, "1.0.0")
    module.install_release(first_request)
    state_path = first_request.install_root / "state" / "windows-install-state.json"
    old_state = state_path.read_bytes()
    old_skill = (
        tmp_path / "skills" / "generate-image-presentation" / "SKILL.md"
    ).read_bytes()
    real_write = module._atomic_write_json

    def fail_state(path: Path, payload) -> None:
        if path.name == "windows-install-state.json":
            raise OSError("injected state commit failure")
        real_write(path, payload)

    monkeypatch.setattr(module, "_atomic_write_json", fail_state)
    second_archive = _payload(tmp_path, "1.1.0")
    second_request = _request(module, tmp_path, second_archive, "1.1.0")
    with pytest.raises(OSError, match="injected state commit failure"):
        module.install_release(second_request)

    assert state_path.read_bytes() == old_state
    assert (
        tmp_path / "skills" / "generate-image-presentation" / "SKILL.md"
    ).read_bytes() == old_skill
    assert not any(
        path.name.startswith("1.1.0-")
        for path in (first_request.install_root / "releases").iterdir()
    )
    assert not any(
        path.name.startswith("1.1.0-")
        for path in (first_request.install_root / "venvs").iterdir()
    )


def test_release_identity_must_match_requested_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_module()
    monkeypatch.setattr(module, "_create_venv", _fake_venv)
    archive = _payload(tmp_path, "1.0.0")
    rewritten = tmp_path / "identity-mismatch.zip"
    with zipfile.ZipFile(archive) as source, zipfile.ZipFile(
        rewritten, "w", compression=zipfile.ZIP_DEFLATED
    ) as target:
        for info in source.infolist():
            value = source.read(info)
            if info.filename.endswith("release-identity.json"):
                identity = json.loads(value)
                identity["version"] = "9.9.9"
                value = json.dumps(identity, sort_keys=True).encode("utf-8")
            target.writestr(info, value)
    request = _request(module, tmp_path, rewritten, "1.0.0")

    with pytest.raises(module.InstallerError) as failure:
        module.install_release(request)

    assert failure.value.code == "release_identity_mismatch"
    assert not (request.install_root / "state" / "windows-install-state.json").exists()


def test_fallback_selection_receipt_survives_bootstrap_workroot_cleanup_and_doctor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_module()
    _patch_fake_venv_transaction(monkeypatch, module)
    archive = _payload(tmp_path, "1.0.0")
    work_root = tmp_path / "bootstrap-work-root"
    work_root.mkdir()
    base_python = work_root / "python.exe"
    base_python.write_bytes(b"fallback-python")
    receipt = _runtime_selection_receipt(
        module,
        work_root / "fallback-authorization.json",
        source="fallback",
        base_python=base_python,
    )
    request = _request(
        module,
        tmp_path,
        archive,
        "1.0.0",
        source="fallback",
        runtime_selection_receipt=receipt,
    )._replace(base_python=base_python)

    result = module.install_release(request)
    shutil.rmtree(work_root)
    state = json.loads(
        (request.install_root / "state" / "windows-install-state.json").read_text(
            encoding="utf-8"
        )
    )
    marker = json.loads(
        (Path(state["active"]["release_root"]) / ".windows-install.json").read_text(
            encoding="utf-8"
        )
    )
    selection = state["active"]["runtime_selection"]

    assert result["active"]["runtime_selection"] == selection
    assert marker["entry"]["runtime_selection"] == selection
    assert [attempt["approach"] for attempt in selection["official_attempts"]] == [
        "venv-ensurepip",
        "venv-explicit-ensurepip",
    ]
    assert [attempt["result"] for attempt in selection["official_attempts"]] == [
        "failed",
        "failed",
    ]
    assert selection["official_attempts"][0]["stderr"] == "[REDACTED]"
    assert selection["fallback_runtime"] == {
        "freeze_id": module._FALLBACK_FREEZE_ID,
        "archive_sha256": module._FALLBACK_ARCHIVE_SHA256,
        "archive_bytes": module._FALLBACK_ARCHIVE_BYTES,
    }
    persisted = json.dumps(state, sort_keys=True)
    assert str(work_root) not in persisted
    assert "python.exe" not in persisted
    assert state["active"]["base_python"] is None

    monkeypatch.setattr(module, "_run_json", lambda *_args, **_kwargs: {"ok": True})
    assert module.doctor(request.install_root)["active"]["runtime_selection"] == selection


@pytest.mark.parametrize("selected", ["venv-ensurepip", "venv-explicit-ensurepip"])
def test_official_selection_receipt_is_durable_without_fallback_authorization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, selected: str
) -> None:
    module = _load_module()
    _patch_fake_venv_transaction(monkeypatch, module)
    archive = _payload(tmp_path, "1.0.0")
    request = _request(module, tmp_path, archive, "1.0.0")
    receipt = _runtime_selection_receipt(
        module,
        tmp_path / "official-runtime-selection.json",
        source="official",
        base_python=request.base_python,
        selected_approach=selected,
    )
    request = request._replace(runtime_selection_receipt=receipt)

    result = module.install_release(request)
    selection = result["active"]["runtime_selection"]
    assert selection["decision"] == "official_selected"
    assert selection["selected_approach"] == selected
    assert "fallback_runtime" not in selection
    assert [attempt["result"] for attempt in selection["official_attempts"]][-1] == "succeeded"


@pytest.mark.parametrize("source", ["official", "fallback"])
def test_runtime_selection_controller_accepts_bomless_json_and_rejects_utf8_bom(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: str
) -> None:
    """Both bootstrap receipt decisions must remain strict-UTF-8 compatible."""
    module = _load_module()
    monkeypatch.setattr(module, "_create_venv", _fake_venv)
    archive = _payload(tmp_path, "1.0.0")
    request = _request(module, tmp_path, archive, "1.0.0")
    receipt = _runtime_selection_receipt(
        module,
        tmp_path / f"{source}-runtime-selection.json",
        source=source,
        base_python=request.base_python,
    )

    # This emulates the bytes written by the PS5.1 bootstrap's
    # UTF8Encoding(false) writer.  The Python controller is intentionally
    # strict: a leading BOM is not silently accepted as JSON.
    assert not receipt.read_bytes().startswith(b"\xef\xbb\xbf")
    parsed = module._normalize_runtime_selection_receipt(
        receipt, runtime_source=source, base_python=request.base_python
    )
    assert parsed["decision"] == (
        "official_selected" if source == "official" else "fallback_authorized"
    )

    receipt.write_bytes(b"\xef\xbb\xbf" + receipt.read_bytes())
    with pytest.raises(module.InstallerError) as failure:
        module._normalize_runtime_selection_receipt(
            receipt, runtime_source=source, base_python=request.base_python
        )
    assert failure.value.code == "runtime_selection_invalid"


def test_malformed_runtime_selection_receipt_fails_closed_before_state_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_module()
    monkeypatch.setattr(module, "_create_venv", _fake_venv)
    archive = _payload(tmp_path, "1.0.0")
    request = _request(module, tmp_path, archive, "1.0.0")
    receipt = _runtime_selection_receipt(
        module,
        tmp_path / "malformed-selection.json",
        source="official",
        base_python=request.base_python,
    )
    malformed = json.loads(receipt.read_text(encoding="utf-8"))
    malformed["fallback_runtime"] = {}
    receipt.write_text(json.dumps(malformed), encoding="utf-8")
    request = request._replace(runtime_selection_receipt=receipt)

    with pytest.raises(module.InstallerError) as failure:
        module.install_release(request)

    assert failure.value.code == "runtime_selection_invalid"
    assert not (request.install_root / "state" / "windows-install-state.json").exists()


def test_malformed_fallback_selection_receipt_fails_closed_before_state_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_module()
    monkeypatch.setattr(module, "_create_venv", _fake_venv)
    archive = _payload(tmp_path, "1.0.0")
    request = _request(module, tmp_path, archive, "1.0.0", source="fallback")
    receipt = _runtime_selection_receipt(
        module,
        tmp_path / "fallback-selection.json",
        source="fallback",
        base_python=request.base_python,
    )
    malformed = json.loads(receipt.read_text(encoding="utf-8"))
    malformed["official_attempts"][1]["result"] = "succeeded"
    malformed["official_attempts"][1]["exit_code"] = 0
    receipt.write_text(json.dumps(malformed), encoding="utf-8")
    request = request._replace(runtime_selection_receipt=receipt)

    with pytest.raises(module.InstallerError) as failure:
        module.install_release(request)

    assert failure.value.code == "runtime_selection_invalid"
    assert not (request.install_root / "state" / "windows-install-state.json").exists()


def test_old_marker_without_optional_runtime_selection_stays_usable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_module()
    _patch_fake_venv_transaction(monkeypatch, module)
    archive = _payload(tmp_path, "1.0.0")
    request = _request(module, tmp_path, archive, "1.0.0")
    module.install_release(request)
    monkeypatch.setattr(module, "_run_json", lambda *_args, **_kwargs: {"ok": True})

    result = module.doctor(request.install_root)

    assert "runtime_selection" not in result["active"]


def test_windows_doctor_binds_manager_and_cli_to_active_install_data_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_module()
    _patch_fake_venv_transaction(monkeypatch, module)
    archive = _payload(tmp_path, "1.0.0")
    request = _request(module, tmp_path, archive, "1.0.0")
    module.install_release(request)
    observed: list[dict[str, str]] = []

    def fake_run_json(_command, *, env, code):
        assert code in {"doctor_runtime_failed", "doctor_product_failed"}
        observed.append(dict(env))
        return {"ok": True}

    monkeypatch.setattr(module, "_run_json", fake_run_json)

    assert module.doctor(request.install_root)["ok"] is True
    assert len(observed) == 2
    for env in observed:
        assert env["IMAGE_PPTGEN_DATA_ROOT"] == str(request.install_root.resolve())
        assert env["PPTGEN_DATA_ROOT"] == str(request.install_root.resolve())


def test_platform_contract_is_builder_ready_and_local_only() -> None:
    contract = json.loads((MODULE_PATH.parent / "contract.json").read_text(encoding="utf-8"))

    assert contract["schema_version"] == 1
    assert contract["platform"] == "windows-amd64"
    assert contract["bootstrap"] == "install.py"
    assert contract["preferred_bootstrap"] == "install.py"
    assert contract["legacy_bootstrap"] == "install.ps1"
    assert contract["bootstrap_python_argument"] == "--bootstrap-python"
    assert contract["bootstrap_python_must_equal_sys_executable"] is True
    assert contract["bootstrap_python_implementation"] == "CPython"
    assert contract["bootstrap_python_version"] == "3.12"
    assert contract["bootstrap_python_must_be_absolute_regular_non_reparse"] is True
    assert contract["bootstrap_versioned"] is True
    assert contract["payload"]["format"] == "zip"
    assert contract["runtime_selection"]["order"] == ["official", "fallback"]
    assert contract["runtime_selection"]["network_discovery"] is False
    fallback_authority = contract["runtime_selection"]["fallback_authorization"]
    assert fallback_authority["freeze_id"] == "pbs-20260718-cp311-plus-cp312-v4"
    assert fallback_authority["official_attempt_count"] == 2
    assert fallback_authority["approaches_must_differ"] is True
    frozen = contract["runtime_selection"]["frozen_inputs"]
    assert frozen["runtime_archive_member_root"] == "python"
    assert frozen["runtime_extracted_python"] == "python.exe"
    assert frozen["wheelhouse_native_cpython_tags"] == ["cp311", "cp312"]
    assert frozen["wheelhouse_bundle_sha256"] == (
        "87944216f61ee713532e297434666b79955cdced8f4e9573775f019ab987aaac"
    )
    assert frozen["wheelhouse_bundle_bytes"] == 15607217
    assert frozen["license_bundle_sha256"] == (
        "6ac662ed717f2f158020b8f6d3bb89ea844aca98d2abf1f76583bb540bd275cd"
    )
    assert frozen["license_bundle_bytes"] == 411633
    assert contract["install"]["requires_administrator"] is False
    assert set(contract["lifecycle"]) == {"install", "doctor", "stop", "rollback"}


@pytest.mark.parametrize("action", ["doctor", "stop", "rollback"])
def test_uninstalled_lifecycle_commands_fail_with_stable_json(
    tmp_path: Path, action: str
) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(MODULE_PATH),
            action,
            "--install-root",
            str(tmp_path / "install"),
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 3
    failure = json.loads(completed.stderr)
    assert failure == {
        "error": "not_installed",
        "message": "Image PPTGen is not installed",
        "ok": False,
        "platform": "windows-amd64",
    }
