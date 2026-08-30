# Issue triage and decision policy

ActiveGraph handles issues as evidence about a small set of project
invariants—not as an unordered implementation queue. This policy makes the
route from report to decision visible and repeatable.

The cross-repository architecture record lives in
[`activegraph-vision`](https://github.com/yoheinakajima/activegraph-vision).
Runtime contract amendments land in [`CONTRACT.md`](CONTRACT.md). An issue can
motivate either, but it does not supersede them.

## The triage unit

The unit of planning is an **invariant and its acceptance evidence**, not a
single symptom. Maintainers group issues that require the same architectural
decision into one milestone and may implement several reports through one
shared seam. Conversely, a large proposal that crosses several invariants is
decomposed before implementation.

Every confirmed issue should answer four questions:

1. **Which layer owns it?** Runtime law, a capability pack, product meaning,
   or an external host/integration.
2. **Which invariant is at risk?** Event authority and ordering, run identity
   and ownership, closed framework taxonomies, backend semantic parity, or a
   documented extension point.
3. **What would disprove the fix?** Name the conformance, replay, failure,
   compatibility, or work-avoided evidence before selecting code shape.
4. **What is deliberately out of scope?** Keep adjacent product or provider
   concerns out of the runtime merely because the issue exposed them.

## Status labels

Status labels are mutually exclusive. Maintainers move an issue through this
state machine:

| Label | Meaning | Exit condition |
| --- | --- | --- |
| `status: needs-info` | Evidence is not yet sufficient to reproduce or classify. | The requested reproduction or inventory is attached. |
| `status: needs-decision` | The problem is credible but architecture or ownership is unresolved. | A contract amendment, ADR, or explicit maintainer decision selects the boundary. |
| `status: confirmed` | The report and owning invariant are established. | Scope and acceptance evidence are recorded. |
| `status: ready-for-pr` | The decision, scope, and gates are concrete enough for implementation. | A linked PR lands or new evidence returns it to decision. |

`status: confirmed` and `status: ready-for-pr` can be applied in the same
triage pass when an existing contract already determines the solution shape.
Priority labels express ordering; they never replace status or ownership.

## Classification and routing

- **Integrity bug:** an accepted fact, replay, projection, identity, or
  lifecycle claim can become false. Integrity work takes precedence over new
  convenience APIs and receives failure-path conformance tests.
- **Performance issue:** results are correct but avoidable work breaks an
  explicit scale expectation. The acceptance gate measures work avoided
  (rows/cells returned, payload decodes, or complexity), not only elapsed time.
- **Architecture proposal:** a new provider, backend, or execution model asks
  for a boundary decision. It remains `status: needs-decision` until ownership
  and failure semantics are recorded. Provider integrations prove themselves
  outside core before adding a core dependency.
- **Contributor audit:** a broad review is evidence, not an alternate contract.
  It must provide the machine-readable inventory described by the audit issue
  template. Maintainers classify rows by invariant and accept them in bounded
  batches; a fork is never merged wholesale as the unit of review.
- **Question or usage gap:** route to documentation unless runtime behavior
  contradicts the documented contract, in which case reclassify as a bug.
- **Out of scope / duplicate / invalid:** close with the owning layer or
  canonical issue named. Closing a proposed mechanism does not reject its
  underlying goal when another boundary owns it.

## Pull-request gate

Non-trivial pull requests link an issue carrying `status: ready-for-pr` and
name the governing contract amendment, ADR, or maintainer decision. Review is
against the invariant and its predeclared gates. A PR that only patches the
reported call site can be rejected even when its local test passes if the same
invariant remains broken elsewhere.

When implementation reveals that the decision is wrong, work returns to
`status: needs-decision`; code does not silently become the new architecture.

## Milestone closure

A milestone closes only when:

- every included issue has a recorded disposition;
- shared conformance and failure gates pass on every affected backend;
- public behavior changes have contract, changelog, and documentation updates;
- contributor credit is preserved in the PR and release notes; and
- deferred work names a decision boundary and trigger, not a vague “later.”

