#!/usr/bin/env python3
"""One-time (or as-needed) environment setup for Module 2 (AgentCore Runtime).

1. Creates a Python virtual environment at ./.venv (safe to re-run; the
   existing venv is reused if present).
2. Installs/upgrades the dependencies from requirements.txt into it
   (openai[bedrock] + boto3 + aws-bedrock-token-generator). This step needs
   network access to PyPI.

Supported Python: 3.12 or newer (checked below before the venv is created).

Run this once before starting the labs, and again whenever requirements.txt
changes. After it finishes, activate the venv and run a lab script directly:

    source .venv/bin/activate                 # macOS / Linux
    .\\.venv\\Scripts\\Activate.ps1             # Windows (PowerShell)
    python labs/lab-01-run-on-runtime/run_on_runtime.py "Hello, BMA!"

Usage:
    python setup.py                 # create or reuse ./.venv, install deps
    python setup.py --force         # delete and recreate the venv from scratch

This replaces the old Bash setup helper and runs natively on macOS, Linux and
Windows. Because it builds the venv, it runs on your BASE interpreter (the one you
invoke it with) BEFORE any venv exists, so it imports only the standard
library — no third-party packages.
"""

# NOTE: stdlib ONLY below. This script runs on the base interpreter before the
# venv (and therefore before boto3/openai) exists, so it must not import any
# third-party package.
import argparse
import shutil
import subprocess
import sys
import venv
from pathlib import Path

# Resolve the directory this script lives in, so it works from any cwd. This is
# the Python equivalent of the shell's "cd $(dirname $0) && pwd" dance.
MODULE_ROOT = Path(__file__).resolve().parent
VENV_DIR = MODULE_ROOT / ".venv"
REQUIREMENTS = MODULE_ROOT / "requirements.txt"

# Require Python 3.12+ — the version these labs are tested against.
MIN_PY = (3, 12)


def check_python_version() -> int | None:
    """Fail early (and clearly) if the running interpreter is too old.

    `sys` here is the interpreter the user invoked, i.e. the BASE interpreter
    that will create the venv. Checking it mirrors the shell script's base
    interpreter check. Returns a non-zero exit code to signal failure, or None
    when the version is fine.
    """
    if sys.version_info[:2] < MIN_PY:
        found = "%d.%d.%d" % sys.version_info[:3]
        print(
            f"Python {MIN_PY[0]}.{MIN_PY[1]}+ is required; this interpreter is "
            f"{found}, which is too old.",
            file=sys.stderr,
        )
        print(
            "Install Python 3.12 or newer, then run this script with it, e.g.:",
            file=sys.stderr,
        )
        print("  python3.12 setup.py", file=sys.stderr)
        return 1
    return None


def venv_python_path() -> Path:
    """Return the path to the venv's Python interpreter for this platform.

    Windows puts it at <venv>\\Scripts\\python.exe; POSIX puts it at
    <venv>/bin/python.
    """
    if sys.platform == "win32":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def activation_command() -> str:
    """Return the platform-correct command to activate the venv."""
    if sys.platform == "win32":
        return ".\\.venv\\Scripts\\Activate.ps1"
    return "source .venv/bin/activate"


def main() -> int:
    # Version guard FIRST — before touching the filesystem or the venv.
    version_error = check_python_version()
    if version_error is not None:
        return version_error

    parser = argparse.ArgumentParser(
        description=(
            "Create (or reuse) the ./.venv virtual environment for Module 2 "
            "and install the lab dependencies into it."
        )
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="delete and recreate the venv from scratch (== old FORCE=1).",
    )
    args = parser.parse_args()

    # --force: wipe any existing venv so it is rebuilt from a clean slate.
    if args.force and VENV_DIR.exists():
        print(f"--force: removing existing virtual environment at {VENV_DIR}", file=sys.stderr)
        shutil.rmtree(VENV_DIR)

    # 1. Create the venv if it does not already exist (re-runs reuse it).
    if not VENV_DIR.exists():
        print(f"Creating virtual environment at {VENV_DIR}", file=sys.stderr)
        # with_pip=True so pip is available inside the new venv immediately.
        venv.create(str(VENV_DIR), with_pip=True)
    else:
        print(f"Reusing existing virtual environment at {VENV_DIR}", file=sys.stderr)

    venv_python = venv_python_path()

    # 2. Install dependencies by running the VENV's own python, so the packages
    # land inside the venv regardless of which interpreter we were launched
    # with. check=True makes a failed pip run abort this script.
    #
    # We deliberately do NOT pass --upgrade on the requirements install: a plain
    # install resolves the soft-pinned versions once and then leaves them put,
    # so re-running setup does not silently pull the newest matching release
    # every time (which would make installs non-reproducible and widen the
    # supply-chain surface). To intentionally refresh, use --force to rebuild
    # the venv, or bump the pins in requirements.txt.
    print(f"Installing dependencies from {REQUIREMENTS}", file=sys.stderr)
    subprocess.run(
        [str(venv_python), "-m", "pip", "install", "--upgrade", "pip"],
        check=True,
    )
    subprocess.run(
        [str(venv_python), "-m", "pip", "install", "-r", str(REQUIREMENTS)],
        check=True,
    )

    # Final guidance, with the activation command for THIS platform.
    print(
        "\n".join(
            [
                "",
                "Setup complete.",
                "",
                "Next:",
                "  1. Activate the virtual environment:",
                f"       {activation_command()}",
                "  2. Run a lab script, for example:",
                '       python labs/lab-01-run-on-runtime/run_on_runtime.py "Hello, BMA!"',
                "",
                "Each lab has its own README.md explaining what to run and what to expect.",
            ]
        ),
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
