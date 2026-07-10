# Sandbox (trial isolation)

Subprocess fork-trial isolation (CONTRACT v1.5 #1). `run_forked_trial`
runs candidate pack code against a **fork** of a saved run inside a
**fresh-interpreter child**, materialized from artifacts pinned by a
bundle hash — so the parent process stays out of the blast radius of a
runaway (memory/CPU) or corrupt in-process state, and the bytes trialed
are the bytes a proposal recorded.

For the design, the worked recorded-segment-replay example, the
`extra_packs` cross-pack path, and the honest per-platform limits, see
`trial-isolation-design.md` in the repository root.

CONTRACT v1.8 #9–#12 also exposes this default behind `TrialExecutor`.
Executors accept a versioned JSON `TrialSpecification` and return a
provider-neutral `TrialResult`; the legacy `run_forked_trial` function still
returns `TrialReport` unchanged. This is an adapter boundary, not a new
security claim.

!!! note "Import path"
    The sandbox surface lives under `activegraph.sandbox` and is
    imported from there (`from activegraph.sandbox import
    run_forked_trial, preflight`); it is intentionally not re-exported
    at the top level.

!!! warning "Crash/state isolation, not a security sandbox"
    A fresh interpreter with rlimits stops runaway memory, CPU, and
    parent-state corruption. It does **not** confine syscalls, the
    network, or filesystem access — that is host territory (containers,
    seccomp). The env allow-list is closed (`PATH`/`HOME`/`LANG` plus
    an explicit `env_passthrough`); the child's `PYTHONPATH` is a
    computed code-location channel, not an ambient forward. The memory
    cap (`RLIMIT_AS`) is enforced on Linux and announced-unavailable
    (never crashed) on macOS/Windows, where the wall-clock kill and
    event budget remain the active nets.

## Running a trial

::: activegraph.sandbox.run_forked_trial

## Executor interface

::: activegraph.sandbox.TrialExecutor

::: activegraph.sandbox.TrialSpecification

::: activegraph.sandbox.TrialResult

::: activegraph.sandbox.TrialBudgetUse

::: activegraph.sandbox.TrialArtifactReference

::: activegraph.sandbox.TrialEventLogReference

::: activegraph.sandbox.TrialFailureDetails

::: activegraph.sandbox.TrialIsolationGuarantees

::: activegraph.sandbox.LocalSubprocessTrialExecutor

::: activegraph.sandbox.RecordingTrialExecutor

Reusable adapter tests live at
`activegraph.sandbox.conformance.TrialExecutorConformance`.

## Startup preflight

::: activegraph.sandbox.preflight

::: activegraph.sandbox.SandboxStartupError

## Inputs

::: activegraph.sandbox.PackSource

::: activegraph.sandbox.TrialLimits

## Result

::: activegraph.sandbox.TrialReport

::: activegraph.sandbox.TRIAL_OUTCOMES
