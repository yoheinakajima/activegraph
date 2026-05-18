"""Trace formatter. CONTRACT #18 — format is the public contract.

Layout: tag column is left-aligned, padded to 26 chars; if the tag itself is
longer, exactly one space follows it.

Standard event types get specific renderings (see CONTRACT for the table).
Anything else is rendered as `[event.emitted] {type} k=v...`.

CONTRACT v0.5 #22: replayed events are rendered with a `[replay.event]`
prefix. After the last replayed event, two synthetic lines appear so
operators can see the load boundary:
    [replay.complete] N events replayed, graph reconstructed
    [runtime.idle]    ready to resume
"""

from __future__ import annotations

from typing import Any

from activegraph.core.event import Event
from activegraph.core.graph import Graph


TAG_COL = 26


def _format_tag(tag_text: str) -> str:
    """Bracketed tag, left-padded to TAG_COL (or one trailing space if longer)."""
    bracketed = f"[{tag_text}]"
    if len(bracketed) >= TAG_COL:
        return bracketed + " "
    return bracketed.ljust(TAG_COL)


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'s' if n != 1 else ''}"


# ---------- per-event formatters ----------


def _fmt_goal_created(e: Event) -> str:
    actor = e.actor or "user"
    goal = e.payload.get("goal", "")
    return f'{_format_tag("goal.created")}{actor}: "{goal}"'


def _fmt_object_created(e: Event) -> str:
    o = e.payload.get("object", {})
    data = o.get("data", {})
    label = data.get("title") or data.get("text") or ""
    label_s = f' "{label}"' if label else ""
    status = data.get("status")
    status_s = f" ({status})" if status else ""
    return f'{_format_tag("object.created")}{o.get("id", "?")}{label_s}{status_s}'


def _fmt_object_removed(e: Event) -> str:
    return f'{_format_tag("object.removed")}{e.payload.get("id", "?")}'


def _fmt_relation_created(e: Event) -> str:
    r = e.payload.get("relation", {})
    return (
        f'{_format_tag("relation.created")}'
        f'{r.get("source", "?")} --{r.get("type", "?")}--> {r.get("target", "?")}'
    )


def _fmt_relation_removed(e: Event) -> str:
    return f'{_format_tag("relation.removed")}{e.payload.get("id", "?")}'


def _fmt_patch_applied(e: Event) -> str:
    target = e.payload.get("target", "?")
    diff = e.payload.get("diff", {})
    if not diff:
        return f'{_format_tag("patch.applied")}{target} (no change)'
    lines = []
    for field, change in diff.items():
        old = change.get("old")
        new = change.get("new")
        body = f"{target} {field}: {old} -> {new}"
        lines.append(f'{_format_tag("patch.applied")}{body}')
    return "\n".join(lines)


def _fmt_patch_proposed(e: Event) -> str:
    p = e.payload.get("patch", {})
    return (
        f'{_format_tag("patch.proposed")}'
        f'{p.get("target", "?")} {p.get("op", "?")} by {p.get("proposed_by", "?")}'
    )


def _fmt_patch_rejected(e: Event) -> str:
    return (
        f'{_format_tag("patch.rejected")}'
        f'{e.payload.get("patch_id", "?")}: {e.payload.get("reason", "?")}'
    )


def _fmt_behavior_started(e: Event) -> str:
    name = e.payload.get("behavior", "?")
    triggering_type = e.payload.get("triggering_event_type")
    triggering_id = e.payload.get("triggering_object_id")
    if triggering_type and triggering_id:
        return f'{_format_tag("behavior.started")}{name}  (matched {triggering_type}: {triggering_id})'
    return f'{_format_tag("behavior.started")}{name}'


def _fmt_relation_behavior_started(e: Event) -> str:
    name = e.payload.get("behavior", "?")
    triggering_type = e.payload.get("triggering_event_type", "?")
    relation_type = e.payload.get("relation_type", "?")
    return (
        f'{_format_tag("relation_behavior.started")}'
        f'{name}  (matched {triggering_type} on {relation_type} edge)'
    )


def _fmt_behavior_completed(e: Event) -> str:
    name = e.payload.get("behavior", "?")
    n_obj = int(e.payload.get("objects_created", 0))
    n_rel = int(e.payload.get("relations_created", 0))
    # Count summary only when behavior produced structure (>= 2 mutations
    # combined). Single-action behaviors render as just `name`. See CONTRACT.
    if n_obj + n_rel >= 2:
        return (
            f'{_format_tag("behavior.completed")}'
            f'{name} ({_plural(n_obj, "object")}, {_plural(n_rel, "relation")})'
        )
    return f'{_format_tag("behavior.completed")}{name}'


def _fmt_behavior_failed(e: Event) -> str:
    name = e.payload.get("behavior", "?")
    et = e.payload.get("exception_type", "?")
    msg = e.payload.get("message", "")
    return f'{_format_tag("behavior.failed")}{name}: {et}: {msg}'


def _fmt_llm_requested(e: Event, *, hide_prompt_normalized: bool = False) -> str:
    """CONTRACT v0.6 #14 (+ v0.7 turn_index + v0.9.1 prompt_normalized rollup):
    `[llm.requested] evt_NNN  behavior  model=... tokens_in~NNNN budget_remaining=$X.XX`

    The `~` prefix on `tokens_in` marks an estimate; absent if no
    pre-call count was made (no cost budget OR cache hit). The
    `budget_remaining=$...` segment is dropped if no cost budget.
    v0.7 adds `turn=N` for tool-loop turns past the first.

    `prompt_normalized=true` was on every line in v0.7-v0.9. v0.9.1
    rolls it up to a single `[trace.flags]` header when uniform across
    all non-replayed `llm.requested` events, dropping the per-line
    flag. Mixed-state traces (rare) keep the per-line flag for
    precision. `hide_prompt_normalized=True` is set by the trace
    facade when the rollup is active.
    """
    p = e.payload or {}
    name = p.get("behavior", "?")
    parts: list[str] = [f"{e.id}  {name}", f"model={p.get('model', '?')}"]
    if p.get("cache_hit"):
        parts.append("cache_hit=true")
    turn_idx = p.get("turn_index")
    if turn_idx is not None and turn_idx > 0:
        parts.append(f"turn={turn_idx}")
    if "estimated_input_tokens" in p:
        parts.append(f"tokens_in~{p['estimated_input_tokens']}")
    if p.get("budget_remaining_usd") is not None:
        parts.append(f"budget_remaining=${_money(p['budget_remaining_usd'])}")
    if p.get("prompt_normalized") and not hide_prompt_normalized:
        parts.append("prompt_normalized=true")
    return f'{_format_tag("llm.requested")}{"  ".join([parts[0], " ".join(parts[1:])])}'


def _fmt_llm_responded(e: Event) -> str:
    """CONTRACT v0.6 #14:
    `[llm.responded] evt_NNN  behavior  tokens_in=NNNN tokens_out=NNN cost=$X.XXX latency=X.Xs`

    Cache hits render with `cache_hit=true` and no cost/latency segments
    (latency is the cached response's recorded latency, not now).
    """
    p = e.payload or {}
    name = p.get("behavior", "?")
    parts: list[str] = [f"{e.id}  {name}"]
    if p.get("cache_hit"):
        parts.append("cache_hit=true")
    in_tok = p.get("input_tokens")
    out_tok = p.get("output_tokens")
    if in_tok is not None:
        parts.append(f"tokens_in={in_tok}")
    if out_tok is not None:
        parts.append(f"tokens_out={out_tok}")
    cost = p.get("cost_usd")
    if cost is not None and not p.get("cache_hit"):
        parts.append(f"cost=${_money(cost)}")
    lat = p.get("latency_seconds")
    if lat is not None and not p.get("cache_hit"):
        parts.append(f"latency={float(lat):.1f}s")
    return f'{_format_tag("llm.responded")}{"  ".join([parts[0], " ".join(parts[1:])])}'


def _money(v: Any) -> str:
    try:
        return f"{float(v):.3f}"
    except (TypeError, ValueError):
        return str(v)


# ---------- v0.7 trace lines: tool.* / pattern.* / behavior.scheduled ------


def _fmt_tool_requested(e: Event) -> str:
    """CONTRACT v0.7 #18:
    `[tool.requested]  evt_NNN  behavior  tool=name args_hash=AAA cache_hit=false`
    """
    p = e.payload or {}
    name = p.get("behavior", "?")
    tool = p.get("tool", "?")
    parts: list[str] = [
        f"{e.id}  {name}",
        f"tool={tool}",
        f"args_hash={_short_hash(p.get('args_hash', '?'))}",
    ]
    if p.get("cache_hit"):
        parts.append("cache_hit=true")
    if p.get("deterministic"):
        parts.append("deterministic=true")
    return f'{_format_tag("tool.requested")}{"  ".join([parts[0], " ".join(parts[1:])])}'


def _fmt_tool_responded(e: Event) -> str:
    """CONTRACT v0.7 #18:
    `[tool.responded] evt_NNN  behavior  tool=name latency=X.Xs cost=$X.XXX cache_hit=false`
    """
    p = e.payload or {}
    name = p.get("behavior", "?")
    tool = p.get("tool", "?")
    parts: list[str] = [f"{e.id}  {name}", f"tool={tool}"]
    if p.get("cache_hit"):
        parts.append("cache_hit=true")
    err = p.get("error")
    if err:
        reason = err.get("reason", "tool.error") if isinstance(err, dict) else "tool.error"
        parts.append(f"error={reason}")
    else:
        lat = p.get("latency_seconds")
        cost = p.get("cost_usd")
        if lat is not None and not p.get("cache_hit"):
            parts.append(f"latency={float(lat):.1f}s")
        if cost is not None and not p.get("cache_hit"):
            parts.append(f"cost=${_money(cost)}")
    return f'{_format_tag("tool.responded")}{"  ".join([parts[0], " ".join(parts[1:])])}'


def _fmt_pattern_matched(e: Event) -> str:
    """CONTRACT v0.7 #18:
    `[pattern.matched]  evt_NNN  behavior  matches=N`
    """
    p = e.payload or {}
    name = p.get("behavior", "?")
    n = int(p.get("matches_count", 0))
    return (
        f'{_format_tag("pattern.matched")}'
        f'{e.id}  {name}  matches={n}'
    )


def _fmt_behavior_scheduled(e: Event) -> str:
    """CONTRACT v0.7 #18:
    `[behavior.scheduled]  evt_NNN  behavior  activate_after=N_events`
    """
    p = e.payload or {}
    name = p.get("behavior", "?")
    n = int(p.get("activate_after", 0))
    return (
        f'{_format_tag("behavior.scheduled")}'
        f'{e.id}  {name}  activate_after={n}_event{"s" if n != 1 else ""}'
    )


def _short_hash(h: str) -> str:
    """Trim a long hex hash to 8 chars for trace readability."""
    if not isinstance(h, str):
        return str(h)
    return h[:8] if len(h) > 8 else h


def _fmt_runtime_idle(_: Event) -> str:
    return f'{_format_tag("runtime.idle")}queue empty, budget remaining'


def _fmt_pack_loaded(e: Event) -> str:
    """CONTRACT v0.9 #25:
    `[pack.loaded]    diligence v0.1.0 (8 object_types, 6 relation_types,
                     7 behaviors, 3 tools, 2 policies, 5 prompts)`

    The pack.loaded event payload carries the pack name, version, and
    the full structural inventory. The trace line summarizes counts;
    the full payload is in the JSONL export and on `activegraph
    inspect --pack-version` for operators who need the prompt hashes.
    """
    p = e.payload or {}
    name = p.get("name", "?")
    version = p.get("version", "?")
    # (count, singular, plural) — "policy" needs the irregular plural;
    # the rest are regular but the table keeps the form explicit so
    # future additions don't drift through _plural()'s simple +s rule.
    counts = [
        (len(p.get("object_types") or []), "object_type", "object_types"),
        (len(p.get("relation_types") or []), "relation_type", "relation_types"),
        (len(p.get("behaviors") or []), "behavior", "behaviors"),
        (len(p.get("tools") or []), "tool", "tools"),
        (len(p.get("policies") or []), "policy", "policies"),
        (len(p.get("prompts") or {}), "prompt", "prompts"),
    ]
    summary = ", ".join(
        f"{n} {singular if n == 1 else plural}"
        for n, singular, plural in counts
        if n > 0
    )
    return f'{_format_tag("pack.loaded")}{name} v{version} ({summary})'


def _fmt_runtime_budget_exhausted(e: Event) -> str:
    by = e.payload.get("exhausted_by", "?")
    return f'{_format_tag("runtime.budget_exhausted")}stopped: {by}'


def _fmt_event_emitted(e: Event) -> str:
    """Fallback for user-emitted (custom) events."""
    payload_kvs = []
    for k, v in (e.payload or {}).items():
        payload_kvs.append(f"{k}={_short(v)}")
    body = " ".join([e.type] + payload_kvs)
    return f'{_format_tag("event.emitted")}{body}'


def _short(v: Any) -> str:
    if isinstance(v, str):
        return v
    return repr(v)


_FORMATTERS = {
    "goal.created": _fmt_goal_created,
    "object.created": _fmt_object_created,
    "object.removed": _fmt_object_removed,
    "relation.created": _fmt_relation_created,
    "relation.removed": _fmt_relation_removed,
    "patch.applied": _fmt_patch_applied,
    "patch.proposed": _fmt_patch_proposed,
    "patch.rejected": _fmt_patch_rejected,
    "behavior.started": _fmt_behavior_started,
    "behavior.completed": _fmt_behavior_completed,
    "behavior.failed": _fmt_behavior_failed,
    "behavior.scheduled": _fmt_behavior_scheduled,
    "relation_behavior.started": _fmt_relation_behavior_started,
    "llm.requested": _fmt_llm_requested,
    "llm.responded": _fmt_llm_responded,
    "tool.requested": _fmt_tool_requested,
    "tool.responded": _fmt_tool_responded,
    "pattern.matched": _fmt_pattern_matched,
    "runtime.idle": _fmt_runtime_idle,
    "pack.loaded": _fmt_pack_loaded,
    "runtime.budget_exhausted": _fmt_runtime_budget_exhausted,
}


def format_event(event: Event, *, hide_prompt_normalized: bool = False) -> str:
    if event.type == "llm.requested":
        return _fmt_llm_requested(event, hide_prompt_normalized=hide_prompt_normalized)
    fn = _FORMATTERS.get(event.type, _fmt_event_emitted)
    return fn(event)


# ---------- v0.9.1: prompt_normalized rollup ----------


def _compute_prompt_normalized_rollup(
    events: list[Event],
    replayed: set[str],
) -> dict | None:
    """Return rollup info if every non-replayed `llm.requested` event carries
    `prompt_normalized=true`. Returns None if there are no such events or if
    the flag is mixed across them — in the mixed case the per-line flag is
    kept (rare; signals a real divergence worth seeing).
    """
    llm_reqs = [
        e for e in events
        if e.type == "llm.requested" and e.id not in replayed
    ]
    if not llm_reqs:
        return None
    if not all((e.payload or {}).get("prompt_normalized") for e in llm_reqs):
        return None
    return {"prompt_normalized": True, "count": len(llm_reqs)}


def _fmt_trace_flags(rollup: dict) -> str:
    n = int(rollup["count"])
    return (
        f'{_format_tag("trace.flags")}'
        f'prompt_normalized=true ({_plural(n, "llm request")})'
    )


# ---------- replay rendering (CONTRACT v0.5 #22) ----------


def _fmt_replay(event: Event) -> str:
    """Render a replayed event with the `[replay.event]` prefix.

    Format: `[replay.event] <evt_id> <event.type> <one-line summary>`
    """
    t = event.type
    p = event.payload or {}
    if t == "object.created":
        o = p.get("object", {})
        oid = o.get("id", "?")
        label = (o.get("data") or {}).get("title") or (o.get("data") or {}).get("text") or ""
        label_s = f' "{label}"' if label else ""
        body = f"{event.id} {t} {oid}{label_s}"
    elif t == "relation.created":
        r = p.get("relation", {})
        body = f'{event.id} {t} {r.get("source")} --{r.get("type")}--> {r.get("target")}'
    elif t == "patch.applied":
        body = f'{event.id} {t} {p.get("target", "?")}'
    elif t == "goal.created":
        body = f'{event.id} {t} "{p.get("goal", "")}"'
    elif t in ("behavior.started", "behavior.completed", "behavior.failed", "relation_behavior.started"):
        body = f'{event.id} {t} {p.get("behavior", "?")}'
    else:
        body = f"{event.id} {t}"
    return f'{_format_tag("replay.event")}{body}'


def _fmt_replay_complete(n: int) -> str:
    return f'{_format_tag("replay.complete")}{n} events replayed, graph reconstructed'


def _fmt_replay_ready() -> str:
    return f'{_format_tag("runtime.idle")}ready to resume'


# ---------- Trace facade exposed via runtime.trace ----------


class Trace:
    def __init__(self, graph: Graph) -> None:
        self._graph = graph

    def lines(self) -> list[str]:
        replayed = self._graph.replayed_ids
        replayed_count = len(replayed)
        rollup = _compute_prompt_normalized_rollup(
            list(self._graph.events), replayed
        )
        hide_per_line = rollup is not None
        out: list[str] = []
        emitted_boundary = False
        emitted_flags = False
        seen_replayed = 0
        for e in self._graph.events:
            if e.id in replayed:
                out.append(_fmt_replay(e))
                seen_replayed += 1
                continue
            if replayed_count > 0 and not emitted_boundary:
                out.append(_fmt_replay_complete(replayed_count))
                out.append(_fmt_replay_ready())
                emitted_boundary = True
            if rollup is not None and not emitted_flags:
                out.append(_fmt_trace_flags(rollup))
                emitted_flags = True
            line = format_event(e, hide_prompt_normalized=hide_per_line)
            out.append(line)
        if replayed_count > 0 and not emitted_boundary:
            out.append(_fmt_replay_complete(replayed_count))
            out.append(_fmt_replay_ready())
        return out

    def print(self) -> None:
        for line in self.lines():
            print(line)

    def export(self, path: str) -> None:
        with open(path, "w") as f:
            for line in self.lines():
                f.write(line + "\n")

    def causal_chain(self, object_id: str) -> str:
        from activegraph.trace.causal import causal_chain

        return causal_chain(self._graph, object_id)
