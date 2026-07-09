# Subprocess fork-trial isolation: design

**Status: IMPLEMENTED — shipped in v1.5.0 as CONTRACT v1.5 #1**
(`activegraph.sandbox.run_forked_trial`; tests in
`tests/test_sandbox_trial.py`). Answers the evolution pack's T5 ask
(its design §6/§9): `fork()` isolates *graph state*, never *process
state* — candidate code running inside a stage-3 trial executes in
the parent's process, before any approval.

## 1. What the runtime already gives us

Fork isolation is store-level: a fork is a run-scoped set of rows in
the same SQLite file, with lineage in the `runs` table. Nothing about
a trial requires the trial to share the parent's *process* — a second
process that opens the same store can `Runtime.load` the fork run,
execute, and append to it, and the parent process can read the
results afterward by loading the same run. Process isolation is
therefore orchestration, not new physics.

## 2. Shape

One helper, deliberately small:

```python
from activegraph.sandbox import run_forked_trial

report = run_forked_trial(
    store_path="assistant.db",
    parent_run_id=parent.run_id,
    at_event=fork_point,
    pack_source=PackSource(...),      # §3
    scenario="fixtures/run_fixtures.py::main",
    limits=TrialLimits(
        wall_clock_seconds=120,
        max_rss_bytes=512 * 2**20,
        max_events=2_000,             # enforced in-child via budget
        max_llm_calls=0,              # trials default to recorded/mocked
    ),
)
```

Mechanics, in order:

1. **Parent-side fork first.** The parent process performs the
   `fork(at_event=...)` itself (cheap, transactional, lineage
   recorded) and passes only the fork's `run_id` to the child. The
   child never receives authority to fork or to touch any other run.
2. **Child = fresh interpreter** (`subprocess`, `sys.executable -m
   activegraph.sandbox._child`), NOT `os.fork()` — no inherited
   Python state, no shared LLM clients, no parent memory. The child's
   argv/stdin carries a single JSON job spec: store URL, fork run id,
   pack source, scenario, limits.
3. **Child materializes the pack from artifacts** (§3), loads it into
   the fork runtime, runs the scenario, and writes NOTHING outside
   the store: its trial activity is ordinary events in the fork's
   run, which is already the audit trail the evolution pack wants.
4. **Limits**: `resource.setrlimit` for address space and CPU in the
   child preamble; wall-clock via parent-side `Popen.wait(timeout)`
   then kill; event/LLM budgets via the existing `Budget` on the
   child's Runtime — three independent nets, each of which alone
   bounds a runaway.
5. **Result comes from the store, not the pipe.** The child's exit
   code and a small stdout JSON tail (§4) signal completion shape;
   the parent then `Runtime.load`s the fork run and reads
   `trace.failures()`, event counts, and fixture assertions itself.
   The pipe is a signal, the store is the record — a crashed child
   that already appended events loses nothing.

## 2b. Worked example: recorded-segment replay

THE consumer use case (evolution stage 3): fork a run, replay a
recorded input segment against the candidate inside the child, and
read failures + counts from the fork's run in the store afterward.
The interface as shipped expresses this with no extension — the key
observation is that **the recorded segment IS the fork's history**.
`fork(at_event=...)` copies the parent's log up to the fork point, so
the child's runtime arrives already holding the recorded inputs; the
scenario reads them back from the trace and re-injects them as fresh
events so the candidate's behaviors process them live.

The scenario file, in full:

```python
# scenario.py — shipped inside the candidate's pack directory
from activegraph.core.event import Event


def main(rt):
    # 1. Read the recorded input segment out of the fork's own
    #    history (copied from the parent at fork time).
    segment = [e for e in rt.trace.events() if e.type == "chat.message"]

    # 2. Re-inject each input as a fresh event (fresh id, replay
    #    actor) so the candidate's behaviors fire on it live.
    for recorded in segment:
        rt.graph.emit(
            Event(
                id=rt.graph.ids.event(),
                type="chat.message",
                payload=dict(recorded.payload),
                actor="trial.replay",
                frame_id=None,
                caused_by=None,
                timestamp=rt.graph.clock.now(),
            )
        )

    # 3. Drain: the candidate processes the whole segment in order.
    rt.run_until_idle()
```

The caller's side is the ordinary trial call — nothing
replay-specific appears in the interface:

```python
report = run_forked_trial(
    store_path,
    parent_run_id=parent_run,
    at_event=segment_tip,          # fork point = end of the segment
    pack_source=PackSource(root_dir=..., expected_bundle_hash=...),
    scenario="scenario.py",        # the replay scenario above
)

# Failures and counts come from the report...
assert report.outcome == "completed"
assert report.behavior_failures == 0

# ...and everything richer is ordinary fork history in the store:
fork = Runtime.load(store_path, run_id=report.fork_run_id, behaviors=[])
replies = [o for o in fork.graph.all_objects() if o.type == "reply"]
failures = fork.trace.failures()   # full tracebacks since v1.3
```

Notes that make this correct rather than merely plausible:

- **Re-inject with fresh ids, don't re-fire the recorded events.**
  The recorded events are history — they already happened, and the
  log is append-only. Replay means *new* events with the recorded
  payloads, attributed to a replay actor (`actor="trial.replay"`),
  so the fork's log honestly records both the original segment and
  the replayed processing of it.
- **Filter by type (or actor, or a payload marker) to pick the
  segment.** The fork's history contains everything the parent
  recorded up to `at_event`; the scenario chooses what counts as
  "input". A tighter segment = fork earlier and filter later events.
- **The parent needs no subscriber for the recorded type.** Inputs
  recorded raw (no behavior fired in the parent) replay exactly the
  same way — the candidate under trial supplies the subscriber.
- **Parent untouched, as always.** The replay happens in the fork's
  run in a fresh interpreter; the executable proof is
  `tests/test_sandbox_trial.py::test_recorded_segment_replay_inside_the_trial`.

Two patterns from the first production consumer (the evolution
pack's stage 3), endorsed here so the next consumer copies them:

- **Ship the replay driver inside the authored file set, pinned.**
  `scenario` resolves inside the bundle-hashed pack root, which
  means a chassis-owned driver either breaks the pin or escapes via
  `..`. The consumer's resolution — authors include the driver file
  (their `fixtures/trial_scenario.py`) verbatim in the pack, with a
  gate refusing any proposal whose copy differs byte for byte — is
  **stronger than an out-of-root scenario parameter would be**, and
  is the recommended pattern: the replay types, the held-out
  fraction, the whole evaluation protocol freeze at proposal
  creation under the same bundle hash the owner later approves.
  Nothing about the evaluation can drift between proposal and
  approval. An out-of-root `scenario_source` escape hatch remains
  unbuilt on these grounds.
- **Write trial results as ordinary fork objects; let the parent
  sweep them.** The driver records its verdicts (in-sample /
  held-out / sweep counts) as untyped marker objects (e.g.
  `trial_stage_result`) in the fork; after the child exits, the
  parent reads them from the store, converts them into its own audit
  objects, and *removes them in the fork* — so by addendum 4d
  (fork-tail removals are ordinary events) they vanish from the
  promote delta and the markers never leak into adopted state. "The
  store is the record" needs no side channel, and the delta stays
  clean.

## 2c. Loading scope: candidate-only by default, `extra_packs` to opt in

**The default is candidate-only isolation.** The child loads
NOTHING but the candidate (`Runtime.load(behaviors=[])` +
`load_pack(candidate)`): recorded-segment replay exercises the
candidate apart from every other pack's behaviors, which is the
right null hypothesis for a candidate-only comparator and is why
the worked example above needs no other pack present.

For **cross-pack interaction trials**, `extra_packs` (v1.7,
CONTRACT v1.5 #1 addendum 1b) is the sanctioned opt-in:

```python
report = run_forked_trial(
    ...,
    pack_source=PackSource(root_dir=candidate, expected_bundle_hash=...),
    extra_packs=(
        PackSource(root_dir=trusted_root, expected_bundle_hash=...),
    ),
)
```

Each entry is materialized in the child by the identical chain the
candidate goes through — bundle hash verified BEFORE import,
manifest schema, two-way surface check — and loaded, in order,
before the candidate. There is no trust shortcut: an extra pack
failing any pin is `materialization_failed` for the whole trial.
The scenario still resolves inside the candidate's root only.

## 3. Artifact-materialization contract

The child cannot receive a `Pack` object (it is code; pickling
callables across interpreters is both fragile and exactly the
smuggling vector to avoid). It receives a **pack source**:

```python
PackSource(
    root_dir=...,            # directory containing the candidate
    manifest_required=True,
    expected_bundle_hash="sha256:...",   # the external pin
)
```

The child, before importing anything: verifies the bundle hash
(`activegraph.packs.manifest.verify_bundle_hash` — the manifest
included, per the v1.4 amendment), loads and schema-validates the
manifest, then imports the pack module from `root_dir` and
`verify_surface`s the live `Pack` against it. Only then `load_pack`.
This makes the trial child the first consumer that runs the full
manifest chain end-to-end, and it means the bytes trialed are pinned
to the bytes the proposal recorded — the same pin the owner later
approves.

## 4. Result schema

Child stdout, last line, JSON:

```json
{"outcome": "completed" | "scenario_failed" | "limits_exceeded"
            | "materialization_failed",
 "fork_run_id": "...",
 "events_appended": 137,
 "behavior_failures": 0,
 "detail": "one line",
 "warnings": ["memory cap (RLIMIT_AS) ... net is OFF ..."]}
```

`warnings` (v1.7.1) lists resource nets that degraded on this
platform — the memory cap on macOS, most notably. The parent folds
them into `TrialReport.warnings` and `.detail` and logs them, so a
trial that ran without a net it requested is loud, never silent.

Everything richer — tracebacks, diff summaries, eval numbers — is
read from the fork's log by the parent (`trace.failures()` payloads
carry full tracebacks since v1.3). The schema is deliberately too
small to lie with: any disagreement between the stdout tail and the
store resolves in favor of the store.

## 5. Honest limits (the section that matters)

- **This is crash/state isolation, not a security sandbox.** A fresh
  interpreter with rlimits stops runaway memory, CPU, and parent-state
  corruption — the T1 accident class. It does NOT stop a malicious
  candidate from opening sockets, reading the filesystem, or
  exfiltrating: syscall and network confinement are host territory
  (containers, seccomp, network namespaces — all OS-specific), and
  the runtime will not ship a Python-level imitation of them. The
  evolution pack's stage-2 static gates (import allow-list, banned
  constructs) remain the pre-execution filter, and its own threat
  model already states that a stage-2 bypass is a critical bug — this
  design does not change that calculus, it removes the *parent
  process* from the blast radius.
- **The store is shared.** The child can write to its fork run and —
  absent OS-level controls — could in principle open the SQLite file
  directly and touch other runs. A read-only view for everything but
  the fork's run requires store-level enforcement (per-run
  authorization inside one SQLite file is not something SQLite gives
  us honestly). v1 posture: stated, not solved; hosts wanting hard
  separation copy the fork to a scratch store (`migrate` exists) and
  promote across stores is then the missing piece — deliberately out
  of scope.
- **LLM keys**: trials default to `max_llm_calls=0` with
  recorded/cached providers; a host that passes live keys into the
  child's environment has made a choice the runtime cannot unmake.
  The helper never forwards the parent's environment wholesale
  (explicit allow-list env pass-through, default empty).
- **Environment vs. code location are two separate channels** (v1.7,
  the Replit soak fix). The env allow-list — `PATH`, `HOME`, `LANG`,
  plus explicit `env_passthrough` — is a *security* control and
  stays closed: no ambient parent env (secrets, API keys, platform
  vars like `REPLIT_*`) crosses into a child running hostile code.
  But a child must still be able to import `activegraph` and its
  deps, and on some platforms (Replit, Nix) the package is
  discoverable only via a platform env var the allow-list correctly
  strips — so a naive child dies at import before it can do anything.
  The resolution keeps the boundary closed and makes code discovery
  an *explicit* input instead of an ambient one: the parent resolves
  the `sys.path` entries that make its OWN code importable
  (`activegraph.__file__`'s root first, then its real `sys.path`
  dirs) and passes them to the child as a computed `PYTHONPATH`.
  Code locations are not secrets; a computed `PYTHONPATH` widens
  nothing that the env allow-list protects. The child can therefore
  import `activegraph` on any box where the parent could, without
  the allow-list growing a single entry. `preflight()` spawns a
  null-job child to verify this end to end at boot, so a broken
  sandbox env fails loud instead of surfacing as an opaque `crashed`
  on the first real trial. Relatedly, the child's stderr is PIPED,
  never `DEVNULL`: a crash before the child can write its report
  tail (import failure, rlimit setup) carries its real cause back
  into `TrialReport.detail` — the pre-execution gate must never
  swallow its own failure cause.
- **The memory budget's guarantee is per-platform, stated honestly**
  (v1.7.1, from a macOS soak). `max_rss_bytes` maps to
  `RLIMIT_AS`, and RLIMIT_AS is **enforced on Linux** but **rejected
  by the Darwin (macOS) kernel** — Darwin does not support limiting a
  process's address space, and `setrlimit(RLIMIT_AS, …)` raises
  `ValueError: current limit exceeds maximum limit`. There is no
  honest Darwin substitute: `RLIMIT_RSS`/`RLIMIT_DATA` are accepted
  but not enforced against total address space (a silent non-cap is
  worse than an announced-off one), and a Python-level RSS watchdog
  is exactly the OS-control imitation this design refuses. So on
  macOS the memory net **degrades to OFF, loudly**: the child records
  a warning that surfaces in `TrialReport.warnings` and `.detail` and
  is logged — never a crash, never a silent skip. The wall-clock kill
  and event budget are unaffected and remain the active nets on every
  platform. **Guarantee, stated plainly: memory-budget enforcement is
  Linux-only in v1; on macOS and Windows the memory cap is announced
  unavailable and the runaway-memory accident class is bounded only by
  wall-clock + events.** A BabyAGI author developing on macOS gets a
  memory net that says it is off rather than one that pretends to be
  on. `preflight()` applies a representative limits block so it
  surfaces this at boot (a null-job gate that skipped limit
  application would pass on macOS and then every real trial would
  crash — the false green this fix closes).
- **Windows**: rlimits are POSIX; on Windows the memory/CPU nets
  degrade to wall-clock + budgets (announced the same way as the
  macOS memory net), stated rather than emulated badly.

## 6. Open questions for review

1. Should the helper also run the parent-side `promote(dry_run=True)`
   after a passing trial and include the plan in the report, so the
   caller gets trial + promotability in one call? Position: no —
   promotability is time-sensitive (staleness rule); compute it at
   adoption time, not trial time.
2. Does the evolution pack want the child to enforce the fixtures'
   `deterministic = true` assertion (no sockets) via an audit hook,
   or is that CI's job? Position: CI's job; the child enforces
   resource limits, not honesty claims.
3. Scratch-store trials (copy fork out, trial there, discard):
   worth first-classing if the shared-store caveat above bothers
   consumers — needs cross-store promote, which is its own design.
