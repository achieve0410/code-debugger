# Security Policy

## Supported versions

Security fixes are provided for the latest release line.

| Version | Supported |
| --- | --- |
| 0.2.x | Yes |
| 0.1.x | No |
| Earlier versions | No |

## Reporting a vulnerability

Report vulnerabilities through
[GitHub private vulnerability reporting](https://github.com/achieve0410/code-debugger/security/advisories/new).
Do not open a public issue with vulnerability details.

Include only the minimum information needed to reproduce the problem:

- affected release or commit;
- affected component and security boundary;
- synthetic reproduction steps;
- expected and observed security behavior; and
- a suggested mitigation, when available.

Do not include credentials, authorization or session data, private keys, real
request or response bodies, query values, absolute local roots, source excerpts,
or data from an analyzed project. Use synthetic placeholders and minimized
structural facts.

The maintainer aims to acknowledge a report within seven calendar days, assess
its impact, and coordinate a fix and disclosure. Remediation timing depends on
the scope of the issue and no fixed resolution SLA is promised. Please allow
time for a patched release before public disclosure.

## Security boundary

`code-debugger` is a loopback-only local development tool, not a hostile-host or
multi-user security boundary. Its supported data, network, runtime, and storage
boundaries are documented in [docs/security-model.md](docs/security-model.md).
Reports that demonstrate a violation of those documented boundaries are in
scope.
