# MissingOptionalDependency

A subsystem you tried to use requires an optional Python package
that isn't installed. The framework keeps optional subsystems off
the default install path so a minimal install stays small — each
subsystem declares its dependency and fires this error when the
subsystem is actually used.

**This is not a "your code is wrong" error.** Your installation is
incomplete for the feature you're trying to use. The recovery is a
`pip install`, not a code change.

## Quick fix

The error message names the missing package and the
`pip install` line that fixes it:

```
MissingOptionalDependency: PostgresEventStore requires the
'psycopg' Python package

How to fix:
  Install the optional dependency:
      pip install 'activegraph[postgres]'
```

## The optional-extras list

Three subsystems require optional packages, declared as installable
extras in `pyproject.toml`:

| Extra | Provides | Required package |
|---|---|---|
| `activegraph[llm]` | LLM behaviors (pack format requires this) | `anthropic`, `pydantic` |
| `activegraph[postgres]` | `PostgresEventStore` | `psycopg>=3.1` |
| `activegraph[prometheus]` | `PrometheusMetrics` | `prometheus_client` |
| `activegraph[opentelemetry]` | `OpenTelemetryMetrics` | `opentelemetry-api`, `opentelemetry-sdk` |
| `activegraph[all]` | All of the above | (everything) |

A minimal install (just `pip install activegraph`) includes the
core runtime, the SQLite store, and the in-memory observability
backend. The optional extras keep their dependencies off the
critical path for users who don't need them.

## How to diagnose

The error message names the package, the feature, and the extras
group:

```python
try:
    store = PostgresEventStore("postgres://...", run_id=...)
except MissingOptionalDependency as e:
    print(e.package)   # 'psycopg'
    print(e.feature)   # 'PostgresEventStore'
    print(e.extras)    # 'postgres'
```

Multi-inherits `ImportError` for back-compat — code that catches
`ImportError` around optional-dep imports continues to work.

## When does this fire

At the first construction or call into the subsystem. The check
runs lazily, on the import inside the subsystem's lazy-import path:

- `PostgresEventStore(...)` first construction → `psycopg` import
- `PrometheusMetrics(...)` first construction → `prometheus_client`
  import
- `OpenTelemetryMetrics(...)` first construction →
  `opentelemetry-api` and `opentelemetry-sdk` imports
- `import activegraph.packs` (or any pack-related import) →
  `pydantic` import (pack format depends on Pydantic models)

A bare `pip install activegraph` followed by an `import activegraph`
won't fire any of these — the error only surfaces when you actually
use the subsystem.

## Why the framework refuses to continue

Each optional subsystem declares its dependency explicitly so the
missing-dep error fires at the boundary, not later inside the
subsystem with a confusing `AttributeError` or import error from a
nested module. The structured error names the install line so
recovery is a one-command operation.

See [`failure-model`](../../concepts/failure-model.md) for the
broader principle.

## What's related

- [Operating in production](../../guides/operating-in-production.md) —
  the canonical reference for choosing extras in a deployed
  runtime.
- [`invalid-store-url-error`](invalid-store-url-error.md) — fires
  before this error in the Postgres-without-psycopg path; the URL
  parses fine, then the store tries to import psycopg.
