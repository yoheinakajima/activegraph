"""Benchmark InMemoryGraphStore vs FalkorDBGraphStore across graph sizes.

The point of the FalkorDB optimizations is that the *structural* read paths
push down into the database, so their cost tracks the size of the **result**
rather than the size of the whole projection. This script measures those
paths — a full-projection scan (the un-pushable baseline), a type-scoped
read, a type-existence check, an indexed scalar predicate, a depth-2
neighborhood walk, a two-hop pattern match, and a cascade delete — on small /
medium / large graphs, for both backends, and prints a
Markdown table you can paste into a doc.

It is a *relative* benchmark: absolute numbers depend on the machine, the
FalkorDB deployment, and the network hop. Treat the ratios between rows and
backends as the signal, not the milliseconds.

Run against a server (recommended)::

    docker run -d --rm -p 6379:6379 falkordb/falkordb:latest
    FALKORDB_HOST=localhost python scripts/benchmark_falkordb.py

Or against the container this repo's tests use::

    docker run -d --rm --name ag-falkordb-test -p 16379:6379 falkordb/falkordb:latest
    FALKORDB_HOST=localhost FALKORDB_PORT=16379 python scripts/benchmark_falkordb.py

Without a reachable FalkorDB the FalkorDB column is skipped and only the
in-memory baseline is reported.

Options::

    --sizes small=200,medium=2000,large=20000   override the graph sizes
    --repeats 5                                  query timing samples (min wins)
    --skip-build-large                           don't build the large graph on
                                                 FalkorDB (its per-edge MERGE
                                                 round-trips dominate wall time)
"""

from __future__ import annotations

import argparse
import time
from dataclasses import dataclass
from typing import Callable, Optional

from activegraph import FalkorDBGraphStore, Graph
from activegraph.runtime.patterns import parse

# Object types cycled across the graph so type-scoped reads return ~1/N_TYPES.
TYPES = ["claim", "evidence", "risk", "memo"]
# Every relation is the same type so the two-hop pattern walks the whole chain.
REL_TYPE = "links"
PATTERN = "(a)-[:links]->(b:claim)-[:links]->(c)"


@dataclass
class Sizes:
    small: int
    medium: int
    large: int

    def items(self) -> list[tuple[str, int]]:
        return [("small", self.small), ("medium", self.medium), ("large", self.large)]


def _build(graph: Graph, n: int) -> list[str]:
    """Populate ``graph`` with ``n`` typed objects in a single ``links`` chain
    and return the object ids in insertion order.
    """
    ids: list[str] = []
    prev: Optional[str] = None
    for i in range(n):
        obj = graph.add_object(TYPES[i % len(TYPES)], {"i": i, "text": f"node {i}"})
        ids.append(obj.id)
        if prev is not None:
            graph.add_relation(prev, obj.id, REL_TYPE)
        prev = obj.id
    return ids


def _time(fn: Callable[[], object], repeats: int) -> float:
    """Return the best (min) wall time in milliseconds over ``repeats`` runs."""
    samples = []
    for _ in range(repeats):
        t0 = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t0) * 1000.0)
    return min(samples)


@dataclass
class Row:
    op: str
    size: str
    n: int
    inmemory_ms: float
    falkordb_ms: Optional[float]


def run(sizes: Sizes, repeats: int, skip_build_large: bool) -> list[Row]:
    fdb_usable, reason = _probe_falkordb()
    if not fdb_usable:
        print(f"# FalkorDB unavailable ({reason}); reporting in-memory only.\n")

    rows: list[Row] = []
    for label, n in sizes.items():
        # In-memory: always.
        mem = _bench_with_build(lambda: Graph(), n, repeats)

        # FalkorDB: gated on a reachable backend; large build is opt-out.
        fdb = None
        if fdb_usable:
            build = not (label == "large" and skip_build_large)
            if not build:
                print(f"# skipping FalkorDB build for size={label} (n={n})")
            fdb = _bench_with_build(
                lambda: _falkordb_graph(label), n, repeats, build=build
            )

        for op in _OP_ORDER:
            rows.append(
                Row(
                    op=op,
                    size=label,
                    n=n,
                    inmemory_ms=mem[op],
                    falkordb_ms=(fdb.get(op) if fdb else None),
                )
            )
    return rows


_OP_ORDER = [
    "build (write)",
    "full scan (all_objects)",
    "type-scoped read",
    "type existence",
    "indexed where (i >= n-8)",
    "neighborhood depth=2",
    "2-hop pattern match",
    "cascade delete",
]


def _bench_with_build(
    make_graph: Callable[[], Graph], n: int, repeats: int, build: bool = True
) -> dict[str, Optional[float]]:
    if not build:
        return {op: None for op in _OP_ORDER}
    graph = make_graph()
    t0 = time.perf_counter()
    ids = _build(graph, n)
    build_ms = (time.perf_counter() - t0) * 1000.0

    mid = ids[len(ids) // 2]
    matcher = parse(PATTERN).compile()
    out: dict[str, Optional[float]] = {
        "build (write)": build_ms,
        "full scan (all_objects)": _time(lambda: graph.all_objects(), repeats),
        "type-scoped read": _time(lambda: graph.objects(type="claim"), repeats),
        "type existence": _time(
            lambda: graph.has_object_of_type("claim"), repeats
        ),
        "indexed where (i >= n-8)": _time(
            lambda: graph.objects(type="claim", where={"i": {">=": n - 8}}),
            repeats,
        ),
        "neighborhood depth=2": _time(
            lambda: graph.neighborhood(mid, depth=2), repeats
        ),
        "2-hop pattern match": _time(
            lambda: matcher.matches(event=None, graph=graph), repeats
        ),
    }
    t0 = time.perf_counter()
    graph.remove_object(mid)
    out["cascade delete"] = (time.perf_counter() - t0) * 1000.0
    _close(graph)
    return out


def _falkordb_graph(label: str) -> Graph:
    store = FalkorDBGraphStore(
        graph_name=f"ag_bench_{label}", indexed_fields={"claim": ["i"]}
    )
    store.clear()
    return Graph(graph_store=store)


def _close(graph: Graph) -> None:
    store = getattr(graph, "_state", None)
    if store is None:
        return
    try:
        store.clear()
        store.close()
    except Exception:  # noqa: BLE001
        pass


def _probe_falkordb() -> tuple[bool, str]:
    try:
        store = FalkorDBGraphStore(graph_name="ag_bench_probe")
        store.clear()
        store.close()
        return True, ""
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


def _fmt(ms: Optional[float]) -> str:
    if ms is None:
        return "—"
    if ms >= 100:
        return f"{ms:,.0f}"
    if ms >= 10:
        return f"{ms:.1f}"
    return f"{ms:.2f}"


def print_table(rows: list[Row]) -> None:
    print("\n| Operation | Size (objects) | InMemory (ms) | FalkorDB (ms) |")
    print("|---|---|---|---|")
    for r in rows:
        size = f"{r.size} ({r.n:,})"
        print(
            f"| {r.op} | {size} | {_fmt(r.inmemory_ms)} | {_fmt(r.falkordb_ms)} |"
        )


def _parse_sizes(spec: str) -> Sizes:
    d = {"small": 200, "medium": 2000, "large": 20000}
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        k, _, v = part.partition("=")
        d[k.strip()] = int(v)
    return Sizes(small=d["small"], medium=d["medium"], large=d["large"])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sizes", default="small=200,medium=2000,large=20000")
    ap.add_argument("--repeats", type=int, default=5)
    ap.add_argument("--skip-build-large", action="store_true")
    args = ap.parse_args()

    sizes = _parse_sizes(args.sizes)
    print(
        f"# benchmark: sizes={sizes.items()} repeats={args.repeats} "
        f"(best-of-{args.repeats} for queries)"
    )
    rows = run(sizes, args.repeats, args.skip_build_large)
    print_table(rows)


if __name__ == "__main__":
    main()
