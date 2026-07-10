# Runtime

The runtime loop. Constructed with a `Graph`, an optional set of
behaviors, an optional LLM provider, an optional budget, and an
optional store. Drives goal runs to completion and persists state
through the attached store.

For the conceptual model, see [`concepts/graph`](../../concepts/graph.md)
and [`concepts/behaviors`](../../concepts/behaviors.md).

::: activegraph.Runtime

::: activegraph.RuntimeStatus

::: activegraph.Frame

::: activegraph.Budget

::: activegraph.DevOverride

::: activegraph.IDGen

## Clocks

::: activegraph.Clock

::: activegraph.FrozenClock

::: activegraph.TickingClock

## Logging + registry helpers

::: activegraph.configure_logging

::: activegraph.get_registry

::: activegraph.clear_registry

::: activegraph.register
