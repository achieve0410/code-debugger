# Release checklist

Use this checklist for every public release. Releases remain source-only unless
the distribution contract is changed explicitly in a reviewed pull request.

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

## Verify

- Run the focused checks for the release changes.
- Run `./scripts/check.sh`.
- Merge through the protected `main` branch only after the required GitHub
  Actions checks pass.

## Publish

- Create the GitHub release from the exact verified `main` commit.
- Include user-visible changes, compatibility notes, security impact, and the
  checks that passed.
- Confirm CI, CodeQL, dependency processing, and security-alert review for the
  release commit.
- Verify the release version, tag, notes, and source archives after publishing.
