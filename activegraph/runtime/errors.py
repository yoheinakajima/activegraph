"""Replay errors. CONTRACT v0.5 #7 (replay strictness), CONTRACT v1.0 #C1
(error format) — migrated to the ActiveGraphError hierarchy in v1.0 PR-A.

`ReplayDivergenceError` is the canonical replay error and the reference
error class for the v1.0 message rewrite series. Its message obeys the
locked format (CONTRACT v1.0 #3); three distinct call sites in the
runtime produce three distinct messages, discriminated by the inputs
to ``__init__`` and snapshot-tested individually:

  1. **prompt_hash_mismatch** — the LLM-cache prompt hash for a recorded
     ``llm.requested`` event no longer matches the live re-run hash.
     Something changed in the behavior code, prompt template, or a
     tool's input arguments.
  2. **type_mismatch** — an event at the same stream position has a
     different ``type`` than recorded. The behavior graph produced a
     different shape of work.
  3. **length_mismatch** — the recorded stream and the live re-run
     produced different numbers of events. A behavior was added, removed,
     or short-circuits differently.
  4. **embedding_hash_mismatch** — a runtime-owned embedding request was
     rebuilt with different model/text content than the recorded call.

The signature ``ReplayDivergenceError(event_id=..., expected=..., actual=...)``
is preserved from v0.5 so existing call sites and tests stay valid; the
discriminator is inferred from the inputs.
"""

from __future__ import annotations

from typing import Any

from activegraph.errors import ReplayError


class NonEmptyGraphStoreError(ReplayError):
    """``Runtime.load`` refused to replay into a GraphStore that already
    holds projection state.

    Replay upserts and removes only entities the log names. Anything the
    log never mentions would remain visible, so the rebuilt view could
    contain facts that never happened. Load checks emptiness and raises
    before applying any event. It does not call ``clear()``: a crash
    during replay would publish an empty or partial projection to anyone
    already reading that store.
    """

    _doc_slug = "non-empty-graph-store-error"

    def __init__(
        self,
        *,
        run_id: str,
        store_type: str,
        objects: int,
        relations: int,
        patches: int,
    ) -> None:
        self.run_id = run_id
        self.store_type = store_type
        self.objects = objects
        self.relations = relations
        self.patches = patches
        if objects == 0 and relations == 0 and patches == 0:
            held = (
                f"{store_type} reported existing projection state that is "
                "not listed as objects, relations, or patches (for example "
                "leftover backend placeholder nodes)."
            )
        else:
            held = (
                f"{store_type} already holds {objects} object(s), "
                f"{relations} relation(s), and {patches} patch(es)."
            )
        super().__init__(
            f"refusing to replay run {run_id!r} into a non-empty {store_type}",
            what_failed=(
                f"Runtime.load was asked to rebuild run {run_id!r} into "
                f"{store_type}, which already holds projection state. {held} "
                "No event was applied and the store was not cleared."
            ),
            why=(
                "The GraphStore is a disposable projection of one event log. "
                "Replay can only upsert or remove entities the log mentions, "
                "so records already in the store would survive and the "
                "rebuilt view would contain facts that are not in the log. "
                "Clearing first is not safe either: if replay then fails, "
                "readers are left with an empty or partial projection."
            ),
            how_to_fix=(
                "Pass an empty GraphStore. A new InMemoryGraphStore(), or a "
                "FalkorDBGraphStore whose graph_name has never been replayed "
                "into, is empty:\n"
                "    store = InMemoryGraphStore()\n"
                "    rt = Runtime.load(path, run_id=run_id, graph_store=store)\n"
                "\n"
                "Do not reuse a store that already holds this or another "
                "run's projection. Runtime.load will not clear the store. "
                "Calling clear() yourself and retrying is possible, but a "
                "crash mid-replay would leave a partial projection; prefer "
                "a fresh store. Building into an isolated store and swapping "
                "it in atomically is not part of this release."
            ),
            context={
                "run_id": run_id,
                "store_type": store_type,
                "objects": objects,
                "relations": relations,
                "patches": patches,
            },
        )


_NO_RECORDED_EVENT_SENTINEL = "<no recorded event>"


class ReplayDivergenceError(ReplayError):
    """Raised when a replay (``replay_strict=True``) or a fork produces an
    event stream that does not match the recorded log.

    ``event_id`` pins the first divergence point so an operator can jump
    directly to it. ``expected`` and ``actual`` describe what was
    recorded vs. what the live re-run produced; one is ``None`` when the
    re-run finished early or produced an extra event with no recorded
    counterpart.
    """

    _doc_slug = "replay-divergence-error"

    def __init__(
        self,
        *,
        event_id: str,
        expected: str,
        actual: str | None,
    ) -> None:
        self.event_id = event_id
        self.expected = expected
        self.actual = actual
        kind, summary, what_failed, why, how_to_fix = _build_message(
            event_id=event_id, expected=expected, actual=actual
        )
        self.kind = kind
        context: dict[str, Any] = {
            "event_id": event_id,
            "kind": kind,
            "expected": expected,
            "actual": actual,
        }
        super().__init__(
            summary,
            what_failed=what_failed,
            why=why,
            how_to_fix=how_to_fix,
            context=context,
        )


def _build_message(
    *, event_id: str, expected: str, actual: str | None
) -> tuple[str, str, str, str, str]:
    """Return ``(kind, summary, what_failed, why, how_to_fix)`` for the
    three replay-divergence shapes. The discriminator is the input shape:

      - ``expected`` starts with ``prompt_hash=`` -> prompt_hash_mismatch
      - ``expected`` starts with ``embedding_hash=`` -> embedding hash mismatch
      - ``expected`` is ``"<no recorded event>"`` or ``actual is None`` ->
        length_mismatch
      - otherwise -> type_mismatch
    """
    if isinstance(expected, str) and expected.startswith("prompt_hash="):
        return _prompt_hash_message(event_id, expected, actual)
    if isinstance(expected, str) and expected.startswith("embedding_hash="):
        return _embedding_hash_message(event_id, expected, actual)
    if expected == _NO_RECORDED_EVENT_SENTINEL or actual is None:
        return _length_message(event_id, expected, actual)
    return _type_message(event_id, expected, actual)


def _prompt_hash_message(
    event_id: str, expected: str, actual: str | None,
) -> tuple[str, str, str, str, str]:
    actual_str = actual if actual is not None else "<no live response>"
    return (
        "prompt_hash_mismatch",
        f"replay diverged at {event_id}: LLM prompt hash mismatch",
        (
            f"Event {event_id} (an `llm.requested` event in the recorded log) had a "
            f"different prompt hash during this replay than the parent run recorded:\n"
            f"  recorded:  {expected}\n"
            f"  live:      {actual_str}"
        ),
        (
            "The replay cache keys on the full prompt hash, so any change to an LLM "
            "behavior's code, a prompt template, a system message, or a tool's input "
            "arguments produces a mismatch. The framework refuses to silently substitute "
            "a stale cached response under a new prompt — that would break the audit "
            "trail the cache is designed to preserve."
        ),
        (
            f"If the change was intentional (you edited a behavior or a prompt template),\n"
            f"re-record the cache from the divergence point:\n"
            f"    activegraph fork <parent-run> --at-event {event_id} --record\n"
            f"\n"
            f"If the change was unintentional, diff your code against the recorded run's\n"
            f"pack version and revert the change:\n"
            f"    activegraph inspect <parent-run> --pack-version\n"
            f"\n"
            f"To see the full recorded prompt for this event:\n"
            f"    activegraph inspect <parent-run> --event {event_id}"
        ),
    )


def _embedding_hash_message(
    event_id: str,
    expected: str,
    actual: str | None,
) -> tuple[str, str, str, str, str]:
    actual_str = actual if actual is not None else "<no live request>"
    return (
        "embedding_hash_mismatch",
        f"replay diverged at {event_id}: embedding input hash mismatch",
        (
            f"Event {event_id} rebuilt a different runtime-owned embedding "
            f"request than the recorded run:\n"
            f"  recorded:  {expected}\n"
            f"  live:      {actual_str}"
        ),
        (
            "Embedding replay keys on the model and complete ordered text "
            "batch. Serving recorded vectors under a different content hash "
            "would silently corrupt retrieval results and provenance."
        ),
        (
            "Restore the recorded model/text construction or intentionally "
            "fork before this request and record a new embedding response."
        ),
    )


def _type_message(
    event_id: str, expected: str, actual: str | None,
) -> tuple[str, str, str, str, str]:
    actual_str = actual if actual is not None else "<no live event>"
    return (
        "type_mismatch",
        f"replay diverged at {event_id}: event type mismatch",
        (
            f"At the stream position pinned to event {event_id}, the live re-run "
            f"produced a different event type than recorded:\n"
            f"  recorded:  {expected!r}\n"
            f"  live:      {actual_str!r}"
        ),
        (
            "Strict replay compares the type stream of non-lifecycle events between the "
            "recorded log and the live re-run. A type mismatch means the behavior graph "
            "took a different branch — usually because a behavior's `where` filter, a "
            "pattern subscription, or a conditional `graph.emit` changed since the "
            "recorded run."
        ),
        (
            f"Identify the behavior that produced event {event_id} in the recorded log:\n"
            f"    activegraph inspect <parent-run> --event {event_id}\n"
            f"\n"
            f"Diff that behavior against your current source. If the change was\n"
            f"intentional, re-run without `replay_strict=True` (or fork with --record\n"
            f"from the divergence point). If unintentional, revert the behavior."
        ),
    )


def _length_message(
    event_id: str, expected: str, actual: str | None,
) -> tuple[str, str, str, str, str]:
    if actual is None:
        return (
            "length_mismatch",
            f"replay diverged at {event_id}: live re-run finished early",
            (
                f"The recorded log contained event {event_id} (type {expected!r}) at this "
                f"position, but the live re-run terminated before producing it.\n"
                f"  recorded:  {expected!r}\n"
                f"  live:      <no event produced>"
            ),
            (
                "Strict replay requires the live re-run to produce the same number and "
                "shape of non-lifecycle events as the recording. A short live re-run means "
                "a behavior that fired in the recorded run no longer fires, or short-"
                "circuits earlier — usually because a pattern subscription, a `where` "
                "filter, or a guard condition was tightened since the recording."
            ),
            (
                f"Identify the behavior that produced {event_id} in the recorded log:\n"
                f"    activegraph inspect <parent-run> --event {event_id}\n"
                f"\n"
                f"Compare that behavior's current trigger conditions against the recorded\n"
                f"run's. If the change was intentional, fork with --record from the\n"
                f"divergence point to refresh the recording. If unintentional, revert."
            ),
        )
    return (
        "length_mismatch",
        f"replay diverged at {event_id}: live re-run produced an unrecorded event",
        (
            f"At the position pinned to event {event_id}, the live re-run produced an "
            f"event of type {actual!r}, but the recorded log had no event here.\n"
            f"  recorded:  <no event recorded>\n"
            f"  live:      {actual!r}"
        ),
        (
            "Strict replay requires the live re-run's event stream to match the "
            "recording position-for-position. An extra live event means a behavior "
            "fires now that did not fire in the recorded run — usually because a new "
            "behavior was added, or a pattern subscription was loosened."
        ),
        (
            f"List the behaviors currently registered and compare against the recorded\n"
            f"pack version:\n"
            f"    activegraph inspect <parent-run> --behaviors\n"
            f"\n"
            f"If the new behavior is intentional, re-record from this position:\n"
            f"    activegraph fork <parent-run> --at-event {event_id} --record\n"
            f"\n"
            f"If the behavior shouldn't fire here, tighten its trigger conditions."
        ),
    )
