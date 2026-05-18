"""activegraph CLI entry point. CONTRACT v0.8 #12–#13.

Subcommands: inspect, replay, fork, diff, export-trace, migrate.

Each one is a thin wrapper around a library API. The CLI does no
business logic — it parses arguments, calls into Python, and formats
output. Programmatic users get the same behavior by importing the
called functions directly.

Exit codes (CONTRACT v0.8 #13):
  0  success
  1  generic error
  2  usage error (click's default — bad arguments, missing options)
  3  not found (run id does not exist, store path does not exist)
  4  corruption (schema mismatch, event log inconsistency)
  5  divergence (replay-strict failure)
"""

from __future__ import annotations

import json as _json
import sys
from typing import Any, Optional

# Click is a hard dep in v0.8. Lazy import so import-time failures
# carry a clear, actionable message rather than the bare ModuleNotFoundError.
try:
    import click
except ImportError:  # pragma: no cover — exercised only without click
    print(
        "activegraph CLI requires click. Install with `pip install click` "
        "or `pip install activegraph[cli]`.",
        file=sys.stderr,
    )
    raise SystemExit(2)


EXIT_OK = 0
EXIT_GENERIC_ERROR = 1
EXIT_USAGE_ERROR = 2
EXIT_NOT_FOUND = 3
EXIT_CORRUPTION = 4
EXIT_DIVERGENCE = 5

EXIT_CODES = {
    "ok": EXIT_OK,
    "generic_error": EXIT_GENERIC_ERROR,
    "usage_error": EXIT_USAGE_ERROR,
    "not_found": EXIT_NOT_FOUND,
    "corruption": EXIT_CORRUPTION,
    "divergence": EXIT_DIVERGENCE,
}


# ---- shared helpers -----------------------------------------------------


def _open_store_or_die(url: str, run_id: str):
    """Open a store at URL+run_id, mapping common errors to exit codes."""
    from activegraph.store import open_store, InvalidStoreURL

    try:
        return open_store(url, run_id=run_id)
    except InvalidStoreURL as e:
        click.echo(str(e), err=True)
        raise SystemExit(EXIT_USAGE_ERROR)
    except FileNotFoundError as e:
        click.echo(str(e), err=True)
        raise SystemExit(EXIT_NOT_FOUND)
    except RuntimeError as e:
        if "schema_version" in str(e):
            click.echo(str(e), err=True)
            raise SystemExit(EXIT_CORRUPTION)
        click.echo(str(e), err=True)
        raise SystemExit(EXIT_GENERIC_ERROR)


def _most_recent_run_id_or_die(url: str) -> str:
    import sqlite3

    from activegraph.store.url import InvalidStoreURL, parse_store_url

    try:
        parsed = parse_store_url(url)
    except InvalidStoreURL as e:
        click.echo(str(e), err=True)
        raise SystemExit(EXIT_USAGE_ERROR)
    try:
        if parsed.scheme == "sqlite":
            from activegraph.store.sqlite import SQLiteEventStore

            rid = SQLiteEventStore.most_recent_run_id(parsed.sqlite_path or "")
        else:
            from activegraph.store.postgres import PostgresEventStore

            rid = PostgresEventStore.most_recent_run_id(parsed.raw)
    except (sqlite3.OperationalError, FileNotFoundError) as e:
        click.echo(f"{url}: {e}", err=True)
        raise SystemExit(EXIT_NOT_FOUND)
    except RuntimeError as e:
        click.echo(str(e), err=True)
        raise SystemExit(EXIT_CORRUPTION if "schema_version" in str(e) else EXIT_GENERIC_ERROR)
    if rid is None:
        click.echo(f"no runs found in {url}", err=True)
        raise SystemExit(EXIT_NOT_FOUND)
    return rid


def _list_runs_or_die(url: str):
    from activegraph.store.url import InvalidStoreURL, parse_store_url

    try:
        parsed = parse_store_url(url)
    except InvalidStoreURL as e:
        click.echo(str(e), err=True)
        raise SystemExit(EXIT_USAGE_ERROR)
    if parsed.scheme == "sqlite":
        from activegraph.store.sqlite import SQLiteEventStore

        return SQLiteEventStore.list_runs(parsed.sqlite_path or "")
    from activegraph.store.postgres import PostgresEventStore

    return PostgresEventStore.list_runs(parsed.raw)


# ---- click group --------------------------------------------------------


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(message="activegraph %(version)s")
def cli() -> None:
    """Inspect, replay, fork, diff, export, and migrate activegraph runs."""


# ---- quickstart ---------------------------------------------------------
# v1.0-rc1: the onboarding command. Lives in its own module
# (activegraph/cli/quickstart.py) because the implementation is large
# enough to deserve isolation and the command surface here is just
# the registration.

from activegraph.cli.quickstart import cmd_quickstart as _cmd_quickstart

cli.add_command(_cmd_quickstart)


# ---- pack ---------------------------------------------------------------


@cli.group("pack")
def cmd_pack() -> None:
    """Pack-related commands: scaffolding, listing installed packs."""


@cmd_pack.command("new")
@click.argument("name")
@click.option(
    "-o", "--output-dir", default=".", show_default=True,
    help="Parent directory under which the new pack package is created.",
)
def cmd_pack_new(name: str, output_dir: str) -> None:
    """Scaffold a new pack package skeleton (CONTRACT v0.9 #14).

    Generates: pyproject.toml, the Python package with stubs for
    object types, behaviors, tools, settings, an example prompt, a
    smoke test, and a README.
    """
    from pathlib import Path

    from activegraph.packs.scaffold import scaffold_pack

    try:
        root = scaffold_pack(Path(output_dir), name)
    except FileExistsError as e:
        click.echo(str(e), err=True)
        raise SystemExit(EXIT_GENERIC_ERROR)
    except ValueError as e:
        click.echo(str(e), err=True)
        raise SystemExit(EXIT_USAGE_ERROR)
    click.echo(f"created {root}")
    click.echo("next steps:")
    click.echo(f"  cd {root}")
    click.echo("  pip install -e .")
    click.echo("  pytest")


@cmd_pack.command("list")
def cmd_pack_list() -> None:
    """List installed packs discovered via the activegraph.packs entry
    point group (CONTRACT v0.9 #11).
    """
    from activegraph.packs import discover

    entries = discover()
    if not entries:
        click.echo("no packs installed")
        return
    for entry in entries:
        click.echo(f"  {entry.name:24s} {entry.version:10s} {entry.entry_point}")


# ---- inspect ------------------------------------------------------------


@cli.command("inspect")
@click.argument("url")
@click.option("--run-id", default=None, help="Run to inspect (default: most recent).")
@click.option("--tail", default=20, show_default=True, help="Recent events to include.")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable output.")
@click.option(
    "--event",
    "event_id",
    default=None,
    help=(
        "Print one event's full payload by id (e.g. evt_042). Used to "
        "investigate a divergence: every ReplayDivergenceError names "
        "the offending event id."
    ),
)
@click.option(
    "--behaviors",
    is_flag=True,
    help=(
        "Print only the registered-behaviors section. Used when "
        "diagnosing a replay length mismatch — compare which behaviors "
        "fire now against which fired in the recorded run."
    ),
)
@click.option(
    "--pack-version",
    is_flag=True,
    help=(
        "Print every pack.loaded event in the run — name, version, "
        "prompt content-hash summary. Used to confirm the pack version "
        "the recorded run was using vs. what's installed today."
    ),
)
def cmd_inspect(
    url: str,
    run_id: Optional[str],
    tail: int,
    as_json: bool,
    event_id: Optional[str],
    behaviors: bool,
    pack_version: bool,
) -> None:
    """Print a status snapshot for a run.

    With ``--event``, ``--behaviors``, or ``--pack-version``, prints only
    that focused section instead of the full status. The three are
    mutually exclusive — they're selectors, not filters.
    """
    from activegraph.observability.status import status_to_dict
    from activegraph.runtime.runtime import Runtime

    if sum([bool(event_id), behaviors, pack_version]) > 1:
        click.echo(
            "--event, --behaviors, and --pack-version are mutually exclusive.",
            err=True,
        )
        raise SystemExit(EXIT_USAGE_ERROR)

    rid = run_id or _most_recent_run_id_or_die(url)
    try:
        # Load without behaviors. Loading replays the event log but
        # registers an empty registry (no behaviors fire on the
        # re-queued events because the loop is never run).
        rt = Runtime.load(url, run_id=rid)
    except FileNotFoundError as e:
        click.echo(str(e), err=True)
        raise SystemExit(EXIT_NOT_FOUND)

    if event_id:
        _print_event(rt, event_id, as_json)
        return
    if behaviors:
        _print_behaviors(rt, as_json)
        return
    if pack_version:
        _print_pack_versions(rt, as_json)
        return

    status = rt.status(recent=tail)

    if as_json:
        click.echo(_json.dumps(status_to_dict(status), default=str))
        return

    click.echo(f"run_id:           {status.run_id}")
    click.echo(f"state:            {status.state}")
    click.echo(f"queue_depth:      {status.queue_depth}")
    click.echo(f"events_processed: {status.events_processed}")
    if status.frame is not None:
        click.echo(f"frame:            {status.frame.id} ({status.frame.name})")
    bud = status.budget
    click.echo("budget:")
    for k, v in bud.used.items():
        lim = bud.limits.get(k)
        click.echo(f"  {k:20s} {v} / {lim if lim is not None else 'unlimited'}")
    if bud.exhausted_by:
        click.echo(f"  exhausted_by:        {bud.exhausted_by}")
    if status.registered_behaviors:
        click.echo("registered behaviors:")
        for b in status.registered_behaviors:
            sub = ",".join(b.subscribed_to) or "(pattern-only)"
            click.echo(f"  {b.name:30s} {b.kind:10s} on={sub}")
    click.echo(f"recent events (last {len(status.recent_events)}):")
    for e in status.recent_events:
        click.echo(f"  {e.id:18s} {e.type}")


# ---- inspect selector helpers ------------------------------------------


def _print_event(rt, event_id: str, as_json: bool) -> None:
    """v1.0 CLI follow-on: print one event's full payload by id."""
    target = next((e for e in rt.graph.events if e.id == event_id), None)
    if target is None:
        click.echo(f"event {event_id!r} not found in run {rt.run_id}", err=True)
        raise SystemExit(EXIT_NOT_FOUND)
    if as_json:
        click.echo(_json.dumps({
            "id": target.id,
            "type": target.type,
            "actor": target.actor,
            "frame_id": target.frame_id,
            "caused_by": target.caused_by,
            "timestamp": target.timestamp,
            "payload": target.payload,
        }, default=str))
        return
    click.echo(f"event:       {target.id}")
    click.echo(f"type:        {target.type}")
    click.echo(f"actor:       {target.actor or '(none)'}")
    click.echo(f"frame:       {target.frame_id or '(none)'}")
    click.echo(f"caused_by:   {target.caused_by or '(none)'}")
    click.echo(f"timestamp:   {target.timestamp}")
    click.echo("payload:")
    click.echo(_json.dumps(target.payload, indent=2, default=str))


def _print_behaviors(rt, as_json: bool) -> None:
    """v1.0 CLI follow-on: print only the registered-behaviors section."""
    status = rt.status(recent=0)
    if as_json:
        click.echo(_json.dumps([
            {"name": b.name, "kind": b.kind, "subscribed_to": list(b.subscribed_to)}
            for b in status.registered_behaviors
        ]))
        return
    if not status.registered_behaviors:
        click.echo("(no behaviors registered in this run)")
        return
    click.echo("registered behaviors:")
    for b in status.registered_behaviors:
        sub = ",".join(b.subscribed_to) or "(pattern-only)"
        click.echo(f"  {b.name:30s} {b.kind:10s} on={sub}")


def _print_pack_versions(rt, as_json: bool) -> None:
    """v1.0 CLI follow-on: print every pack.loaded event in the run.

    A `pack.loaded` event carries the pack name, version, and the
    declared+content-hash of every prompt the pack ships. v0.9 #13 locked
    this event as the audit trail for which pack version produced which
    artifacts; v0.9 #10 locked the prompt content-hash as the replay
    contract, so the prompt hashes here are what `ReplayDivergenceError`
    compares against during fork/replay.
    """
    loads = [e for e in rt.graph.events if e.type == "pack.loaded"]
    if as_json:
        click.echo(_json.dumps([
            {
                "event_id": e.id,
                "pack": (e.payload or {}).get("name"),
                "version": (e.payload or {}).get("version"),
                "prompts": (e.payload or {}).get("prompts", {}),
            }
            for e in loads
        ]))
        return
    if not loads:
        click.echo("(no packs loaded in this run)")
        return
    click.echo(f"packs loaded ({len(loads)}):")
    for e in loads:
        p = e.payload or {}
        name = p.get("name", "?")
        version = p.get("version", "?")
        prompts = p.get("prompts") or {}
        click.echo(f"  {name:24s} {version:14s}  ({e.id})")
        for prompt_name, meta in prompts.items():
            if isinstance(meta, dict):
                short = str(meta.get("hash", ""))
                prompt_ver = meta.get("version", "")
                short = short.removeprefix("sha256:")[:12] if short else "?"
                click.echo(
                    f"    prompt {prompt_name:24s} v{prompt_ver:<8s} hash={short}"
                )
            else:
                short = str(meta)[:12]
                click.echo(f"    prompt {prompt_name:24s} hash={short}")


# ---- replay -------------------------------------------------------------


@cli.command("replay")
@click.argument("url")
@click.option("--run-id", required=True, help="Run to replay.")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable output.")
def cmd_replay(url: str, run_id: str, as_json: bool) -> None:
    """Rebuild the graph from a run's event log (no behaviors fire)."""
    from activegraph.runtime.runtime import Runtime

    try:
        rt = Runtime.load(url, run_id=run_id)
    except FileNotFoundError as e:
        click.echo(str(e), err=True)
        raise SystemExit(EXIT_NOT_FOUND)
    except __import__("sqlite3").OperationalError as e:
        click.echo(f"{url}: {e}", err=True)
        raise SystemExit(EXIT_NOT_FOUND)

    summary = {
        "run_id": run_id,
        "events": len(rt.graph.events),
        "objects": len(rt.graph.all_objects()),
        "relations": len(rt.graph.all_relations()),
    }
    if as_json:
        click.echo(_json.dumps(summary))
        return
    click.echo(f"run_id:    {summary['run_id']}")
    click.echo(f"events:    {summary['events']}")
    click.echo(f"objects:   {summary['objects']}")
    click.echo(f"relations: {summary['relations']}")


# ---- fork ---------------------------------------------------------------


@cli.command("fork")
@click.argument("url")
@click.option("--run-id", required=True, help="Parent run to fork from.")
@click.option("--at-event", required=True, help="Event id to fork at (inclusive).")
@click.option("--label", default=None, help="Optional label for the new run.")
@click.option(
    "--to",
    "to_url",
    default=None,
    help="Destination store URL. Defaults to the source store.",
)
@click.option(
    "--record",
    is_flag=True,
    help=(
        "Mark this fork as a re-recording. Appends `-recording` to the "
        "label (or sets the label to `recording` if none was given) and "
        "prints follow-on guidance. Use after a ReplayDivergenceError "
        "when the divergence was intentional — fork at the offending "
        "event id, then run the new run without `replay_strict=True` "
        "so the LLM cache and tool cache write-through new entries."
    ),
)
@click.option("--json", "as_json", is_flag=True, help="Machine-readable output.")
def cmd_fork(
    url: str,
    run_id: str,
    at_event: str,
    label: Optional[str],
    to_url: Optional[str],
    record: bool,
    as_json: bool,
) -> None:
    """Create a new run by copying events up to and including --at-event."""
    from activegraph.core.ids import IDGen
    from activegraph.runtime.runtime import _now_iso
    from activegraph.store.url import InvalidStoreURL, parse_store_url

    if to_url is None:
        to_url = url
    if record:
        # Label suffix is informational; the actual "recording" semantics
        # emerge when the new run is later loaded with replay_strict=False
        # (the default). v1.0 #C3-adjacent — no new runtime capability;
        # this is operator UX over the existing fork primitive.
        label = f"{label}-recording" if label else "recording"
    if to_url != url:
        click.echo(
            "cross-store fork not supported in v0.8. Fork in the source "
            "store, then `activegraph migrate` the new run.",
            err=True,
        )
        raise SystemExit(EXIT_USAGE_ERROR)

    try:
        parsed = parse_store_url(url)
    except InvalidStoreURL as e:
        click.echo(str(e), err=True)
        raise SystemExit(EXIT_USAGE_ERROR)

    new_run_id = IDGen().run()
    try:
        if parsed.scheme == "sqlite":
            from activegraph.store.sqlite import SQLiteEventStore

            n = SQLiteEventStore.fork_run(
                parsed.sqlite_path or "",
                parent_run_id=run_id,
                new_run_id=new_run_id,
                at_event_id=at_event,
                label=label,
                created_at=_now_iso(),
            )
        else:
            from activegraph.store.postgres import PostgresEventStore

            n = PostgresEventStore.fork_run(
                parsed.raw,
                parent_run_id=run_id,
                new_run_id=new_run_id,
                at_event_id=at_event,
                label=label,
                created_at=_now_iso(),
            )
    except KeyError as e:
        click.echo(str(e), err=True)
        raise SystemExit(EXIT_NOT_FOUND)

    out = {
        "parent_run_id": run_id,
        "new_run_id": new_run_id,
        "at_event": at_event,
        "label": label,
        "events_copied": n,
    }
    if as_json:
        if record:
            out["recording"] = True
        click.echo(_json.dumps(out))
        return
    click.echo(f"forked {run_id} at {at_event} -> {new_run_id} ({n} events)")
    if record:
        click.echo(
            "  recording fork: load this run without replay_strict=True to "
            "accept new LLM/tool cache entries."
        )


# ---- diff ---------------------------------------------------------------


@cli.command("diff")
@click.argument("url")
@click.option("--run-a", required=True, help="Left-hand run.")
@click.option("--run-b", required=True, help="Right-hand run.")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable output.")
def cmd_diff(url: str, run_a: str, run_b: str, as_json: bool) -> None:
    """Structural diff between two runs in the same store."""
    from activegraph.runtime.diff import compute_diff
    from activegraph.runtime.runtime import Runtime

    try:
        rt_a = Runtime.load(url, run_id=run_a)
        rt_b = Runtime.load(url, run_id=run_b)
    except FileNotFoundError as e:
        click.echo(str(e), err=True)
        raise SystemExit(EXIT_NOT_FOUND)
    except __import__("sqlite3").OperationalError as e:
        click.echo(f"{url}: {e}", err=True)
        raise SystemExit(EXIT_NOT_FOUND)

    diff = compute_diff(rt_a.graph, rt_b.graph, run_a, run_b)
    summary = {
        "run_a": run_a,
        "run_b": run_b,
        "shared_events": len(diff.shared_events),
        "parent_only_events": len(diff.parent_only_events),
        "fork_only_events": len(diff.fork_only_events),
        "divergent_objects": len(diff.divergent_objects),
        "divergent_relations": len(diff.divergent_relations),
    }
    if as_json:
        click.echo(_json.dumps(summary))
        return
    click.echo(f"diff {run_a} vs {run_b}:")
    for k, v in summary.items():
        if k in ("run_a", "run_b"):
            continue
        click.echo(f"  {k:24s} {v}")
    if diff.divergent_objects:
        click.echo("divergent objects:")
        for o in diff.divergent_objects:
            click.echo(f"  - {o.summary()}")
    if diff.divergent_relations:
        click.echo("divergent relations:")
        for r in diff.divergent_relations:
            click.echo(f"  - {r.summary()}")


# ---- export-trace -------------------------------------------------------


@cli.command("export-trace")
@click.argument("url")
@click.option("--run-id", required=True, help="Run to export.")
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["text", "jsonl"]),
    default="text",
    show_default=True,
)
@click.option(
    "-o", "--output",
    "out_path",
    default=None,
    help="Output file (default: stdout).",
)
def cmd_export_trace(url: str, run_id: str, fmt: str, out_path: Optional[str]) -> None:
    """Dump a run's event log as text or JSONL."""
    from activegraph.runtime.runtime import Runtime

    try:
        rt = Runtime.load(url, run_id=run_id)
    except FileNotFoundError as e:
        click.echo(str(e), err=True)
        raise SystemExit(EXIT_NOT_FOUND)
    except __import__("sqlite3").OperationalError as e:
        click.echo(f"{url}: {e}", err=True)
        raise SystemExit(EXIT_NOT_FOUND)

    if fmt == "jsonl":
        lines = (_json.dumps(e.to_dict()) for e in rt.graph.events)
        if out_path:
            with open(out_path, "w") as f:
                for ln in lines:
                    f.write(ln + "\n")
        else:
            for ln in lines:
                click.echo(ln)
        return

    # text format — use the trace printer
    from activegraph.trace.printer import Trace

    trace = Trace(rt.graph)
    if out_path:
        with open(out_path, "w") as f:
            trace.print(file=f) if _supports_file_arg(trace.print) else _fallback_text(trace, f)
    else:
        trace.print()


def _supports_file_arg(fn) -> bool:
    import inspect

    try:
        sig = inspect.signature(fn)
        return "file" in sig.parameters
    except (TypeError, ValueError):
        return False


def _fallback_text(trace, f) -> None:
    """Trace.print writes to stdout; redirect for backward-compat printers."""
    import contextlib

    with contextlib.redirect_stdout(f):
        trace.print()


# ---- migrate ------------------------------------------------------------


@cli.command("migrate")
@click.option("--from", "src", required=True, help="Source store URL.")
@click.option("--to", "dst", required=True, help="Destination store URL.")
@click.option(
    "--run-id",
    multiple=True,
    help="Migrate only these run(s). Repeat to specify multiple.",
)
@click.option(
    "--skip-corrupted",
    is_flag=True,
    help=(
        "Skip events whose payload fails JSON decode instead of failing "
        "the run. The skipped event ids appear in the per-run report's "
        "`skipped_events`. The resulting destination run is PARTIAL — the "
        "operator is on notice. Use this to recover the readable subset "
        "of a run with a corrupted event payload."
    ),
)
@click.option("--json", "as_json", is_flag=True, help="Machine-readable output.")
def cmd_migrate(
    src: str,
    dst: str,
    run_id: tuple[str, ...],
    skip_corrupted: bool,
    as_json: bool,
) -> None:
    """Copy runs from a source store to a destination store.

    Transaction-per-run: each run is written in a single destination
    transaction. A failure mid-run rolls back that run's destination
    state. Writes are idempotent (ON CONFLICT DO NOTHING) so re-running
    after a failure is safe.

    With ``--skip-corrupted``, a corrupted-payload event is skipped
    (recorded in the per-run ``skipped_events``) instead of failing
    the whole run. The destination run is partial; the operator is
    on notice.
    """
    from activegraph.observability.migration import migrate
    from activegraph.store.url import InvalidStoreURL, parse_store_url

    try:
        parse_store_url(src)
        parse_store_url(dst)
    except InvalidStoreURL as e:
        click.echo(str(e), err=True)
        raise SystemExit(EXIT_USAGE_ERROR)

    only = list(run_id) if run_id else None
    report = migrate(src, dst, only_run_ids=only, skip_corrupted=skip_corrupted)

    if as_json:
        out = {
            "source_url": report.source_url,
            "dest_url": report.dest_url,
            "runs": [
                {
                    "run_id": r.run_id,
                    "status": r.status,
                    "events_migrated": r.events_migrated,
                    **({"error": r.error} if r.error else {}),
                    **(
                        {"skipped_events": list(r.skipped_events)}
                        if r.skipped_events
                        else {}
                    ),
                }
                for r in report.runs
            ],
        }
        click.echo(_json.dumps(out))
    else:
        click.echo(f"migrate {src} -> {dst}")
        for r in report.runs:
            line = f"  {r.status:7s} run={r.run_id} events={r.events_migrated}"
            if r.error:
                line += f" error={r.error}"
            if r.skipped_events:
                line += f" skipped={len(r.skipped_events)}"
            click.echo(line)
            if r.skipped_events:
                for sid in r.skipped_events:
                    click.echo(f"    skipped (corrupted): {sid}")
        click.echo(
            f"summary: {sum(1 for r in report.runs if r.status == 'ok')} ok, "
            f"{len(report.failures)} failed"
        )

    if not report.ok:
        raise SystemExit(EXIT_GENERIC_ERROR)


# ---- entrypoint ---------------------------------------------------------


def main(argv: Optional[list[str]] = None) -> int:
    """Programmatic entry point. Returns an exit code rather than raising
    SystemExit when called from tests via CliRunner.

    The ``[project.scripts]`` shim invokes this; pyproject hooks up
    ``activegraph -> activegraph.cli.main:main``.
    """
    try:
        cli.main(args=argv, standalone_mode=False)
    except SystemExit as e:
        return int(e.code or 0)
    except click.exceptions.UsageError as e:
        e.show()
        return EXIT_USAGE_ERROR
    except click.exceptions.ClickException as e:
        e.show()
        return EXIT_GENERIC_ERROR
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
