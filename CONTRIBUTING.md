# Contributing to Active Graph

This document names the contribution policy in force during Active
Graph's early public phase. The policy is deliberately narrow today
and relaxes as the contributor community matures — see
[Out of scope today](#out-of-scope-today) for the items deferred
and the criteria that re-open them.

The framework's source of truth for design decisions is
[`CONTRACT.md`](CONTRACT.md). Every locked decision is a numbered
section; every revision is appended, never modified (Standing Rule
§1). The contribution policy below preserves that discipline at the
contributor surface.

## Issues-first contribution policy

Active Graph uses an **issues-first** policy:

- **Issues are open.** Bug reports, feature requests, questions,
  and documentation feedback are all welcome. Use one of the three
  templates under [`.github/ISSUE_TEMPLATE/`](.github/ISSUE_TEMPLATE/).
- **Code pull requests are maintainer-only by default.** The
  abstractions are still settling (twelve milestones of audit
  discipline against a stable surface; the surface is now the
  contract). External code PRs other than the trivial-fix class
  below are accepted only after an issue discussion has agreed on
  the change's shape. Drive-by code PRs that bypass the
  issue-first gate will be closed with a pointer back to this
  document. Contributors who have carried a full issue →
  discussion → PR arc to merge may be granted direct-PR rights by
  the maintainer, named here (CONTRACT v1.3 #2):
  - [@dudizimber](https://github.com/dudizimber) (the GraphStore /
    FalkorDB arc: issues #38/#41/#43/#45 → PRs #39/#46, v1.2.0)
- **`status: ready-for-pr` is the public implementation gate.** Once a
  maintainer has recorded ownership, scope, and acceptance evidence and
  applied that label, the linked issue is open to an external implementation
  unless its maintainer comment says otherwise. The label authorizes the
  agreed shape, not an adjacent redesign. See [`TRIAGE.md`](TRIAGE.md) for the
  state model.
- **Documentation and trivial-fix PRs may be opened directly.**
  Typo fixes, example improvements, doc clarifications, and
  broken-link fixes do not need an issue first — whether they live
  under [`docs/`](docs/) or in source docstrings, comments, and
  error-message prose. The test for "trivial" is mechanical, not
  editorial: no test's expected behavior changes, no public
  signature changes, no [`CONTRACT.md`](CONTRACT.md) surface is
  touched. The CI gates (pytest, mypy, docstrings, docs build)
  apply as always — a "trivial" PR that turns a gate red was not
  trivial, and gets the issue-first pointer.

The policy is a pre-launch posture, not a permanent stance. It
exists to preserve the contract-amendment discipline that produced
the v1.0 surface while the contributor community is still small
enough that drift can be caught in code review. As the community
matures, the policy relaxes — first to allow trusted-contributor
code PRs without the issue gate, then to broaden the contributor
surface generally.

## What a good issue looks like

Each template prompts for the structure that makes a triage pass
deterministic:

- **Bug reports** name a minimal reproduction (code that triggers
  the issue), the expected behavior, the actual behavior, the
  framework version (`activegraph --version`), the Python version,
  and the OS. The minimal reproduction is load-bearing — issues
  without one usually round-trip for clarification before any
  diagnosis can begin.
- **Feature requests** name the problem you are trying to solve
  (not the solution you have in mind), the current workaround
  (what you are doing today), the proposed solution (what you
  would like to be different), and any open questions you have
  about shape or trade-offs. Problem framing first; solution
  shape second.
- **Questions** name what you tried, what you saw, what you
  expected, and the relevant context (framework version, what you
  are building). The framework's doc site at
  [docs.activegraph.ai](https://docs.activegraph.ai) covers the
  conceptual surface; questions are best for "I read the doc page
  and the gap I hit is X."

The templates head with a one-line pointer to the docs and to this
document. Checking those first usually answers the question or
sharpens the report.

Broad audits use the dedicated **Contributor audit** template. They include a
machine-readable row per finding and group rows by invariant. A fork, aggregate
count, or large diff without that inventory cannot be reviewed as one unit;
maintainers will request decomposition before evaluating implementation.

Architecture proposals use the dedicated **Architecture proposal** template.
Provider, backend, and distributed-execution ideas begin with an ownership and
failure-model decision. Integrations normally prove value in a separately
versioned host or adapter before asking runtime core to absorb a dependency.

## Community management tooling

Community-management tooling — issue triage, surfacing for
maintainer review, and a public dashboard of community state — is
planned, built on Active Graph itself (the framework managing its
own community as a reference deployment). The tooling will be
open-sourced when it ships. Specific URLs, launch date, and
repository name will be named at that point.

The pointer here is intentionally soft: forward-dated promises in
a contributor document rot the moment a date slips. The
commitment is to the tooling and to publishing the source; the
mechanics land when they land.

## License

Active Graph is licensed under the [Apache License 2.0](LICENSE).

By submitting a contribution to Active Graph (via pull request,
patch, or any other form of intentional submission for inclusion
in the work), you agree that your contribution is received under
the same Apache License 2.0, per §5 ("Submission of Contributions")
of the license. No separate Contributor License Agreement (CLA) or
Developer Certificate of Origin (DCO) signature is required at
this time; the Apache 2.0 §5 implicit grant is the contract.

Trademark, attribution, and notice requirements are governed by
the [LICENSE](LICENSE) (Apache 2.0 §§4, 6) and [NOTICE](NOTICE)
files at the repository root. Downstream redistributors preserve
NOTICE per §4(d).

## Out of scope today

Items deferred from the v1.0.5.post1 contribution-surface release
and tracked for a future revisit (see CONTRACT v1.0.5.post1 #1's
"deliberately does NOT touch" section):

- **No CLA or DCO requirement.** Apache 2.0 §5's implicit grant
  is the contract today. If contribution volume grows past the
  maintainer-review bandwidth, or if enterprise legal desks
  request the ceremony, the CLA-vs-DCO decision lands then.
- ~~No `CODE_OF_CONDUCT.md`.~~ **Shipped (CONTRACT v1.3 #2):**
  [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md) is Contributor
  Covenant v2.1 with public reporting channels — an X post
  mentioning [@yoheinakajima](https://x.com/yoheinakajima) or a
  public issue. The original ordering rule is honored by naming
  channels that are already staffed rather than standing up an
  inbox that would not be.
- **Broader contributor surface — first relaxation shipped
  (CONTRACT v1.3 #2).** The trivial-fix carve-out and the
  trusted-contributor list above are the calibrated broadening
  this bullet promised. Further relaxation waits on more observed
  contribution patterns.

## Reaching the maintainers

For project matters, file an issue — the issues surface is the
contact channel. Conduct reports are public too, by design
(CONTRACT v1.3 #2): mention
[@yoheinakajima](https://x.com/yoheinakajima) in a public post on X
(preferred) or open a public issue — read
[`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md) first, especially its
plain-spoken note that there is no private channel. When the
community-management tooling lands, additional contact channels
land with it.
