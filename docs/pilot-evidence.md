# Pilot evidence protocol

`code-debugger` does not currently publish third-party pilot, production-use,
benchmark, or adoption results. Synthetic fixtures and CI prove maintained
behavior; they do not prove external usage.

This protocol defines the minimum bar for any future public pilot note. It does
not add telemetry or authorize collection from analyzed projects.

## Consent and participation

- Participation requires explicit, informed consent from a person authorized to
  use the tested repository.
- Participation is optional and may be withdrawn before publication.
- The participant approves the final aggregate statement before publication.
- No background, automatic, or undisclosed measurement is permitted.
- A pilot must use a public repository or remain value-free and anonymous.

## Allowed publication fields

A pilot note may contain only:

- the `code-debugger` release or commit tested;
- a bounded date window;
- a participant count or coarse range;
- supported framework categories, without repository identity;
- the synthetic or consented workflow tested;
- a predeclared, aggregate outcome such as setup completion or whether a
  supported route chain was rendered; and
- explicit limitations, unresolved cases, and opt-out or withdrawal notes.

Small groups should use ranges when an exact count could identify a participant.
Results must distinguish successful supported behavior from unsupported facts
that correctly remained `Unresolved`.

## Prohibited collection and publication

Never collect, retain, or publish:

- repository names, organizations, URLs, or absolute roots;
- source files, excerpts, expressions, symbols, or identifiers;
- credentials, keys, cookies, headers, authorization or session data;
- request or response bodies, query values, logs, or runtime payloads;
- graph snapshots or screenshots from non-public analyzed projects;
- customer, employee, or contributor personal data; or
- telemetry, persistent identifiers, or cross-session tracking.

Pilot evidence must not be described as representative of all projects,
framework versions, or production environments.

## Publication template

Copy this template only after the consent and minimization requirements are
satisfied. Leave unsupported fields unpublished rather than estimating them.

```text
Version or commit:
Date window:
Participant count or range:
Framework categories:
Consented workflow:
Aggregate outcome:
Unresolved or unsupported cases:
Limitations:
Consent confirmation:
Withdrawal or correction contact:
```

The maintainer reviews every pilot note against
[the security model](security-model.md) before publication.
