"""Run the complete SDK suite against a wheel, without access to checkout sources."""

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", type=Path)
    args = parser.parse_args()
    wheel = args.wheel.resolve(strict=True)

    with tempfile.TemporaryDirectory(prefix="stripe-link-sdk-test-") as directory:
        isolated = Path(directory)
        requirements = isolated / "test-requirements.txt"
        subprocess.run(
            [
                "uv",
                "export",
                "--locked",
                "--only-dev",
                "--no-emit-project",
                "--no-hashes",
                "--no-header",
                "--format",
                "requirements.txt",
                "--output-file",
                str(requirements),
            ],
            cwd=PACKAGE,
            check=True,
            stdout=subprocess.DEVNULL,
        )
        shutil.copytree(
            PACKAGE / "tests",
            isolated / "tests",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        # Preserve pytest/ty configuration, but do not copy src/ or .venv/.
        shutil.copyfile(PACKAGE / "pyproject.toml", isolated / "pyproject.toml")
        environment = isolated / ".venv"
        python = environment / (
            "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
        )
        subprocess.run(
            [
                "uv",
                "venv",
                "--python",
                sys.executable,
                str(environment),
            ],
            check=True,
        )
        subprocess.run(
            [
                "uv",
                "pip",
                "install",
                "--python",
                str(python),
                "--requirement",
                str(requirements),
                str(wheel),
            ],
            check=True,
        )
        subprocess.run(
            [
                str(python),
                "-I",
                "-c",
                """
import sys
from importlib.metadata import distribution
from pathlib import Path

import link
import pytest

installed = Path(link.__file__).resolve()
package = distribution("stripe-link-sdk")
expected = Path(package.locate_file("link/__init__.py")).resolve()
assert installed == expected and installed.is_relative_to(Path(sys.prefix).resolve()), (
    f"Tests must import the installed wheel, got {installed}"
)
print(f"Testing installed wheel: {installed}", flush=True)
raise SystemExit(pytest.main(["tests", "-q"]))
""",
            ],
            cwd=isolated,
            check=True,
        )


if __name__ == "__main__":
    main()
