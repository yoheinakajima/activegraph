"""``activegraph quickstart`` — the v1.0 onboarding command.

Two modes:

- ``activegraph quickstart`` — fixture-backed diligence demo. No API
  key, no network. Always-deterministic (FrozenClock + fixed run id +
  seeded behaviors) so the output is byte-identical across machines.
- ``activegraph quickstart --interactive`` — guided REPL that walks
  the developer through writing their first behavior.

The transcript at ``examples/quickstart_session.txt`` is the contract
for both modes. Every behavior of the command matches a line in the
transcript. The trace lines in the output come from the canonical
:class:`activegraph.trace.printer.Trace` — the quickstart command
does not reformat trace output. The prose framing ("what just
happened", "try next") is quickstart-specific.

CONTRACT v1.0 #1 (the spec), #C3 (no ``--live`` mode), #4d (every
``More:`` link in error messages resolves; same discipline applies
to the "try next" footer's doc links).
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Optional, TextIO

import click


# Always-deterministic demo setup. The quickstart command is a demo;
# its output should be the same on every machine. Real-run behavior
# is what examples/diligence_real_run.py already provides for
# developers who graduate past quickstart.
#
# The frozen timestamp is deliberately stylized (2026-01-01) so it
# reads as obviously synthetic rather than "this demo is from a year
# ago" — relevant when a developer runs quickstart in 2027 or later.
_QUICKSTART_FROZEN_TIMESTAMP = "2026-01-01T00:00:00Z"
_QUICKSTART_RUN_ID = "quickstart_demo_run"
_QUICKSTART_DB_DIR = "/tmp/activegraph_quickstart"
_QUICKSTART_DB_PATH = f"{_QUICKSTART_DB_DIR}/{_QUICKSTART_RUN_ID}.db"

# Interactive mode's behavior-file location: cwd-subdir for
# discoverability (per CONTRACT v1.0 #1 prompt review — better than
# tempdir because the developer wants to refer back to what they
# just wrote).
_INTERACTIVE_SUBDIR = "activegraph_quickstart"
_INTERACTIVE_BEHAVIOR_FILENAME = "my_first_behavior.py"


# ---------- fixture-backed mode ------------------------------------------


def run_fixture_mode(stream: Optional[TextIO] = None) -> int:
    """Run the fixture-backed diligence demo.

    All output is written to ``stream`` (default stdout). Returns 0.
    Always-deterministic; safe to snapshot-test.
    """
    out = stream if stream is not None else sys.stdout
    write = lambda s: out.write(s + "\n")

    from activegraph import Graph, IDGen, FrozenClock, Runtime, configure_logging
    from activegraph.packs.diligence import (
        DiligenceSettings,
        pack as diligence_pack,
    )
    from activegraph.packs.diligence.fixtures import (
        RecordedDiligenceProvider,
        THREE_COMPANIES,
        company_goal,
    )

    # Header — matches transcript BEAT 2.
    write("activegraph quickstart — running the bundled Diligence pack on fixtures.")
    write("")
    write("This will take about 20 seconds. No API key required.")
    write(f"  pack:       {diligence_pack.name} v{diligence_pack.version}  (no external network calls)")
    write(f"  companies:  {', '.join(c['name'] for c in THREE_COMPANIES)}")
    write("  provider:   RecordedDiligenceProvider (fixture-backed)")
    write("")

    # Suppress structured logging so the only output is the quickstart's.
    configure_logging(level="ERROR", json_output=False)

    # Fresh DB on every run. The fixed run_id means re-running quickstart
    # overwrites the previous demo — correct for a demo; we don't want
    # quickstart leaving N database files in /tmp.
    Path(_QUICKSTART_DB_DIR).mkdir(parents=True, exist_ok=True)
    if os.path.exists(_QUICKSTART_DB_PATH):
        os.remove(_QUICKSTART_DB_PATH)

    provider = RecordedDiligenceProvider(companies=THREE_COMPANIES)
    graph = Graph(
        ids=IDGen(),
        clock=FrozenClock(_QUICKSTART_FROZEN_TIMESTAMP),
        run_id=_QUICKSTART_RUN_ID,
    )
    rt = Runtime(
        graph,
        llm_provider=provider,
        persist_to=_QUICKSTART_DB_PATH,
        budget={
            "max_llm_calls": 80,
            "max_tool_calls": 100,
            "max_cost_usd": "5.00",
        },
        seed=0,
    )
    rt.load_pack(
        diligence_pack,
        settings=DiligenceSettings(
            llm_model="claude-sonnet-4-5",
            max_documents_per_company=5,
            max_claims_per_document=10,
            confidence_threshold_for_review=0.7,
            min_questions=8,
            max_questions=12,
        ),
    )

    for company in THREE_COMPANIES:
        rt.run_goal(company_goal(company))

    rt.save_state()

    # Trace — canonical printer output. The quickstart does not reformat;
    # any drift between the trace shown here and the trace documented on
    # the doc site is a bug in this code path, not the printer.
    for line in rt.trace.lines():
        write(line)
    write("")

    # Pull the memos. Print the first in full, mention the others.
    memos = [o for o in rt.graph.all_objects() if o.type == "memo"]
    if memos:
        _print_memo_section(write, rt, memos[0])
        if len(memos) > 1:
            others = [_company_name_for_memo(rt, m) for m in memos[1:]]
            write(
                f"Memos for {', '.join(others)} were produced under the same "
                f"contract. Output written to sqlite:///{_QUICKSTART_DB_PATH}."
            )
            write("")

    _print_what_just_happened(write)
    _print_try_next(write)
    return 0


def _company_name_for_memo(rt, memo) -> str:
    """Resolve a memo's company_id to a human name for the prose."""
    cid = (memo.data or {}).get("company_id")
    if cid is None:
        return "<unknown>"
    co = rt.graph.get_object(cid)
    if co is None:
        return cid
    return (co.data or {}).get("name") or cid


def _print_memo_section(write, rt, memo) -> None:
    write("-" * 76)
    company = _company_name_for_memo(rt, memo)
    write(f" Memo: {company}")
    write("-" * 76)
    write("")
    data = memo.data or {}

    summary = data.get("summary", "")
    if summary:
        write("Summary:")
        for line in _wrap_indented(summary, indent="  ", width=74):
            write(line)
        write("")

    claims = data.get("key_claims") or []
    if claims:
        write("Key claims:")
        for c in claims:
            text = c.get("text") or c.get("claim", "")
            evidence_ids = c.get("evidence_ids") or []
            ev_clause = (
                f" (evidence: {', '.join(evidence_ids)})" if evidence_ids else ""
            )
            write(f"  - {text}{ev_clause}")
        write("")

    contradictions = data.get("open_contradictions") or []
    note = data.get("contradictions_note")
    write("Open contradictions:")
    if contradictions:
        for c in contradictions:
            write(f"  - {c}")
    elif note:
        write(f"  ({note})")
    else:
        write("  (none surfaced for this company)")
    write("")

    risks = data.get("risks") or []
    write("Risks:")
    if risks:
        for r in risks:
            title = r.get("title", "")
            severity = r.get("severity", "")
            related = r.get("related_claim_ids") or []
            sev_clause = f"; severity: {severity}" if severity else ""
            rel_clause = (
                f" (related claims: {', '.join(related)}{sev_clause})"
                if related
                else (f" ({sev_clause.lstrip('; ')})" if severity else "")
            )
            write(f"  - {title}{rel_clause}")
    else:
        write("  (none identified)")
    write("")


def _wrap_indented(text: str, *, indent: str, width: int) -> list[str]:
    """Wrap prose to a width with a leading indent. Standalone (no
    textwrap import) to keep output stable across Python versions."""
    out: list[str] = []
    current = indent
    for word in text.split():
        if len(current) + len(word) + 1 > width and current.strip():
            out.append(current.rstrip())
            current = indent + word
        else:
            current = (current + " " + word) if current.strip() else (indent + word)
    if current.strip():
        out.append(current.rstrip())
    return out


def _print_what_just_happened(write) -> None:
    write("-" * 76)
    write(" What just happened")
    write("-" * 76)
    write("")
    write("  1. You loaded a pack. A pack is a Python package that registers")
    write("     object types, behaviors, tools, and prompts. The Diligence pack")
    write("     ships with activegraph.")
    write("")
    write("  2. The runtime received three goals (one per company) and reactive")
    write("     behaviors fired automatically as objects appeared on the graph.")
    write("     You did not write a workflow. The behaviors are pattern-matched")
    write("     against event types and object shapes.")
    write("")
    write("  3. Every LLM call was cached against a recorded fixture, so the run")
    write("     is deterministic and runs offline. The trace above shows")
    write("     `cache_hit=true` on every llm.responded — production runs would")
    write("     hit a real provider instead.")
    write("")
    write("  4. Each memo cites evidence for every claim, surfaces at least one")
    write("     risk, and either lists open contradictions or states explicitly")
    write("     that none were found. This is the pack's \"verifiable memo bar\" —")
    write("     a memo without these properties is a bug in the pack.")
    write("")
    write("  5. Every event is on disk. The full causal chain from goal to memo")
    write("     is reconstructable from the event log alone.")
    write("")


def _print_try_next(write) -> None:
    from activegraph.errors import DOCS_BASE_URL
    write("-" * 76)
    write(" Try next")
    write("-" * 76)
    write("")
    write(f"  See your run:        activegraph inspect sqlite:///{_QUICKSTART_DB_PATH}")
    write("  Write a behavior:    activegraph quickstart --interactive")
    write(f"  Read the tutorial:   {DOCS_BASE_URL}/quickstart")
    write(f"  Concept: the graph:  {DOCS_BASE_URL}/concepts/graph")
    write(f"  Concept: behaviors:  {DOCS_BASE_URL}/concepts/behaviors")
    write(f"  Common patterns:     {DOCS_BASE_URL}/cookbook/common-patterns")
    write("")


# ---------- interactive mode ---------------------------------------------


_INTERACTIVE_SCAFFOLD = '''\
"""Your first activegraph behavior — scaffolded by `activegraph quickstart --interactive`.

This behavior fires whenever a claim object is created and emits a
`growth.flagged` event for claims that mention revenue growth above 25%.

Edit the TODO below, save, then type `continue` in the quickstart prompt.
"""

import re

from activegraph import behavior


@behavior(
    name="growth_flagger",
    on=["object.created"],
    where={"object.type": "claim"},
)
def growth_flagger(event, graph, ctx):
    """Flag claims that mention revenue growth above 25%."""
    text = event.payload["object"]["data"].get("text", "")
    # TODO: parse the text for a growth percentage and emit
    # `growth.flagged` with the claim id when the growth is > 25%.
    #
    # Hint: a regex like r"(\\d+)%\\s+YoY" captures the percentage.
    # Then: graph.emit("growth.flagged", {"claim_id": ..., "growth": ...})
    pass
'''


def _prepare_interactive_subdir(stream: TextIO, prompt_fn) -> Optional[Path]:
    """Set up the activegraph_quickstart/ subdir, handling collision
    helpfully (CONTRACT v1.0 #1 errata clarified: the quickstart is in
    onboarding mode, not debug mode; fail-loud is wrong here).

    Returns the Path to the behavior file, or None if the developer
    chose to quit.
    """
    subdir = Path.cwd() / _INTERACTIVE_SUBDIR
    behavior_file = subdir / _INTERACTIVE_BEHAVIOR_FILENAME
    if not subdir.exists():
        subdir.mkdir(parents=True)
        return behavior_file

    # Collision. Offer overwrite / suffix / quit.
    stream.write(
        f"\nAn `{_INTERACTIVE_SUBDIR}/` directory already exists in the\n"
        f"current working directory. What should I do?\n"
        f"  [o] overwrite the existing behavior file\n"
        f"  [s] suffix the new one (my_first_behavior_2.py, etc.)\n"
        f"  [q] quit\n"
    )
    choice = prompt_fn("choose [o/s/q]: ", default="s").strip().lower()
    if choice == "q":
        return None
    if choice == "o":
        return behavior_file
    # Default to suffix on anything not explicitly 'o' or 'q'.
    n = 2
    while True:
        candidate = subdir / f"my_first_behavior_{n}.py"
        if not candidate.exists():
            return candidate
        n += 1


def run_interactive_mode(
    stream: Optional[TextIO] = None,
    *,
    prompt_fn=None,
) -> int:
    """Guided REPL for writing the developer's first behavior.

    ``prompt_fn`` is injectable for testing — production passes
    :func:`click.prompt`; tests pass a scripted-stdin function.
    Returns 0 on success, 1 if the developer chose to quit.
    """
    out = stream if stream is not None else sys.stdout
    prompt_fn = prompt_fn if prompt_fn is not None else _default_prompt
    write = lambda s: out.write(s + "\n")

    write("activegraph quickstart --interactive — write your first behavior.")
    write("")
    write("We'll add a behavior that flags claims about revenue growth above")
    write("25%. The behavior will fire whenever a claim is created and emit a")
    write("`growth.flagged` event with the claim id.")
    write("")
    write("Step 1 of 4 — create a file.")
    write("")

    behavior_file = _prepare_interactive_subdir(out, prompt_fn)
    if behavior_file is None:
        write("Goodbye.")
        return 1

    behavior_file.write_text(_INTERACTIVE_SCAFFOLD)
    write(f"Created {behavior_file}.")
    write("")
    write("Step 2 of 4 — fill in the TODO.")
    write("")
    write(
        f"Open {behavior_file} in your editor and replace the TODO with the\n"
        f"parsing logic. When you've saved the file, type `continue` to test\n"
        f"it. (We don't watch your filesystem — explicit over magic.)"
    )
    write("")

    while True:
        cmd = prompt_fn("[continue / quit]: ", default="continue").strip().lower()
        if cmd in ("quit", "q"):
            write("")
            write(
                f"Goodbye. Your behavior is at {behavior_file} — keep it, modify\n"
                f"it, or delete the {_INTERACTIVE_SUBDIR}/ directory when you're done."
            )
            return 0
        if cmd not in ("continue", "c", ""):
            write(f"(unrecognized: {cmd!r}; type `continue` or `quit`)")
            continue

        # Run the goal against the developer's behavior. The run is
        # silent except for the per-fire summary at the end — we don't
        # repeat the full trace from fixture mode here.
        n_fires = _run_user_behavior(behavior_file, write)

        write("")
        write(f"Step 3 of 4 — your behavior fired {n_fires} time(s) across the run.")
        write("")
        write("Edit the file again and type `continue` to re-test, or `quit` to")
        write("finish.")
        write("")


def _run_user_behavior(behavior_file: Path, write) -> int:
    """Import the developer's behavior in a fresh subprocess-equivalent
    (importlib mechanics — we deliberately do NOT use importlib.reload;
    auto-reload was rejected per CONTRACT v1.0 BEAT 5 errata). Run a
    single-company goal against the fixture data and report how many
    times the developer's behavior fired.

    For simplicity in v1.0-rc1, this uses importlib.util.spec_from_file_location
    to load the file fresh on each `continue` — each invocation gets a
    fresh module object, so the @behavior decorator's side effects re-fire
    each round.
    """
    import importlib.util
    from activegraph import (
        Graph,
        IDGen,
        FrozenClock,
        Runtime,
        clear_registry,
        configure_logging,
    )
    from activegraph.packs.diligence import (
        DiligenceSettings,
        pack as diligence_pack,
    )
    from activegraph.packs.diligence.fixtures import (
        RecordedDiligenceProvider,
        THREE_COMPANIES,
        company_goal,
    )

    # Fresh registry per round so the developer's behavior re-registers
    # cleanly. The diligence pack's behaviors re-register via load_pack
    # in the same shape they did in fixture mode.
    clear_registry()
    spec = importlib.util.spec_from_file_location(
        "_activegraph_quickstart_user_behavior", str(behavior_file)
    )
    if spec is None or spec.loader is None:
        write(f"(could not load {behavior_file}; check for syntax errors)")
        return 0
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as e:
        write(f"(error loading your behavior: {type(e).__name__}: {e})")
        return 0

    configure_logging(level="ERROR", json_output=False)
    provider = RecordedDiligenceProvider(companies=[THREE_COMPANIES[0]])
    graph = Graph(ids=IDGen(), clock=FrozenClock(_QUICKSTART_FROZEN_TIMESTAMP))
    rt = Runtime(
        graph,
        llm_provider=provider,
        budget={
            "max_llm_calls": 80,
            "max_tool_calls": 100,
            "max_cost_usd": "5.00",
        },
        seed=0,
    )
    rt.load_pack(
        diligence_pack,
        settings=DiligenceSettings(
            llm_model="claude-sonnet-4-5",
            max_documents_per_company=5,
            max_claims_per_document=10,
        ),
    )
    rt.run_goal(company_goal(THREE_COMPANIES[0]))

    # Count behavior.completed events for the developer's behavior name.
    # If the user renamed it, this returns 0 — that's a finding worth
    # surfacing in v1.1 (read all behavior.completed events, list any
    # that aren't pack-prefixed). For rc1, the scaffold's name is
    # 'growth_flagger'; matching by name keeps the count honest.
    return sum(
        1 for e in rt.graph.events
        if e.type == "behavior.completed"
        and (e.payload or {}).get("behavior") == "growth_flagger"
    )


def _default_prompt(question: str, *, default: str = "") -> str:
    """click.prompt wrapper. Separate function so tests can inject a
    scripted-stdin replacement without touching click internals."""
    return click.prompt(question, default=default, show_default=False)


# ---------- click command registration -----------------------------------


@click.command("quickstart")
@click.option(
    "--interactive",
    is_flag=True,
    help=(
        "Walk through writing your first behavior with a guided REPL. "
        "Writes a scaffolded behavior file to ./activegraph_quickstart/ "
        "in the current directory; the directory persists so you can "
        "keep what you wrote."
    ),
)
def cmd_quickstart(interactive: bool) -> None:
    """Run the bundled diligence demo, or walk through writing your first behavior.

    Default mode runs the diligence pack against bundled fixtures with
    no API key and no network. The run is byte-deterministic across
    machines (FrozenClock + fixed run id + seeded behaviors); see the
    transcript at ``examples/quickstart_session.txt`` for the locked
    output shape.

    ``--interactive`` walks the developer through writing a custom
    behavior with prompts and explanations.
    """
    if interactive:
        rc = run_interactive_mode()
    else:
        rc = run_fixture_mode()
    if rc != 0:
        sys.exit(rc)
