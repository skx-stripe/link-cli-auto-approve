"""Check PyPI release state and validate the distributions before publishing."""

import argparse
import json
import sys
import tarfile
import tomllib
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from email import policy
from email.parser import BytesParser
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1]


def distribution_names(version: str) -> set[str]:
    return {
        f"stripe_link_sdk-{version}-py3-none-any.whl",
        f"stripe_link_sdk-{version}.tar.gz",
    }


def needs_release(version: str) -> bool:
    encoded_version = urllib.parse.quote(version, safe="")
    url = f"https://pypi.org/pypi/stripe-link-sdk/{encoded_version}/json"
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            release = json.load(response)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return True
        raise

    filenames = {file["filename"] for file in release["urls"]}
    if filenames != distribution_names(version):
        raise RuntimeError(
            "PyPI has an incomplete or unexpected release for "
            f"stripe-link-sdk {version}: "
            f"{sorted(filenames)}. Inspect the release and publish a fresh version; "
            "existing files cannot be overwritten."
        )
    return False


def validate_distributions(version: str, directory: Path) -> None:
    filenames = {
        file.name for file in directory.iterdir() if not file.name.startswith(".")
    }
    if filenames != distribution_names(version):
        raise RuntimeError(
            f"Expected one wheel and one sdist for {version}: {filenames}"
        )

    # Only Python modules and the typing marker are runtime assets. New data
    # assets must be explicitly added here before they can be released.
    module_files = {
        path.relative_to(PACKAGE / "src").as_posix(): path.read_bytes()
        for path in (PACKAGE / "src/link").rglob("*.py")
        if "__pycache__" not in path.parts
    }
    module_files["link/py.typed"] = (PACKAGE / "src/link/py.typed").read_bytes()

    def check_files(actual: list[str], expected: set[str]) -> None:
        if len(actual) != len(set(actual)):
            raise RuntimeError("Duplicate archive entries")
        if set(actual) != expected:
            raise RuntimeError(
                f"Unexpected archive files: {sorted(set(actual) - expected)}; "
                f"missing archive files: {sorted(expected - set(actual))}"
            )

    def check_content(name: str, actual: bytes, expected: bytes) -> None:
        if actual != expected:
            raise RuntimeError(f"Packaged file does not match source: {name}")

    def check_metadata(raw: bytes) -> None:
        # Twine handles generic metadata and README validation. These checks
        # tie the metadata to the specific package/version we intend to ship.
        metadata = BytesParser(policy=policy.default).parsebytes(raw)
        expected = {
            "Name": "stripe-link-sdk",
            "Version": version,
        }
        for field, value in expected.items():
            if metadata[field] != value:
                raise RuntimeError(
                    f"Invalid {field}: {metadata[field]!r}, expected {value!r}"
                )

    with zipfile.ZipFile(
        directory / f"stripe_link_sdk-{version}-py3-none-any.whl"
    ) as wheel:
        prefix = f"stripe_link_sdk-{version}.dist-info"
        contents = {
            **module_files,
            f"{prefix}/licenses/LICENSE": (PACKAGE / "LICENSE").read_bytes(),
        }
        check_files(
            [entry.filename for entry in wheel.infolist() if not entry.is_dir()],
            set(contents)
            | {f"{prefix}/{name}" for name in ("METADATA", "WHEEL", "RECORD")},
        )
        for name, expected_content in contents.items():
            check_content(name, wheel.read(name), expected_content)
        check_metadata(wheel.read(f"{prefix}/METADATA"))

    with tarfile.open(directory / f"stripe_link_sdk-{version}.tar.gz") as sdist:
        prefix = f"stripe_link_sdk-{version}"
        contents = {
            **{f"{prefix}/src/{name}": data for name, data in module_files.items()},
            **{
                f"{prefix}/{name}": (PACKAGE / name).read_bytes()
                for name in ("pyproject.toml", "README.md", "LICENSE")
            },
        }
        if any(not entry.isfile() and not entry.isdir() for entry in sdist):
            raise RuntimeError("Source archive contains links or special files")
        check_files(
            [entry.name for entry in sdist if entry.isfile()],
            set(contents) | {f"{prefix}/PKG-INFO"},
        )

        def read_member(name: str) -> bytes:
            member = sdist.extractfile(name)
            if member is None:
                raise RuntimeError(f"Missing sdist file: {name}")
            with member:
                return member.read()

        for name, expected_content in contents.items():
            check_content(name, read_member(name), expected_content)
        check_metadata(read_member(f"{prefix}/PKG-INFO"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["check", "validate"])
    parser.add_argument("--dist", type=Path, default=PACKAGE / "dist")
    args = parser.parse_args()
    project = tomllib.loads((PACKAGE / "pyproject.toml").read_text())["project"]
    if project["name"] != "stripe-link-sdk":
        raise RuntimeError("This publisher is only configured for stripe-link-sdk")
    version = project["version"]
    if args.command == "check":
        publish = needs_release(version)
        print(f"publish={str(publish).lower()}")
        state = "ready to publish" if publish else "already published; skipping"
        print(f"stripe-link-sdk {version}: {state}", file=sys.stderr)
    else:
        validate_distributions(version, args.dist)
        print(f"Validated stripe-link-sdk {version} wheel and source distribution")


if __name__ == "__main__":
    main()
