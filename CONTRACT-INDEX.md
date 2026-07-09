# Active Graph contract and planning index

This repository now separates locked contract history, shipped-change
history, current v1.1 scope, and unscheduled ideas. Use this map before
adding new planning notes.

## Document roles

- **[`CONTRACT.md`](CONTRACT.md) — locked decisions and historical
  archeology.** This remains the source of truth for decisions that
  were already locked by a release amendment. It is intentionally long:
  older milestone sections preserve why decisions were made, what was
  explicitly out of scope, and what follow-on work was filed. Do not use
  new inline CONTRACT prose as the primary place to scope v1.1
  implementation work.
- **[`CHANGELOG.md`](CHANGELOG.md) — shipped changes and migration
  notes.** This records what changed in each release, how users migrate,
  and which release surfaced a follow-on candidate. It can point to
  planning documents, but it is not the current v1.1 roadmap.
- **[`v1.1-plan.md`](v1.1-plan.md) — original consolidated backlog from
  the post-v1.0.3 review.** This file is a backlog source, not the
  current roadmap. It collected scattered v1.1 candidates after the
  post-v1.0.3 contract review and is useful for provenance, source
  amendments, and original priority notes.
- **[`ROADMAP.md`](ROADMAP.md) — scoped roadmap.** This is the
  scoping document for a planning cycle (last authored for v1.3 on
  2026-07-03; the v1.3–v1.7 line has since shipped, so treat it as a
  dated planning artifact rather than live status; the closed v1.1
  roadmap is archived at the bottom of the same file). It translates
  the backlog into mergeable phases and marks each item as MUST,
  SHOULD, or DEFERRED.
- **[`FUTURE_IDEAS.md`](FUTURE_IDEAS.md) — valid but unscheduled
  ideas.** This keeps useful candidates visible without letting them
  block the current cycle unless the maintainer explicitly promotes
  them into `ROADMAP.md`. Each entry carries a revisit trigger — the
  condition under which its deferral expires.
- **[`CONTRACT-review-findings.md`](CONTRACT-review-findings.md) —
  archival audit record.** This is the post-v1.0.3 review artifact: an
  audit trail of findings, consistency checks, and candidates surfaced
  during that review. Treat it as archival evidence, not as the live
  roadmap.

## How to classify new planning items

1. If the item is a locked release decision, document it in
   `CONTRACT.md` through the normal amendment process.
2. If the item is a shipped user-facing change or migration note,
   document it in `CHANGELOG.md`.
3. If the item is proposed for the current roadmap cycle, classify it
   in `ROADMAP.md` with a phase and a MUST / SHOULD / DEFERRED marker.
4. If the item is valid but should not block the current cycle, add it
   to `FUTURE_IDEAS.md` with a one-line reason it is deferred and a
   revisit trigger.
5. Do not add new v1.1 planning items only to `v1.1-plan.md`; that file
   is the original backlog source.
