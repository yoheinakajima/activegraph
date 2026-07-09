# InvalidActivateAfter

A behavior decorator passed an unparseable or out-of-range value
to `activate_after`. The scheduler refuses values that don't denote
a positive integer event count; wall-clock units are deliberately
out of scope (CONTRACT v0.7 #13).

Multi-inherits `ValueError` for back-compat — code that catches
`ValueError` around behavior registration continues to work.

## Quick fix

Pass a positive integer event count:

```python
# Accepted:
@behavior(name="...", activate_after=5)
@behavior(name="...", activate_after="5")
@behavior(name="...", activate_after="5 events")
@behavior(name="...", activate_after="5 event")

# Refused (raises this error):
@behavior(name="...", activate_after=0)            # must be >= 1
@behavior(name="...", activate_after=-1)           # must be >= 1
@behavior(name="...", activate_after=True)         # bool not int
@behavior(name="...", activate_after="5 seconds")  # wall-clock
@behavior(name="...", activate_after="five")       # unparseable
```

## How to diagnose

The error's `kind` field discriminates which validation rule fired:

```
InvalidActivateAfter: activate_after='5 seconds' is invalid
(wall-clock unit)
```

From code:

```python
try:
    @behavior(activate_after="5 seconds")
    def my_behavior(...):
        ...
except InvalidActivateAfter as e:
    print(e.spec)   # '5 seconds'
    print(e.kind)   # 'wall-clock unit' | 'unparseable string' | etc.
```

The five `kind` discriminators each have their own recovery prose
inline in the error message — bool-not-int, wall-clock unit,
unparseable string, wrong-type, must-be->=1.

## Why wall-clock units are refused

`activate_after` schedules a behavior to fire N events after its
triggering event. The runtime evaluates the schedule against the
event log, not against wall-clock time, so replay produces identical
timing (CONTRACT v0.7 #13). Wall-clock units would let scheduling
depend on real time, which would make replay non-deterministic.

If you genuinely need wall-clock scheduling, file an issue — the
v1+ contract leaves room for it behind a separate primitive.

## When does this fire

At behavior registration — the `@behavior` / `@llm_behavior`
decorator runs at module import time and the scheduler parses
`activate_after` then. Misconfiguration surfaces before any goal
runs.

## What's related

- [Behaviors API reference](../api/behaviors.md) — the
  canonical reference for the `@behavior` decorator including
  `activate_after`.
