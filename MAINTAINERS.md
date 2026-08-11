# Maintainers

`code-debugger` is currently maintained by:

- [@achieve0410](https://github.com/achieve0410) — project direction, releases,
  security response, community moderation, and final merge decisions.

The project is solo-maintained today. `CODEOWNERS` routes review requests; it
does not imply independent approval or a second maintainer. Repository rulesets
and required checks remain the source of truth for merge enforcement.

## Responsibilities

The current maintainer is responsible for:

- preserving the local-first security and data-minimization boundaries;
- reviewing graph-contract, analyzer, API, storage, and runtime changes;
- triaging issues and pull requests;
- coordinating vulnerability reports and patched releases;
- maintaining supported versions, fixtures, and release evidence; and
- enforcing the [Code of Conduct](CODE_OF_CONDUCT.md).

## Decisions and response expectations

Material changes to graph contracts, security, persistence, public APIs, or
supported framework behavior should begin with an issue. Decisions are based on
the documented product boundary, reproducible synthetic evidence, maintenance
cost, and the smallest change that solves the user problem.

General issue and pull-request responses are best effort; no response-time SLA
is promised. The seven-calendar-day acknowledgement target applies only to
private vulnerability reports as documented in [SECURITY.md](SECURITY.md).

## Adding maintainers

Maintainer access may be offered after sustained, reviewable contributions that
demonstrate:

- sound judgment around privacy and fail-closed behavior;
- reliable review and follow-through;
- respectful community participation; and
- familiarity with the analyzer, graph, API, and release contracts.

Adding a maintainer requires updating this file, `CODEOWNERS`, repository
permissions, and relevant branch or release rules. Until that happens, no
contributor should represent themselves as a project maintainer.

## Contact routes

- Bugs and bounded feature proposals: use the repository issue forms.
- Design discussion and contributor questions: use GitHub Discussions.
- Vulnerabilities: use
  [private vulnerability reporting](https://github.com/achieve0410/code-debugger/security/advisories/new).
- Conduct reports: follow [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
