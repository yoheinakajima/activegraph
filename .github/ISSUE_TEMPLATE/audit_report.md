---
name: Contributor audit
about: Submit a broad code audit as a decomposable evidence inventory.
title: "[Audit] "
labels: ["status: needs-info"]
assignees: []
---

<!--
Read TRIAGE.md. A large fork or aggregate count is not independently
reviewable. Attach or link a CSV/JSON inventory with one row per finding; do
not ask maintainers to infer the inventory from a branch diff.
-->

## Audited base

- Upstream commit SHA:
- Audit tooling / method:
- Relevant environment:

## Executive finding

<!-- Which one to three invariants account for most findings? -->

## Machine-readable inventory

<!--
Attach CSV or JSON. Required columns/keys per finding:

id, path, line, category, invariant, evidence, expected_behavior,
suggested_owning_layer, proposed_acceptance_test

If a line is generated or no longer exists on the audited SHA, say so.
-->

Inventory link or attachment:

## Reproduction commands

```text
# exact commands and outputs needed to reproduce the inventory
```

## Proposed batches

<!--
Group findings that share one invariant and acceptance gate. Do not group by
the order in which a fork happened to change files.
-->

## Existing implementation, if any

<!--
Optional branch/commit references. The inventory remains the review input;
implementation is evaluated only after triage selects ownership and scope.
-->

