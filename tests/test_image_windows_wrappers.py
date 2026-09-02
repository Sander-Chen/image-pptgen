from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WINDOWS_ROOT = ROOT / "packaging" / "image" / "platform" / "windows"


def test_windows_cli_wrapper_uses_active_scripts_python_and_archive_manager() -> None:
    wrapper = (WINDOWS_ROOT / "image-pptgen.ps1").read_text(encoding="utf-8")

    assert "windows-install-state.json" in wrapper
    assert "Scripts\\python.exe" in wrapper
    assert "app\\runtime_manager.py" in wrapper
    assert "ensure-ready" in wrapper
    assert "--app-root" in wrapper
    assert "--data-root" in wrapper
    assert "IMAGE_PPTGEN_PYTHON" in wrapper
    assert "$env:IMAGE_PPTGEN_DATA_ROOT = $InstallRoot" in wrapper
    assert "$env:PPTGEN_DATA_ROOT = $InstallRoot" in wrapper
    assert wrapper.index("& $Python $RuntimeManager ensure-ready") < wrapper.index(
        "& $Cli @args"
    )


def test_windows_wrappers_are_user_level_and_have_stable_commands() -> None:
    bootstrap = (WINDOWS_ROOT / "install.ps1").read_text(encoding="utf-8")
    manage = (WINDOWS_ROOT / "image-pptgen-manage.ps1").read_text(encoding="utf-8")
    cmd_wrapper = (WINDOWS_ROOT / "image-pptgen.cmd").read_text(encoding="utf-8")

    assert "LOCALAPPDATA" in bootstrap
    assert "Program Files" not in bootstrap
    assert "RunAs" not in bootstrap
    assert "Start-Process" not in bootstrap
    assert "SetEnvironmentVariable('Path', $NextPath, 'User')" in bootstrap
    assert "desktop_restart_required" in bootstrap
    for action in ("install", "doctor", "stop", "rollback"):
        assert action in (bootstrap + manage).lower()
    assert "ExecutionPolicy Bypass" in cmd_wrapper
    assert "%~dp0image-pptgen.ps1" in cmd_wrapper


def test_server_wrapper_uses_active_release_and_python() -> None:
    wrapper = (WINDOWS_ROOT / "image-pptgen-server.ps1").read_text(
        encoding="utf-8"
    )

    assert "windows-install-state.json" in wrapper
    assert "Scripts\\python.exe" in wrapper
    assert "app\\image-launcher.py" in wrapper
    assert "PPTGEN_CODEX_INHERIT_USER_CONFIG" in wrapper
    assert "$env:IMAGE_PPTGEN_DATA_ROOT = $InstallRoot" in wrapper
    assert "$env:PPTGEN_DATA_ROOT = $InstallRoot" in wrapper
