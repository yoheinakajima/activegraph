"""Core primitives. Knows nothing about runtime or behaviors."""

from activegraph.core.graph_store import GraphStore, InMemoryGraphStore

__all__ = [
    "GraphStore",
    "InMemoryGraphStore",
]
