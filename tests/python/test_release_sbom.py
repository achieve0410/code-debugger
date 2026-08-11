from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path


class ReleaseSbomTest(unittest.TestCase):
    SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "finalize-release-sbom.py"
    COMMIT = "a" * 40
    SOURCE = "code-debugger"

    def raw_sbom(self, version: str | None = None) -> dict[str, object]:
        return {
            "spdxVersion": "SPDX-2.3",
            "dataLicense": "CC0-1.0",
            "SPDXID": "SPDXRef-DOCUMENT",
            "name": self.SOURCE,
            "documentNamespace": "https://example.invalid/raw",
            "creationInfo": {
                "created": "2026-08-09T00:00:00Z",
                "creators": ["Tool: syft-1.50.0"],
            },
            "packages": [
                {
                    "name": self.SOURCE,
                    "SPDXID": "SPDXRef-DocumentRoot-Directory",
                    "versionInfo": version or self.COMMIT,
                }
            ],
            "relationships": [],
        }

    def run_finalizer(
        self,
        directory: Path,
        raw: dict[str, object],
    ) -> subprocess.CompletedProcess[str]:
        input_path = directory / "raw.spdx.json"
        output_path = directory / "sbom.spdx.json"
        input_path.write_text(json.dumps(raw), encoding="utf-8")
        return subprocess.run(
            [
                str(self.SCRIPT),
                str(input_path),
                str(output_path),
                self.SOURCE,
                self.COMMIT,
            ],
            check=False,
            capture_output=True,
            text=True,
        )

    def test_finalizer_binds_spdx_to_archive_commit(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            directory = Path(directory_name)
            result = self.run_finalizer(directory, self.raw_sbom())
            self.assertEqual(result.returncode, 0, result.stderr)

            output = json.loads((directory / "sbom.spdx.json").read_text(encoding="utf-8"))
            self.assertEqual(
                output["documentComment"],
                (
                    f"archive-commit-sha={self.COMMIT}; "
                    "syft-version=1.50.0; "
                    "syft-commit=16223e6dd7893fe578787658ceb876257483d404"
                ),
            )
            self.assertEqual(output["packages"][0]["versionInfo"], self.COMMIT)

    def test_finalizer_rejects_a_different_source_commit(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            directory = Path(directory_name)
            result = self.run_finalizer(directory, self.raw_sbom("b" * 40))
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("archive commit is absent from SPDX source package", result.stderr)
            self.assertFalse((directory / "sbom.spdx.json").exists())


if __name__ == "__main__":
    unittest.main()
