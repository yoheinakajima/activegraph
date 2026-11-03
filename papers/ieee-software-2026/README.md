# IEEE Software preparation packet

Source-owned draft prepared November 2, 2026 for **The Log Is the Agent: Engineering Inspectable Agent Runs**. This is a distinct practitioner article based on current runtime source `7070112c63b7cffc4b3aef8b7e907c8f2f9a9165`, with unchanged arXiv v1 archived for provenance. No runtime behavior, accepted contract or public preprint is changed.

**State: ready for manager/author review; not submission-ready.** Current private journal history is unknown. Author metadata, photo, complete disclosures, rights and exclusivity attestation remain pending. The packet has not been submitted, uploaded to a journal, publicly deposited or accepted. A source draft PR is a preparation artifact.

## Review files

- `article.tex` / `article.pdf`: practitioner draft, seven references, short abstract and three actionable insights. The editable figure is embedded in the LaTeX source; table compares Temporal first, then LangGraph and ActiveGraph.
- `supplement.tex` / `supplement.pdf`: exact offline method, results, negative finding, reproduction and limitations.
- `evidence/identity-checked/verification.json`: observed fixture counts, raw/normalized hashes, complete differing-field inventory, projection/reload/fork checks and strict-verifier boundary.
- `evidence/fixture-events.jsonl`, `fixture-events-second.jsonl`, `fixture-events-normalized.jsonl`, `fixture-projection.json`: retained exact synthetic outputs, not external research or personal data.
- `verification/verify.py`: bounded credential-free in-process execution with network guard; writes only to a supplied output directory.
- `verification/check_package.py`: static format/hash/inventory check, no runtime campaign.
- `claim-audit.md`, `references.md`, `corrections.md`: claim-to-source mapping, primary-source bibliography audit and correction chronology.
- `author-worksheet.md`, `rights-and-availability.md`, `publisher-checklist.md`: unfilled/public-fact-only templates and later submission gates. Never commit private filled answers; `.private/` is ignored local storage if needed.
- `cover-letter.md`: an **unsent** concrete letter requiring final author facts/route choice.
- `readiness.md`: LOG-001–028 dispositions, checks and exact remaining owner inputs.
- `manifest.json`: SHA-256 inventory, software inputs/environment and verification record. It excludes itself and disposable build/cache/log files to avoid self-reference.
- `archive/arxiv-v1/`: original v1 bytes and sources. Do not edit, redate or rebuild these as the new article.

## Results and the main correction

The fixed synthetic fixture produces 671 events, 93 objects, 76 relations, 103 fixture-served model requests and 48 tool requests, with zero attempted network operations. Its projected objects/relations match between executions and after saved-log reconstruction. **Raw event bytes do not match**: 48 events in the identity-checked pair differ only in measured tool-response latency. Removing only `tool.responded.payload.latency_seconds` yields a separately defined normalized equality. Both raw hashes and the full difference inventory are retained. The normalization was added after inspecting the mismatch; it is not a pre-registered result or a generic verifier improvement.

Strict replay accepts an object-payload-only change preserving event types and rejects an extra event. A completed-cutoff fork preserves its copied prefix and leaves the parent unchanged after one child annotation. These are narrow checks, not arbitrary payload/byte guarantees, an execution-recovery study, task-accuracy evidence, independent replication or demonstrated self-improvement.

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

No native editor/compiler app tool was exposed in this worker session; the existing local Tectonic tool was used instead. Each page was rendered with Poppler and visually inspected. Main article count is conservatively checked by converting all visible text (including references/header/visual text) to plain text and adding 250 per figure/table; this deliberately overcounts rather than relying on exclusions. The main manuscript can be revised further without approaching the current 4,200-word limit.

## Route and action boundaries

The official **Building Trustworthy Software in the Time of AI** call is due November 5, 2026, expected publication July/August 2027; no cutoff hour/time zone was stated. Use the special issue only if the corrected packet and author approvals are complete in time. The general IEEE Software call remains the fallback; it does not remove evidence/author gates. No abstract inquiry is needed to draft this packet.

The current call directs authors to `https://ieee.atyponrex.com/journal/sw-cs`. A read-only browser check reached IEEE account creation/sign-in; authenticated article types/required fields and final costs were not inspected. No account action is authorized here. Traditional publishing avoids optional OA payment under inspected general policy; current general magazine OA rate is USD 2,995 plus applicable tax with title exceptions. Final title-specific terms, non-APC fees and membership/coverage remain to be verified, never presumed. Author must approve the concrete package and route before any submission, fee election, legal agreement or public update.
