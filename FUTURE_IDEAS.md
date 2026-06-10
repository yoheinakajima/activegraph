# Active Graph future ideas

These are valid candidates, but they should not block v1.1 unless the
maintainer explicitly promotes them into `ROADMAP.md`. Each item stays
visible here with a one-line reason it is future / deferred rather than
part of the current v1.1 blocker set.

- **Native structured-output mode.** Deferred because v1.1 first needs
  to ship provider tool-shape parity without also changing structured
  output semantics; native JSON schema modes can follow in a separate
  provider-focused pass.
- **Dict-form `output_schema` support.** Deferred because the current
  schema surface should stay narrow while v1.1 focuses on documented
  drift gates and provider parity.
- **`on_failure` callback.** Deferred because callback semantics could
  complicate the locked events-not-exceptions failure model and need a
  separate design pass.
- **Fire-once aggregation triggers.** Deferred because aggregation
  trigger semantics are runtime behavior, not a prerequisite for the
  v1.1 cleanup path.
- **Full Pack* error migration sweep.** Deferred because the complete
  sweep is larger than the current scoped v1.1 slices and needs careful
  per-site recovery prose.
- **DB error wrappers.** Deferred because useful wrappers require
  real-world driver-failure experience rather than invented recovery
  guidance.
- **Type-completeness ratchet.** Deferred because it is a quality ratchet
  that can land after the drift-gate foundation is in place.
- **Docstring-completeness ratchet.** Deferred because it is an ongoing
  API documentation quality bar, not a blocker for the first v1.1
  reconciliation and implementation slices.
- **`Runtime.load` auto-provider ergonomics.** Deferred because it is
  convenience behavior with migration implications and is not required
  for the current v1.1 roadmap.
- **Fork cache pre-population symmetry.** Deferred because Phase 4 keeps
  the fork scope centered on `fork --set`; cache symmetry can follow as
  a separate fork-ergonomics improvement.
- **Content negotiation for docs host.** Deferred because it requires
  docs-host infrastructure beyond static GitHub Pages and should not
  block v1.1 code/documentation cleanup.
- **Editorial doc-readability pass.** Deferred because it is open-ended
  editorial polish, while v1.1 needs bounded mergeable slices.
- **CLA / DCO decision.** Deferred because Apache 2.0's implicit grant is
  sufficient for the current contribution volume unless legal or scale
  pressure changes.
- **`CODE_OF_CONDUCT.md` plus contact channel.** Deferred because the
  reporting document should not ship before a real contact channel is
  chosen and staffed.
- **Contribution-policy relaxation.** Deferred because the current
  issues-first posture can be revisited after observing actual v1.0.x
  contribution patterns.
