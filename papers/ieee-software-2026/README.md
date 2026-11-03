# IEEE Software preparation packet

Source-owned draft prepared November 2, 2026 for **The Log Is the Agent: Engineering Inspectable Agent Runs**. This is a distinct practitioner article based on current runtime source `7070112c63b7cffc4b3aef8b7e907c8f2f9a9165`, with unchanged arXiv v1 archived for provenance. No runtime behavior, accepted contract or public preprint is changed.

**State: ready for manager/author review; not submission-ready.** Approved public author metadata, funding/interests and genuine photograph are included. Assistance is reconstructed with explicit unknown historical versions; final author responsibility/rights, fresh exclusivity/related-manuscript declaration and portal/action approval remain pending. Private journal history is not recorded here. The packet has not been submitted, uploaded to a journal, publicly deposited or accepted. A source draft PR is a preparation artifact.

## Review files

- `article.tex` / `article.pdf`: practitioner draft, seven references, 128-word abstract and three actionable insights. The editable figure is embedded in the LaTeX source; table compares Temporal first, then LangGraph and ActiveGraph.
- `supplement.tex` / `supplement.pdf`: exact offline method, results, negative finding, reproduction and limitations.
- `evidence/identity-checked/verification.json`: observed fixture counts, raw/normalized hashes, complete differing-field inventory, projection/reload/fork checks and strict-verifier boundary.
- `evidence/fixture-events.jsonl`, `fixture-events-second.jsonl`, `fixture-events-normalized.jsonl`, `fixture-projection.json`: retained exact synthetic outputs, not external research or personal data.
- `verification/verify.py`: bounded credential-free in-process execution with network guard; writes only to a supplied output directory.
- `verification/check_package.py`: static format/hash/inventory check, no runtime campaign.
- `review-dispositions.md`: source-verified dispositions for all Claude C-LOG-1–10 and Gemini G-LOG-1–5 findings, including retractions and deferred expansions.
- `verification/check_strict_effects.py` / `evidence/strict-effects/strict-effects.json`: bounded effect-free mock confirms tool/ordinary behavior reexecution during strict load despite model cache; no real external effects.
- `verification/audit_retained.py` / `evidence/review-clarifications.json`: derives pair47/48,budget units and concrete synthetic trace from sealed saved outputs,tests optimized-Python refusal without runtime import.
- `claim-audit.md`, `references.md`, `corrections.md`: claim-to-source mapping, primary-source bibliography audit and correction chronology.
- `author-worksheet.md`, `rights-and-availability.md`, `publisher-checklist.md`: approved-public-fact worksheets and exact later submission gates. Never commit private filled answers; `.private/` is ignored local storage if needed.
- `cover-letter.md`: an **unsent** concrete letter requiring final author approval and route preflight.
- `tool-use-audit.md`: stage-specific AI/tool assistance, pinned Git evidence, unknown original writing history and actual verification limits.
- `author-photo.jpg`: approved genuine 3538×3538 photo, original bytes unchanged; included in article biography.
- `readiness.md`: LOG-001–028 dispositions, checks and exact remaining owner inputs.
- `manifest.json`: SHA-256 inventory, software inputs/environment and verification record. It excludes itself and disposable build/cache/log files to avoid self-reference.
- `archive/arxiv-v1/`: original v1 bytes and sources. Do not edit, redate or rebuild these as the new article.

## Results and the main correction

The fixed synthetic fixture produces 671 events, 93 objects, 76 relations, 103 fixture-served model requests and 48 tool requests, with zero attempted network operations. Its projected objects/relations match between executions and after saved-log reconstruction. **Raw event bytes do not match**: 48 events in the identity-checked pair differ only in measured tool-response latency. Removing only `tool.responded.payload.latency_seconds` yields a separately defined normalized equality. Both raw hashes and the full difference inventory are retained. The normalization was added after inspecting the mismatch; it is not a pre-registered result or a generic verifier improvement.

Strict replay accepts an object-payload-only change preserving event types and rejects an extra event. A later effect-free mock also confirms that strict verification reruns tool bodies/ordinary behavior code while caching model responses;use effect-free/dry-run adapters before live-effect verification. This is not a new diligence campaign. A final-event fork copies the whole run and leaves the parent's in-memory event list unchanged after one child annotation;the persisted parent is not reloaded. These are narrow checks, not arbitrary payload/byte guarantees, an execution-recovery study, task-accuracy evidence, independent replication or demonstrated self-improvement.

## Reproduction

Use a fresh non-iCloud source checkout at the exact baseline and a disposable Python 3.11 environment. Installing the local package and dependencies is separate from executing the guard:

```sh
python3.11 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -I papers/ieee-software-2026/verification/verify.py --output /absolute/disposable/output
```

The paper files live on the preparation branch; the runtime source remains baseline-equivalent. The exact observed environment is in `manifest.json`; no provider extras are required for the article script. The runtime-input pin checks every one of the 110 imported package/fixture files and exact inventory before any fixture execution; a different installed implementation fails closed. Run without credentials. The guard fails closed on attempted sockets/DNS/bind; it is not an OS sandbox. Raw timing hashes will vary on a fresh execution. The normalized event and full projection hashes describe the semantic reproducibility contracts actually checked. Verifying the retained manifest does not rerun model calls. For a portable saved-output-only check needing no Git, Pandoc or installed runtime, run `python verification/check_package.py --saved-only` from this packet directory. The earlier root `evidence/` raw pair is retained unchanged for chronology; the identity-checked subdirectory is primary.

Build using the existing Tectonic engine and cached resources (no new TeX runtime installation):

```sh
tectonic --only-cached --keep-logs --untrusted papers/ieee-software-2026/article.tex
tectonic --only-cached --keep-logs --untrusted papers/ieee-software-2026/supplement.tex
python papers/ieee-software-2026/verification/check_package.py
```

No native editor/compiler app tool was exposed in this worker session; the existing local Tectonic tool was used instead. Each page was rendered with Poppler and visually inspected. The revised review-stage PDFs are rendered and checked;current page counts and format total are recorded in readiness/manifest. Main article count is conservatively checked by converting all visible text (including references/header/visual text) to plain text and adding 250 per figure/table and the biography photo; this deliberately overcounts rather than relying on exclusions. The main manuscript can be revised further without approaching the current 4,200-word limit.

## Route and action boundaries

The official **Building Trustworthy Software in the Time of AI** call is due November 5, 2026, expected publication July/August 2027; no cutoff hour/time zone was stated. Use the special issue only if the corrected packet and author approvals are complete in time. The general IEEE Software call remains the fallback; it does not remove evidence/author gates. No abstract inquiry is needed to draft this packet.

The current call directs authors to `https://ieee.atyponrex.com/journal/sw-cs`. A read-only browser check reached IEEE account creation/sign-in; authenticated article types/required fields and final costs were not inspected. No account action is authorized here. Traditional publishing avoids optional OA payment under inspected general policy; current general magazine OA rate is USD 2,995 plus applicable tax with title exceptions. Final title-specific terms, non-APC fees and membership/coverage remain to be verified, never presumed. Author must approve the concrete package and route before any submission, fee election, legal agreement or public update.

## Required CI status

The initial exact-head full CI test matrix fails on one existing error-format snapshot: Pydantic2.14.2 emits `errors.pydantic.dev/2.14/v/missing`, while the unchanged snapshot expects2.13. The focused test reproduces identically from the exported baseline and paper head; unchanged native Git object IDs and the exact one-string difference are recorded in `evidence/ci-diagnosis.json`. This is not a paper-induced test or collection failure. **Required CI is red, so the PR is not merge-ready.** No native snapshot, dependency, workflow or runtime was changed in this paper-only preparation.

## Immutable evidence/code snapshot after two-model review

Runtime source is7070112. Evidence/code ancestor is [ba41ac771e6d58b4856acaa8dc8f5d9f624e287a](https://github.com/yoheinakajima/activegraph/tree/ba41ac771e6d58b4856acaa8dc8f5d9f624e287a/papers/ieee-software-2026). It contains original sealed pairs,primary receipt,pins,new effect-free mock/static audit and optimized-Python refusal,preceding the final editorial revision. The printed PDFs link the immutable ancestor rather than pretending baseline7070112 contains papers/. This does not create a DOI,release,merge or journal deposit. The old receipt's literal zero-live-call field is an inference from fixed fixture provider/guard observations,not independently instrumented telemetry. Guarded APIs are connect/connect_ex/bind/create_connection/getaddrinfo,not every socket API. Existing assertions are required;python-O is refused before output/runtime work.
