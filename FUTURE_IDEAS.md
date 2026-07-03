# Active Graph future ideas

These are valid candidates, but they should not block the current
roadmap cycle (`ROADMAP.md`, v1.3) unless the maintainer explicitly
promotes them. Each item stays visible here with a one-line reason it
is deferred **and a revisit trigger** — the condition under which the
deferral expires and the item must be re-reviewed. The v1.3 scoping
pass (2026-07-03) found that several v1.1-era deferrals had expired
without anyone noticing because the file recorded reasons but not
triggers; the trigger column prevents that recurrence. Promoted in
that pass (now in `ROADMAP.md`): native structured-output mode,
type-completeness ratchet, docstring-completeness ratchet,
`CODE_OF_CONDUCT.md` plus contact channel, contribution-policy
relaxation.

- **Dict-form `output_schema` support.** Deferred because the schema
  surface should stay narrow while native structured-output mode
  (ROADMAP Phase 2) settles. *Revisit when: native mode ships.*
- **`on_failure` callback.** Deferred because callback semantics could
  complicate the locked events-not-exceptions failure model and need a
  separate design pass. *Revisit when: a user presents a failure-routing
  need the event log can't serve.*
- **Fire-once aggregation triggers.** Deferred because aggregation
  trigger semantics are runtime behavior with no current demand signal.
  *Revisit when: an issue asks for it with a concrete use case.*
- **Full Pack\* error migration sweep.** Deferred because the complete
  sweep is larger than any current roadmap slice and needs careful
  per-site recovery prose. *Revisit when: a roadmap cycle has spare
  MUST capacity, or a Pack\* error confuses a real user.*
- **DB error wrappers.** Deferred because useful wrappers require
  real-world driver-failure experience rather than invented recovery
  guidance. *Revisit when: driver-failure reports arrive (the FalkorDB
  backend widens this surface).*
- **`Runtime.load` auto-provider ergonomics.** Deferred because it is
  convenience behavior with migration implications and is not required
  by the current roadmap. *Revisit when: a second user-test flags it
  (first was v1.0-rc1 B2).*
- **Fork cache pre-population symmetry.** Deferred because fork scope
  stays centered on `fork --set`; cache symmetry can follow as a
  separate fork-ergonomics improvement. *Revisit when: fork ergonomics
  get a dedicated slice.*
- **Content negotiation for docs host.** Deferred because it requires
  docs-host infrastructure beyond static GitHub Pages. *Revisit when:
  the docs host moves off GitHub Pages.*
- **Editorial doc-readability pass.** Deferred because it is open-ended
  editorial polish, and roadmap cycles need bounded mergeable slices.
  *Revisit when: user-test findings cluster on doc comprehension.*
- **CLA / DCO decision.** Deferred because Apache 2.0's implicit grant
  is sufficient for the current contribution volume. *Revisit when:
  legal or scale pressure changes — e.g. a corporate contributor asks
  for one.*
- **Additional graph backends (Neo4j, Postgres-graph).** Deferred
  because backends are contribution-shaped, not roadmap-shaped;
  `GraphStoreConformance` (CONTRACT v1.2 #5) is the extension contract
  waiting for them. *Revisit when: one arrives inbound — follow the
  v1.2 merge-then-lock pattern.*
