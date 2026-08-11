# Release checklist

Use this checklist for every public release. Releases contain a versioned source
archive, SHA-256 checksum, exact-archive SPDX SBOM, GitHub artifact provenance,
and an SBOM attestation whose subject is the archive. They do not publish
npm/PyPI packages, prebuilt binaries, containers, or remote installers.

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
- Confirm Actions default workflow permissions are read-only, immutable
  releases are enabled, and the `v*` tag ruleset prevents updates or deletion
  while allowing the release workflow to create a new tag.

## Verify

- Run the focused checks for the release changes.
- Run `./scripts/check.sh`.
- Merge through the protected `main` branch only after the required GitHub
  Actions checks pass.
- Build the source archive with
  `./scripts/package-release.sh release-artifacts "v${VERSION}" "$(git rev-parse HEAD)"`
  and verify its checksum. The explicit commit must equal clean `HEAD`.
- Confirm the archive contains no `venv/`, `node_modules/`, `pem/`,
  `.kg-debugger/`, `web/dist/`, private keys, caches, or analyzed-project data.
- Run `./scripts/install-smoke.sh` against the archive and checksum on Linux and
  macOS.

## Publish

- Do not create or push the release tag manually. Dispatch the workflow from
  protected `main` with
  `gh workflow run release.yml --ref main -f version="${VERSION}"`.
- The workflow seals `${GITHUB_SHA}` before dependency installation, consumes
  only that archive in verification and Linux/macOS smoke jobs, generates SPDX
  2.3 from the verified extracted archive with the pinned Syft binary, creates
  artifact provenance and an archive-subject SBOM attestation, then creates the
  exact tag and a draft release only after every gate passes.
- Include user-visible changes, compatibility notes, security impact, and
  verification evidence in the draft release notes before publication.
- Confirm CI, CodeQL, security-alert review, exact-archive SBOM, and both
  attestations for the release commit.
- Verify the tag object SHA equals the workflow run `headSha`.
- Download the archive and run `gh attestation verify` for both the standard
  provenance and `https://spdx.dev/Document` predicate, constrained to
  `.github/workflows/release.yml`, `refs/heads/main`, and the release commit
  digest.
- Verify the checksum and run the archive installation smoke before manually
  publishing the draft.
- Verify the release version, tag, notes, SBOM, attestations, and source
  archives after publishing.
- Do not replace or mutate published assets; publish a corrected release
  instead.
