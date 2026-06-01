# Tau2 Regimes Port Milestone Report

## 1. Purpose and scope

This report is an implementation handoff for porting the tau2-bench exploration in this repository into a tau2 target in the separate `regimes` repository. It is intentionally technical and mechanism-oriented.

Scope constraints:

- This is target-port and mechanism-validation evidence, not an empirical-improvement claim.
- No ActiveGraph control, intervention, blocking, repair, rollback, or tau2 task improvement has been shown yet.
- The existing evidence supports designing a `regimes` target, outcome schema, failure taxonomy, tool-ownership taxonomy, and artifact ingestion path.
- The report is documentation-only. It does not require running tau2, model-backed episodes, LLM/API services, or API keys.
- `vendor/tau2-bench` must remain unchanged for this milestone.

## 2. Relation to Regimes

### Mapping tau2 to Regimes concepts

| Regimes concept | Tau2 mapping | Implementation notes |
| --- | --- | --- |
| `Target` | `Tau2Target` | A target wrapper that can initially operate in artifact-only mode over existing `runs/**` outputs, with optional paid-run execution deferred until guardrails and report generation exist. |
| `EvalBackend` | Tau2 runner/artifact backend | First backend should be deterministic artifact ingestion. A later backend can invoke tau2 only after stop conditions are met. |
| `Outcome` | `Tau2Outcome` | A normalized per-task/per-trial record containing reward, DB score, communicate score, action/env/NL subscores, termination reason, tool timeline, expected/matched reads/writes, observer artifacts, and source paths. |
| `RegimeTaxonomy` | Deterministic tau2 failure-regime classifier | Classifies outcomes into success, no-write, partial-progress, max-steps, argument-mismatch, missing-read, post-write-mismatch, observer-gap, and insufficient-evidence regimes. |
| `ActionSpace` | Tau2 tool calls and write intents | The action space should preserve read tools, assistant business writes, user/environment writes, evaluator replay writes, and observer-supported/unsupported writes as typed actions. |
| Held-out/gated discipline | Tau2 paid/model-backed runs | Full tau2 and model-backed episodes should remain gated until artifact-only parsing, deterministic classification, guardrails, and false-positive measurement exist. |

### Relation to LongMemEval `assemble-internal`

The LongMemEval `assemble-internal` failure pattern is primarily a reconciliation failure: evidence fragments exist, but the system assembles, resolves, or reconciles internal state incorrectly before producing an answer. Tau2 exposes a related but structurally different class of failures: write-intent/tool-argument grounding. In tau2, the agent may read relevant state, form an apparent user-facing intent, and still fail to execute the required write, execute it with incomplete arguments, choose the wrong payment/state transition, omit prerequisite evidence, or communicate a result that diverges from the database.

Tau2 is therefore a structurally different `regimes` target:

- multi-turn rather than single-turn or document-only;
- policy-constrained rather than free-form answer-only;
- tool-using rather than pure generation;
- stateful, with DB mutations and evaluator replay semantics;
- action-scored, with read/write action expectations in addition to reward and communication quality.

## 3. Artifact inventory

### Core tau2 exploration artifacts

| Artifact | Domain/task | Outcome | Runtime events | Observer/artifact counts | Analysis directories / notes |
| --- | --- | --- | ---: | --- | --- |
| `runs/20260531-184109-726391` | `mock`, 10 tasks | pass rate `0.800`; average reward `0.8000` | 446 | no observer counts reported | Mock full traced baseline; useful for validating artifact ingestion and aggregate reporting. |
| `runs/20260531-193831-340466` | `airline`, task `0` | reward `1.0`; DB check `1.0` | 70 | no observer counts reported | Read-only success fixture; useful for success classification and no-write false-positive checks. |
| `runs/20260531-204930-608103` | `airline`, task `8` | reward `0.0`; DB check `0.0`; read actions `3/3`; write actions `0/1` | not recorded in prompt context | no observer counts reported | Baseline failure. Expected Sophia + Kevin on HAT271 for `$348`; observed Sophia only for `$174`. Analysis: `airline_task8_failure_analysis/`. |
| `runs/20260531-222346-104165` | `airline`, task `8`, prompt variant | reward `0.0`; DB check `0.0`; read actions `2/3`; write actions `0/1` | not recorded in prompt context | no observer counts reported | Preserved Sophia + Kevin but wrong payment and missing/failed search evidence. Comparison: `prompt_variant_comparison/`; write-intent analysis: `write_intent_analysis/`. |
| `runs/write_intent_gap_scan_20260531-230310/` | multi-case scan | 8 cases scanned; 7/8 detectable offline today; 3/8 require runtime observation; 2/8 require future ActiveGraph control | n/a | n/a | Gap scan indicates generalization beyond airline task 8 is partial but meaningful. |
| Latest `run_write_intent_observer_smoke.py` artifacts | fixture smoke | observer smoke passed | n/a | fixture cases only | Fixtures include airline task 8 baseline, prompt variant, successful `create_task_1`, no-write failure, and DB/scoring ambiguity. |
| `runs/20260601-033142-413096` | `airline`, task `8`, paid observer-enabled | task 8 failure context; expected task ledger missing at runtime | not recorded in prompt context | `observer_events=39`; `constraint_ledger_snapshots=13`; `write_intent_diffs=13` | Live observer emits `runtime_incomplete_ledger` and not-evaluable diffs when task-aware expected constraints are unavailable at runtime. |
| `runs/20260601-033543-612139` | `airline`, 5 tasks | average reward `0.200`; pass `0.200`; read actions `5/7`; DB match `2/4`; normal stop `4`; max steps `1` | 526 | observer files all had `0` lines | Emission gap analysis found likely write candidates `cancel_reservation` and `send_certificate`; likely hook coverage gap rather than absence of writes. |

### Cross-domain mini-tests

| Artifact | Domain/task | Outcome | Tool evidence | Observer result | Regimes use |
| --- | --- | --- | --- | --- | --- |
| `runs/20260601-041116-120115` | `airline`, task `1` | reward `0.0`; DB match `0.0`; read actions `2/2`; normal stop | tools included `get_user_details`, `get_reservation_details`, `cancel_reservation` | observer files had `0` rows | Cancellation/write coverage-gap fixture; demonstrates airline write coverage gap beyond task 8. |
| `runs/20260601-041151-558787` | `retail`, task `0` | reward `1.0`; DB match `1.0`; read actions `4/4`; write actions `1/1` | write tool `exchange_delivered_order_items` | observer files had `0` rows | Successful non-airline assistant/business write fixture; useful for false-positive and unsupported-write classification. |
| `runs/20260601-041221-091109` | `telecom`, 1 task | reward `1.0`; DB match `1.0`; write actions `2/2` | user-side write tools `toggle_airplane_mode`, `toggle_roaming` | observer files had `0` rows | Successful user/environment write fixture; proves ownership typing must distinguish assistant business writes from user/environment writes. |

Cross-domain interpretation:

- Observer coverage gaps generalize beyond airline task 8.
- Retail exposes successful assistant/business write flows that are not currently observed.
- Telecom exposes successful user/environment write flows that are not currently observed.
- Tau2 write ownership should be typed rather than inferred from the presence of any DB mutation.

## 4. Proposed `Tau2Outcome` schema

A first `regimes` implementation should use a strict, serializable outcome model. Suggested fields:

```python
@dataclass(frozen=True)
class Tau2Outcome:
    # Identity
    run_id: str
    domain: str
    task_id: int | None
    trial_id: str | None
    artifact_root: str

    # Scores
    reward: float | None
    db_check: float | None
    communicate_score: float | None
    action_score: float | None
    env_score: float | None
    nl_assertion_score: float | None

    # Termination
    termination_reason: str | None          # normal_stop, max_steps, error, unknown
    max_steps_reached: bool | None

    # Expected/matched actions
    expected_read_actions: int | None
    matched_read_actions: int | None
    expected_write_actions: int | None
    matched_write_actions: int | None

    # Tool timeline
    tool_timeline: tuple[Tau2ToolCall, ...]
    read_tools_seen: tuple[str, ...]
    write_tools_seen: tuple[str, ...]

    # Write analysis
    write_candidates: tuple[Tau2WriteCandidate, ...]
    write_ownership_types: tuple[str, ...]  # e.g. assistant_business_write
    write_argument_mismatches: tuple[Tau2WriteArgumentMismatch, ...]

    # Observer artifacts
    observer_event_count: int | None
    constraint_ledger_snapshot_count: int | None
    write_intent_diff_count: int | None
    observer_warning_count: int | None
    observer_warnings: tuple[str, ...]
    observer_artifact_rows_by_file: Mapping[str, int]

    # Evidence and source paths
    source_artifact_paths: tuple[str, ...]
    analysis_directories: tuple[str, ...]
    parse_warnings: tuple[str, ...]
```

Nested records should be deterministic and JSON-serializable:

- `Tau2ToolCall`: turn index, actor, tool name, arguments, result summary, success/error status, raw artifact pointer.
- `Tau2WriteCandidate`: tool name, ownership type, source heuristic, prerequisite reads, expected arguments, observed arguments, expected post-state, observed post-state, confidence.
- `Tau2WriteArgumentMismatch`: field path, expected value, observed value, source evidence, mismatch severity.

## 5. Proposed tau2 failure-regime taxonomy

A deterministic taxonomy should classify each `Tau2Outcome` into one primary regime and zero or more secondary tags.

| Regime | Definition | Current examples / notes |
| --- | --- | --- |
| `success` | Reward/DB/action checks indicate task success. | Mock successes, airline task 0, retail task 0, telecom mini-test. |
| `failed_no_write` | Expected write actions exist, but no matched write occurs. | Airline task 8 baseline and prompt variant. |
| `failed_partial_progress` | Some reads or state transitions match, but required completion criteria fail. | Airline task 8 variants with partial passenger/search/payment progress. |
| `failed_max_steps` | Episode terminates due to max steps. | One task in airline 5-task observer-enabled batch. |
| `write_argument_mismatch` | A write occurs or is attempted with incomplete/wrong arguments, such as missing passenger or wrong payment amount. | Prompt variant preserved passenger set but payment/search evidence remained wrong or missing. |
| `missing_prerequisite_read` | A required read/search action is absent before a write or final answer. | Prompt variant read actions `2/3`; failed or missing search evidence. |
| `post_write_state_mismatch` | A write is present but final DB state differs from expected evaluator state. | Use when DB check fails despite apparent write progress. |
| `communication_correct_db_wrong` | User-facing response appears correct while DB check fails. | Needed to separate communication success from state mutation success. |
| `scoring_evaluation_ambiguity` | Artifacts are insufficient to determine whether failure is model behavior, evaluator replay, or score interpretation. | Included in observer smoke fixture set. |
| `observer_emission_gap` | Expected observer artifacts are absent or empty despite likely write-relevant activity. | Airline 5-task batch and cross-domain mini-tests with 0-row observer files. |
| `observer_emission_gap_by_tool_coverage` | Observer gap is attributable to missing hook coverage for specific tools. | `cancel_reservation`, `send_certificate`, `exchange_delivered_order_items`, `toggle_airplane_mode`, `toggle_roaming`. |
| `observer_unsupported_write_tool` | Tool is classified as a write but unsupported by current observer hook/registry. | Retail and telecom successful writes with 0 observer rows. |
| `insufficient_evidence` | Artifacts do not support a confident classification. | Use as a conservative fallback instead of overfitting heuristics. |

Classifier precedence should prefer explicit success first, then terminal failures (`failed_max_steps`), then action-count failures, then argument/post-state mismatches, then observer-only gaps. Secondary tags should retain observer and evidence warnings even when the primary regime is `success`.

## 6. Proposed write ownership / tool taxonomy

| Tool class | Definition | Current examples |
| --- | --- | --- |
| `assistant_business_write` | Assistant-initiated mutation of business/application state. | `book_reservation`, `cancel_reservation`, `send_certificate`, `exchange_delivered_order_items`. |
| `user_environment_write` | User-side or environment-side mutation that may be required for task success but is not an assistant business write. | `toggle_airplane_mode`, `toggle_roaming`. |
| `evaluator_replay_write` | Mutation performed by evaluator replay or scoring mechanics rather than the assistant policy. | Needed to avoid attributing evaluator-side state transitions to assistant behavior. |
| `observer_supported_write` | Write tool currently covered by observer hooks in at least one path. | `book_reservation` is currently observer-supported in some paths. |
| `observer_unsupported_write` | Write tool known or suspected to mutate state but currently missed by observer hooks. | `cancel_reservation`, `send_certificate`, `exchange_delivered_order_items`, `toggle_airplane_mode`, `toggle_roaming`. |
| `read_only_tool` | Tool used only to inspect state/evidence. | `get_user_details`, `get_reservation_details`, `get_product_details`. |
| `ambiguous_tool` | Tool whose read/write/evaluator ownership cannot be resolved from current artifacts or naming alone. | Conservative fallback for new tau2 tools until registry review. |

Initial registry entries should include:

| Tool | Ownership | Observer status | Notes |
| --- | --- | --- | --- |
| `book_reservation` | `assistant_business_write` | `observer_supported_write` in some paths | Primary airline booking write observed by task 8 experiments. |
| `cancel_reservation` | `assistant_business_write` | `observer_unsupported_write` currently observer-missed | Airline mini-test and 5-task batch indicate hook coverage gap. |
| `send_certificate` | `assistant_business_write` or system/customer-facing write | `observer_unsupported_write` currently observer-missed | Needs exact ownership review because it may be customer-facing/system side effect. |
| `exchange_delivered_order_items` | `assistant_business_write` | `observer_unsupported_write` currently observer-missed | Retail success fixture; critical false-positive case. |
| `toggle_airplane_mode` | `user_environment_write` | `observer_unsupported_write` currently observer-missed | Telecom success fixture; should not be conflated with assistant business writes. |
| `toggle_roaming` | `user_environment_write` | `observer_unsupported_write` currently observer-missed | Telecom success fixture; same ownership concerns as airplane-mode toggle. |
| `get_user_details` | `read_only_tool` | n/a | Read evidence. |
| `get_reservation_details` | `read_only_tool` | n/a | Read evidence. |
| `get_product_details` | `read_only_tool` | n/a | Read evidence. |

## 7. Proposed action seams

Ordered from lowest to highest risk:

1. **Artifact-only analysis**: parse existing run directories into `Tau2Outcome` records; generate deterministic histograms and reports without running tau2.
2. **Passive observer**: ingest observer artifact files and warnings; never change tool calls, prompts, or environment state.
3. **Write-tool registry / ownership registry**: classify tools by ownership and observer coverage; identify unsupported write tools as reportable gaps.
4. **Task-aware ledger source**: load expected task constraints into a ledger abstraction outside `activegraph-tau2-bench`, preferably inside `regimes`, so runtime ledgers can become evaluable without modifying vendor code first.
5. **Pre-write warning seam**: emit warnings before risky writes when task-aware constraints and prerequisite reads are available; no blocking or rewriting.
6. **Future gated control seam**: only after metrics and guardrails are proven, consider gated interventions such as blocking, rewriting, repair, or rollback.

Not ready: blocking, rewriting, repair, and rollback. Current artifacts validate detection and coverage questions, not safe control.

## 8. Current known implementation gaps

- Observer hook coverage misses airline writes beyond `book_reservation`, including `cancel_reservation` and `send_certificate`.
- Observer hook coverage misses the retail write `exchange_delivered_order_items`.
- Observer hook coverage misses telecom user/environment writes `toggle_airplane_mode` and `toggle_roaming`.
- Live ledgers are `runtime_incomplete_ledger` records without task-aware expected constraints.
- Batch observer emission needs a broader write-tool registry before 0-row observer artifacts can be interpreted correctly.
- False-positive behavior is not yet measured across enough successful write tasks.
- Tool ownership typing is missing, especially the distinction between assistant business writes, user/environment writes, and evaluator replay writes.

## 9. Proposed regimes implementation plan

Suggested package structure:

```text
src/regimes/targets/tau2/
  __init__.py
  outcome.py
  artifacts.py
  taxonomy.py
  tool_registry.py
  ledger.py
  observer.py
  reports.py
  target.py

tests/targets/tau2/
```

Suggested responsibilities:

| Module | Responsibility |
| --- | --- |
| `outcome.py` | `Tau2Outcome` and nested typed records. |
| `artifacts.py` | Deterministic parsers for existing `runs/**` directories and observer files. |
| `taxonomy.py` | Primary/secondary failure-regime classifier and histogram helpers. |
| `tool_registry.py` | Ownership and observer-support registry for tau2 tools. |
| `ledger.py` | Task-aware expected-constraint model and runtime ledger reconciliation interfaces. |
| `observer.py` | Passive observer artifact ingestion and warning normalization. |
| `reports.py` | Markdown/JSON report writers for batch summaries. |
| `target.py` | `Tau2Target` implementation with artifact-only mode first and paid-run backend disabled by default. |

Suggested PR ladder:

1. Artifact-only target skeleton.
2. Deterministic taxonomy.
3. Tool ownership registry.
4. Report writer.
5. Observer artifact ingestion.
6. Task-aware ledger support.
7. Optional paid-run bridge later.

## 10. Stop conditions before full tau2

Full tau2 should not run until all of the following are true:

- `Tau2Target` artifact mode exists in `regimes`.
- Current artifacts parse into `Tau2Outcome` records.
- Deterministic regime histogram works.
- Tool ownership registry exists.
- Observer coverage is broadened for known missed write tools.
- A small batch report exists.
- False-positive behavior is measured on successful writes.
- Metrics and guardrails are declared.

Declared metrics:

Primary:

- reward;
- DB check.

Secondary:

- write action correctness;
- read action correctness;
- communicate score;
- normal stop / max steps.

Guardrails:

- no unnecessary write blocking;
- no increased max steps;
- no communication regression;
- no read-action regression;
- observer warning false-positive rate on successes.

## 11. Recommended next implementation step

Recommended next PR in `regimes`:

- Add an artifact-only tau2 target skeleton to `regimes`.
- Parse the current artifact set into `Tau2Outcome` records.
- Implement deterministic taxonomy and a small markdown/JSON report writer.
- Add the first write-tool ownership registry with the known airline, retail, and telecom tools above.

Do not run more paid tau2 until the target/report abstraction exists, unless collecting a very specific missing fixture. Do not add task-aware ledger loading inside `activegraph-tau2-bench` first; move that abstraction into `regimes`.
