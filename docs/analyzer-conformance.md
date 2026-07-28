# Analyzer conformance

Analyzer conformance is a static, fixture-backed workflow for contributors. It
uses the existing analyzers, fixtures, and tests only; it is not a plugin API,
loader system, remote corpus, or extension point.

## Fixture contribution workflow

1. Add or update synthetic, non-sensitive examples under `fixtures/`. Keep real
   project data, secrets, request/response bodies, cookies, headers, URL query
   values, source excerpts, and absolute roots out of fixtures and issues.
2. Put frontend examples below the fixture's `frontend/` tree and Django
   examples below the fixture's `backend/` tree. Existing examples include
   `fixtures/react-django`, `fixtures/vue-django`, `fixtures/nuxt-django`, and
   `fixtures/react-django-realistic`.
3. If the fixture is part of the conformance set, update
   `fixtures/analyzer-conformance.json`. Keep expected summaries structural:
   route IDs, node-kind counts, edge-kind counts, diagnostic codes, and bounded
   proof counts. Do not add raw source expressions or values.
4. For full graph examples that already use expected snapshots, update files in
   `fixtures/expected/` with minimized IDs and catalog-backed diagnostics only.
5. Run the focused analyzer check:

   ```sh
   npm run test:js
   ```

6. Before opening a pull request, run the complete repository check:

   ```sh
   ./scripts/check.sh
   ```

For local inspection of a frontend fixture, use the same static analyzer command
that the manifest records:

```sh
node analyzers/index.mjs --repository <namespace> --frontend-only <fixture-root>
```

## Support and limitations matrix

| Framework / layer | Current support | Synthetic fixture anchor | Limitations that must remain explicit |
| --- | --- | --- | --- |
| React | React Router v6 JSX/data routes, components, events, HTTP calls, request payload structure, and static navigation links | `fixtures/react-django`, `fixtures/react-django-realistic` | Dynamic targets, ambiguous components, and unproven finite URL domains remain `Unresolved`. |
| Vue 3 | Vue SFC components, router declarations, template events, HTTP calls, request payload structure, and static navigation links | `fixtures/vue-django` | Dynamic components and unsupported syntax remain `Unresolved`. |
| Nuxt 3+ | File-based `pages/` routes, `useFetch`/`$fetch`, `NuxtLink`, `navigateTo`, and payload structure | `fixtures/nuxt-django` | `server/api` routes are terminal `Unresolved` boundaries and are never traversed. Layout and middleware chains are outside the route graph. |
| Django | URL patterns, views, helpers, recognizable ORM/model boundaries, and unique import-root proof | `fixtures/react-django/backend`, `fixtures/vue-django/backend`, `fixtures/nuxt-django/backend` | Django settings, project code, and custom converters are never imported or executed. Custom converters, `path` converters, ambiguous import roots, and unproven dynamic values remain `Unresolved`. |

Unsupported framework behavior is a conformance result, not a test failure, when
it is represented by catalog-backed `Unresolved` evidence or diagnostics.

## Release metadata consistency

Release changes must keep all public version references aligned:

- `package.json`
- `package-lock.json`
- `pyproject.toml`
- `src/kg_debugger/__init__.py`
- the `README.md` project-status line
- the supported release line in `SECURITY.md`

Use `docs/releasing.md` for the release checklist. The JavaScript conformance
tests read these files directly so stale release metadata fails in the existing
`npm run test:js` workflow.

## Contract boundaries

The conformance harness must not add dependencies, dynamic plugins, arbitrary
code execution beyond the existing static analyzers/tests, remote downloads,
new API routes, storage fields, schema versions, diagnostic catalogs, or public
extension surfaces. New analyzer facts must remain minimized and structural.
