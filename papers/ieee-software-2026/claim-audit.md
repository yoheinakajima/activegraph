# Retained claims and evidence boundary

Baseline: ActiveGraph `7070112c63b7cffc4b3aef8b7e907c8f2f9a9165`. The new article deliberately uses only the bundled synthetic diligence example. No private Atlas details, Regimes downstream result, independent-replication claim, packing-run total or other case replay denominator appears in the manuscript. Those candidates are unnecessary to the first IEEE account and were omitted rather than borrowing evidence across versions.

| Retained article statement | Source or observed evidence | Limit / disposition |
| --- | --- | --- |
| Events project into typed domain objects/relations; accepted mutations enter through graph emission | `activegraph/core/graph.py`, `activegraph/core/event.py`, fixture JSONL and projection | Captured history, compatible projector/schema and environment; not all external facts/code in the log |
| Behaviors subscribe/react and emit changes; relation logic can participate | `activegraph/runtime/scheduler.py`, `activegraph/behaviors`, `activegraph/packs/diligence` | Control design remains in subscriptions, code, policies and budgets; no elimination of coordination |
| Load reconstructs saved state without firing recorded behaviors | `Runtime.load`, `runtime.py:3341–3353`; native replay tests; exact projection-reload check in `evidence/identity-checked/verification.json` | A loaded runtime can later continue new work; reconstruction is not fresh model execution |
| Strict verification is not general payload/byte equality | `_verify_replay` at `runtime.py:4309` and `Runtime.load` docstring at `:3288`; observed payload-only drift accepted, extra-event drift rejected | Selected non-lifecycle event types/length and model/embedding hashes;model cache does not prevent tool/ordinary-code effects;effect-free counting mock confirms tool1→2 whileFake.complete stays2;no arbitrary determinism or comprehensive wall-clock detection |
| Runtime fork copies an inclusive valid prefix and projects it | `Runtime.fork` at `runtime.py:3423`; `SQLiteEventStore.fork_run`; native fork tests; completed-cutoff copy/annotation check | SQLite runtime API; no `_verify_replay` call in fork; no arbitrary in-flight cutoff/recovery study |
| Parent full history may populate fork caches | `Runtime.fork` constructs `LLMCache.from_events(self.graph.events)` and ToolCache likewise | Record cache flags and available history; no causally identified outside-world counterfactual |
| Atomic promote-block cuts rejected | `_reject_mid_promote_block_fork` call in `Runtime.fork` | Source inspection, not a new promotion/snapshot campaign |
| Fixture counts: 671 events,93 objects,76 relations,103 model-request events,48 tool requests | `verification/verify.py`, `evidence/identity-checked/verification.json`, full retained event/projection bytes | Synthetic fixture only; provider label and cost fields are fixture metadata, not live billing or task accuracy |
| Two fixture projections and saved-log reload compare exactly | Full object/relation serialization with all fields and preserved ordering | Exact observed fixture/configuration only; no general payload-verifier inference |
| Raw event bytes differ; normalized events compare equal | Both retained raw files/hashes; 48 differing-event entries; normalization deletes only `tool.responded.payload.latency_seconds` | Normalization was added after observed failure; retain negative finding and separate labels |
| Completed-cutoff child annotation leaves parent events unchanged | Verification code prefix comparison and before/after parent bytes | Final-event full-history copy and one annotation;in-memory parent list only,no persisted-parent reload or earlier-prefix intervention;no changed-model/cache experiment |
| Guard saw zero attempts at its intercepted connect/connect_ex/bind/create_connection/getaddrinfo APIs | Explicit in-process guard and fixture-only provider path | Not OS sandbox or assurance about arbitrary code; no services, SDK credentials or live provider path |
| Temporal replay/history/reset and LangGraph saved-prefix/suffix semantics | Official docs, primary metadata audited in `references.md` | Contemporary documented behavior, not historical priority or tested parity |
| No demonstrated task-performance superiority/self-improvement | Explicit scope throughout article/supplement | No new benchmark, live model study or downstream scientific claim added |

## Documentation discrepancy is not silently repaired

Current `docs/concepts/replay.md` and `docs/concepts/forking.md` describe stronger/default strict semantics than the inspected runtime method establishes. The paper follows source behavior and the narrow executed checks. Runtime documentation and implementation were not changed in this article PR, and no existing accepted contract was rewritten. Any software/documentation correction is a separate native maintainer issue/milestone, with exact scope and tests. This article does not require that redesign to be truthful.

## What the specimen does not measure

No latency/overhead, memory/disk scaling, resumed in-flight recovery, provider authenticity, capture-time tamper resistance, task correctness, independent replication, baseline comparison, multiwriter safety, compaction retention or self-improvement outcome is claimed. A response cache does not undo outside-world effects or authorize another write. The main text includes these limits; they are not hidden in the supplement.

## Two-model review additions

All findings/dispositions and exact source pointers are in review-dispositions.md. In particular,new effect-free strict mock is distinct from the original671eventfixture;strict tool cache is not enabled by the verifier. Existing primary/earlier pairs stay sealed;static audit records47vs48,55cumulative model-behavior invocations rather than103request turns,48toolcalls and.2638fixturecost. Fork defaults/reset/requeue behavior is source-inspection evidence;choose explicit child budgets/randomness/tools before continuation. Synthetic claim#11/request027/response028/tool021+025/questiontrigger009/document#10 trace is checked from retained bytes,not a new truth/evaluation result. Evidence/code ancestorba41ac7 is distinct from final manuscript head.
