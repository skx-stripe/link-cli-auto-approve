"""Exercise release decisions without publishing or contacting PyPI."""

import io
import json
import tarfile
import tempfile
import unittest
import urllib.error
import zipfile
from email.message import Message
from pathlib import Path
from unittest.mock import patch

from release import distribution_names, needs_release, validate_distributions


class ReleaseTests(unittest.TestCase):
    def response(self, filenames: set[str]) -> io.BytesIO:
        return io.BytesIO(
            json.dumps({"urls": [{"filename": name} for name in filenames]}).encode()
        )

    def test_unpublished_version(self) -> None:
        error = urllib.error.HTTPError(
            "https://pypi.org", 404, "Not Found", Message(), None
        )
        with patch("release.urllib.request.urlopen", side_effect=error) as request:
            self.assertTrue(needs_release("0.1.0"))
        request.assert_called_once_with(
            "https://pypi.org/pypi/stripe-link-sdk/0.1.0/json", timeout=30
        )

    def test_complete_release_is_skipped(self) -> None:
        with patch(
            "release.urllib.request.urlopen",
            return_value=self.response(distribution_names("0.1.0")),
        ):
            self.assertFalse(needs_release("0.1.0"))

    def test_incomplete_or_unexpected_release_fails(self) -> None:
        for filenames in [
            set(),
            {"stripe_link_sdk-0.1.0-py3-none-any.whl"},
            {"stripe_link_sdk-0.1.0.tar.gz"},
            distribution_names("0.2.0"),
        ]:
            with (
                self.subTest(filenames=filenames),
                patch(
                    "release.urllib.request.urlopen",
                    return_value=self.response(filenames),
                ),
                self.assertRaisesRegex(RuntimeError, "incomplete or unexpected"),
            ):
                needs_release("0.1.0")

    def test_http_errors_are_not_treated_as_unpublished(self) -> None:
        for status in [403, 429, 500, 503]:
            error = urllib.error.HTTPError(
                "https://pypi.org", status, "error", Message(), None
            )
            with (
                self.subTest(status=status),
                patch("release.urllib.request.urlopen", side_effect=error),
                self.assertRaises(urllib.error.HTTPError),
            ):
                needs_release("0.1.0")

    def test_network_failure_is_not_treated_as_unpublished(self) -> None:
        with (
            patch(
                "release.urllib.request.urlopen",
                side_effect=urllib.error.URLError("connection failed"),
            ),
            self.assertRaises(urllib.error.URLError),
        ):
            needs_release("0.1.0")

    def test_invalid_response_is_not_treated_as_unpublished(self) -> None:
        for body in [b"not JSON", b"{}"]:
            with (
                self.subTest(body=body),
                patch("release.urllib.request.urlopen", return_value=io.BytesIO(body)),
                self.assertRaises((ValueError, KeyError)),
            ):
                needs_release("0.1.0")


class DistributionTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.package = Path(directory.name)
        self.dist = self.package / "dist"
        self.dist.mkdir()
        source = {
            "src/link/__init__.py": b"from .client import Client\n",
            "src/link/client.py": b"class Client: pass\n",
            "src/link/py.typed": b"",
            "pyproject.toml": (
                b'[project]\nname = "stripe-link-sdk"\nversion = "0.1.0"\n'
            ),
            "README.md": "# Link SDK — example\n".encode(),
            "LICENSE": b"MIT license\n",
        }
        for name, data in source.items():
            path = self.package / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)

        self.metadata = b"Name: stripe-link-sdk\nVersion: 0.1.0\n\nDescription\n"
        self.prefix = "stripe_link_sdk-0.1.0"
        self.wheel = {
            "link/__init__.py": source["src/link/__init__.py"],
            "link/client.py": source["src/link/client.py"],
            "link/py.typed": b"",
            f"{self.prefix}.dist-info/METADATA": self.metadata,
            f"{self.prefix}.dist-info/WHEEL": b"Wheel-Version: 1.0\n",
            f"{self.prefix}.dist-info/RECORD": b"",
            f"{self.prefix}.dist-info/licenses/LICENSE": source["LICENSE"],
        }
        self.sdist = {f"{self.prefix}/{name}": data for name, data in source.items()}
        self.sdist[f"{self.prefix}/PKG-INFO"] = self.metadata
        package_patch = patch("release.PACKAGE", self.package)
        package_patch.start()
        self.addCleanup(package_patch.stop)

    def validate(self) -> None:
        with zipfile.ZipFile(
            self.dist / f"{self.prefix}-py3-none-any.whl", "w"
        ) as wheel:
            for name, data in self.wheel.items():
                wheel.writestr(name, data)
        with tarfile.open(self.dist / f"{self.prefix}.tar.gz", "w:gz") as sdist:
            for name, data in self.sdist.items():
                info = tarfile.TarInfo(name)
                info.size = len(data)
                sdist.addfile(info, io.BytesIO(data))
        validate_distributions("0.1.0", self.dist)

    def test_expected_contents_pass(self) -> None:
        self.validate()

    def test_unexpected_wheel_assets_fail(self) -> None:
        for name in [
            "link/.env",
            "link/data.json",
            "link/__pycache__/client.pyc",
            "tests/test.py",
        ]:
            with self.subTest(name=name):
                self.wheel[name] = b"unwanted"
                with self.assertRaisesRegex(RuntimeError, "Unexpected archive files"):
                    self.validate()
                del self.wheel[name]

    def test_unexpected_source_archive_assets_fail(self) -> None:
        for name in [
            "uv.lock",
            "scripts/release.py",
            "tests/test.py",
            "src/link/data.json",
        ]:
            with self.subTest(name=name):
                key = f"{self.prefix}/{name}"
                self.sdist[key] = b"unwanted"
                with self.assertRaisesRegex(RuntimeError, "Unexpected archive files"):
                    self.validate()
                del self.sdist[key]

    def test_missing_runtime_files_fail(self) -> None:
        for archive, name in [
            (self.wheel, "link/py.typed"),
            (self.wheel, "link/client.py"),
            (self.sdist, f"{self.prefix}/src/link/client.py"),
        ]:
            with self.subTest(name=name):
                original = archive.pop(name)
                with self.assertRaisesRegex(RuntimeError, "missing archive files"):
                    self.validate()
                archive[name] = original

    def test_changed_file_contents_fail(self) -> None:
        for archive, name in [
            (self.wheel, "link/client.py"),
            (self.sdist, f"{self.prefix}/README.md"),
            (self.sdist, f"{self.prefix}/pyproject.toml"),
        ]:
            with self.subTest(name=name):
                original = archive[name]
                archive[name] = b"incorrect content"
                with self.assertRaisesRegex(RuntimeError, "does not match source"):
                    self.validate()
                archive[name] = original

    def test_wrong_package_identity_fails(self) -> None:
        for archive, name in [
            (self.wheel, f"{self.prefix}.dist-info/METADATA"),
            (self.sdist, f"{self.prefix}/PKG-INFO"),
        ]:
            for before, after, field in [
                (b"stripe-link-sdk", b"another-package", "Name"),
                (b"0.1.0", b"0.2.0", "Version"),
            ]:
                with self.subTest(name=name, field=field):
                    archive[name] = self.metadata.replace(before, after)
                    with self.assertRaisesRegex(RuntimeError, f"Invalid {field}"):
                        self.validate()
                    archive[name] = self.metadata


if __name__ == "__main__":
    unittest.main()
