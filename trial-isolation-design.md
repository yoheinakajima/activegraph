# Subprocess fork-trial isolation: design

**Status: DESIGN FOR REVIEW — no implementation this cycle.**
Proposed as a CONTRACT v1.4/v1.5 item when locked. Answers the
evolution pack's T5 ask (its design §6/§9): `fork()` isolates *graph
state*, never *process state* — candidate code running inside a
stage-3 trial executes in the parent's process, before any approval.

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
    store_url="sqlite:///assistant.db",
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
 "detail": "one line"}
```

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
- **Windows**: rlimits are POSIX; on Windows the memory/CPU nets
  degrade to wall-clock + budgets, stated in the API docs rather
  than emulated badly.

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
