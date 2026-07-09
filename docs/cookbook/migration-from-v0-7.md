# Migration from v0.7

This page is the runbook for upgrading runs and code from v0.7
through to v1.0. Each milestone added surface; backward
compatibility was preserved throughout (CONTRACT v0.7 #22 / v0.8
#14 / v0.9 #21), so the upgrades are additive — your existing
behaviors keep working as you adopt new primitives.

Three milestones span the upgrade path: **v0.8** added the
Postgres store, store URLs, migration, and the observability
surface; **v0.9** added the pack format and shipped the Diligence
reference pack; **v1.0** is the adoption-surface milestone (this
release) — the per-error catalog, the doc site, the quickstart,
and the gates.

The order below is the order to apply the changes. Skip steps
that don't apply.

## 1. Upgrade the activegraph package

```bash
pip install --upgrade activegraph
```

`activegraph[all]` pulls in the optional extras (anthropic,
psycopg, prometheus_client, pydantic). For a minimal install,
just `activegraph` — see
[`missing-optional-dependency`](../reference/errors/missing-optional-dependency.md)
for the per-feature extras.

## 2. Migrate the store schema

The store schema_version advanced across milestones. v1.0 expects
schema_version `'1'`; runs written by older builds carry their
own. Mismatched schemas raise
[`schema-version-mismatch`](../reference/errors/schema-version-mismatch.md)
at open time.

To migrate runs forward:

```bash
activegraph migrate --from sqlite:///old.db --to sqlite:///new.db
```

The migration is transaction-per-run, idempotent, one-directional
(CONTRACT v0.8 #5). Each run migrates in a single transaction; a
failed run leaves the destination unchanged for that run, and
re-running picks up where it left off.

If the source has corrupted event payloads, add `--skip-corrupted`
to recover the readable subset (CONTRACT v1.0 PR-C):

```bash
activegraph migrate --from sqlite:///old.db --to sqlite:///new.db \
    --skip-corrupted
```

The skipped event ids appear in the per-run report.

## 3. Adopt connection URLs (v0.7 → v0.8)

v0.7 store construction took a path argument:

```python
# v0.7:
rt = Runtime(graph, persist_to="/path/to/run.db")
```

v0.8 added connection URLs as the canonical addressing form,
with the path form preserved as shorthand for SQLite:

```python
# v0.8+:
rt = Runtime(graph, persist_to="/path/to/run.db")           # still works
rt = Runtime(graph, store=SQLiteEventStore("/path/to/run.db"))
rt = Runtime(graph, store=PostgresEventStore("postgres://host/db"))
```

URLs are required for the CLI and for cross-store operations
(`activegraph migrate`, `activegraph inspect <url>`). See
[`invalid-store-url-error`](../reference/errors/invalid-store-url-error.md)
for the URL grammar.

If your code passes a bare filesystem path to a CLI command (an
easy v0.7-era habit), the CLI rejects it with `InvalidStoreURL`
naming the corrected URL. The fix is `sqlite:///<your-path>` or
`sqlite:////<absolute-path>` (note the slash count).

## 4. Adopt the pack format (v0.8 → v0.9)

v0.9 introduced packs. If your v0.7/v0.8 code declared behaviors,
tools, and object types globally with `@behavior` / `@tool`, it
keeps working — packs are additive. Loading a pack adds its
behaviors and tools to the runtime alongside the global ones.

To author a pack from existing v0.7/v0.8 code, see
[Authoring packs](../guides/authoring-packs.md). The shipped
[Diligence pack](../reference/api/packs/diligence.md) is the reference example.

Two things to know if you're loading third-party packs:

- **Pack name conflicts.** Two loaded packs claiming the same
  canonical symbol raises
  [`pack-conflict-error`](../reference/errors/pack-conflict-error.md).
  Rename one pack or load them in separate runtimes.
- **Pack version pinning.** A runtime holds at most one version
  of any pack; loading a different version raises
  [`pack-version-conflict-error`](../reference/errors/pack-version-conflict-error.md).

## 5. Adopt the v1.0 error hierarchy (v0.9 → v1.0)

Every exception the framework raises now inherits from
`ActiveGraphError`. The v1.0 hierarchy preserves builtin lineage
through multi-inheritance, so existing `except ValueError` /
`except KeyError` / `except TypeError` clauses keep working:

```python
# v0.9 — these patterns still work in v1.0:
try:
    store.get_event(event_id)
except KeyError:
    ...

try:
    graph.add_object("claim", bad_data)
except ValueError:
    ...
```

The v1.0 hierarchy adds richer catches:

```python
# v1.0 — broader catches with structured context:
try:
    rt = Runtime.load(url, run_id=rid)
except activegraph.StorageError as e:
    log(e.what_failed, e.how_to_fix, e.context)
except activegraph.ActiveGraphError as e:
    log(e.what_failed, e.how_to_fix)
```

See [`failure-model`](../concepts/failure-model.md) for the
hierarchy and the events-not-exceptions principle.

## 6. Adopt the v1.0 CLI follow-ons

v1.0 added five operator-facing CLI flags that the error messages
reference in their recovery prose:

- `activegraph inspect <run> --event <event-id>`
- `activegraph inspect <run> --behaviors`
- `activegraph inspect <run> --pack-version`
- `activegraph fork <run> --at-event <evt> --set <key>=<value>`
- `activegraph fork <run> --at-event <evt> --record`
- `activegraph migrate --from <src> --to <dst> --skip-corrupted`

If your operator runbooks reference older flag combinations, the
new flags are additive — old commands keep working. See the
[CLI reference](../reference/cli/) for the full surface.

## 7. Adopt structured logging (v0.7 → v0.8)

v0.8 added structured logging with a documented schema. If your
v0.7 deployment was reading the trace stream from stderr, the
v0.8 schema is richer and JSON-shaped; opt in via:

```python
from activegraph import configure_logging
configure_logging(level="INFO", json_output=True)
```

The structured schema is documented under
[Operating in production](../guides/operating-in-production.md).
The legacy text logs still emit when `json_output=False`.

## 8. Adopt the metrics protocol (v0.7 → v0.8)

v0.8 added a three-method `Metrics` protocol with two shipped
backends (NoOp by default, Prometheus opt-in). Existing code
without metrics keeps working — `NoOpMetrics` is the default,
so no surface changes if you don't opt in. To enable Prometheus:

```bash
pip install 'activegraph[prometheus]'
```

```python
from activegraph import PrometheusMetrics
rt = Runtime(graph, metrics=PrometheusMetrics())
```

See [Operating in production](../guides/operating-in-production.md)
for the metric names and the operator contract (CONTRACT v0.8 #9).

## Backward compatibility — what's guaranteed

Every v0–v0.9 test passes unchanged in v1.0 (CONTRACT v1.0 #9).
The only deliberately-changed user-visible surface is two trace
snapshot files (`llm_trace.txt`, `tool_trace.txt`) that gained
the `[trace.flags]` rollup header in v0.9.1 — operator-visible
but additive, not removing.

For specific compatibility questions, the per-milestone CONTRACT
sections enumerate the back-compat clauses:

- v0.7 #22 — v0/v0.5/v0.6 tests pass; trace snapshots stay
  byte-identical except for the `prompt_normalized=true` flag
- v0.8 #14 — v0–v0.7 tests pass; library APIs unchanged
- v0.9 #21 — v0–v0.8 tests pass; pack loading is opt-in
- v1.0 #9 — all 384 v0–v0.9 tests pass through every v1.0 PR

If something that worked in v0.7–v0.9 doesn't work in v1.0,
that's a bug — file an issue at
[GitHub Issues](https://github.com/yoheinakajima/activegraph/issues).

## What's related

- [`schema-version-mismatch`](../reference/errors/schema-version-mismatch.md)
  — the error this page is forward-referenced from.
- [Operating in production](../guides/operating-in-production.md)
  — the v0.8+ operator surface in detail.
- [Authoring packs](../guides/authoring-packs.md) — the v0.9 pack
  format reference.
- [`failure-model`](../concepts/failure-model.md) — the v1.0
  hierarchy and the events-not-exceptions principle.
