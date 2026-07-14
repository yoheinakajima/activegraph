# AGENTS.md — activegraph (runtime)

Working guide for coding agents. The docs in `activegraph-vision` (ADRs,
DECISIONS.md, GLOSSARY.md, BUILD_PLAN.md) are the cross-repo spec and win
over any prompt; genuine conflicts get flagged, never silently resolved in code.

## What this repo is

The event-sourced reactive graph runtime — the deterministic trust substrate
of the stack. Objects + typed relations + an append-only event log; reactive
behaviors (function/class/LLM/relation); fork-and-diff with cache replay;
record-and-replay across all external I/O (LLM, tools, MCP, embeddings,
fetch, clocks); the pack format; action-class authority. Published to PyPI
as `activegraph` (pyproject version 1.10.0, Python >= 3.11).

**Boundary — what must NEVER live here:** product, agent, planner, or swarm
ontology (goals, tasks-as-product, onboarding, companions, scoring, UX copy).
Runtime law only (vision D002). Capabilities belong in `activegraph-packs`;
product meaning belongs in `babyagi-activegraph`. The one bundled pack is the
Diligence reference pack.

## Contract discipline (do this FIRST)

- `CONTRACT.md` is the source of truth. Amendments land BEFORE code.
- Amendments are APPENDED, never edited in place (Standing Rule §1), numbered
  per milestone (`## v1.9 #1 ...`), each stating what it deliberately does
  NOT touch. Tests anchor on the boundary the contract names (Standing Rule §2).
- `CONTRACT-INDEX.md` maps the document set; current-cycle scoping lives in
  `ROADMAP.md`, not inline in CONTRACT.md.

## Setup

- Shared venv for all repos: `/Users/yoheinakajima/code/runtime-and-packs/.venv`
  (Python 3.11 from Homebrew — system python is 3.9). `activegraph-packs` and
  `babyagi-activegraph` are installed editable; the runtime in the venv is the
  PyPI wheel (1.10.0). Running python/pytest FROM this repo root imports the
  local source (cwd precedence), so gates exercise your changes; run
  `.venv/bin/pip install -e ".[dev]"` if you need the local runtime importable
  from elsewhere.
- Git pushes work over SSH remotes (`git@github.com:`), not HTTPS; no `gh` CLI.

## Gates (run from this repo root; all must pass)

```bash
/Users/yoheinakajima/code/runtime-and-packs/.venv/bin/python -m pytest -m "not slow" -q
/Users/yoheinakajima/code/runtime-and-packs/.venv/bin/python -m mypy
/Users/yoheinakajima/code/runtime-and-packs/.venv/bin/python scripts/gate_docstrings.py
```

- **pytest**: CI (`.github/workflows/tests.yml`) runs `pytest -m "not slow"`
  on Ubuntu 3.11 + 3.12 with a Postgres service. Slow-marked gates
  (wheel-completeness, deploy-verification) have dedicated workflows.
  **Known-good macOS baseline: 4 failures in `tests/test_sandbox_trial.py`
  are pre-existing environmental (RLIMIT_AS unsupported on macOS; CI is
  Ubuntu).** Any other red is yours.
- **mypy**: allowlist-driven strict mode configured in `pyproject.toml`. A
  newly clean module joins BOTH the `[tool.mypy] files` list AND the strict
  `[[tool.mypy.overrides]]` module list. Regenerate the audit with
  `python scripts/audit_types.py`.
- **docstrings**: Ring 0 (`__all__` symbols) need docstrings or a reasoned
  entry in `docstring_gaps.toml`; Ring 1 is a threshold gate.

## Canonical paths

- `activegraph/core/` — graph, events, patches, ids, clock, views, graph store
- `activegraph/runtime/` — scheduler, authority, budget, promote, diff, patterns
- `activegraph/behaviors/`, `activegraph/tools/` — decorators + base types
- `activegraph/store/` — memory/sqlite/postgres/falkordb behind the
  `EventStore`/`GraphStore` protocols; `activegraph/llm/` — providers
- `activegraph/sandbox/`, `activegraph/sinks/`, `activegraph/observability/`
- `activegraph/packs/` — pack format + the bundled Diligence reference pack
  (non-`.py` pack data must be declared in `[tool.setuptools.package-data]`)
- `tests/` — flat `test_*.py` (markers: `slow`, `postgres`); snapshot suites
- `scripts/` — the gates and audits; `docs/` + `mkdocs.yml` — docs.activegraph.ai

## Definition of done

1. CONTRACT.md amendment landed before/with the change, appended and numbered.
2. All three gates green locally (modulo the 4 known macOS sandbox failures).
3. New public symbols docstringed; new clean modules added to both mypy lists.
4. `CHANGELOG.md` updated; docs pages touched if the public surface moved.
5. No product/agent/planner/swarm ontology introduced into the runtime.
