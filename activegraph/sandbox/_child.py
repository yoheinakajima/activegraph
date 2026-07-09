"""The trial child: fresh-interpreter half of the sandbox. v1.5 #1.

Reads one JSON job spec from stdin, applies rlimits, materializes the
candidate pack from artifacts (bundle-hash-pinned, manifest-verified),
loads the fork run, runs the scenario under budgets, and prints one
JSON report line to stdout. Everything richer than the report tail is
already in the store — the child's trial activity is ordinary events
in the fork's run.

Exit codes (mirrored in ``activegraph.sandbox._EXIT_TO_OUTCOME``):
0 completed, 30 scenario_failed, 40 limits_exceeded,
50 materialization_failed. Anything else reads as ``crashed``.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import traceback
from pathlib import Path
from typing import Any, Callable, Optional


_LIMIT_WARNINGS: list[str] = []


def _report(
    outcome: str,
    *,
    fork_run_id: str,
    events_appended: int,
    behavior_failures: int,
    detail: str,
    exit_code: int,
) -> None:
    print(
        json.dumps(
            {
                "outcome": outcome,
                "fork_run_id": fork_run_id,
                "events_appended": events_appended,
                "behavior_failures": behavior_failures,
                "detail": detail[:500],
                # Degraded-limit warnings (v1.7.1): a resource net that
                # could not be applied on this platform is announced,
                # never silently skipped. The parent folds these into
                # TrialReport.warnings and TrialReport.detail.
                "warnings": list(_LIMIT_WARNINGS),
            }
        ),
        flush=True,
    )
    raise SystemExit(exit_code)


def _apply_rlimits(limits: dict[str, Any]) -> list[str]:
    """Apply POSIX resource caps portably, returning a warning for any
    cap that could not be set on this platform.

    RLIMIT_AS (the memory net) is settable on Linux but rejected by the
    Darwin kernel (``ValueError: current limit exceeds maximum limit``
    — Darwin does not support address-space limiting). Rather than
    crash (a trial child that can't start is useless) or silently skip
    (a memory net that quietly does nothing is worse than one that
    announces it is off), an unsettable cap DEGRADES: we record a loud
    warning and let the wall-clock kill and event budget — which are
    unaffected — remain the active nets. We never RAISE a hard limit
    (that needs privilege and is what triggered the Darwin crash): the
    target is clamped to the existing hard limit, so the call only ever
    lowers, and the strong soft==hard cap is preserved where the kernel
    allows it. Returns the list of degradation warnings (empty when all
    requested caps applied).
    """
    warnings: list[str] = []
    try:
        import resource
    except ImportError:  # pragma: no cover — Windows
        if limits.get("max_rss_bytes"):
            warnings.append(_mem_off("no `resource` module on this platform"))
        return warnings

    def _cap(kind: str, name: str, requested: int) -> None:
        limit = getattr(resource, name)
        _, hard = resource.getrlimit(limit)
        target = (
            requested
            if hard == resource.RLIM_INFINITY
            else min(requested, hard)
        )
        try:
            resource.setrlimit(limit, (target, target))
        except (ValueError, OSError) as e:
            warnings.append(
                _mem_off(f"{sys.platform}: {type(e).__name__}: {e}")
                if kind == "memory"
                else (
                    f"CPU cap (RLIMIT_CPU) could not be applied on "
                    f"{sys.platform}: {type(e).__name__}: {e}"
                )
            )

    max_rss = limits.get("max_rss_bytes")
    if max_rss:
        _cap("memory", "RLIMIT_AS", max_rss)
    cpu = limits.get("cpu_seconds")
    if cpu:
        _cap("cpu", "RLIMIT_CPU", cpu)
    return warnings


def _mem_off(reason: str) -> str:
    return (
        f"memory cap (RLIMIT_AS) could not be applied ({reason}); the "
        f"memory net is OFF for this trial — the wall-clock kill and "
        f"event budget still bound a runaway"
    )


def _materialize_pack(job: dict[str, Any]) -> Any:
    """Verify pins, then import one pack source and return its Pack.

    ``job`` carries ``pack_root`` / ``expected_bundle_hash`` /
    ``manifest_required`` — the top-level job spec for the candidate,
    or one ``extra_packs`` entry (v1.7 addendum 1b), which uses the
    identical chain. Order is the design's §3: bundle hash BEFORE any
    import (the pin covers manifest.toml per the v1.4 amendment),
    then manifest schema, then import, then the two-way surface check
    against the live Pack.
    """
    from activegraph.packs import Pack
    from activegraph.packs.manifest import (
        load_manifest,
        verify_bundle_hash,
        verify_surface,
    )

    root = Path(job["pack_root"])
    manifest = None
    if job.get("expected_bundle_hash"):
        verify_bundle_hash(job["expected_bundle_hash"], root)
    if job.get("manifest_required", True):
        manifest = load_manifest(root)

    spec = importlib.util.spec_from_file_location(
        manifest.name if manifest else root.name,
        root / "__init__.py",
        submodule_search_locations=[str(root)],
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import pack module at {root}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    packs = [v for v in vars(module).values() if isinstance(v, Pack)]
    if manifest is not None:
        packs = [p for p in packs if p.name == manifest.name]
    if len(packs) != 1:
        raise RuntimeError(
            f"pack module must expose exactly one module-level Pack"
            f"{' named ' + manifest.name if manifest else ''}; "
            f"found {len(packs)}"
        )
    pack = packs[0]
    if manifest is not None:
        verify_surface(manifest, pack)
    return pack


def _resolve_scenario(
    root: Path, scenario: str
) -> Optional[Callable[[Any], None]]:
    if not scenario:
        return None
    path_part, _, func_name = scenario.partition("::")
    func_name = func_name or "main"
    scenario_path = (root / path_part).resolve()
    spec = importlib.util.spec_from_file_location(
        "_trial_scenario", scenario_path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import scenario at {scenario_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    fn = getattr(module, func_name, None)
    if not callable(fn):
        raise RuntimeError(
            f"scenario {scenario!r} has no callable {func_name!r}"
        )
    return fn


def main() -> None:
    job = json.loads(sys.stdin.read())

    # Apply resource limits BEFORE the preflight branch (v1.7.1): the
    # limit-application path is exactly what varies by host (Darwin
    # rejects RLIMIT_AS), so preflight must exercise it to be a real
    # go/no-go gate — a null job that skipped it would pass on a box
    # where every real trial then crashed. Degradation warnings are
    # stashed for _report and echoed on the preflight tail.
    _LIMIT_WARNINGS.extend(_apply_rlimits(job.get("limits", {})))

    # Preflight (v1.7): a null job that proves the child could START —
    # importing activegraph under the sandbox env succeeded (before this
    # function ran) AND resource limits could be applied (just above).
    # Echo the marker plus any degradation warnings and exit clean. No
    # fork, no store, no candidate touched.
    if job.get("preflight"):
        print(
            json.dumps({"preflight": "ok", "warnings": list(_LIMIT_WARNINGS)}),
            flush=True,
        )
        raise SystemExit(0)

    fork_run_id = str(job["fork_run_id"])
    initial = int(job.get("initial_events", 0))

    def counts(rt: Any) -> tuple[int, int]:
        return (
            max(0, len(rt.graph.events) - initial),
            len(rt.trace.failures()),
        )

    try:
        # Extra packs first (addendum 1b): each pinned and verified
        # exactly like the candidate, loaded before it below.
        extra = [_materialize_pack(p) for p in job.get("extra_packs", [])]
        pack = _materialize_pack(job)
    except Exception as e:  # noqa: BLE001 — outcome, not crash
        _report(
            "materialization_failed",
            fork_run_id=fork_run_id,
            events_appended=0,
            behavior_failures=0,
            detail=f"{type(e).__name__}: {e}",
            exit_code=50,
        )

    from activegraph.runtime.runtime import Runtime

    budget: dict[str, Any] = {}
    if job["limits"].get("max_events"):
        budget["max_events"] = job["limits"]["max_events"]
    # max_llm_calls=0 (the default) needs no budget dimension: the
    # child configures NO llm_provider, so key-freedom is structural —
    # an LLM behavior in the candidate fails loud at registration
    # (MissingProviderError), it cannot silently reach a network. A
    # positive cap is recorded for a future provider-wiring seam.
    if job["limits"].get("max_llm_calls"):
        budget["max_llm_calls"] = job["limits"]["max_llm_calls"]

    rt = Runtime.load(
        job["store_path"],
        run_id=fork_run_id,
        behaviors=[],
        budget=budget or None,
    )
    for trusted in extra:
        rt.load_pack(trusted)
    rt.load_pack(pack)

    try:
        scenario_fn = _resolve_scenario(
            Path(job["pack_root"]), job.get("scenario", "")
        )
        if scenario_fn is not None:
            scenario_fn(rt)
        else:
            rt.run_until_idle()
    except MemoryError:
        appended, failures = counts(rt)
        _report(
            "limits_exceeded",
            fork_run_id=fork_run_id,
            events_appended=appended,
            behavior_failures=failures,
            detail="MemoryError under RLIMIT_AS",
            exit_code=40,
        )
    except Exception as e:  # noqa: BLE001 — outcome, not crash
        appended, failures = counts(rt)
        _report(
            "scenario_failed",
            fork_run_id=fork_run_id,
            events_appended=appended,
            behavior_failures=failures,
            detail=f"{type(e).__name__}: {e}\n"
            + traceback.format_exc(limit=3),
            exit_code=30,
        )

    appended, failures = counts(rt)
    exhausted = any(
        e.type == "runtime.budget_exhausted"
        for e in rt.graph.events[initial:]
    )
    if exhausted:
        _report(
            "limits_exceeded",
            fork_run_id=fork_run_id,
            events_appended=appended,
            behavior_failures=failures,
            detail="trial budget exhausted (see runtime.budget_exhausted)",
            exit_code=40,
        )
    _report(
        "completed",
        fork_run_id=fork_run_id,
        events_appended=appended,
        behavior_failures=failures,
        detail="",
        exit_code=0,
    )


if __name__ == "__main__":
    main()
