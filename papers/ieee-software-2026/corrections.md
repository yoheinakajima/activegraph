# Additive correction and preparation record

Recorded November 2 2026. The public May v1 is retained byte-for-byte; this is a new draft and an additive record, not an amended historical artifact.

| Public-v1 framing | Current draft disposition |
| --- | --- |
| Replay/byte reproduction stated broadly; green strict replay described as proof | Separate recorded-state projection, selected-invariant behavior validation and resumed/new work. State payload-only drift limitation and demonstrate it with small offline check. |
| Every behavior contract violation, including wall clock, implied detectable | Remove universal promise; only checked invariants/equality fields support conclusions. |
| Forking/prefix reuse described as largely absent elsewhere | Temporal first; credit established event-history replay/reset and LangGraph saved-prefix branching/suffix reexecution. No historical priority claim. |
| Cheap/free prefix wording | No new model calls for historical reconstruction differs from local copy/projection CPU/storage. No overhead/latency benchmark claimed. |
| Fork described without current backend/cut constraints; docs imply strict by default | Describe source method, SQLite restriction, inclusive valid completed cutoff and promotion-block constraint; fork does not itself invoke strict verifier. |
| Log alone/total lineage/rules capture | Scope to captured events/responses/relations and separately pinned code/schema/environment/source. Provenance does not establish truth, provider attestation or action authority. |
| Fixture modeled as byte-deterministic run | Fresh current-source executions exposed measured tool-response latency. Retain raw inequality and exact field inventory; report separately defined normalized event and full-projection equality. |
| Self-improvement affordance | Keep as possible future use; no demonstrated self-improvement or task-accuracy gain. No Regimes result imported. |
| Architecture-paper length/abstract | New practitioner draft,141-word abstract,three actionable insights,one editable figure,one comparison table,seven references. Conservative counted total in manifest. |
| Latest install command left release implicit | Pin7070112 current source and environment; source metadata/tag/PyPI are separate identities. |

## Verification chronology

1. Source inspection established that strict replay compares event types/length and selected hashes, with an explicit payload-only limitation; no runtime redesign was attempted.
2. A first bounded two-run fixture check asserted raw JSONL equality. It failed on measured `tool.responded.payload.latency_seconds`. A diagnostic repeat confirmed the mismatch class. Those temporary failed attempts were not claimed as successful results and no fixture/code was changed to force equality.
3. The verification script was revised to retain both raw outputs/hashes, enumerate every differing field, and reject differences outside that exact timing field. A distinct normalization was introduced **after** observing the mismatch. This is an engineering check, not a preregistered experiment.
4. The first retained check passed the stated projection/reload/normalized-event/fork-boundary/strict-limitation checks. The raw-equality result remains false. That retained pair has 47 differing latency fields, recorded in full.
5. The initial scoped pytest harness invocation failed 15 temporary-directory setup cases because the parent scratch directory had not been created;21 cases passed. Creating that directory fixed the harness; rerun passed 36/36. Runtime/tests were unchanged.
6. Cached TeX compilation initially lacked optional Latin Modern/picture-line fonts. The new article figure was expressed with standard boxes/math arrows and cached default fonts; no TeX runtime or plugin was installed. Final compilation and visual inspection are documented in readiness/manifest. No archived-v1 file was changed.

No failed finding was converted into a source change or hidden by replacing a generic verifier. Exact retained result bytes are in the manifest. Public-v1 correction/upload, any runtime/documentation repair and any broader experiment remain separate later actions.

7. Manager review requested an executable source-identity guard. Added `verification/runtime-inputs.json` pinning all 110 baseline package/fixture files; the script resolves the actual imported package, verifies every SHA256 and exact file inventory, and fails on mismatch. One focused rerun in `evidence/identity-checked/` passed with unchanged normalized/projection hashes and its own two raw timing hashes/difference inventory. The earlier root evidence files remain unchanged. The identity-checked receipt is the primary result for final review.

8. Exact-head sourcePR CI run41585888136 failed one existing error-format snapshot in both Python matrix legs. A focused reproduction from the PR and an exported baseline7070112 failed identically with Pydantic2.14.2. Actual error text equals the snapshot after replacing only its embedded Pydantic documentation URL from2.13 to2.14. Native runtime,tests,dependency declarations and workflow Git object IDs match the baseline. This preexisting dependency-version-sensitive snapshot issue remains a required CI blocker; no paper-induced defect or native fix was introduced. Exact proof is in `evidence/ci-diagnosis.json`; article/supplement bytes are unchanged.
