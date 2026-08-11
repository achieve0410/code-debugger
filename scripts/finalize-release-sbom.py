#!/usr/bin/env python3
"""Validate Syft SPDX output and bind it to one release archive commit."""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import cast

SYFT_VERSION = "1.50.0"
SYFT_COMMIT = "16223e6dd7893fe578787658ceb876257483d404"
COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}")


def fail(message: str) -> None:
    raise SystemExit(message)


def read_document(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        fail(f"invalid raw SPDX document: {error}")
    if not isinstance(value, dict):
        fail("raw SPDX document must be a JSON object")
    return cast(dict[str, object], value)


def has_expected_source(
    packages: object,
    source_name: str,
    archive_commit: str,
) -> bool:
    if not isinstance(packages, list):
        return False
    matches = 0
    for package in packages:
        if (
            isinstance(package, dict)
            and package.get("name") == source_name
            and package.get("versionInfo") == archive_commit
        ):
            matches += 1
    return matches == 1


def validate_document(
    document: dict[str, object],
    source_name: str,
    archive_commit: str,
) -> None:
    if document.get("spdxVersion") != "SPDX-2.3":
        fail("raw SBOM must use SPDX-2.3")
    if document.get("name") != source_name:
        fail("raw SBOM source name does not match the release archive")
    creation_info = document.get("creationInfo")
    creators = creation_info.get("creators") if isinstance(creation_info, dict) else None
    if not isinstance(creators, list) or f"Tool: syft-{SYFT_VERSION}" not in creators:
        fail(f"raw SBOM was not produced by Syft {SYFT_VERSION}")
    if not has_expected_source(document.get("packages"), source_name, archive_commit):
        fail("archive commit is absent from SPDX source package")
    if document.get("documentComment") not in (None, ""):
        fail("raw SBOM unexpectedly contains a document comment")


def write_document(path: Path, document: dict[str, object]) -> None:
    if path.exists():
        fail("final SPDX output already exists")
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = f"{json.dumps(document, ensure_ascii=True, indent=2, sort_keys=True)}\n"
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            dir=path.parent,
            encoding="utf-8",
            delete=False,
        ) as output:
            temporary = Path(output.name)
            output.write(encoded)
        os.replace(temporary, path)
    except OSError as error:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        fail(f"final SPDX output could not be written: {error}")


def main(argv: list[str]) -> int:
    if len(argv) != 5:
        fail(
            "Usage: finalize-release-sbom.py "
            "<raw.spdx.json> <output.spdx.json> <source-name> <archive-commit>"
        )
    input_path = Path(argv[1])
    output_path = Path(argv[2])
    source_name = argv[3]
    archive_commit = argv[4]
    if not source_name or COMMIT_PATTERN.fullmatch(archive_commit) is None:
        fail("source name and full lowercase archive commit are required")
    if input_path.resolve() == output_path.resolve():
        fail("raw and final SPDX paths must differ")

    document = read_document(input_path)
    validate_document(document, source_name, archive_commit)
    document["documentComment"] = (
        f"archive-commit-sha={archive_commit}; "
        f"syft-version={SYFT_VERSION}; "
        f"syft-commit={SYFT_COMMIT}"
    )
    write_document(output_path, document)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
