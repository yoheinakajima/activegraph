## Governing issue and decision

<!--
Link the issue carrying `status: ready-for-pr`. Name the CONTRACT amendment,
activegraph-vision ADR, or explicit maintainer decision that owns the shape.
Documentation-only and mechanically trivial fixes may write "trivial-fix
carve-out" instead.
-->

- Issue:
- Decision / contract:

## Invariant and scope

<!--
State the invariant this changes and what is deliberately out of scope. Keep
product meaning, capability ontology, and provider-specific orchestration out
of runtime core unless the linked decision explicitly puts them here.
-->

## Failure and compatibility model

<!--
What happens on partial failure, replay, stale state, unsupported input, and
upgrade from the prior public version? Name any intentionally unchanged path.
-->

## Acceptance evidence

<!--
List exact commands/results. Performance changes measure work avoided in
addition to elapsed time. Backend changes include conformance parity; event
changes include live/replay and failure-path evidence.
-->

- [ ] `pytest -m "not slow" -q`
- [ ] `mypy`
- [ ] `python scripts/gate_docstrings.py`
- [ ] Contract/changelog/docs updated where public behavior moved

## Contributor credit

<!-- Name reporters, designers, and prior implementations whose evidence shaped this PR. -->

