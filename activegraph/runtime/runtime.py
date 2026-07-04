"""The runtime loop. Single-threaded FIFO. CONTRACT #10.

Responsibilities (v0):
- Subscribe to graph events, enqueue them.
- Pop events, find matching behaviors, invoke each in registration order.
- Wrap behavior calls with behavior.started / behavior.completed
  (or relation_behavior.started / behavior.completed for relation behaviors).
- Catch any behavior exception, emit behavior.failed (CONTRACT #13).
- Stop on idle or budget exhaustion.

Added in v0.5:
- `persist_to=PATH` (sugar) or `store=...` attaches a durable EventStore.
- `Runtime.load(path, run_id=None)` rebuilds from an event log (CONTRACT v0.5 #5).
- `runtime.save_state(path=None)` flushes / late-binds persistence.
- `runtime.fork(at_event, label=None)` branches a run (#9).
- `runtime.diff(other)` returns a structural `Diff` (#10).
- `replay_strict=True` re-runs behaviors and verifies the output matches
  the recorded log; on the first divergence, raises ReplayDivergenceError
  (#7).

Added in v0.6:
- `llm_provider=` plugs an LLMProvider (Anthropic, recorded, scripted).
- `replay_llm_cache=True` pre-populates a content-keyed cache from
  recorded `llm.responded` events so re-runs (forks, strict-replay)
  do not call the API.
- The `_invoke_llm` path is the runtime-owned LLM lifecycle:
  assemble prompt → cache lookup → optional cost pre-check →
  emit llm.requested → call provider (or use cached) → emit
  llm.responded → parse output → invoke handler. Failures flow as
  `behavior.failed` with a `reason` from CONTRACT v0.6 #11.
- `replay_strict=True` + recorded llm.responded whose prompt hash
  does not match the live re-assembly → ReplayDivergenceError pinned
  to the offending llm.requested event id (decision-2 adjustment).

Added in v0.7:
- `tools=[t1, t2, ...]` plugs a list of `Tool` objects; absent tools
  fall back to the global `@tool` registry.
- The `_invoke_llm` path becomes a turn loop: provider's response can
  include `tool_calls`, in which case the runtime invokes the tool,
  echoes the result back into messages, and re-calls the provider.
  `max_tool_turns` caps the loop. Per-turn LLM and tool events are
  emitted; replay reads them back in order.
- `replay_tool_cache=True` (parallel to `replay_llm_cache=True`)
  pre-populates a content-keyed cache from `tool.responded` events.
  By default ALL tools (deterministic or not) serve from cache on
  replay; `replay_reinvoke_deterministic=True` opts in to actually
  re-invoking deterministic tools (CONTRACT v0.7 tool-determinism).
- `pattern=` on a behavior compiles a Cypher subset matcher; the
  registry's `match()` includes the bindings. Behaviors fire once
  per event when both `on=` and `pattern=` agree (CONTRACT v0.7 #11).
- `activate_after=N` schedules a behavior to fire N events later,
  with the `where=` re-checked at fire time (CONTRACT v0.7 #13).
"""

from __future__ import annotations

import json
import random as _random
import time as _time
import traceback


def _monotonic() -> float:
    return _time.monotonic()
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Callable, Iterable, NamedTuple, Optional, Union, cast

from activegraph.behaviors.base import Behavior, LLMBehavior, RelationBehavior
from activegraph.behaviors.decorators import get_registry
from activegraph.core.event import Event
from activegraph.core.graph import Graph, evaluate_where as _evaluate_where
from activegraph.core.graph_store import GraphStore
from activegraph.core.ids import IDGen
from activegraph.core.view import View
from activegraph.frame import Frame
from activegraph.llm.cache import LLMCache
from activegraph.llm.errors import LLMBehaviorError, MissingProviderError
from activegraph.llm.provider import LLMProvider
from activegraph.llm.types import LLMMessage, ToolCall
from activegraph.policy import Policy
from activegraph.runtime.behavior_graph import BehaviorGraph
from activegraph.runtime.budget import Budget
from activegraph.runtime.diff import Diff, compute_diff
from activegraph.runtime.errors import ReplayDivergenceError
from activegraph.runtime.queue import EventQueue
from activegraph.runtime.registry import Registry
from activegraph.runtime.scheduler import DelayedQueue, ScheduledEntry
from activegraph.runtime.view_builder import build_view
from activegraph.tools.base import Tool
from activegraph.tools.cache import (
    CachedToolResponse,
    canonicalize_args,
    hash_tool_call,
)
from activegraph.tools.context import ToolContext
from activegraph.tools.decorators import get_tool_registry

if TYPE_CHECKING:
    from activegraph.packs.loader import PackRuntimeState
    from activegraph.trace.printer import Trace
from activegraph.tools.errors import (
    MissingToolError,
    ToolError,
    UnknownToolError,
)
from activegraph.tools.recorded import DirectToolInvoker

from activegraph.observability.logging import get_logger, runtime_log_extra
from activegraph.observability.metrics import Metrics, NoOpMetrics
from activegraph.observability.status import (
    BehaviorInfo,
    BudgetSnapshot,
    EventSummary,
    FrameSnapshot,
    RuntimeStatus,
)


@dataclass
class Context:
    view: View
    frame: Optional[Frame]
    policy: Optional[Policy]
    random: _random.Random
    clock: Any  # Clock-like
    llm_provider: Optional[LLMProvider] = None
    # v0.7: pattern bindings for the current invocation. Empty list for
    # behaviors that don't declare a pattern. The handler is fired
    # ONCE per event regardless of how many bindings the pattern
    # produced — iterating `ctx.matches` is the developer's job
    # (CONTRACT v0.7 #12).
    matches: list[Any] = field(default_factory=list)
    # v0.9: pack-aware context. Set by the runtime when invoking a
    # pack-owned behavior.
    #   `settings` — the executing behavior's pack's settings instance,
    #                or None if the behavior is not pack-owned.
    #   `_runtime` — backref so ctx.pack_settings(...) and
    #                ctx.propose_object(...) can reach the runtime.
    settings: Any = None
    _runtime: Any = None

    def pack_settings(self, pack_name: str) -> Any:
        """Look up settings for any loaded pack by name. Returns the
        Pydantic settings instance, or None if the pack isn't loaded.
        CONTRACT v0.9 #7 (Form 3 / cross-pack lookup).
        """
        if self._runtime is None or self._runtime._pack_state is None:
            return None
        return self._runtime._pack_state.pack_settings.get(pack_name)

    def propose_object(
        self,
        object_type: str,
        data: dict[str, Any],
        *,
        reason: str = "",
    ) -> str:
        """Defer creation of an object behind a policy approval.

        Returns the proposal id. The object materializes when
        `runtime.approve(id)` is called. Intended for use by behaviors
        whose pack policy gates `object_type` writes.

        Convenience: behaviors can just call `graph.add_object` if their
        pack settings say auto-approval is on; this helper is the
        explicit path when gating is enabled.
        """
        if self._runtime is None:
            from activegraph.runtime.exec_errors import RuntimeContextRequiredError
            raise RuntimeContextRequiredError(method="ctx.propose_object")
        proposal_id: str = self._runtime._add_pending_approval(
            object_type=object_type, data=data, reason=reason
        )
        return proposal_id


class BehaviorFailure(NamedTuple):
    """Structured view of a ``behavior.failed`` event for
    :attr:`Runtime.errors`. CONTRACT v1.0.3 #3.

    Five fields capture the operationally important parts of a
    failure: which behavior, which event triggered it, the v0.6 #11
    reason code (when present), the Python exception class name, and
    the exception message. ``failed_event_id`` ties the structured
    view back to the underlying ``behavior.failed`` event so callers
    that want the full payload (traceback, payload extras) can
    re-read it from ``runtime.graph._events``.

    Not exposed as a class users construct — it's a projection of
    the events the runtime emits, and the runtime is the only writer.
    Distinct from Python's builtin ``RuntimeError``; named
    ``BehaviorFailure`` to avoid the shadow.
    """

    behavior: str
    event_id: str
    reason: Optional[str]
    exception_type: str
    message: str
    failed_event_id: str


# Map v0.6 #11 reason-code prefixes to the framework error class's
# doc-page slug. Used by the WARNING log emitted from
# `_emit_behavior_failed` so the log line carries a More: URL the
# operator can open. The slugs are class-level (the v1.0 #4 More:
# URL convention) — a per-reason slug doesn't exist today.
_REASON_PREFIX_TO_DOC_SLUG: tuple[tuple[str, str], ...] = (
    ("llm.", "llm-behavior-error"),
    ("tool.", "tool-error"),
    ("budget.", "budget-exhausted"),
)


def _doc_url_for_reason(reason: str) -> str:
    """Return the More: doc-page URL for a v0.6 #11 reason code.

    Defaults to the generic execution-error page when no prefix
    matches (covers ``exception.*`` reasons from generic catches).
    """
    from activegraph.errors import DOCS_BASE_URL

    for prefix, slug in _REASON_PREFIX_TO_DOC_SLUG:
        if reason.startswith(prefix):
            return f"{DOCS_BASE_URL}/errors/{slug}"
    return f"{DOCS_BASE_URL}/errors/execution-error"


class Runtime:
    """Drives behaviors over an event-sourced :class:`~activegraph.core.graph.Graph`.

    The runtime owns the dispatch loop: an entry point emits an event
    (``run_goal``, or any mutation on the graph), matching behaviors
    fire against a runtime-built read-only
    :class:`~activegraph.core.view.View` each, and whatever they
    propose lands back in the log as more events,
    until the graph goes idle or the :class:`~activegraph.runtime.budget.Budget`
    ends the run. Construction wires the run-level choices: behaviors
    and tools (defaulting to the decorator registries), persistence
    (``persist_to=`` / ``store=``), the LLM provider with its retry
    and replay caches, policy, frame, budget, and metrics. Failures
    inside behaviors become ``behavior.failed`` events, not
    exceptions (CONTRACT v1.0 #4b) — read them from :attr:`errors`;
    exceptions surface only at construction and entry points.
    :meth:`Runtime.load` rebuilds a recorded run from its log;
    :meth:`Runtime.fork` branches one from any historical event.
    """

    def __init__(
        self,
        graph: Graph,
        behaviors: Optional[Iterable[Union[Behavior, RelationBehavior]]] = None,
        frame: Optional[Frame] = None,
        policy: Optional[Policy] = None,
        budget: Optional[dict[str, Any]] = None,
        seed: int = 0,
        *,
        persist_to: Optional[str] = None,
        store: Optional[Any] = None,
        replay_strict: bool = False,
        llm_provider: Optional[LLMProvider] = None,
        replay_llm_cache: bool = False,
        llm_cache: Optional[LLMCache] = None,
        llm_retry_max_attempts: int = 3,
        llm_retry_initial_delay_seconds: float = 0.5,
        llm_retry_max_delay_seconds: float = 8.0,
        # v0.7 additions
        tools: Optional[Iterable[Tool]] = None,
        replay_tool_cache: bool = False,
        tool_cache: Any = None,  # ToolCache; Any to avoid import cycle
        replay_reinvoke_deterministic: bool = False,
        tool_invoker: Any = None,  # defaults to DirectToolInvoker()
        # v0.8: observability
        metrics: Optional[Metrics] = None,
        # CONTRACT v1.3 #1 #2: opt-in native structured output. Default
        # False for the v1.3 cycle - native mode changes prompt hashes,
        # so flipping it must be a deliberate, cache-invalidating act.
        native_structured_output: bool = False,
    ) -> None:
        self.graph = graph
        self.frame = frame
        self.policy = policy
        self.budget = Budget(budget or {})
        self._random = _random.Random(seed)
        self.replay_strict = replay_strict
        # CONTRACT v0.6 #3: provider is set once at construction.
        self.llm_provider: Optional[LLMProvider] = llm_provider
        # CONTRACT v1.3 #1: the opt-in lever plus the per-behavior
        # resolved modes ("native" | "prompt"), filled by
        # _ensure_registry. Runtime-local on purpose: behaviors are
        # shared objects and the mode depends on this runtime's flag.
        self.native_structured_output: bool = bool(native_structured_output)
        self._structured_output_modes: dict[str, str] = {}
        self.replay_llm_cache: bool = replay_llm_cache
        # Cache is content-keyed by prompt hash. May be pre-populated
        # (load/fork with replay_llm_cache=True) or lazily filled.
        self._llm_cache: Optional[LLMCache] = llm_cache
        self.llm_retry_max_attempts = max(1, int(llm_retry_max_attempts))
        self.llm_retry_initial_delay_seconds = max(
            0.0, float(llm_retry_initial_delay_seconds)
        )
        self.llm_retry_max_delay_seconds = max(
            self.llm_retry_initial_delay_seconds,
            float(llm_retry_max_delay_seconds),
        )
        # During _verify_replay we install the sequence of expected
        # prompt hashes (from recorded llm.requested events). A live
        # re-assembled prompt whose hash doesn't match the next
        # expected one is a divergence — pinned to the new
        # llm.requested event id (decision-2 adjustment).
        self._strict_expected_hashes: Optional[list[str]] = None

        # v0.7: tool plumbing
        self._explicit_tools = list(tools) if tools is not None else None
        self.tool_registry: dict[str, Tool] = {}
        self.replay_tool_cache: bool = replay_tool_cache
        from activegraph.tools.cache import ToolCache as _ToolCache
        self._tool_cache = tool_cache if tool_cache is not None else _ToolCache()
        self.replay_reinvoke_deterministic: bool = replay_reinvoke_deterministic
        self._tool_invoker = tool_invoker if tool_invoker is not None else DirectToolInvoker()

        # If behaviors are passed explicitly, snapshot now. Otherwise, defer
        # to the global registry — the user may decorate behaviors after
        # constructing the Runtime (the README quickstart does exactly this).
        self._explicit_behaviors = (
            list(behaviors) if behaviors is not None else None
        )
        self.registry: Optional[Registry] = None

        # frame id (cheap auto-allocate so provenance always has one)
        if self.frame is not None and self.frame.id is None:
            self.frame.id = graph.ids.frame()

        # v0.8: observability — metrics defaults to NoOp so the runtime
        # is fully functional without any metrics backend configured.
        self.metrics: Metrics = metrics if metrics is not None else NoOpMetrics()
        self._log = get_logger("activegraph.runtime")

        self._queue = EventQueue()
        # v0.7: delayed queue for activate_after scheduling.
        self._delayed = DelayedQueue()
        # Event tick counter: increments for every non-lifecycle event
        # processed. This is the time axis for activate_after.
        self._tick: int = 0
        # Stash for tool result message between _invoke_tool and the
        # turn-loop caller. Always cleared after consumption.
        self._last_tool_result_message: Optional[LLMMessage] = None
        self._inside_dispatch = False
        graph.add_listener(self._on_event)
        self._idle_emitted = False

        # ---- v0.5: persistence wiring ----
        if persist_to is not None and store is not None:
            from activegraph.runtime.config_errors import InvalidRuntimeConfiguration
            raise InvalidRuntimeConfiguration(
                "Runtime(...) was passed both `persist_to=` and `store=`",
                what_failed=(
                    "Runtime construction received both a `persist_to=` path "
                    "and an explicit `store=` instance. The two kwargs are "
                    "alternative ways to attach storage — only one can be "
                    "used per Runtime."
                ),
                why=(
                    "`persist_to=` is shorthand for 'open a SQLite store at "
                    "this path and attach it.' `store=` is the explicit form "
                    "for any EventStore implementation. If both were "
                    "accepted, the runtime would have to pick one or merge "
                    "them, and silent precedence rules would surface as bugs "
                    "the first time an operator switched stores."
                ),
                how_to_fix=(
                    "Pass exactly one:\n"
                    "    Runtime(graph, persist_to='/path/to/run.db')\n"
                    "or:\n"
                    "    Runtime(graph, store=SQLiteEventStore('/path/to/run.db'))\n"
                    "\n"
                    "The two forms produce equivalent runtimes for SQLite. "
                    "Use `store=` when you need a non-SQLite backend or want "
                    "to share an open store across runtimes."
                ),
            )
        if persist_to is not None:
            store = _open_sqlite_store(persist_to, graph.run_id)
            store.upsert_run(
                created_at=_now_iso(),
                frame_id=self.frame.id if self.frame else None,
            )
        if store is not None:
            graph.attach_store(store)

        # ---- v0.9: pack state (lazy) ----
        # Holds the per-runtime pack bookkeeping populated by
        # `load_pack`. Initialized lazily on first access via the
        # loader's `_ensure_pack_state`. The `_pack_behaviors` and
        # `_pack_tools` lists are merged into the registry inside
        # `_ensure_registry` (which rebuilds `tool_registry` from
        # scratch each call).
        self._pack_state: Optional[PackRuntimeState] = None
        self._pack_behaviors: list[Any] = []
        self._pack_tools: list[Any] = []

        # ---- v1.0.2.post1 #1 (b): eager cross-provider validation ----
        # First run the bulk validation pass against whatever's already
        # in the registry; only if it passes do we add this Runtime to
        # the live-set. Failed constructions stay out of the WeakSet so
        # they can't influence subsequent register() / @llm_behavior
        # validation calls. The lazy path inside _ensure_registry()
        # stays as a defensive double-check.
        if self.llm_provider is not None:
            source = (
                self._explicit_behaviors
                if self._explicit_behaviors is not None
                else get_registry()
            )
            _resolve_and_validate_llm_models(source, self.llm_provider)
        from activegraph.runtime._live import track_runtime
        track_runtime(self)

    # ---------- public surface ----------

    @property
    def run_id(self) -> str:
        return self.graph.run_id

    @property
    def errors(self) -> list[BehaviorFailure]:
        """Accumulated ``behavior.failed`` events as structured tuples.

        v1.0.3 #3. Reads from ``self.graph._events`` on each access —
        the events are the source of truth and this property is a
        projection. No caching, no listener registration, no new
        state. Callers can inspect failures programmatically without
        reaching into ``graph._events`` or parsing payload dicts.

        Each :class:`BehaviorFailure` carries five operationally
        useful fields plus the underlying ``behavior.failed`` event
        id for callers that want to re-read the full payload (e.g.,
        traceback, LLM payload extras).
        """
        out: list[BehaviorFailure] = []
        for e in self.graph._events:
            if e.type != "behavior.failed":
                continue
            p = e.payload
            out.append(
                BehaviorFailure(
                    behavior=p.get("behavior", ""),
                    event_id=p.get("event_id", ""),
                    reason=p.get("reason"),
                    exception_type=p.get("exception_type", ""),
                    message=p.get("message", ""),
                    failed_event_id=e.id,
                )
            )
        return out

    # ---------- listener ----------

    def _on_event(self, event: Event) -> None:
        # v0.8: count every emitted event, lifecycle or not. The metric
        # tag is the event type. The graph's listener fires for every
        # event passing through emit(), including lifecycle events that
        # we suppress from re-matching below.
        self.metrics.counter(
            "activegraph_events_emitted_total",
            {"event_type": event.type},
        )
        # Don't enqueue our own lifecycle events for re-matching.
        # v0.7 adds `llm.*`, `tool.*`, `pattern.*`, `behavior.scheduled`
        # to the suppression list — they're internal to the runtime's
        # bookkeeping. (User behaviors that want to audit LLM/tool
        # activity can still subscribe via the registry's lookup.)
        if (
            event.type.startswith("behavior.")
            or event.type.startswith("relation_behavior.")
            or event.type.startswith("runtime.")
            or event.type.startswith("llm.")
            or event.type.startswith("tool.")
            or event.type.startswith("pattern.")
            # v0.9: approval bookkeeping is internal; CONTRACT v0.9 #13
            # deliberately keeps `pack.loaded` queue-visible so pack-aware
            # behaviors can subscribe, but `approval.*` is suppressed.
            or event.type.startswith("approval.")
        ):
            return
        self._queue.push(event)
        self.metrics.gauge(
            "activegraph_queue_depth", {}, float(len(self._queue))
        )
        # New activity → we're not idle anymore.
        self._idle_emitted = False
        # INFO: one log line per enqueued event. High-volume; operators
        # filter at WARNING in production dashboards.
        self._log.info(
            "event emitted",
            extra=runtime_log_extra(
                run_id=self.graph.run_id,
                event_id=event.id,
            ),
        )

    # ---------- public entry points ----------

    def _resolve_structured_output_mode(self, b: LLMBehavior) -> str:
        """Resolve "native" or "prompt" for one behavior. CONTRACT v1.3 #1.

        Native requires all four: the runtime opt-in flag, the
        provider's getattr-guarded capability claim for the resolved
        model, and the schema passing the offline subset pre-flight.
        Fallback is silent-but-audited (v1.3 #1 #4): the resolved mode
        rides every llm.requested payload, and a schema outside the
        subset logs one debug line here at registration.
        """
        if not self.native_structured_output or b.model is None:
            return "prompt"
        supports = getattr(
            self.llm_provider, "supports_native_structured_output", None
        )
        if supports is None or not supports(b.model):
            return "prompt"
        from activegraph.llm.native import native_schema_compatible
        from activegraph.llm.prompt import schema_to_json

        if not native_schema_compatible(schema_to_json(b.output_schema)):
            self._log.debug(
                "structured-output schema outside the native subset; "
                "behavior %s stays on the prompt-embedded path",
                b.name,
            )
            return "prompt"
        return "native"

    def _ensure_registry(self) -> None:
        source = (
            self._explicit_behaviors
            if self._explicit_behaviors is not None
            else get_registry()
        )
        # v0.9: pack-owned behaviors live in `_pack_behaviors` (filled
        # by `load_pack`) and are merged on top of the global / explicit
        # source. Pack behaviors carry canonical (namespace-prefixed)
        # names; they never collide with non-pack behaviors because the
        # `pack.` prefix is reserved for packs.
        if self._pack_behaviors:
            source = list(source) + list(self._pack_behaviors)
        # CONTRACT v0.6 #21: LLM behaviors fail loud at registration if
        # there is no provider. We do not silently fall back to a mock —
        # a missing provider is almost always a real misconfiguration.
        if self.llm_provider is None:
            for b in source:
                if isinstance(b, LLMBehavior):
                    raise MissingProviderError(behavior_name=b.name)
        else:
            # CONTRACT v1.0.2 #1: resolve the provider's default model for
            # behaviors that didn't pin one, and validate explicit model
            # names against cross-provider mismatches.
            _resolve_and_validate_llm_models(source, self.llm_provider)
            # CONTRACT v1.3 #1 #1: resolve the structured-output mode per
            # behavior - a pure function of (flag, capability, model,
            # schema pre-flight); no network, no clock.
            for b in source:
                if isinstance(b, LLMBehavior) and b.output_schema is not None:
                    self._structured_output_modes[b.name] = (
                        self._resolve_structured_output_mode(b)
                    )
        self.registry = Registry(source)

        # v0.7: assemble the tool registry. Explicit tools= override the
        # global @tool registry, mirroring how behaviors= works.
        if self._explicit_tools is not None:
            tools_source = list(self._explicit_tools)
        else:
            tools_source = list(get_tool_registry())
        # v0.9: pack-owned tools merge in here (filled by `load_pack`).
        # These carry canonical (namespace-prefixed) names and may also
        # be registered under their short name if `export_globally=True`.
        if self._pack_tools:
            tools_source = tools_source + list(self._pack_tools)
        # LLM behaviors may also bring their own tools via tools=[...] on
        # the decorator; pull those in too so the name lookup is unified.
        for b in source:
            if isinstance(b, LLMBehavior):
                for t in b.tools:
                    if isinstance(t, Tool) and t not in tools_source:
                        tools_source = list(tools_source) + [t]
        # CONTRACT v0.7 #2: each LLM behavior with tools= must reference
        # registered tools by Tool object or by name string. Names are
        # resolved against the merged registry. Missing → MissingToolError.
        self.tool_registry = {}
        for t in tools_source:
            if not isinstance(t, Tool):
                from activegraph.runtime.registration_errors import (
                    InvalidToolRegistration,
                )
                raise InvalidToolRegistration(t)
            self.tool_registry[t.name] = t
            # v0.9: globally-exported pack tools are also registered
            # under their short name.
            if getattr(t, "_export_globally", False):
                short = getattr(t, "_short_name", None) or t.name.split(".", 1)[-1]
                if short != t.name:
                    self.tool_registry[short] = t
        for b in source:
            if not isinstance(b, LLMBehavior):
                continue
            for t in b.tools:
                name = t.name if isinstance(t, Tool) else str(t)
                if name not in self.tool_registry:
                    raise MissingToolError(
                        name,
                        behavior_name=b.name,
                        registered=tuple(self.tool_registry.keys()),
                    )

    def run_goal(self, goal: str, *, actor: str = "user") -> None:
        self._ensure_registry()
        # Stamp the run row's goal (best-effort; only meaningful with a store).
        if self.graph.store is not None and hasattr(self.graph.store, "upsert_run"):
            self.graph.store.upsert_run(
                created_at=_now_iso(),
                goal=goal,
                frame_id=self.frame.id if self.frame else None,
            )
        ev = Event(
            id=self.graph.ids.event(),
            type="goal.created",
            payload={"goal": goal},
            actor=actor,
            frame_id=self.frame.id if self.frame else None,
            caused_by=None,
            timestamp=self.graph.clock.now(),
        )
        self.budget.start()
        self.graph.emit(ev)
        self.run_until_idle()

    def run_until_idle(self) -> None:
        self._ensure_registry()
        if self.budget._start is None:
            self.budget.start()
        self._loop(stop=lambda: False)
        self._emit_idle_or_exhausted()

    def run_until(self, predicate: Callable[[Graph], bool]) -> None:
        self._ensure_registry()
        if self.budget._start is None:
            self.budget.start()
        self._loop(stop=lambda: predicate(self.graph))
        self._emit_idle_or_exhausted()

    # ---------- loop ----------

    def _loop(self, *, stop: Callable[[], bool]) -> None:
        while (self._queue or self._delayed) and self.budget.remaining():
            if stop():
                return
            # Always drain main queue first. Delayed entries are checked
            # after each event tick so they fire at the right moment.
            if self._queue:
                event = self._queue.pop()
                assert event is not None
                self.budget.consume("max_events")
                self._tick += 1
                matches = cast(Registry, self.registry).match(event, self.graph)
                for b, rels, p_matches in matches:
                    if not self.budget.remaining():
                        break
                    # v0.7: activate_after defers invocation. Schedule and
                    # emit `behavior.scheduled` instead of invoking now.
                    if b.activate_after is not None:
                        self._schedule(b, event, p_matches)
                        continue
                    if p_matches and (b.on and b.pattern):
                        self._emit_pattern_matched(b, event, p_matches)
                    elif p_matches and not b.on:
                        # Pattern-only behavior: still emit a marker so
                        # the trace shows what fired.
                        self._emit_pattern_matched(b, event, p_matches)
                    if isinstance(b, RelationBehavior):
                        for r in rels:
                            if not self.budget.remaining():
                                break
                            self._invoke_relation(b, r, event, p_matches)
                    elif isinstance(b, LLMBehavior):
                        self._invoke_llm(b, event, p_matches)
                    else:
                        self._invoke(b, event, p_matches)
            # Drain due delayed entries (after every tick).
            self._fire_due_delayed()

    # ---- v0.7: delayed-queue scheduling (activate_after) -----------------

    def _schedule(
        self,
        behavior: Any,
        event: Event,
        pattern_matches: list[Any],
    ) -> None:
        """Emit behavior.scheduled and push onto the delayed queue."""
        sched_evt = self._emit_lifecycle(
            "behavior.scheduled",
            {
                "behavior": behavior.name,
                "event_id": event.id,
                "activate_after": behavior.activate_after,
                "fire_at_tick": self._tick + behavior.activate_after,
                "current_tick": self._tick,
            },
        )
        self._delayed.push(
            ScheduledEntry(
                behavior_name=behavior.name,
                behavior_index=cast(Registry, self.registry).index_of(behavior),
                triggering_event_id=event.id,
                fire_at_event_count=self._tick + behavior.activate_after,
                where_recheck_path=None,
                scheduled_event_id=sched_evt.id,
            )
        )

    def _fire_due_delayed(self) -> None:
        due = self._delayed.pop_due(self._tick)
        for entry in due:
            if not self.budget.remaining():
                # Re-push and exit — budget exhausted before all due
                # entries fired; preserved for next run_until_idle.
                self._delayed.push(entry)
                break
            behavior = cast(Registry, self.registry).all()[entry.behavior_index]
            # Re-fetch the triggering event so the handler still sees it.
            ev = self._find_event(entry.triggering_event_id)
            if ev is None:
                continue
            # Re-check where= against the LATEST graph state.
            if behavior.where and not _evaluate_where(behavior.where, ev.payload):
                # Silently skip per CONTRACT v0.7 #13. The trace already
                # has the behavior.scheduled event; absence of a
                # behavior.started is sufficient evidence the where
                # didn't hold.
                continue
            # Re-check pattern as well so a stale pattern hit doesn't
            # fire after the graph has moved on. We pass empty matches
            # if there's no pattern.
            p_matches: list[Any] = []
            if behavior.pattern_matcher is not None:
                p_matches = behavior.pattern_matcher.matches(ev, self.graph)
                if not p_matches:
                    continue
            # Dispatch as normal (without re-scheduling — we are AT the
            # fire moment).
            if isinstance(behavior, RelationBehavior):
                # For relation behaviors we'd need to refetch relations.
                # Defer this rare combination to a future enhancement.
                continue
            if isinstance(behavior, LLMBehavior):
                self._invoke_llm(behavior, ev, p_matches)
            else:
                self._invoke(behavior, ev, p_matches)

    def _find_event(self, event_id: str) -> Optional[Event]:
        for e in self.graph.events:
            if e.id == event_id:
                return e
        return None

    def _emit_pattern_matched(self, behavior: Any, event: Event, p_matches: list[Any]) -> None:
        """Emit a pattern.matched marker so the trace shows the bindings."""
        self._emit_lifecycle(
            "pattern.matched",
            {
                "behavior": behavior.name,
                "event_id": event.id,
                "matches_count": len(p_matches),
                "pattern": behavior.pattern,
            },
        )

    # ---------- invocation ----------

    def _invoke(self, b: Behavior, event: Event, matches: Optional[list[Any]] = None) -> None:
        self.budget.consume("max_behavior_calls")
        bgraph = BehaviorGraph(
            self.graph,
            actor=b.name,
            caused_by=event.id,
            frame_id=self.frame.id if self.frame else None,
        )
        view = build_view(b, event, self.graph)
        ctx = Context(
            view=view,
            frame=self.frame,
            policy=self.policy,
            random=self._random,
            clock=self.graph.clock,
            matches=list(matches or []),
            settings=self._pack_settings_for_behavior(b),
            _runtime=self,
        )

        # v0.8: count and time the handler call. Only function behaviors
        # are instrumented here; LLM and relation behaviors have their
        # own invocation paths and their own metrics hooks.
        self.metrics.counter(
            "activegraph_behaviors_invoked_total", {"behavior": b.name}
        )

        self._emit_lifecycle(
            "behavior.started",
            {
                "behavior": b.name,
                "event_id": event.id,
                "triggering_event_type": event.type,
                "triggering_object_id": _maybe_object_id(event),
            },
        )
        _t0 = _monotonic()
        try:
            b.run(event, bgraph, ctx)
        except Exception as e:
            self.metrics.histogram(
                "activegraph_behaviors_duration_seconds",
                {"behavior": b.name},
                _monotonic() - _t0,
            )
            self.metrics.counter(
                "activegraph_behaviors_failed_total",
                {"behavior": b.name, "reason": f"exception.{type(e).__name__}"},
            )
            # v1.0.3 #3: route through _emit_behavior_failed so the
            # WARNING log line and the event emission stay in one
            # place. The previous ERROR log is removed — the
            # centralized emitter handles logging at WARNING for
            # every behavior.failed.
            self._emit_behavior_failed(b.name, event.id, e)
            return

        self.metrics.histogram(
            "activegraph_behaviors_duration_seconds",
            {"behavior": b.name},
            _monotonic() - _t0,
        )
        self._emit_lifecycle(
            "behavior.completed",
            {
                "behavior": b.name,
                "event_id": event.id,
                "objects_created": bgraph.counters.objects_created,
                "relations_created": bgraph.counters.relations_created,
                "patches_applied": bgraph.counters.patches_applied,
                "patches_proposed": bgraph.counters.patches_proposed,
                "events_emitted": bgraph.counters.events_emitted,
            },
        )

    def _invoke_llm(
        self,
        b: LLMBehavior,
        event: Event,
        matches: Optional[list[Any]] = None,
    ) -> None:
        """LLM behavior lifecycle, v0.7 — turn loop over tool calls.

        Order:
          behavior.started
            (loop, up to max_tool_turns):
              assemble prompt   (with tools= if b.tools)
              cache lookup      (LLM cache)
              optional cost gate (if max_cost_usd)
              emit llm.requested
              provider.complete() | cached
              emit llm.responded
              if response.tool_calls: dispatch each tool, append result
                                       to messages, loop again
              else: break with response.parsed
            handler(event, bgraph, ctx, parsed)
          behavior.completed
        """

        self.budget.consume("max_behavior_calls")
        self.budget.consume("max_llm_calls")

        view = build_view(b, event, self.graph)
        bgraph = BehaviorGraph(
            self.graph,
            actor=b.name,
            caused_by=event.id,
            frame_id=self.frame.id if self.frame else None,
        )
        ctx = Context(
            view=view,
            frame=self.frame,
            policy=self.policy,
            random=self._random,
            clock=self.graph.clock,
            llm_provider=self.llm_provider,
            matches=list(matches or []),
            settings=self._pack_settings_for_behavior(b),
            _runtime=self,
        )

        self._emit_lifecycle(
            "behavior.started",
            {
                "behavior": b.name,
                "event_id": event.id,
                "triggering_event_type": event.type,
                "triggering_object_id": _maybe_object_id(event),
            },
        )

        # v0.7: resolve tool objects (decorator may have stored names or
        # objects). Build the provider-facing tool definitions list.
        tools_for_call: list[Tool] = []
        for t in b.tools:
            name = t.name if isinstance(t, Tool) else str(t)
            tool_obj = self.tool_registry.get(name)
            if tool_obj is None:
                self._emit_behavior_failed(
                    b.name,
                    event.id,
                    MissingToolError(name, registered=tuple(self.tool_registry.keys())),
                    reason="tool.unknown_tool",
                    extras={"tool": name},
                )
                return
            tools_for_call.append(tool_obj)
        tool_defs = [t.to_definition() for t in tools_for_call] if tools_for_call else None
        tool_request_event_ids: list[str] = []

        # ---- 1. Assemble base prompt -------------------------------------
        so_mode = self._structured_output_modes.get(b.name, "prompt")
        try:
            prompt = b.build_prompt(
                event,
                self.graph,
                frame=self.frame,
                structured_output_mode=so_mode,
            )
        except Exception as e:  # pragma: no cover — defensive
            self._emit_behavior_failed(
                b.name,
                event.id,
                e,
                reason="llm.prompt_assembly_error",
            )
            return

        # The base prompt has the system text + a single user message
        # assembled from view+event+instruction. The turn loop will
        # append assistant/tool messages as it goes.
        running_messages: list[LLMMessage] = list(prompt.messages)

        # The successful LLM request event id gets stamped onto every
        # object/relation/patch the handler creates. With retry enabled,
        # failed attempts remain in the event log but provenance points
        # at the request whose response actually fed the handler.
        last_llm_request_id: Optional[str] = None
        successful_llm_request_id: Optional[str] = None
        response = None  # final non-tool response

        for turn_idx in range(max(1, b.max_tool_turns)):
            if not self.budget.remaining():
                self._emit_behavior_failed(
                    b.name,
                    event.id,
                    RuntimeError("budget exhausted mid-turn-loop"),
                    reason=_budget_reason(self.budget.exhausted_by()),
                )
                return
            turn_hash = _hash_turn_prompt(
                prompt=prompt,
                messages=running_messages,
                tool_defs=tool_defs,
            )

            # ---- Cache lookup ----------------------------------------------
            cached: Optional[Any] = None
            if self.replay_llm_cache and self._llm_cache is not None:
                cached = self._llm_cache.get(turn_hash)

            # ---- Pre-call cost gate ----------------------------------------
            pre_estimate_cost: Optional[Decimal] = None
            estimated_input_tokens: Optional[int] = None
            if cached is None and self.budget.has_cost_limit():
                try:
                    estimated_input_tokens = cast(LLMProvider, self.llm_provider).count_tokens(
                        system=prompt.system,
                        messages=running_messages,
                        model=prompt.model,
                    )
                except Exception as e:
                    self._emit_behavior_failed(
                        b.name,
                        event.id,
                        e,
                        reason="llm.network_error",
                        extras={"model": prompt.model, "phase": "count_tokens"},
                    )
                    return
                pre_estimate_cost = cast(LLMProvider, self.llm_provider).estimate_cost(
                    input_tokens=estimated_input_tokens,
                    output_tokens=prompt.max_tokens,
                    model=prompt.model,
                )
                if not self.budget.cost_remaining(pre_estimate_cost):
                    self._emit_behavior_failed(
                        b.name,
                        event.id,
                        RuntimeError("max_cost_usd would be exceeded"),
                        reason="budget.cost_exhausted",
                        extras={
                            "estimated_cost_usd": str(pre_estimate_cost),
                            "budget_remaining_usd": str(
                                self.budget.cost_remaining_amount()
                            ),
                            "model": prompt.model,
                        },
                    )
                    return

            # ---- Get the response (cache or provider, with retry) ----------
            turn_response = None
            max_attempts = 1 if cached is not None else self.llm_retry_max_attempts
            first_attempt_request_id: Optional[str] = None
            retry_caused_by: Optional[str] = None
            for attempt_index in range(max_attempts):
                if attempt_index > 0 and not self.budget.remaining():
                    self._emit_behavior_failed(
                        b.name,
                        event.id,
                        RuntimeError("budget exhausted before LLM retry"),
                        reason=_budget_reason(self.budget.exhausted_by()),
                        extras={
                            "model": prompt.model,
                            "attempt_index": attempt_index,
                            "max_attempts": max_attempts,
                        },
                    )
                    return

                requested_payload: dict[str, Any] = {
                    "behavior": b.name,
                    "model": prompt.model,
                    "prompt_hash": turn_hash,
                    "deterministic": prompt.deterministic,
                    "cache_hit": cached is not None,
                    "turn_index": turn_idx,
                    # CONTRACT v0.6 follow-up: surface the prompt-normalized
                    # flag explicitly so trace consumers can see that
                    # volatile-field stripping ran (always true in v0.7).
                    "prompt_normalized": True,
                }
                if b.output_schema is not None:
                    # CONTRACT v1.3 #1 #4: fallback is silent-but-audited;
                    # the resolved mode rides every structured-output
                    # request event.
                    requested_payload["structured_output_mode"] = so_mode
                if attempt_index > 0:
                    requested_payload["attempt_index"] = attempt_index
                    requested_payload["max_attempts"] = max_attempts
                    if first_attempt_request_id is not None:
                        requested_payload["retry_of"] = first_attempt_request_id
                if turn_idx == 0 and attempt_index == 0:
                    # Only include full prompt body on turn 0's first attempt;
                    # subsequent turns/retries can be reconstructed from
                    # messages plus the shared prompt hash.
                    requested_payload["prompt"] = prompt.to_hashable()
                if estimated_input_tokens is not None:
                    requested_payload["estimated_input_tokens"] = estimated_input_tokens
                if pre_estimate_cost is not None:
                    requested_payload["estimated_cost_usd"] = str(pre_estimate_cost)
                if self.budget.has_cost_limit():
                    remaining = self.budget.cost_remaining_amount()
                    requested_payload["budget_remaining_usd"] = (
                        str(remaining) if remaining is not None else None
                    )
                requested_evt = self._emit_llm_event(
                    "llm.requested",
                    requested_payload,
                    actor=b.name,
                    caused_by=(
                        retry_caused_by
                        if retry_caused_by is not None
                        else event.id if turn_idx == 0 else last_llm_request_id
                    ),
                )
                if first_attempt_request_id is None:
                    first_attempt_request_id = requested_evt.id
                last_llm_request_id = requested_evt.id

                # ---- Strict-replay hash check -----------------------------
                if (
                    attempt_index == 0
                    and self.replay_strict
                    and self._strict_expected_hashes is not None
                ):
                    expected = (
                        self._strict_expected_hashes.pop(0)
                        if self._strict_expected_hashes
                        else None
                    )
                    if expected is not None and expected != turn_hash:
                        raise ReplayDivergenceError(
                            event_id=requested_evt.id,
                            expected=f"prompt_hash={expected}",
                            actual=f"prompt_hash={turn_hash}",
                        )

                if cached is not None:
                    turn_response = cached
                    successful_llm_request_id = requested_evt.id
                    break

                try:
                    call_t0 = _monotonic()
                    # CONTRACT v1.3 #1 #3: the kwarg is passed only when
                    # native resolved, so prompt-mode calls stay
                    # byte-identical to pre-v1.3 (providers that never
                    # claim the capability never see the parameter).
                    native_kwargs: dict[str, Any] = (
                        {"structured_output_mode": "native"}
                        if so_mode == "native"
                        else {}
                    )
                    turn_response = cast(LLMProvider, self.llm_provider).complete(
                        system=prompt.system,
                        messages=running_messages,
                        model=prompt.model,
                        max_tokens=prompt.max_tokens,
                        temperature=prompt.temperature,
                        top_p=prompt.top_p,
                        output_schema=b.output_schema,
                        timeout_seconds=b.timeout_seconds,
                        tools=tool_defs,
                        **native_kwargs,
                    )
                except LLMBehaviorError as e:
                    latency = _monotonic() - call_t0
                    retryable = _is_transient_llm_reason(e.reason)
                    error_evt = self._emit_llm_error_response(
                        behavior_name=b.name,
                        requested_evt=requested_evt,
                        prompt_hash=turn_hash,
                        model=prompt.model,
                        reason=e.reason,
                        message=str(e),
                        payload_extras=e.payload_extras,
                        attempt_index=attempt_index,
                        max_attempts=max_attempts,
                        retryable=retryable,
                        latency_seconds=latency,
                    )
                    if retryable and attempt_index + 1 < max_attempts:
                        delay = _llm_retry_delay_seconds(
                            attempt_index=attempt_index,
                            initial=self.llm_retry_initial_delay_seconds,
                            maximum=self.llm_retry_max_delay_seconds,
                            payload_extras=e.payload_extras,
                        )
                        retry_caused_by = error_evt.id
                        if delay > 0:
                            _time.sleep(delay)
                        continue
                    extras = dict(e.payload_extras)
                    if retryable:
                        extras.update(
                            {
                                "attempts": attempt_index + 1,
                                "max_attempts": max_attempts,
                                "retry_exhausted": True,
                            }
                        )
                    self._emit_behavior_failed(
                        b.name, event.id, e, reason=e.reason, extras=extras,
                    )
                    return
                except Exception as e:
                    latency = _monotonic() - call_t0
                    extras = {"model": prompt.model}
                    error_evt = self._emit_llm_error_response(
                        behavior_name=b.name,
                        requested_evt=requested_evt,
                        prompt_hash=turn_hash,
                        model=prompt.model,
                        reason="llm.network_error",
                        message=str(e),
                        payload_extras=extras,
                        attempt_index=attempt_index,
                        max_attempts=max_attempts,
                        retryable=True,
                        latency_seconds=latency,
                    )
                    if attempt_index + 1 < max_attempts:
                        delay = _llm_retry_delay_seconds(
                            attempt_index=attempt_index,
                            initial=self.llm_retry_initial_delay_seconds,
                            maximum=self.llm_retry_max_delay_seconds,
                            payload_extras=extras,
                        )
                        retry_caused_by = error_evt.id
                        if delay > 0:
                            _time.sleep(delay)
                        continue
                    self._emit_behavior_failed(
                        b.name, event.id, e,
                        reason="llm.network_error",
                        extras={
                            "model": prompt.model,
                            "attempts": attempt_index + 1,
                            "max_attempts": max_attempts,
                            "retry_exhausted": True,
                        },
                    )
                    return
                successful_llm_request_id = requested_evt.id
                self._llm_cache.record(
                    turn_hash, turn_response, requesting_event_id=requested_evt.id
                ) if self._llm_cache is not None else None
                if self._llm_cache is None:
                    self._llm_cache = LLMCache()
                    self._llm_cache.record(
                        turn_hash, turn_response, requesting_event_id=requested_evt.id
                    )
                self.budget.add_cost(turn_response.cost_usd)
                break

            if turn_response is None:
                return

            # ---- Emit llm.responded ---------------------------------------
            responded_payload = turn_response.to_dict() | {
                "behavior": b.name,
                "prompt_hash": turn_hash,
                "turn_index": turn_idx,
            }
            self._emit_llm_event(
                "llm.responded",
                responded_payload,
                actor=b.name,
                caused_by=requested_evt.id,
            )

            # ---- Branch: tool calls vs final response ---------------------
            # `tool_calls` is optional on LLMResponse; some test providers
            # return ad-hoc objects without it. Treat missing/None/empty
            # as "no tool calls" so backward compatibility holds.
            response_tool_calls = getattr(turn_response, "tool_calls", None) or []
            if not response_tool_calls:
                response = turn_response
                break

            # Tool calls. Append the assistant turn to messages, then
            # dispatch each tool. v1.0.3 #4: the assistant turn must
            # carry both the text and the tool_use blocks. Anthropic
            # requires every following tool_result block to reference
            # a tool_use_id from the preceding assistant message; the
            # Vertex AI proxy enforces this strictly (HTTP 400 without
            # matching blocks), and the direct API tolerates the raw-
            # text-only form for now but is expected to tighten. The
            # provider adapter reconstructs the wire-format content
            # blocks from `tool_calls`.
            running_messages.append(
                LLMMessage(
                    role="assistant",
                    content=turn_response.raw_text or "",
                    tool_calls=tuple(response_tool_calls),
                )
            )
            for call in response_tool_calls:
                # CONTRACT v0.7 #6: budget enforcement BEFORE invocation
                # so an exhausted budget fails the behavior, doesn't
                # silently no-op. Check the tool-call counter FIRST so
                # we surface the specific v0.7 reason code rather than
                # the generic budget name.
                limit = self.budget.limits.get("max_tool_calls", float("inf"))
                if self.budget.used.get("max_tool_calls", 0.0) >= limit:
                    self._emit_behavior_failed(
                        b.name, event.id,
                        RuntimeError("max_tool_calls exhausted"),
                        reason="budget.tool_calls_exhausted",
                        extras={"tool": call.name},
                    )
                    return
                if not self.budget.remaining():
                    self._emit_behavior_failed(
                        b.name, event.id,
                        RuntimeError("budget exhausted before tool call"),
                        reason=_budget_reason(self.budget.exhausted_by()),
                        extras={"tool": call.name},
                    )
                    return
                # Tool must have been declared by the behavior.
                if not any(
                    (isinstance(t, Tool) and t.name == call.name)
                    or (isinstance(t, str) and t == call.name)
                    for t in b.tools
                ):
                    declared = tuple(
                        t if isinstance(t, str) else getattr(t, "name", repr(t))
                        for t in (b.tools or [])
                    )
                    self._emit_behavior_failed(
                        b.name, event.id,
                        UnknownToolError(
                            f"LLM called tool {call.name!r} which is not "
                            f"declared on @llm_behavior(tools=[...])",
                            tool_name=call.name,
                            behavior_name=b.name,
                            declared_tools=declared,
                        ),
                        reason="tool.unknown_tool",
                        extras={"tool": call.name},
                    )
                    return
                tool_obj = self.tool_registry[call.name]
                tr_id = self._invoke_tool(
                    behavior=b,
                    event=event,
                    tool=tool_obj,
                    call=call,
                    last_llm_request_id=requested_evt.id,
                    ctx_frame=self.frame,
                )
                if tr_id is None:
                    # tool failed — wrapper already emitted behavior.failed.
                    return
                tool_request_event_ids.append(tr_id)
                # Append tool result back into the conversation so the
                # next turn sees it. The result message was stashed by
                # _invoke_tool on self._last_tool_result_message.
                if self._last_tool_result_message is not None:
                    running_messages.append(self._last_tool_result_message)
                    self._last_tool_result_message = None

            # All tool calls succeeded; loop again with new messages.
            continue
        else:
            # for/else: we ran out of turns without a final response.
            self._emit_behavior_failed(
                b.name,
                event.id,
                RuntimeError(
                    f"exceeded max_tool_turns={b.max_tool_turns} without "
                    f"a non-tool response"
                ),
                reason="tool.max_turns_exhausted",
                extras={"max_tool_turns": b.max_tool_turns},
            )
            return

        # ---- Hydrate parsed output (cache may have dicts) -----------------
        if (
            b.output_schema is not None
            and response.parsed is not None
            and not isinstance(response.parsed, b.output_schema)
        ):
            try:
                response.parsed = b.output_schema.model_validate(response.parsed)
            except Exception as e:
                self._emit_behavior_failed(
                    b.name,
                    event.id,
                    e,
                    reason="llm.schema_violation",
                    extras={
                        "raw_text": response.raw_text,
                        "schema": b.output_schema.__name__,
                        "validation_errors": str(e),
                        "from_cache": True,
                    },
                )
                return

        # ---- Validate output ---------------------------------------------
        if b.output_schema is not None and response.parsed is None:
            self._emit_behavior_failed(
                b.name,
                event.id,
                LLMBehaviorError(
                    "llm.parse_error",
                    "provider returned no parsed output despite output_schema",
                ),
                reason="llm.parse_error",
                extras={
                    "raw_text": response.raw_text,
                    "schema": b.output_schema.__name__,
                },
            )
            return

        # ---- Invoke developer handler with provenance stamping -----------
        bgraph._llm_request_event_id = successful_llm_request_id  # noqa: SLF001
        bgraph._tool_request_event_ids = list(tool_request_event_ids)  # noqa: SLF001
        try:
            cast("Callable[..., None]", b.handler)(event, bgraph, ctx, response.parsed)
        except LLMBehaviorError as e:
            self._emit_behavior_failed(
                b.name, event.id, e, reason=e.reason,
                extras=e.payload_extras,
            )
            return
        except Exception as e:
            self._emit_behavior_failed(b.name, event.id, e)
            return

        self._emit_lifecycle(
            "behavior.completed",
            {
                "behavior": b.name,
                "event_id": event.id,
                "objects_created": bgraph.counters.objects_created,
                "relations_created": bgraph.counters.relations_created,
                "patches_applied": bgraph.counters.patches_applied,
                "patches_proposed": bgraph.counters.patches_proposed,
                "events_emitted": bgraph.counters.events_emitted,
                "tool_calls": len(tool_request_event_ids),
            },
        )

    # ---- v0.7: single tool invocation ------------------------------------

    def _invoke_tool(
        self,
        *,
        behavior: LLMBehavior,
        event: Event,
        tool: Tool,
        call: ToolCall,
        last_llm_request_id: str,
        ctx_frame: Optional[Frame],
    ) -> Optional[str]:
        """Dispatch a single tool call. Emits tool.requested + tool.responded.

        Returns the `tool.requested` event id on success; None on
        failure (caller has already emitted behavior.failed).
        """
        import logging
        import uuid

        self.budget.consume("max_tool_calls")
        args_hash = hash_tool_call(tool_name=tool.name, args=call.args)

        # Validate input
        try:
            if tool.input_schema is not None:
                input_obj = tool.input_schema.model_validate(call.args)
            else:
                input_obj = call.args
        except Exception as e:
            # Emit a tool.requested + tool.responded(error=...) pair
            # before bubbling the failure, so the trace is complete.
            req_evt = self._emit_tool_event(
                "tool.requested",
                {
                    "behavior": behavior.name,
                    "tool": tool.name,
                    "args_hash": args_hash,
                    "args": canonicalize_args(call.args),
                    "call_id": call.id,
                    "cache_hit": False,
                },
                actor=behavior.name,
                caused_by=last_llm_request_id,
            )
            err = {"reason": "tool.invalid_input", "message": str(e)}
            self._emit_tool_event(
                "tool.responded",
                {
                    "behavior": behavior.name,
                    "tool": tool.name,
                    "args_hash": args_hash,
                    "error": err,
                    "cache_hit": False,
                    "latency_seconds": 0.0,
                    "cost_usd": "0",
                },
                actor=behavior.name,
                caused_by=req_evt.id,
            )
            self._emit_behavior_failed(
                behavior.name, event.id, e,
                reason="tool.invalid_input",
                extras={"tool": tool.name, "validation_errors": str(e)},
            )
            return None

        # Cache lookup
        cached_tool: Optional[CachedToolResponse] = None
        if self.replay_tool_cache:
            cached_tool = self._tool_cache.get(args_hash)
            # Determinism opt-in: re-invoke deterministic tools instead
            # of serving from cache.
            if cached_tool is not None and tool.deterministic and self.replay_reinvoke_deterministic:
                cached_tool = None

        # Cost gate (tools and LLM share max_cost_usd budget)
        if cached_tool is None and self.budget.has_cost_limit():
            if not self.budget.cost_remaining(tool.cost_per_call):
                self._emit_behavior_failed(
                    behavior.name, event.id,
                    RuntimeError("max_cost_usd would be exceeded by tool"),
                    reason="budget.cost_exhausted",
                    extras={
                        "tool": tool.name,
                        "estimated_cost_usd": str(tool.cost_per_call),
                    },
                )
                return None

        # Emit tool.requested
        req_evt = self._emit_tool_event(
            "tool.requested",
            {
                "behavior": behavior.name,
                "tool": tool.name,
                "args_hash": args_hash,
                "args": canonicalize_args(call.args),
                "call_id": call.id,
                "cache_hit": cached_tool is not None,
                "deterministic": tool.deterministic,
            },
            actor=behavior.name,
            caused_by=last_llm_request_id,
        )

        if cached_tool is not None:
            tool_response = cached_tool
        else:
            tool_ctx = ToolContext(
                behavior_name=behavior.name,
                event_id=event.id,
                frame=ctx_frame,
                idempotency_key=str(uuid.uuid4()),
                timeout_seconds=tool.timeout_seconds,
                logger=logging.getLogger(f"activegraph.tools.{tool.name}"),
            )
            try:
                tool_response = self._tool_invoker.invoke(tool, input_obj, tool_ctx)
            except ToolError as e:
                self._emit_tool_event(
                    "tool.responded",
                    {
                        "behavior": behavior.name,
                        "tool": tool.name,
                        "args_hash": args_hash,
                        "error": {
                            "reason": e.reason,
                            "message": str(e),
                            **e.payload_extras,
                        },
                        "cache_hit": False,
                        "latency_seconds": 0.0,
                        "cost_usd": "0",
                    },
                    actor=behavior.name,
                    caused_by=req_evt.id,
                )
                self._emit_behavior_failed(
                    behavior.name, event.id, e,
                    reason=e.reason,
                    extras={"tool": tool.name, **e.payload_extras},
                )
                return None
            self._tool_cache.record(
                args_hash, tool_response, requesting_event_id=req_evt.id
            )
            self.budget.add_cost(tool_response.cost_usd)

        # Validate output (if schema)
        validated_output = tool_response.output
        if tool.output_schema is not None and tool_response.output is not None:
            try:
                model_inst = tool.output_schema.model_validate(tool_response.output)
                dump = getattr(model_inst, "model_dump", None)
                if callable(dump):
                    try:
                        validated_output = dump(mode="json")
                    except TypeError:
                        validated_output = dump()
            except Exception as e:
                self._emit_tool_event(
                    "tool.responded",
                    {
                        "behavior": behavior.name,
                        "tool": tool.name,
                        "args_hash": args_hash,
                        "error": {
                            "reason": "tool.invalid_output",
                            "message": str(e),
                        },
                        "cache_hit": tool_response.cache_hit,
                        "latency_seconds": tool_response.latency_seconds,
                        "cost_usd": str(tool_response.cost_usd),
                    },
                    actor=behavior.name,
                    caused_by=req_evt.id,
                )
                self._emit_behavior_failed(
                    behavior.name, event.id, e,
                    reason="tool.invalid_output",
                    extras={
                        "tool": tool.name,
                        "validation_errors": str(e),
                    },
                )
                return None

        # Emit tool.responded
        self._emit_tool_event(
            "tool.responded",
            {
                "behavior": behavior.name,
                "tool": tool.name,
                "args_hash": args_hash,
                "output": validated_output,
                "error": None,
                "cache_hit": tool_response.cache_hit,
                "latency_seconds": tool_response.latency_seconds,
                "cost_usd": str(tool_response.cost_usd),
                "deterministic": tool.deterministic,
            },
            actor=behavior.name,
            caused_by=req_evt.id,
        )

        # Echo the tool result back into the message stream so the
        # next LLM turn can see it.
        import json as _json
        running_messages_append = getattr(self, "_current_running_messages", None)
        # NOTE: we use the caller's local running_messages via closure;
        # to avoid threading it through, the caller appends after this.
        # Stash the tool-result content on the request event payload
        # for now so the caller can fetch it. Simpler: return a payload.

        # We append in the caller — but we need to return the content
        # too. Refactor: pass running_messages by reference via mutating
        # method. Cleanest: do it here using a passed list.
        # (Inlined below by storing in self._last_tool_result_message.)
        self._last_tool_result_message = LLMMessage(
            role="tool",
            content=_json.dumps(validated_output, sort_keys=True, default=str),
            tool_use_id=call.id,
            tool_name=tool.name,
        )
        return req_evt.id

    def _emit_tool_event(
        self,
        type_: str,
        payload: dict[str, Any],
        *,
        actor: str,
        caused_by: Optional[str],
    ) -> Event:
        ev = Event(
            id=self.graph.ids.event(),
            type=type_,
            payload=payload,
            actor=actor,
            frame_id=self.frame.id if self.frame else None,
            caused_by=caused_by,
            timestamp=self.graph.clock.now(),
        )
        self.graph.emit(ev)
        return ev

    def _emit_llm_event(
        self,
        type_: str,
        payload: dict[str, Any],
        *,
        actor: str,
        caused_by: Optional[str],
    ) -> Event:
        ev = Event(
            id=self.graph.ids.event(),
            type=type_,
            payload=payload,
            actor=actor,
            frame_id=self.frame.id if self.frame else None,
            caused_by=caused_by,
            timestamp=self.graph.clock.now(),
        )
        self.graph.emit(ev)
        return ev

    def _emit_llm_error_response(
        self,
        *,
        behavior_name: str,
        requested_evt: Event,
        prompt_hash: str,
        model: str,
        reason: str,
        message: str,
        payload_extras: dict[str, Any],
        attempt_index: int,
        max_attempts: int,
        retryable: bool,
        latency_seconds: float,
    ) -> Event:
        """Emit the response-side audit record for a failed LLM attempt.

        Successful calls already emit ``llm.responded`` from
        :class:`LLMResponse.to_dict`. Transient failures need the same
        request/response shape so operators can distinguish "the provider
        failed" from "the provider returned an empty extraction" when
        inspecting long-running cache builds.
        """

        error = {
            "reason": reason,
            "message": message,
            **dict(payload_extras or {}),
        }
        return self._emit_llm_event(
            "llm.responded",
            {
                "behavior": behavior_name,
                "prompt_hash": prompt_hash,
                "model": model,
                "error": error,
                "cache_hit": False,
                "retryable": retryable,
                "attempt_index": attempt_index,
                "max_attempts": max_attempts,
                "latency_seconds": latency_seconds,
                "cost_usd": "0",
            },
            actor=behavior_name,
            caused_by=requested_evt.id,
        )

    def _emit_behavior_failed(
        self,
        behavior_name: str,
        event_id: str,
        exc: BaseException,
        *,
        reason: Optional[str] = None,
        extras: Optional[dict[str, Any]] = None,
    ) -> None:
        """Centralized behavior.failed emission. CONTRACT #13 plus
        v0.6 #11 (optional `reason` + LLM-specific extras).

        v1.0.3 #3: emits a WARNING log line so callers running
        ``runtime.run_goal()`` see failures without subscribing to
        ``behavior.failed`` or inspecting ``graph._events``. Users
        opt out via standard ``logging.getLogger('activegraph.runtime')``
        configuration. Every ``behavior.failed`` emission routes
        through this method so exactly one log line is produced per
        failure at one consistent level.
        """

        tb_str = "".join(
            traceback.format_exception(type(exc), exc, exc.__traceback__)
        ) if exc.__traceback__ is not None else traceback.format_exc()
        payload: dict[str, Any] = {
            "behavior": behavior_name,
            "event_id": event_id,
            "exception_type": type(exc).__name__,
            "message": str(exc),
            "traceback": tb_str,
        }
        if reason is not None:
            payload["reason"] = reason
        if extras:
            for k, v in extras.items():
                if k not in payload:
                    payload[k] = v
        # v1.0.3 #3: WARNING log with doc_url pointing at the reason's
        # documentation page. Slug is class-level (the v1.0 #4 More:
        # URL convention) — LLMBehaviorError for llm.*, ToolError for
        # tool.*, ConfigurationError for budget.*, else the generic
        # execution-error page.
        log_reason = reason or f"exception.{type(exc).__name__}"
        self._log.warning(
            f"behavior failed: {behavior_name} (reason={log_reason})",
            extra=runtime_log_extra(
                run_id=self.graph.run_id,
                event_id=event_id,
                behavior=behavior_name,
                reason=log_reason,
                error_type=type(exc).__name__,
                error_message=str(exc),
                doc_url=_doc_url_for_reason(log_reason),
            ),
        )
        self._emit_lifecycle("behavior.failed", payload)

    def _invoke_relation(
        self,
        b: RelationBehavior,
        relation: Any,
        event: Event,
        matches: Optional[list[Any]] = None,
    ) -> None:
        self.budget.consume("max_behavior_calls")
        bgraph = BehaviorGraph(
            self.graph,
            actor=b.name,
            caused_by=event.id,
            frame_id=self.frame.id if self.frame else None,
        )
        view = build_view(b, event, self.graph)
        ctx = Context(
            view=view,
            frame=self.frame,
            policy=self.policy,
            random=self._random,
            clock=self.graph.clock,
            matches=list(matches or []),
            settings=self._pack_settings_for_behavior(b),
            _runtime=self,
        )

        self._emit_lifecycle(
            "relation_behavior.started",
            {
                "behavior": b.name,
                "event_id": event.id,
                "triggering_event_type": event.type,
                "relation_type": b.relation_type,
                "relation_id": relation.id,
            },
        )
        try:
            b.run(relation, event, bgraph, ctx)
        except Exception as e:
            # v1.0.3 #3: route through _emit_behavior_failed so
            # relation-behavior failures go through the same WARNING
            # log + event emission path as function and LLM behaviors.
            self._emit_behavior_failed(b.name, event.id, e)
            return

        self._emit_lifecycle(
            "behavior.completed",
            {
                "behavior": b.name,
                "event_id": event.id,
                "objects_created": bgraph.counters.objects_created,
                "relations_created": bgraph.counters.relations_created,
                "patches_applied": bgraph.counters.patches_applied,
                "patches_proposed": bgraph.counters.patches_proposed,
                "events_emitted": bgraph.counters.events_emitted,
            },
        )

    # ---------- v0.8: runtime.status ----------

    def status(self, recent: int = 20) -> RuntimeStatus:
        """Frozen snapshot of the runtime. CONTRACT v0.8 #11.

        Cheap to call. No graph traversal beyond a tail-slice of the
        event log. Returns immutable data; mutating any field raises.

        ``recent`` controls the length of the ``recent_events`` tail.
        The CLI's ``inspect --tail N`` passes through.
        """
        if recent < 0:
            from activegraph.runtime.config_errors import InvalidRuntimeConfiguration
            raise InvalidRuntimeConfiguration(
                f"runtime.status(recent={recent}) — recent must be >= 0",
                what_failed=(
                    f"runtime.status(recent={recent}) was called with a "
                    f"negative count. The `recent` argument controls the "
                    f"length of the `recent_events` tail in the status "
                    f"snapshot."
                ),
                why=(
                    "A negative recent count has no defined semantics — "
                    "the tail length is a non-negative integer by "
                    "construction. The framework refuses the call rather "
                    "than silently coerce to zero, because the caller's "
                    "intent is ambiguous (did they mean zero? did they "
                    "compute the value and end up with a negative? did "
                    "they want 'all events' and pass -1 from another "
                    "API's convention?)."
                ),
                how_to_fix=(
                    "Pass a non-negative integer:\n"
                    "    rt.status(recent=20)    # last 20 events\n"
                    "    rt.status(recent=0)     # no recent events in the\n"
                    "                            # snapshot (just totals)\n"
                    "\n"
                    "To get every event, read `rt.graph.events` directly "
                    "rather than passing a large `recent`."
                ),
                context={"recent": recent},
            )
        snap = self.budget.snapshot()
        budget_snap = BudgetSnapshot(
            used=dict(snap.get("used") or {}),
            limits=dict(snap.get("limits") or {}),
            cost_used_usd=str(snap.get("cost_used_usd", "0")),
            cost_limit_usd=snap.get("cost_limit_usd"),
            exhausted_by=self.budget.exhausted_by(),
        )

        # State derivation: log-based, so a freshly loaded runtime and
        # the runtime that saved the log agree. Walk back through the
        # event log for the most recent terminal lifecycle event.
        # CONTRACT v0.8 #11.
        state: str = "stopped"
        for ev in reversed(self.graph.events):
            t = ev.type
            if t == "runtime.budget_exhausted":
                state = "exhausted"
                break
            if t == "runtime.idle":
                state = "idle"
                break

        frame_snap: Optional[FrameSnapshot] = None
        if self.frame is not None:
            frame_snap = FrameSnapshot(
                id=self.frame.id,
                name=getattr(self.frame, "name", None),
            )

        # Behaviors: only enumerable if we've built the registry. Pre-run
        # status() calls work fine but show an empty list (which is what
        # the operator should see — registry isn't materialized yet).
        b_infos: list[BehaviorInfo] = []
        if self.registry is not None:
            for b in self.registry.all():
                kind = "function"
                if isinstance(b, RelationBehavior):
                    kind = "relation"
                elif isinstance(b, LLMBehavior):
                    kind = "llm"
                b_infos.append(
                    BehaviorInfo(
                        name=b.name,
                        kind=kind,
                        subscribed_to=tuple(b.on or ()),
                        pattern=getattr(b, "pattern", None),
                        activate_after=getattr(b, "activate_after", None),
                    )
                )

        events = self.graph.events
        tail = events[-recent:] if recent > 0 else []
        e_summaries = tuple(
            EventSummary(
                id=e.id, type=e.type, actor=e.actor, timestamp=e.timestamp
            )
            for e in tail
        )

        return RuntimeStatus(
            run_id=self.graph.run_id,
            state=state,  # type: ignore[arg-type]
            queue_depth=len(self._queue),
            events_processed=len(events),
            budget=budget_snap,
            frame=frame_snap,
            registered_behaviors=tuple(b_infos),
            recent_events=e_summaries,
        )

    # ---------- lifecycle / idle emit (bypass queue) ----------

    def _emit_lifecycle(self, type_: str, payload: dict[str, Any]) -> Event:
        ev = Event(
            id=self.graph.ids.event(),
            type=type_,
            payload=payload,
            actor="runtime",
            frame_id=self.frame.id if self.frame else None,
            caused_by=payload.get("event_id"),
            timestamp=self.graph.clock.now(),
        )
        self.graph.emit(ev)
        return ev

    def _emit_idle_or_exhausted(self) -> None:
        if self._idle_emitted:
            return
        if not self.budget.remaining():
            self._emit_lifecycle(
                "runtime.budget_exhausted",
                {
                    "exhausted_by": self.budget.exhausted_by(),
                    "snapshot": self.budget.snapshot(),
                },
            )
        else:
            self._emit_lifecycle(
                "runtime.idle",
                {"snapshot": self.budget.snapshot()},
            )
        self._idle_emitted = True

    # ---------- trace + graph dump (delegate to trace module) ----------

    # ---------- v0.9: pack public API ----------

    def load_pack(self, pack: Any, settings: Any = None) -> bool:
        """Load a pack into the runtime.

        Returns True on first load, False if the same `(name, version)`
        was already loaded (CONTRACT v0.9 #6 idempotency). Raises
        `PackVersionConflictError` for name-match-version-mismatch and
        `PackConflictError` for any contributor name collision.
        Pre-mutation: a failed load leaves the runtime exactly as it was.
        """
        from activegraph.packs.loader import load_pack_into_runtime
        loaded: bool = load_pack_into_runtime(self, pack, settings=settings)
        return loaded

    def loaded_packs(self) -> list[Any]:
        """List of currently-loaded packs."""
        if self._pack_state is None:
            return []
        return list(self._pack_state.loaded_packs.values())

    def get_behavior(self, name: str) -> Any:
        """Look up a registered behavior by canonical or short name.

        Short names resolve when unambiguous (load-time conflict check
        guarantees this invariant). Raises `LookupError` if not found
        or `ValueError` if ambiguous. CONTRACT v0.9 #8.
        """
        from activegraph.runtime.registration_errors import (
            AmbiguousBehaviorError,
            BehaviorNotFoundError,
        )
        # Canonical lookup first
        if "." in name:
            for b in self._pack_behaviors:
                if b.name == name:
                    return b
            raise BehaviorNotFoundError(
                name, registered=tuple(b.name for b in self._pack_behaviors),
            )
        # Short name
        if self._pack_state is None:
            raise BehaviorNotFoundError(
                name, registered=tuple(b.name for b in self._pack_behaviors),
                pack_state=False,
            )
        from activegraph.packs.loader import AMBIGUOUS
        canonical = self._pack_state.behavior_short_to_canonical.get(name)
        if canonical is None:
            raise BehaviorNotFoundError(
                name,
                registered=tuple(b.name for b in self._pack_behaviors),
                pack_state=True,
            )
        if canonical == AMBIGUOUS:
            # Find which packs collide on this short name.
            owners = tuple(
                p for c, p in self._pack_state.behavior_owners.items()
                if c.endswith(f".{name}")
            )
            raise AmbiguousBehaviorError(name, packs=owners)
        return self.get_behavior(canonical)

    def get_tool(self, name: str) -> Tool:
        """Look up a registered tool by canonical or short name.

        Same resolution rule as `get_behavior`. CONTRACT v0.9 #8 / #9.
        """
        from activegraph.runtime.registration_errors import (
            AmbiguousToolError,
            ToolNotFoundError,
        )
        if "." in name:
            t = self.tool_registry.get(name)
            if t is None:
                raise ToolNotFoundError(
                    name, registered=tuple(self.tool_registry.keys()),
                )
            return t
        # Short name
        if self._pack_state is None:
            t = self.tool_registry.get(name)
            if t is None:
                raise ToolNotFoundError(
                    name, registered=tuple(self.tool_registry.keys()),
                )
            return t
        from activegraph.packs.loader import AMBIGUOUS
        canonical = self._pack_state.tool_short_to_canonical.get(name)
        if canonical is None:
            # Maybe a globally-exported tool registered under its short name
            t = self.tool_registry.get(name)
            if t is None:
                raise ToolNotFoundError(
                    name, registered=tuple(self.tool_registry.keys()),
                )
            return t
        if canonical == AMBIGUOUS:
            owners = tuple(
                p for c, p in self._pack_state.tool_owners.items()
                if c.endswith(f".{name}")
            )
            raise AmbiguousToolError(name, packs=owners)
        return self.tool_registry[canonical]

    def _pack_settings_for_behavior(self, b: Any) -> Any:
        """Return the settings instance for the pack that owns `b`, or
        None if `b` is not pack-owned.
        """
        if self._pack_state is None:
            return None
        owner = getattr(b, "_pack_owner", None)
        if owner is None:
            return None
        return self._pack_state.pack_settings.get(owner)

    # ---------- v0.9: approval flow ----------

    def pending_approvals(self) -> list[Any]:
        """List of currently-pending approvals (in creation order)."""
        if self._pack_state is None:
            return []
        return list(self._pack_state.pending_approvals)

    def _add_pending_approval(
        self, *, object_type: str, data: dict[str, Any], reason: str = ""
    ) -> str:
        """Internal: create a PendingApproval and return its id.

        Called from `ctx.propose_object(...)`. The id is later used by
        `runtime.approve(id)` to materialize the deferred object.
        """
        from activegraph.packs import PendingApproval
        from activegraph.packs.loader import _ensure_pack_state

        state = _ensure_pack_state(self)
        # Find the pack that gates this object type, if any.
        gating = state.gated_object_types.get(object_type, [])
        owner_pack = gating[0].split(".", 1)[0] if gating else ""
        n = state._next_approval_n
        state._next_approval_n += 1
        approval_id = f"approval_{n:03d}"
        pa = PendingApproval(
            id=approval_id,
            kind="object",
            object_type=object_type,
            data=dict(data),
            reason=reason,
            pack=owner_pack,
        )
        state.pending_approvals.append(pa)
        # Emit an event so the trace shows the proposal.
        self.graph.emit(
            Event(
                id=self.graph.ids.event(),
                type="approval.proposed",
                payload={
                    "approval_id": approval_id,
                    "object_type": object_type,
                    "reason": reason,
                    "pack": owner_pack,
                },
                actor="runtime",
                frame_id=self.frame.id if self.frame else None,
                caused_by=None,
                timestamp=self.graph.clock.now(),
            )
        )
        return approval_id

    def approve(self, approval_id: str, approved_by: Optional[str] = None) -> str:
        """Materialize a pending approval. Returns the new object id.

        Raises `LookupError` if `approval_id` is not pending. Emits an
        `approval.granted` event followed by the deferred `object.created`.
        """
        from activegraph.runtime.exec_errors import ApprovalNotFoundError
        if self._pack_state is None:
            raise ApprovalNotFoundError(approval_id, pending_count=0)
        for i, pa in enumerate(self._pack_state.pending_approvals):
            if pa.id != approval_id:
                continue
            self._pack_state.pending_approvals.pop(i)
            self.graph.emit(
                Event(
                    id=self.graph.ids.event(),
                    type="approval.granted",
                    payload={
                        "approval_id": approval_id,
                        "object_type": pa.object_type,
                        "approved_by": approved_by or "user",
                    },
                    actor="runtime",
                    frame_id=self.frame.id if self.frame else None,
                    caused_by=None,
                    timestamp=self.graph.clock.now(),
                )
            )
            obj = self.graph.add_object(pa.object_type, pa.data, actor=approved_by or "user")
            return obj.id
        raise ApprovalNotFoundError(
            approval_id, pending_count=len(self._pack_state.pending_approvals)
        )

    @property
    def trace(self) -> "Trace":
        from activegraph.trace.printer import Trace

        return Trace(self.graph)

    def print_trace(self) -> None:
        self.trace.print()

    def export_trace(self, path: str) -> None:
        with open(path, "w") as f:
            for ev in self.graph.events:
                f.write(json.dumps(ev.to_dict()) + "\n")

    def print_graph(self) -> None:
        print("graph:")
        print(f"  objects ({len(self.graph.all_objects())}):")
        for o in self.graph.all_objects():
            label = o.data.get("title") or o.data.get("text") or ""
            extra = f' "{label}"' if label else ""
            status = o.data.get("status")
            status_s = f" ({status})" if status else ""
            print(f"    {o.id}{extra}{status_s}")
        print(f"  relations ({len(self.graph.all_relations())}):")
        for r in self.graph.all_relations():
            print(f"    {r.source} --{r.type}--> {r.target}")

    # ---------- v0.5: save / load / fork / diff ----------

    def save_state(self, path: Optional[str] = None) -> str:
        """Persist the event log.

        - With a store already attached: flush (no path needed). If `path` is
          given it must match the attached store's path.
        - Without a store: late-bind a SQLite store at `path` and append all
          in-memory events to it (CONTRACT v0.5 #5).
        Returns the path the events were written to.
        """
        attached = self.graph.store
        if attached is not None:
            attached_path = getattr(attached, "path", None)
            if path is not None and attached_path is not None and path != attached_path:
                from activegraph.runtime.config_errors import InvalidRuntimeConfiguration
                raise InvalidRuntimeConfiguration(
                    f"save_state(path={path!r}) — runtime already persisting to {attached_path!r}",
                    what_failed=(
                        f"runtime.save_state(path={path!r}) was called, but "
                        f"this runtime is already persisting to "
                        f"{attached_path!r}. Save targets are pinned at "
                        f"runtime construction; save_state cannot redirect."
                    ),
                    why=(
                        "A runtime's store is its source of truth for the "
                        "event log. Redirecting save mid-run would split the "
                        "log across two stores — replay would only see one "
                        "half. The framework refuses the redirect to keep "
                        "the audit trail consistent."
                    ),
                    how_to_fix=(
                        "To save to the originally-attached store, omit the "
                        "`path=` argument — `save_state()` flushes whatever "
                        "store is attached:\n"
                        "    rt.save_state()\n"
                        "\n"
                        "To move a run to a different store, use "
                        "`activegraph migrate` after the run completes:\n"
                        f"    activegraph migrate --from sqlite:///{attached_path} "
                        f"--to sqlite:///{path}"
                    ),
                    context={
                        "requested_path": path,
                        "attached_path": attached_path,
                    },
                )
            # SQLite autocommit means already durable, but call commit() defensively.
            conn = getattr(attached, "_conn", None)
            if conn is not None:
                try:
                    conn.commit()
                except Exception:
                    pass
            return attached_path or "<in-memory>"

        if path is None:
            from activegraph.runtime.config_errors import InvalidRuntimeConfiguration
            raise InvalidRuntimeConfiguration(
                "save_state() requires path= when no store is attached",
                what_failed=(
                    "runtime.save_state() was called without a `path=` "
                    "argument, but this runtime has no store attached. "
                    "Without either, save_state has nowhere to write."
                ),
                why=(
                    "save_state() is the bridge between an in-memory "
                    "runtime and a durable store. It needs either a "
                    "pre-attached store (from Runtime construction) or an "
                    "explicit `path=` argument naming a SQLite file. "
                    "Defaulting to a temp file would silently lose runs "
                    "the next time the process exited."
                ),
                how_to_fix=(
                    "Either attach a store at construction time:\n"
                    "    rt = Runtime(graph, persist_to='/path/to/run.db')\n"
                    "    rt.run_goal('...')\n"
                    "    rt.save_state()\n"
                    "or pass a path explicitly:\n"
                    "    rt.save_state(path='/path/to/run.db')\n"
                    "\n"
                    "For ephemeral runs that should not persist, omit "
                    "save_state() — the in-memory graph is the run's "
                    "lifetime."
                ),
            )

        store = _open_sqlite_store(path, self.graph.run_id)
        store.upsert_run(
            created_at=_now_iso(),
            goal=_first_goal(self.graph),
            frame_id=self.frame.id if self.frame else None,
        )
        # Append everything we've accumulated in memory.
        for ev in self.graph.events:
            store.append(ev)
        self.graph.attach_store(store)
        return path

    @classmethod
    def load(
        cls,
        path: str,
        run_id: Optional[str] = None,
        *,
        behaviors: Optional[Iterable[Union[Behavior, RelationBehavior]]] = None,
        frame: Optional[Frame] = None,
        policy: Optional[Policy] = None,
        budget: Optional[dict[str, Any]] = None,
        seed: int = 0,
        replay_strict: bool = False,
        llm_provider: Optional[LLMProvider] = None,
        replay_llm_cache: bool = False,
        llm_retry_max_attempts: int = 3,
        llm_retry_initial_delay_seconds: float = 0.5,
        llm_retry_max_delay_seconds: float = 8.0,
        tools: Optional[Iterable[Tool]] = None,
        replay_tool_cache: bool = False,
        replay_reinvoke_deterministic: bool = False,
        metrics: Optional[Metrics] = None,
        graph_store: Optional[GraphStore] = None,
        native_structured_output: bool = False,
    ) -> "Runtime":
        """Open `path`, choose a run, replay its events, return a Runtime
        wired to continue from where the log left off.

        If `run_id` is None, loads the most recently appended-to run
        (CONTRACT v0.5 #6).

        `replay_strict=True` re-fires behaviors from the recorded seed
        events and compares the resulting event-type stream (id, type) to
        the log. KNOWN LIMITATION (v0.5): payload-only drift is not
        detected; see CONTRACT v0.5 #7. Tightens in v0.6 with LLMs.

        v0.8: ``path`` accepts a URL (sqlite:///... or postgres://...)
        in addition to a bare SQLite path. Backward-compatible.

        v1.2: ``graph_store`` selects where the materialized projection
        lives while the log is replayed into it. Defaults to the in-memory
        store; pass a :class:`~activegraph.core.graph_store.GraphStore`
        (e.g. ``FalkorDBGraphStore``) to rebuild the current-state view in
        an external graph database. The event log remains the source of
        truth — this only changes where the projection is materialized.
        """
        chosen = run_id or _most_recent_run_id(path)
        if chosen is None:
            raise FileNotFoundError(f"no runs found in {path}")

        store = _open_sqlite_store(path, run_id=chosen)
        graph = Graph(ids=IDGen(), run_id=chosen, graph_store=graph_store)
        events = list(store.iter_events())
        for ev in events:
            graph._replay_event(ev)  # noqa: SLF001 — internal seam
        graph.ids.reseed_from_events(events)
        graph.attach_store(store)

        # If we're caching, harvest llm.responded events from the log so
        # forks and strict-replay re-runs serve from cache instead of
        # the API. Safe to construct even when there are no LLM events
        # (just an empty cache).
        cache = LLMCache.from_events(events) if replay_llm_cache else None
        # v0.7: same pattern for the tool cache.
        from activegraph.tools.cache import ToolCache as _ToolCache
        tcache = _ToolCache.from_events(events) if replay_tool_cache else None

        rt = cls(
            graph,
            behaviors=behaviors,
            frame=frame,
            policy=policy,
            budget=budget,
            seed=seed,
            replay_strict=replay_strict,
            llm_provider=llm_provider,
            replay_llm_cache=replay_llm_cache,
            llm_cache=cache,
            llm_retry_max_attempts=llm_retry_max_attempts,
            llm_retry_initial_delay_seconds=llm_retry_initial_delay_seconds,
            llm_retry_max_delay_seconds=llm_retry_max_delay_seconds,
            tools=tools,
            replay_tool_cache=replay_tool_cache,
            tool_cache=tcache,
            replay_reinvoke_deterministic=replay_reinvoke_deterministic,
            metrics=metrics,
            native_structured_output=native_structured_output,
        )
        # Make sure the run row exists (older files might predate it; in v0.5
        # they shouldn't, but be defensive).
        store.upsert_run(created_at=_now_iso())

        # Re-queue events whose behaviors never fired (CONTRACT v0.5 diff #8).
        # Events that already have a behavior.started referencing them are
        # left alone — re-firing them would duplicate work. Events that were
        # in the queue when the runtime stopped (e.g. budget exhausted) get
        # processed on the next run_until_idle / run_goal call. Behaviors
        # that started but never completed still lose their in-progress
        # work — that's the original tradeoff, unchanged.
        _requeue_unfired(rt, events)

        if replay_strict:
            _verify_replay(
                graph,
                events,
                behaviors,
                frame,
                policy,
                budget,
                seed,
                llm_provider=llm_provider,
                native_structured_output=native_structured_output,
            )

        return rt

    def fork(
        self,
        at_event: str,
        label: Optional[str] = None,
        *,
        behaviors: Optional[Iterable[Union[Behavior, RelationBehavior]]] = None,
        llm_provider: Optional[LLMProvider] = None,
        replay_llm_cache: bool = False,
        llm_retry_max_attempts: int | None = None,
        llm_retry_initial_delay_seconds: float | None = None,
        llm_retry_max_delay_seconds: float | None = None,
        tools: Optional[Iterable[Tool]] = None,
        replay_tool_cache: bool = False,
        replay_reinvoke_deterministic: bool = False,
        graph_store: Optional[GraphStore] = None,
    ) -> "Runtime":
        """Branch this run at `at_event` into an independent new run.

        Requires a SQLite store. Copies events from the parent's log up to
        and including `at_event` into a fresh `run_id`, replays them into a
        new Graph, then returns a Runtime that operates on that Graph.
        Forks-of-forks work the same way (CONTRACT v0.5 #9).

        v1.2: ``graph_store`` selects where the fork's materialized
        projection lives while the copied log is replayed into it. Defaults
        to the in-memory store; pass a
        :class:`~activegraph.core.graph_store.GraphStore` (e.g.
        ``FalkorDBGraphStore``) to rebuild the fork's current-state view in
        an external graph database. The fork's event log remains the source
        of truth — this only changes where the projection is materialized.
        """
        from activegraph.store.sqlite import SQLiteEventStore

        store = self.graph.store
        if store is None or not isinstance(store, SQLiteEventStore):
            from activegraph.runtime.config_errors import IncompatibleRuntimeState
            current_kind = (
                "no store attached" if store is None else type(store).__name__
            )
            raise IncompatibleRuntimeState(
                f"runtime.fork() requires a SQLite-backed runtime (current: {current_kind})",
                what_failed=(
                    f"runtime.fork() was called on a runtime with "
                    f"{current_kind}. The fork primitive currently only "
                    f"supports SQLite-backed runtimes."
                ),
                why=(
                    "Fork copies events up to the fork point using the "
                    "store's native primitives (SQLite uses a direct SQL "
                    "copy under a single transaction). Postgres has a "
                    "different transactional shape and an in-memory store "
                    "has no copy primitive at all. v0.8 deliberately "
                    "scoped the fork command to SQLite first — the "
                    "limitation is documented in CONTRACT v0.8 #5."
                ),
                how_to_fix=(
                    "Migrate the run to a SQLite store first, then fork:\n"
                    "    activegraph migrate --from <current-url> --to sqlite:///fork-source.db\n"
                    "    activegraph fork sqlite:///fork-source.db --run-id <run> --at-event <evt>\n"
                    "\n"
                    "For Postgres-native forking, file an issue — the "
                    "primitive shape (transactional copy of events up to a "
                    "seq cutoff) is known, and a contributor with Postgres "
                    "operational experience could land it as a v1.1 follow-on."
                ),
                context={"current_store_kind": current_kind},
            )

        new_run_id = self.graph.ids.run()
        SQLiteEventStore.fork_run(
            store.path,
            parent_run_id=self.graph.run_id,
            new_run_id=new_run_id,
            at_event_id=at_event,
            label=label,
            created_at=_now_iso(),
        )
        fork_store = SQLiteEventStore(store.path, run_id=new_run_id)
        fork_graph = Graph(ids=IDGen(), run_id=new_run_id, graph_store=graph_store)
        events = list(fork_store.iter_events())
        for ev in events:
            fork_graph._replay_event(ev)  # noqa: SLF001
        fork_graph.ids.reseed_from_events(events)
        fork_graph.attach_store(fork_store)

        # Reuse behaviors from the explicit list if the parent had one; else
        # let the new Runtime fall back to the global registry like v0 does.
        fork_behaviors = behaviors
        if fork_behaviors is None and self._explicit_behaviors is not None:
            fork_behaviors = list(self._explicit_behaviors)

        # Cache is populated from the PARENT's recorded llm.responded
        # events (not the fork's, which only contains events up to and
        # including at_event). A diverging fork that regenerates an
        # identical prompt will hit the cache; a divergent prompt will
        # fall through to the provider (CONTRACT v0.6 #8).
        cache = (
            LLMCache.from_events(self.graph.events) if replay_llm_cache else None
        )
        # v0.7: tool cache pre-populated from the parent's events.
        from activegraph.tools.cache import ToolCache as _ToolCache
        tcache = (
            _ToolCache.from_events(self.graph.events)
            if replay_tool_cache
            else None
        )

        rt = Runtime(
            fork_graph,
            behaviors=fork_behaviors,
            frame=self.frame,
            policy=self.policy,
            budget=None,  # fresh budget for the fork
            seed=0,
            llm_provider=llm_provider if llm_provider is not None else self.llm_provider,
            replay_llm_cache=replay_llm_cache,
            llm_cache=cache,
            llm_retry_max_attempts=(
                self.llm_retry_max_attempts
                if llm_retry_max_attempts is None
                else llm_retry_max_attempts
            ),
            llm_retry_initial_delay_seconds=(
                self.llm_retry_initial_delay_seconds
                if llm_retry_initial_delay_seconds is None
                else llm_retry_initial_delay_seconds
            ),
            llm_retry_max_delay_seconds=(
                self.llm_retry_max_delay_seconds
                if llm_retry_max_delay_seconds is None
                else llm_retry_max_delay_seconds
            ),
            tools=tools,
            replay_tool_cache=replay_tool_cache,
            tool_cache=tcache,
            replay_reinvoke_deterministic=replay_reinvoke_deterministic,
            # CONTRACT v1.3 #1: forks inherit the parent's mode posture
            # so pre-populated caches stay reachable.
            native_structured_output=self.native_structured_output,
        )
        # CONTRACT v0.5 diff #8 (extended to fork in v0.6): events whose
        # behaviors never started get re-queued. For a fork at an early
        # event (e.g. goal.created) this is how downstream behaviors fire
        # on the fork — without it, fork-then-run-until-idle would be a
        # no-op when there's no new seed event.
        _requeue_unfired(rt, events)
        return rt

    def diff(self, other: "Runtime") -> Diff:
        return compute_diff(self.graph, other.graph, self.run_id, other.run_id)


# ---------- helpers ----------


_BUDGET_REASON_MAP = {
    "max_tool_calls": "budget.tool_calls_exhausted",
    "max_cost_usd": "budget.cost_exhausted",
    "max_llm_calls": "budget.llm_calls_exhausted",
}


_TRANSIENT_LLM_REASONS = frozenset({"llm.network_error", "llm.rate_limited"})


def _is_transient_llm_reason(reason: str) -> bool:
    return reason in _TRANSIENT_LLM_REASONS


def _llm_retry_delay_seconds(
    *,
    attempt_index: int,
    initial: float,
    maximum: float,
    payload_extras: Optional[dict[str, Any]] = None,
) -> float:
    extras = payload_extras or {}
    retry_after = extras.get("retry_after_seconds")
    if retry_after is not None:
        try:
            return max(0.0, min(float(retry_after), maximum))
        except (TypeError, ValueError):
            pass
    if initial <= 0:
        return 0.0
    delay: float = min(initial * (2 ** attempt_index), maximum)
    return delay


def _resolve_and_validate_llm_models(source: list[Any], provider: LLMProvider) -> None:
    """Stamp provider defaults onto LLMBehaviors with model=None, and
    raise InvalidRuntimeConfiguration for explicit names that belong to
    a different shipped provider. CONTRACT v1.0.2 #1.

    The cross-provider mismatch check delegates to
    :func:`activegraph.runtime._live._validate_one` so the same check
    fires from both binding moments (Runtime construction here,
    register()/decoration in ``_live.validate_behavior_against_live_runtimes``).
    """
    from activegraph.runtime._live import _validate_one

    # Custom providers that pre-date v1.0.2 don't declare `default_model`.
    # Fall back to the v1.0.1 hardcoded default — keeps every pre-v1.0.2
    # call site (which silently inherited "claude-sonnet-4-5") working
    # byte-identically without code changes. The runtime only swaps in a
    # different default when the provider opts in by declaring one.
    provider_default = getattr(provider, "default_model", None) or "claude-sonnet-4-5"

    for b in source:
        if not isinstance(b, LLMBehavior):
            continue
        if b.model is None:
            # v1.0.2 #1 (a): resolve to the provider's default_model
            # (or the v1.0.1 fallback for custom providers that don't
            # declare one).
            b.model = provider_default
            continue
        # v1.0.2 #1 (b): cross-provider mismatch check.
        _validate_one(b, provider)


def _budget_reason(name: Optional[str]) -> str:
    if name is None:
        return "budget.exhausted"
    return _BUDGET_REASON_MAP.get(name, f"budget.{name.removeprefix('max_')}_exhausted")


def _hash_turn_prompt(
    *,
    prompt: Any,
    messages: list[Any],
    tool_defs: Optional[list[Any]],
) -> str:
    """Hash the prompt+messages+tools for a single turn.

    Used as the LLM cache key per-turn (CONTRACT v0.7 per-turn cache
    decision). Includes the running messages list so each turn in a
    tool loop produces a distinct hash; same shape as v0.6's
    prompt.hash() otherwise.
    """
    import hashlib
    import json as _json

    payload = {
        "model": prompt.model,
        "system": prompt.system,
        "messages": [m.to_dict() for m in messages],
        "output_schema_name": prompt.output_schema_name,
        "output_schema_json": prompt.output_schema_json,
        "max_tokens": int(prompt.max_tokens),
        "temperature": float(prompt.temperature),
        "top_p": float(prompt.top_p),
        "deterministic": bool(prompt.deterministic),
        "tools": list(tool_defs) if tool_defs else None,
    }
    # CONTRACT v1.3 #1 #7: mode is part of prompt identity; the field is
    # emitted only when native so every pre-v1.3 hash stays
    # byte-identical.
    if getattr(prompt, "structured_output_mode", "prompt") == "native":
        payload["structured_output_mode"] = "native"
    canonical = _json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _maybe_object_id(event: Event) -> Optional[str]:
    obj = event.payload.get("object") if isinstance(event.payload, dict) else None
    if isinstance(obj, dict):
        return obj.get("id")
    return None


def _now_iso() -> str:
    return (
        datetime.now(tz=timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _first_goal(graph: Graph) -> Optional[str]:
    for e in graph.events:
        if e.type == "goal.created":
            return e.payload.get("goal")
    return None


def _most_recent_run_id(path_or_url: str) -> Optional[str]:
    """Backend-aware most_recent_run_id used by Runtime.load."""
    if "://" in path_or_url:
        from activegraph.store.url import parse_store_url

        parsed = parse_store_url(path_or_url)
        if parsed.scheme == "postgres":
            from activegraph.store.postgres import PostgresEventStore

            run_id: Optional[str] = PostgresEventStore.most_recent_run_id(parsed.raw)
            return run_id
        # sqlite via URL — fall through to the SQLite helper on the path
        path_or_url = parsed.sqlite_path or ""
    from activegraph.store.sqlite import SQLiteEventStore

    return SQLiteEventStore.most_recent_run_id(path_or_url)


def _open_sqlite_store(path_or_url: str, run_id: str) -> Any:
    """Open a store by path (v0.5-v0.7 sugar) or URL (v0.8).

    Typed ``Any``, not ``EventStore``: callers use ``upsert_run``, a
    persistent-backend extra that is deliberately not part of the minimal
    ``EventStore`` protocol (CONTRACT v0.5 #2).

    A bare path like ``/tmp/run.db`` is treated as a SQLite path —
    preserves backward compatibility with all existing call sites.
    Anything containing ``://`` is parsed as a connection URL.
    """
    if "://" in path_or_url:
        from activegraph.store import open_store

        return open_store(path_or_url, run_id=run_id)
    from activegraph.store.sqlite import SQLiteEventStore

    return SQLiteEventStore(path_or_url, run_id=run_id)


def _requeue_unfired(rt: "Runtime", events: list[Event]) -> None:
    """Push events that haven't yet triggered any behavior back into the queue.

    See CONTRACT v0.5 diff #8 in CONTRACT.md for the rationale.

    INVARIANT: under the single-threaded, run-to-completion loop
    (CONTRACT #10), an event has either been popped — in which case ALL
    matching behaviors have already had behavior.started emitted on it, or
    the runtime crashed before the loop could pop the next event. There is
    no partial-fanout state.

    The naive inference "no behavior.started ever referenced this event id"
    ⟹ "this event was still in the queue when the runtime stopped" is false:
    an event with zero matching subscribers is popped-and-discarded with no
    behavior.started emitted. In a real run, the majority of events
    (llm.requested, llm.responded, tool.requested, tool.responded,
    relation.created, patch.applied, downstream object.created) have no
    subscribers and would be falsely requeued. The bug surface is
    `runtime.status().queue_depth` reading e.g. 416 on a freshly loaded
    cleanly-drained run (CONTRACT v1.0 user-test finding C3).

    The fix uses `runtime.idle` as the high-water mark. The runtime emits
    `runtime.idle` only after the queue empties (see `_emit_idle_or_exhausted`);
    every event at or before the last `runtime.idle` has by definition been
    popped. Only events emitted after the last `runtime.idle` are candidates
    for "still in queue when the runtime stopped."

    `runtime.budget_exhausted` is NOT a drain marker. It fires when the
    budget hits while the queue may still have events; those un-popped
    events are exactly the resume-recovery cases v0.5 #8 was designed for.
    Using `runtime.budget_exhausted` as a high-water mark would break
    budget-bounded pause-and-resume (a documented v0.5 contract surface).

    When v1 introduces parallelism (decision #16: out of scope for v0.5),
    this still holds — `runtime.idle` fires only when the queue is empty
    regardless of loop concurrency. The double-fire-from-partial-fanout
    concern documented in the original v0.5 invariant is still real for
    a crash mid-fanout, but that's bounded to events after the last idle,
    not the whole log.
    """
    # Find the highest index of a runtime.idle event. Events at or before
    # that index were necessarily processed-and-drained; only the suffix
    # can hold unprocessed events.
    drain_idx: int = -1
    for i, e in enumerate(events):
        if e.type == "runtime.idle":
            drain_idx = i
    suffix = events[drain_idx + 1:]
    if not suffix:
        return
    fired_on: set[str] = set()
    for e in suffix:
        if (
            e.type.startswith("behavior.")
            or e.type.startswith("relation_behavior.")
        ):
            eid = e.payload.get("event_id") if isinstance(e.payload, dict) else None
            if eid:
                fired_on.add(eid)
    for e in suffix:
        if _is_lifecycle(e):
            continue
        if e.id in fired_on:
            continue
        rt._queue.push(e)  # noqa: SLF001 — internal seam by design
    if rt._queue:
        rt._idle_emitted = False


# ---------- replay strictness (CONTRACT v0.5 #7) ----------


def _verify_replay(
    loaded_graph: Graph,
    recorded_events: list[Event],
    behaviors: Optional[Iterable[Union[Behavior, RelationBehavior]]],
    frame: Optional[Frame],
    policy: Optional[Policy],
    budget: Optional[dict[str, Any]],
    seed: int,
    *,
    llm_provider: Optional[LLMProvider] = None,
    native_structured_output: bool = False,
) -> None:
    """Replay the run from scratch — fresh Graph, behaviors fire — and
    compare the resulting event stream to the recorded log.

    On the first mismatch (id or type), raise ReplayDivergenceError.

    CONTRACT v0.6: replay_strict implies cache-on for verification
    regardless of replay_llm_cache — otherwise we'd hit the live API.
    A live-rebuilt prompt whose hash doesn't match the recorded one is
    itself a divergence (decision-2 adjustment).
    """

    # Seed events: everything with no caused_by (goal.created, user-emitted
    # bootstrap events). Behaviors re-derive everything else.
    seed_events = [e for e in recorded_events if e.caused_by is None and not _is_lifecycle(e)]
    if not seed_events:
        return  # nothing to verify

    # Pre-populate the cache from recorded llm.responded events and
    # extract the sequence of expected prompt hashes (in the order the
    # behaviors fired them) so we can pin divergence at the right
    # event id.
    non_replayable_llm_ids = _non_replayable_llm_attempt_event_ids(recorded_events)
    expected_hashes = [
        cast(str, e.payload.get("prompt_hash"))
        for e in recorded_events
        if (
            e.type == "llm.requested"
            and e.id not in non_replayable_llm_ids
            and e.payload.get("prompt_hash")
        )
    ]
    cache = LLMCache.from_events(recorded_events)

    fresh = Graph(ids=IDGen(), run_id="verify_" + loaded_graph.run_id)
    fresh_rt = Runtime(
        fresh,
        behaviors=behaviors,
        frame=frame,
        policy=policy,
        budget=budget,
        seed=seed,
        llm_provider=llm_provider,
        replay_llm_cache=True,
        llm_cache=cache,
        replay_strict=True,
        native_structured_output=native_structured_output,
    )
    # Install the expected-hash queue. _invoke_llm pops one per LLM call
    # and raises ReplayDivergenceError if the live hash differs.
    fresh_rt._strict_expected_hashes = list(expected_hashes)
    fresh_rt._ensure_registry()

    # Replay seed events through emit so behaviors fire.
    for e in seed_events:
        new_id = fresh.ids.event()
        replay_ev = Event(
            id=new_id,
            type=e.type,
            payload=dict(e.payload),
            actor=e.actor,
            frame_id=e.frame_id,
            caused_by=None,
            timestamp=e.timestamp,
        )
        fresh.emit(replay_ev)
    fresh_rt.run_until_idle()

    # Compare type-stream of non-lifecycle events.
    rec_stream = [
        (e.id, e.type)
        for e in recorded_events
        if not _is_lifecycle(e) and e.id not in non_replayable_llm_ids
    ]
    new_stream = [(e.id, e.type) for e in fresh.events if not _is_lifecycle(e)]
    for i, (rec, new) in enumerate(zip(rec_stream, new_stream)):
        if rec[1] != new[1]:
            raise ReplayDivergenceError(
                event_id=rec[0], expected=rec[1], actual=new[1]
            )
    if len(rec_stream) != len(new_stream):
        # Length mismatch — pin the divergence point.
        offending = rec_stream[len(new_stream)][0] if len(new_stream) < len(rec_stream) else new_stream[len(rec_stream)][0]
        expected = rec_stream[len(new_stream)][1] if len(new_stream) < len(rec_stream) else "<no recorded event>"
        actual = new_stream[len(rec_stream)][1] if len(rec_stream) < len(new_stream) else None
        raise ReplayDivergenceError(event_id=offending, expected=expected, actual=actual)


def _non_replayable_llm_attempt_event_ids(events: list[Event]) -> set[str]:
    """Return failed LLM attempt request/response ids.

    Provider availability is not deterministic replay output. A run that
    hit two transient network failures before a successful third attempt
    should replay from the successful ``llm.responded`` cache entry, not
    require the provider to fail twice again.
    """

    ids: set[str] = set()
    for e in events:
        if e.type != "llm.responded" or not e.payload.get("error"):
            continue
        ids.add(e.id)
        if e.caused_by is not None:
            ids.add(e.caused_by)
    return ids


def _is_lifecycle(e: Event) -> bool:
    return (
        e.type.startswith("behavior.")
        or e.type.startswith("relation_behavior.")
        or e.type.startswith("runtime.")
    )
