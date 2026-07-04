"""ID generation. CONTRACT #1 and CONTRACT v0.5 #6 (run ids are ULIDs).

All IDs flow through one generator so tests can swap in a deterministic one.
v0 uses short prefixed monotonic strings; v0.5 adds `run()` for ULID-shaped
run identifiers and `from_events()` to rebuild counters after a replay.
"""

from __future__ import annotations

import os
import re
import time
from typing import Iterable

from activegraph.core.event import Event


_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _ulid() -> str:
    """26-char Crockford base32 ULID. Time-prefixed, random suffix.

    Not strictly monotonic within the same millisecond — good enough for
    run identifiers (low write rate, no sort-criticality).
    """
    ms = int(time.time() * 1000) & ((1 << 48) - 1)
    rand = int.from_bytes(os.urandom(10), "big")
    n = (ms << 80) | rand
    out: list[str] = []
    for _ in range(26):
        out.append(_CROCKFORD[n & 0x1F])
        n >>= 5
    return "".join(reversed(out))


_OBJ_RE = re.compile(r"^(?P<type>[^#]+)#(?P<n>\d+)$")
_NUM_RE = re.compile(r"^[a-zA-Z]+_(?P<n>\d+)$")


class IDGen:
    """Per-graph monotonic ID generator. Not thread-safe (single-threaded loop).

    Objects share one global counter prefixed by type — ``task#1``,
    ``task#2``, ``claim#3``, not ``claim#1`` (CONTRACT #1); events,
    relations, patches, and frames each have their own ``evt_`` /
    ``rel_`` / ``patch_`` / ``frame_`` sequence. Replay does not call
    this: recorded events carry their ids, which is what keeps forked
    and reloaded runs aligned with their logs.
    """

    def __init__(self) -> None:
        # CONTRACT #1: objects use a global counter prefixed by type
        # (matches the README trace: task#1, task#2, claim#3 — not claim#1).
        self._object_counter = 0
        self._event_counter = 0
        self._relation_counter = 0
        self._patch_counter = 0
        self._frame_counter = 0

    # ---- generators ----

    def object(self, type_: str) -> str:
        self._object_counter += 1
        return f"{type_}#{self._object_counter}"

    def event(self) -> str:
        self._event_counter += 1
        return f"evt_{self._event_counter:03d}"

    def relation(self) -> str:
        self._relation_counter += 1
        return f"rel_{self._relation_counter:03d}"

    def patch(self) -> str:
        self._patch_counter += 1
        return f"patch_{self._patch_counter:03d}"

    def frame(self) -> str:
        self._frame_counter += 1
        return f"frame_{self._frame_counter:03d}"

    def run(self) -> str:
        # ULID per CONTRACT v0.5 #6. Not counter-based: runs live in storage
        # and are looked up by id, so collisions across files are the risk.
        return _ulid()

    # ---- replay reconstruction (CONTRACT v0.5 #14, #15) ----

    def reseed_from_events(self, events: Iterable[Event]) -> None:
        """Set counters past the highest id seen in `events`.

        Used after replay so subsequent `object()/event()/...` continue
        monotonically from where the loaded log ended. Forks call this too,
        which is why two forks at the same point produce IDs that diverge
        identically (decision #12 — fine because the IDs live in different
        runs).
        """
        max_obj = 0
        max_evt = 0
        max_rel = 0
        max_patch = 0
        max_frame = 0
        for e in events:
            n = _suffix_num(e.id)
            if n is not None:
                max_evt = max(max_evt, n)
            if e.frame_id:
                fn = _suffix_num(e.frame_id)
                if fn is not None:
                    max_frame = max(max_frame, fn)
            p = e.payload or {}
            if e.type == "object.created":
                obj_id = (p.get("object") or {}).get("id", "")
                m = _OBJ_RE.match(obj_id)
                if m:
                    max_obj = max(max_obj, int(m.group("n")))
            elif e.type == "relation.created":
                rel_id = (p.get("relation") or {}).get("id", "")
                rn = _suffix_num(rel_id)
                if rn is not None:
                    max_rel = max(max_rel, rn)
            elif e.type in ("patch.proposed", "patch.applied"):
                patch_id = (p.get("patch") or {}).get("id", "")
                pn = _suffix_num(patch_id)
                if pn is not None:
                    max_patch = max(max_patch, pn)
            elif e.type == "patch.rejected":
                pn = _suffix_num(p.get("patch_id", "") or "")
                if pn is not None:
                    max_patch = max(max_patch, pn)
        self._object_counter = max(self._object_counter, max_obj)
        self._event_counter = max(self._event_counter, max_evt)
        self._relation_counter = max(self._relation_counter, max_rel)
        self._patch_counter = max(self._patch_counter, max_patch)
        self._frame_counter = max(self._frame_counter, max_frame)


def _suffix_num(s: str) -> int | None:
    m = _NUM_RE.match(s or "")
    if not m:
        return None
    return int(m.group("n"))
