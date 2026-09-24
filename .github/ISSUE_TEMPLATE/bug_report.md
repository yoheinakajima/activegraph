---
name: Bug report
about: Report unexpected runtime, store, behavior, or LLM-integration behavior.
title: ""
labels: ["bug", "status: needs-info"]
assignees: []
---

<!--
Before filing: check the doc site at https://docs.activegraph.ai
(error pages under /errors/ document known failure modes) and
CONTRIBUTING.md for the contribution policy.

A minimal reproduction is load-bearing. Issues without one usually
round-trip for clarification before any diagnosis can begin.
-->

## Reproduction

<!--
Minimal Python code that triggers the issue. Trim to the smallest
runnable example that still reproduces. Inline it here rather than
linking out so it stays attached to the issue history.
-->

```python
# minimal reproduction here
```

## Expected behavior

<!-- What you expected to happen. -->

## Actual behavior

<!--
What actually happened. Include the full traceback if one was
raised, and any `behavior.failed` event payload or WARNING log line
if the failure was emitted rather than raised.
-->

## Invariant at risk

<!--
If known: event authority/ordering, run identity/writer ownership, replay,
framework lifecycle/taxonomy, backend parity, or public extension behavior.
It is fine to write "unsure"; maintainers will classify it.
-->

## Failure-path evidence

<!--
What state changed before or after the failure? For persistence issues, include
whether the in-memory graph and reopened event log agree. Remove secrets and
private payloads.
-->

## Framework version

<!-- Output of `activegraph --version`. -->

```
activegraph X.Y.Z
```

## Python version

<!-- Output of `python --version`. -->

## OS

<!-- e.g. macOS 14.5, Ubuntu 22.04, Windows 11. -->

## Anything else worth knowing

<!--
Optional. Recent upgrades, custom stores, non-default LLM provider,
pack-loading order, anything you suspect might matter.
-->
