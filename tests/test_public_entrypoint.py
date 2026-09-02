from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def test_readme_is_the_public_image_3_0_contract():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert readme.startswith("# Image PPT 3.0 Public")
    assert "只接收 Deck" in readme
    assert "Image Route (3.0)" in readme
    assert "History、Run Detail 和 Codex Audit" in readme
    assert "Page 2" in readme and "seed" in readme and "Gemini Palette" in readme
    assert "Codex Native Image 3.0" in readme
    assert "Codex Native Image 3.0 Luna Low Director" in readme
    assert "GPT-5.6 Sol low" in readme
    assert "GPT-5.6 Luna low" in readme
    assert "python3 public_server.py" in readme

    # The internal module may be named for maintainers, but the old command and
    # full-product opening must not remain a public start path or headline.
    assert "python3 server.py" not in readme
    assert "PPT-Gen-Platform 是一个面向" not in readme

    unsupported = readme.split("## 明确不支持的能力", 1)[1]
    for legacy_feature in (
        "HTML",
        "ImageDirect",
        "Requirements",
        "Colors",
        "Settings",
        "Evaluation",
        "RunFail",
        "Force",
        "Retry",
    ):
        assert legacy_feature in unsupported
    assert "非 public /\n不支持" in unsupported


def _isolated_env(tmp_path: Path) -> dict[str, str]:
    env = os.environ.copy()
    public_data = tmp_path / "public-data"
    env.update(
        {
            "PYTHONPATH": str(ROOT),
            "PPTGEN_PUBLIC_DATA_DIR": str(public_data),
            "PPT_DB_PATH": str(public_data / "ppt.db"),
            "PPT_ARTIFACTS_DIR": str(public_data / "artifacts"),
        }
    )
    return env


def test_direct_server_entrypoint_fails_closed_with_public_command(tmp_path: Path):
    db_path = tmp_path / "should-not-be-created.db"
    artifacts_path = tmp_path / "should-not-be-created-artifacts"
    env = _isolated_env(tmp_path)
    env["PPT_DB_PATH"] = str(db_path)
    env["PPT_ARTIFACTS_DIR"] = str(artifacts_path)

    completed = subprocess.run(
        [sys.executable, "server.py"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode != 0
    assert "python3 public_server.py" in completed.stderr
    assert not db_path.exists()
    assert not artifacts_path.exists()


def test_public_missing_dist_does_not_expose_legacy_html_docs(tmp_path: Path):
    missing_dist = tmp_path / "missing-dist"
    script = f"""
from pathlib import Path
import public_server

public_server.server.FRONTEND_DIR = Path({str(missing_dist)!r})
response = public_server.app.test_client().get("/")
print("STATUS:", response.status_code)
print("BODY:", response.get_data(as_text=True))
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=_isolated_env(tmp_path),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "STATUS: 503" in completed.stdout
    assert "HTML-PPT-Gen" not in completed.stdout
    for legacy_label in ("requirements", "colors", "prompts", "api_docs"):
        assert legacy_label not in completed.stdout.lower()
