#!/usr/bin/env python3
"""Verify and safely extract one code-debugger release archive."""

from __future__ import annotations

import hashlib
import re
import sys
import tarfile
from pathlib import Path, PurePosixPath

CHUNK_SIZE = 1024 * 1024
MAX_MEMBERS = 10_000
MAX_CONTENT_BYTES = 1024 * 1024 * 1024
ARCHIVE_PATTERN = re.compile(r"code-debugger-v[0-9A-Za-z][0-9A-Za-z._-]*\.tar\.gz")
CHECKSUM_PATTERN = re.compile(r"([0-9a-f]{64})  ([^\n]+)")


def fail(message: str) -> None:
    raise SystemExit(message)


def expected_digest(archive: Path, checksum: Path) -> str:
    try:
        line = checksum.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError) as error:
        fail(f"invalid release checksum file: {error}")
    match = CHECKSUM_PATTERN.fullmatch(line)
    if match is None or match.group(2) != archive.name:
        fail("invalid release checksum file")
    return match.group(1)


def copy_verified_archive(source: Path, destination: Path, expected: str) -> None:
    digest = hashlib.sha256()
    try:
        with source.open("rb") as input_file, destination.open("xb") as output_file:
            while chunk := input_file.read(CHUNK_SIZE):
                digest.update(chunk)
                output_file.write(chunk)
    except OSError as error:
        fail(f"release archive could not be copied: {error}")
    if digest.hexdigest() != expected:
        destination.unlink(missing_ok=True)
        fail("release checksum verification failed")


def validated_members(archive: tarfile.TarFile, root_name: str) -> list[tarfile.TarInfo]:
    members = archive.getmembers()
    if not members or len(members) > MAX_MEMBERS:
        fail("release archive has an invalid member count")

    seen: set[str] = set()
    content_bytes = 0
    for member in members:
        name = member.name
        path = PurePosixPath(name)
        if (
            not name
            or "\\" in name
            or path.is_absolute()
            or any(part in {"", ".", ".."} for part in path.parts)
            or not path.parts
            or path.parts[0] != root_name
        ):
            fail(f"unsafe release archive path: {name}")
        folded_name = name.rstrip("/").casefold()
        if folded_name in seen:
            fail(f"duplicate release archive path: {name}")
        seen.add(folded_name)
        if not (member.isdir() or member.isreg()):
            fail(f"unsafe release archive member type: {name}")
        if member.isreg():
            content_bytes += member.size
            if content_bytes > MAX_CONTENT_BYTES:
                fail("release archive content exceeds the size limit")
    return members


def extract_verified_archive(verified: Path, destination: Path, root_name: str) -> Path:
    try:
        with tarfile.open(verified, mode="r:gz") as archive:
            members = validated_members(archive, root_name)
            archive.extractall(destination, members=members, filter="data")
    except (OSError, tarfile.TarError) as error:
        fail(f"invalid release archive: {error}")

    root = destination / root_name
    if not root.is_dir():
        fail("release archive is missing its expected root directory")
    return root


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        fail(
            "Usage: verify-release-archive.py "
            "<archive.tar.gz> <archive.tar.gz.sha256> <destination>"
        )
    try:
        archive = Path(argv[1]).resolve(strict=True)
        checksum = Path(argv[2]).resolve(strict=True)
        destination = Path(argv[3]).resolve(strict=True)
    except OSError as error:
        fail(f"release archive input does not exist: {error}")
    if not archive.is_file() or not checksum.is_file() or not destination.is_dir():
        fail("release archive inputs and destination must be regular paths")
    if any(destination.iterdir()):
        fail("release extraction destination must be empty")
    if ARCHIVE_PATTERN.fullmatch(archive.name) is None:
        fail(f"invalid release archive name: {archive.name}")

    root_name = archive.name.removesuffix(".tar.gz")
    verified = destination / ".verified-release.tar.gz"
    copy_verified_archive(archive, verified, expected_digest(archive, checksum))
    root = extract_verified_archive(verified, destination, root_name)
    print(root)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
