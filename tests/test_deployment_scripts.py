from __future__ import annotations

import re
import shutil
import subprocess
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_installers_are_offline_and_do_not_start_runtime_services() -> None:
    scripts = [ROOT / "scripts" / "setup.ps1", ROOT / "scripts" / "setup.sh"]
    forbidden = re.compile(
        r"\b(Invoke-WebRequest|Start-BitsTransfer|curl|wget)\b|"
        r"\bapt(?:-get)?\s+(?:update|install)\b|"
        r"pip\s+install(?![^\n]*(?:--no-index|-NoIndex))",
        re.IGNORECASE,
    )
    for path in scripts:
        text = path.read_text(encoding="utf-8")
        assert not forbidden.search(text), path
        assert "serve-web" not in text
        assert "run-monthly-batch" not in text
        assert "bundle-manifest.json" in text


def test_linux_scripts_have_valid_bash_syntax() -> None:
    scripts = [
        ROOT / "scripts" / "setup.sh",
        ROOT / "scripts" / "bootstrap_postgresql.sh",
        ROOT / "scripts" / "run_web.sh",
        ROOT / "scripts" / "run_monthly_orchestration.sh",
    ]
    bash = shutil.which("bash")
    git_bash = Path("C:/Program Files/Git/bin/bash.exe")
    if git_bash.is_file():
        bash = str(git_bash)
    assert bash is not None
    completed = subprocess.run(
        [bash, "-n", *(str(path) for path in scripts)],
        shell=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert completed.returncode == 0, completed.stderr


def test_windows_setup_whatif_has_no_side_effects(tmp_path: Path) -> None:
    command = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(ROOT / "scripts" / "setup.ps1"),
        "-WhatIf",
        "-InstallRoot",
        str(tmp_path / "path with spaces"),
    ]
    completed = subprocess.run(
        command,
        shell=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    repeated = subprocess.run(
        command,
        shell=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    assert repeated.returncode == 0, repeated.stderr
    assert not (tmp_path / "path with spaces").exists()
    assert "OFFLINE_SETUP_PLAN" in completed.stdout
    assert repeated.stdout == completed.stdout


def test_windows_setup_preserves_quoted_paths_and_isolates_database_bootstrap() -> None:
    script = (ROOT / "scripts" / "setup.ps1").read_text(encoding="utf-8")

    assert 'TargetDir=`"$pythonRoot`"' in script
    assert 'INSTALLLOCATION=`"$officeRoot`"' in script
    assert 'Start-Process -FilePath "powershell.exe"' in script
    assert '"-KeepAdminPassword"' in script


def test_linux_setup_can_enable_linger_without_starting_the_monthly_timer() -> None:
    script = (ROOT / "scripts" / "setup.sh").read_text(encoding="utf-8")

    assert "--enable-linger-for" in script
    assert 'loginctl enable-linger -- "$linger_user"' in script
    assert "systemctl --user enable" not in script


def test_package_configuration_includes_runtime_assets() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    for required in (
        "assets/fonts/carlito/*.ttf",
        "assets/fonts/liberation-sans/*.ttf",
        "infrastructure/postgresql_migrations/*.sql",
        "webapp/static/*.js",
        "templates/corporate/*.docx",
    ):
        assert required in pyproject
    assert "tzdata==" in (
        ROOT / "requirements" / "lock" / "windows-py314.txt"
    ).read_text(encoding="utf-8")


def test_built_wheel_contains_fonts_templates_migrations_and_static_assets(
    tmp_path: Path,
) -> None:
    completed = subprocess.run(
        [
            str(ROOT / ".venv" / "Scripts" / "python.exe"),
            "-m",
            "pip",
            "wheel",
            "--no-deps",
            "--no-build-isolation",
            "--wheel-dir",
            str(tmp_path),
            str(ROOT),
        ],
        shell=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    wheels = list(tmp_path.glob("tenable_reports-*.whl"))
    assert len(wheels) == 1
    with zipfile.ZipFile(wheels[0]) as archive:
        names = set(archive.namelist())
    assert any(name.endswith("assets/fonts/carlito/Carlito-Regular.ttf") for name in names)
    assert any(name.endswith("postgresql_migrations/0001_initial.sql") for name in names)
    assert any(name.endswith("webapp/static/environment_status.js") for name in names)
    assert any(name.endswith("share/tenable-reports/templates/corporate/README.md") for name in names)
    assert any(name.endswith(".docx") and "templates/corporate" in name for name in names)
