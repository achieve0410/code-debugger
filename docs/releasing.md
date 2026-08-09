# Release checklist

Use this checklist for every public release. Releases contain a versioned source
archive, SHA-256 checksum, SPDX dependency SBOM, and GitHub artifact
attestation. They do not publish npm/PyPI packages, prebuilt binaries,
containers, or remote installers.

## Prepare

- Choose the release version and supported release line.
- Update the version in `package.json`, `package-lock.json`, `pyproject.toml`,
  and `src/kg_debugger/__init__.py`.
- Update the project-status and compatibility version references in `README.md`
  when the release line changes.
- Update the supported-version table in `SECURITY.md`.
- Confirm issue forms and public documentation do not contain stale concrete
  version examples.
- Confirm no generated, private, or analyzed-project artifacts are staged.
- Confirm the release commit is clean and reachable from protected `main`.

## Verify

- Run the focused checks for the release changes.
- Run `./scripts/check.sh`.
- Merge through the protected `main` branch only after the required GitHub
  Actions checks pass.
- Build the source archive with `./scripts/package-release.sh` and verify its
  checksum.
- Confirm the archive contains no `venv/`, `node_modules/`, `pem/`,
  `.kg-debugger/`, `web/dist/`, private keys, caches, or analyzed-project data.
- Run `./scripts/install-smoke.sh` against the archive and checksum on Linux and
  macOS.

## Publish

- Push the exact `v<version>` tag from the verified `main` commit. The
  tag-triggered release workflow reruns the full check, builds the archive,
  performs Linux/macOS clean-install smoke, exports the dependency-graph SBOM,
  attests the artifacts, and creates a draft only after every gate passes.
- Include user-visible changes, compatibility notes, security impact, and
  verification evidence in the generated release notes.
- Confirm CI, CodeQL, dependency processing, security-alert review, SBOM, and
  artifact attestations for the release commit.
- Verify the release version, tag, notes, and source archives after publishing.
- Do not replace or mutate published assets; publish a corrected release
  instead.
