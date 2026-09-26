"""Runtime.load fail-closed integrity. Issues #81 and #82.

Load resumes a canonical run and replays it into an empty projection.
It does not mint a run for an unknown id, repair a missing catalog row,
or apply events into a GraphStore that already holds state.

Acceptance case 5 from #82 (one run's atomic projection replacement
cannot disturb another run) is out of scope: this release refuses a
non-empty store instead of publishing a new projection generation.
"""

from __future__ import annotations

import os
import sqlite3
import uuid
from collections.abc import Iterator

import pytest

from activegraph import (
    FrozenClock,
    Graph,
    IDGen,
    InMemoryGraphStore,
    NonEmptyGraphStoreError,
    Object,
    Patch,
    Relation,
    RunNotFoundError,
    Runtime,
    SQLiteEventStore,
    behavior,
    clear_registry,
)
from activegraph.core.graph_store import GraphStore
from activegraph.store.retention import compact


PG_URL = os.environ.get("ACTIVEGRAPH_TEST_POSTGRES_URL")


def _runs(path: str) -> list[str]:
    return [record.run_id for record in SQLiteEventStore.list_runs(path)]


def _projection(store: GraphStore) -> tuple[list[str], list[str], list[str]]:
    return (
        sorted(obj.id for obj in store.all_objects()),
        sorted(rel.id for rel in store.all_relations()),
        sorted(patch.id for patch in store.all_patches()),
    )


def _ghost_object() -> Object:
    return Object(
        id="ghost#1",
        type="ghost",
        data={"name": "stale"},
        version=1,
        provenance={"created_by": "test"},
    )


def _ghost_relation() -> Relation:
    return Relation(
        id="rel_ghost",
        source="ghost#1",
        target="other#1",
        type="haunts",
        data={"note": "stale"},
        provenance={"created_by": "test"},
    )


def _ghost_patch() -> Patch:
    return Patch(
        id="patch_ghost",
        target="ghost#1",
        op="update",
        value={"name": "stale"},
        expected_version=1,
        proposed_by="test",
        rationale="leftover",
        evidence=["evt_stale"],
        status="proposed",
    )


def _seed(store: GraphStore, kinds: tuple[str, ...]) -> None:
    if "object" in kinds:
        store.put_object(_ghost_object())
    if "relation" in kinds:
        store.put_relation(_ghost_relation())
    if "patch" in kinds:
        store.put_patch(_ghost_patch())


def _person_run(tmp_path) -> tuple[str, str, str]:
    database = str(tmp_path / "runs.sqlite")
    graph = Graph()
    runtime = Runtime(graph, persist_to=database)
    person = graph.add_object("person", {"name": "Alice"})
    return database, runtime.run_id, person.id


class _EnumeratingGraphStore(GraphStore):
    """Third-party store that does not override ``is_empty``."""

    def __init__(self) -> None:
        self._objects: dict[str, Object] = {}
        self._relations: dict[str, Relation] = {}
        self._patches: dict[str, Patch] = {}

    def put_object(self, obj: Object) -> None:
        self._objects[obj.id] = obj

    def get_object(self, object_id: str) -> Object | None:
        return self._objects.get(object_id)

    def remove_object(self, object_id: str) -> None:
        self._objects.pop(object_id, None)

    def all_objects(self) -> list[Object]:
        return list(self._objects.values())

    def put_relation(self, rel: Relation) -> None:
        self._relations[rel.id] = rel

    def get_relation(self, relation_id: str) -> Relation | None:
        return self._relations.get(relation_id)

    def remove_relation(self, relation_id: str) -> None:
        self._relations.pop(relation_id, None)

    def all_relations(self) -> list[Relation]:
        return list(self._relations.values())

    def put_patch(self, patch: Patch) -> None:
        self._patches[patch.id] = patch

    def get_patch(self, patch_id: str) -> Patch | None:
        return self._patches.get(patch_id)

    def all_patches(self) -> list[Patch]:
        return list(self._patches.values())

    def remove_patch(self, patch_id: str) -> None:
        self._patches.pop(patch_id, None)


def _open_falkor(name: str):
    from activegraph.store.falkordb import FalkorDBGraphStore

    store = FalkorDBGraphStore(graph_name=name)
    store.clear()
    return store


@pytest.fixture(params=["memory", "falkordb"])
def projection_store(request) -> Iterator[GraphStore]:
    if request.param == "memory":
        yield InMemoryGraphStore()
        return
    try:
        store = _open_falkor(f"ag_load_{uuid.uuid4().hex[:8]}")
    except Exception as exc:  # noqa: BLE001 — optional backend
        pytest.skip(f"FalkorDB unavailable: {type(exc).__name__}: {exc}")
    try:
        yield store
    finally:
        store.clear()
        store.close()


# ---------- #81: load does not create runs ----------


def test_issue_81_unknown_explicit_run_is_not_created(tmp_path) -> None:
    """The reported repro: a missing run_id must not appear in list_runs()."""
    database = str(tmp_path / "runs.sqlite")
    missing_run_id = "run_missing"
    before = _runs(database)

    with pytest.raises(RunNotFoundError) as excinfo:
        Runtime.load(database, run_id=missing_run_id)

    after = _runs(database)
    err = excinfo.value
    assert before == []
    assert after == []
    assert err.reason == "missing"
    assert err.run_id == missing_run_id
    assert err.event_count == 0
    assert isinstance(err, FileNotFoundError)
    assert "persist_to" in str(err)
    assert SQLiteEventStore.catalog_status(database, missing_run_id) == (False, 0)


def test_unknown_run_id_via_sqlite_url_does_not_register(tmp_path) -> None:
    database = str(tmp_path / "runs.sqlite")
    SQLiteEventStore.list_runs(database)
    url = f"sqlite:///{database}"
    with pytest.raises(RunNotFoundError) as excinfo:
        Runtime.load(url, run_id="run_missing")
    assert excinfo.value.reason == "missing"
    assert _runs(database) == []


def test_missing_database_file_is_not_created_for_explicit_run_id(tmp_path) -> None:
    database = tmp_path / "does-not-exist.sqlite"
    with pytest.raises(RunNotFoundError) as excinfo:
        Runtime.load(str(database), run_id="run_missing")
    assert excinfo.value.reason == "missing"
    assert not database.exists()


def test_explicit_empty_run_still_loads(tmp_path) -> None:
    database = str(tmp_path / "runs.sqlite")
    created = Runtime(Graph(), persist_to=database)
    assert SQLiteEventStore.catalog_status(database, created.run_id) == (True, 0)

    loaded = Runtime.load(database, run_id=created.run_id)

    assert loaded.run_id == created.run_id
    assert list(loaded.graph.events) == []
    assert _runs(database) == [created.run_id]


def test_load_without_run_id_does_not_create_a_missing_file(tmp_path) -> None:
    database = tmp_path / "missing.sqlite"
    with pytest.raises(RunNotFoundError) as excinfo:
        Runtime.load(str(database))
    assert excinfo.value.reason == "empty_catalog"
    assert not database.exists()


def test_load_without_run_id_fails_when_catalog_is_empty(tmp_path) -> None:
    database = str(tmp_path / "runs.sqlite")
    assert _runs(database) == []

    with pytest.raises(RunNotFoundError) as excinfo:
        Runtime.load(database)

    err = excinfo.value
    assert err.reason == "empty_catalog"
    assert err.run_id is None
    assert "no runs found" in str(err)
    assert "persist_to" in str(err)
    assert _runs(database) == []
    assert isinstance(err, FileNotFoundError)


def test_load_without_run_id_still_opens_the_most_recent_run(tmp_path) -> None:
    database = str(tmp_path / "runs.sqlite")
    first = Runtime(Graph(), persist_to=database)
    first.graph.add_object("person", {"name": "older"})
    second = Runtime(Graph(), persist_to=database)
    second.graph.add_object("person", {"name": "newer"})

    loaded = Runtime.load(database)

    assert loaded.run_id == second.run_id
    assert _runs(database) == [first.run_id, second.run_id]


def test_orphan_events_are_not_repaired_into_a_run(tmp_path) -> None:
    database = str(tmp_path / "runs.sqlite")
    runtime = Runtime(Graph(), persist_to=database)
    runtime.graph.add_object("person", {"name": "Alice"})
    run_id = runtime.run_id
    assert runtime.graph.store is not None
    runtime.graph.store.close()

    conn = sqlite3.connect(database)
    conn.execute("DELETE FROM runs WHERE run_id = ?", (run_id,))
    conn.commit()
    conn.close()
    present, event_count = SQLiteEventStore.catalog_status(database, run_id)
    assert present is False
    assert event_count > 0

    with pytest.raises(RunNotFoundError) as excinfo:
        Runtime.load(database, run_id=run_id)

    assert excinfo.value.reason == "orphan_events"
    assert excinfo.value.event_count == event_count
    assert "canonical" in str(excinfo.value)
    assert SQLiteEventStore.catalog_status(database, run_id) == (False, event_count)
    assert _runs(database) == []


def test_unknown_run_does_not_disturb_an_existing_run(tmp_path) -> None:
    database, run_id, person_id = _person_run(tmp_path)
    before_events = SQLiteEventStore.catalog_status(database, run_id)

    with pytest.raises(RunNotFoundError):
        Runtime.load(database, run_id="run_missing")

    assert _runs(database) == [run_id]
    assert SQLiteEventStore.catalog_status(database, run_id) == before_events
    loaded = Runtime.load(database, run_id=run_id)
    assert [obj.id for obj in loaded.graph.all_objects()] == [person_id]


# ---------- #82: refuse a non-empty projection before replay ----------


def test_issue_82_stale_object_is_refused_before_replay(tmp_path) -> None:
    """The reported repro: ghost#1 must not sit beside person#1."""
    database, run_id, person_id = _person_run(tmp_path)
    projection = InMemoryGraphStore()
    seeded = Graph(graph_store=projection)
    ghost = seeded.add_object("ghost", {"name": "stale"})
    before = _projection(projection)
    _, event_count = SQLiteEventStore.catalog_status(database, run_id)

    with pytest.raises(NonEmptyGraphStoreError) as excinfo:
        Runtime.load(database, run_id=run_id, graph_store=projection)

    err = excinfo.value
    assert err.run_id == run_id
    assert err.objects == 1
    assert err.relations == 0
    assert err.patches == 0
    assert "InMemoryGraphStore" in str(err)
    assert "clear()" in str(err)
    assert _projection(projection) == before
    assert [obj.id for obj in projection.all_objects()] == [ghost.id]
    assert person_id not in {obj.id for obj in projection.all_objects()}
    assert SQLiteEventStore.catalog_status(database, run_id)[1] == event_count


@pytest.mark.parametrize(
    "kinds",
    [
        ("object",),
        ("relation",),
        ("patch",),
        ("object", "relation", "patch"),
    ],
)
def test_every_projected_entity_kind_blocks_replay(
    tmp_path, projection_store: GraphStore, kinds: tuple[str, ...]
) -> None:
    database, run_id, person_id = _person_run(tmp_path)
    _seed(projection_store, kinds)
    before = _projection(projection_store)

    with pytest.raises(NonEmptyGraphStoreError):
        Runtime.load(database, run_id=run_id, graph_store=projection_store)

    assert _projection(projection_store) == before
    assert person_id not in before[0]


def test_empty_store_replays_exactly_the_log(
    tmp_path, projection_store: GraphStore
) -> None:
    database, run_id, person_id = _person_run(tmp_path)
    assert projection_store.is_empty() is True

    loaded = Runtime.load(database, run_id=run_id, graph_store=projection_store)

    assert loaded.graph._state is projection_store  # noqa: SLF001
    assert [obj.id for obj in loaded.graph.all_objects()] == [person_id]
    assert "ghost#1" not in {obj.id for obj in projection_store.all_objects()}


def test_third_party_store_uses_enumeration_default(tmp_path) -> None:
    """Unknown backends are not skipped and are not always refused.

    The base ``is_empty`` allows replay only when the required
    enumerations are empty. A subclass that does not override it gets
    that behavior.
    """
    database, run_id, person_id = _person_run(tmp_path)
    empty = _EnumeratingGraphStore()
    assert type(empty).is_empty is GraphStore.is_empty
    loaded = Runtime.load(database, run_id=run_id, graph_store=empty)
    assert [obj.id for obj in loaded.graph.all_objects()] == [person_id]

    stale = _EnumeratingGraphStore()
    stale.put_object(_ghost_object())
    with pytest.raises(NonEmptyGraphStoreError):
        Runtime.load(database, run_id=run_id, graph_store=stale)
    assert [obj.id for obj in stale.all_objects()] == ["ghost#1"]


def test_compacted_replay_follows_the_same_rule(
    tmp_path, projection_store: GraphStore
) -> None:
    clear_registry()

    @behavior(name="seed_person", on=["goal.created"])
    def seed_person(event, graph, ctx):
        graph.add_object("person", {"name": event.payload.get("goal", "")})

    runtime = Runtime(
        Graph(ids=IDGen(), clock=FrozenClock()),
        behaviors=[seed_person],
    )
    runtime.run_goal("Alice")
    database = str(tmp_path / "compacted.sqlite")
    runtime.save_state(database)
    run_id = runtime.run_id
    expected_ids = sorted(obj.id for obj in runtime.graph.all_objects())
    assert runtime.graph.store is not None
    runtime.graph.store.close()
    del runtime

    compact(database, run_id)
    hot = list(SQLiteEventStore(database, run_id=run_id).iter_events())
    assert hot[0].type == "runtime.snapshot"

    _seed(projection_store, ("object", "relation", "patch"))
    before = _projection(projection_store)
    with pytest.raises(NonEmptyGraphStoreError):
        Runtime.load(database, run_id=run_id, graph_store=projection_store)
    assert _projection(projection_store) == before
    assert not any(obj_id in before[0] for obj_id in expected_ids)

    if isinstance(projection_store, InMemoryGraphStore):
        fresh: GraphStore = InMemoryGraphStore()
        closer = None
    else:
        fresh = _open_falkor(f"ag_load_fresh_{uuid.uuid4().hex[:8]}")
        closer = fresh.close
    try:
        loaded = Runtime.load(database, run_id=run_id, graph_store=fresh)
        assert sorted(obj.id for obj in loaded.graph.all_objects()) == expected_ids
        assert "ghost#1" not in {obj.id for obj in fresh.all_objects()}
    finally:
        if closer is not None:
            fresh.clear()
            closer()


# ---------- Postgres catalog parity (#81) ----------


@pytest.mark.postgres
@pytest.mark.skipif(
    PG_URL is None,
    reason="set ACTIVEGRAPH_TEST_POSTGRES_URL to run Postgres tests",
)
def test_postgres_unknown_explicit_run_is_not_created() -> None:
    from activegraph.store.postgres import PostgresEventStore

    assert PG_URL is not None
    missing = f"run_missing_{uuid.uuid4().hex[:8]}"
    before = {record.run_id for record in PostgresEventStore.list_runs(PG_URL)}
    assert missing not in before

    with pytest.raises(RunNotFoundError) as excinfo:
        Runtime.load(PG_URL, run_id=missing)

    after = {record.run_id for record in PostgresEventStore.list_runs(PG_URL)}
    assert excinfo.value.reason == "missing"
    assert missing not in after
    assert after == before
    assert PostgresEventStore.catalog_status(PG_URL, missing) == (False, 0)


@pytest.mark.postgres
@pytest.mark.skipif(
    PG_URL is None,
    reason="set ACTIVEGRAPH_TEST_POSTGRES_URL to run Postgres tests",
)
def test_postgres_explicit_empty_run_loads_and_orphan_events_fail_closed() -> None:
    import psycopg

    from activegraph.store.postgres import PostgresEventStore

    assert PG_URL is not None
    run_id = f"run_empty_{uuid.uuid4().hex[:8]}"
    created = Runtime(Graph(run_id=run_id), persist_to=PG_URL)
    loaded: Runtime | None = None
    try:
        assert PostgresEventStore.catalog_status(PG_URL, run_id)[0] is True
        loaded = Runtime.load(PG_URL, run_id=run_id)
        assert loaded.run_id == run_id
        assert list(loaded.graph.events) == []

        created.graph.add_object("person", {"name": "Ada"})
        with psycopg.connect(PG_URL, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM runs WHERE run_id = %s", (run_id,))
        present, event_count = PostgresEventStore.catalog_status(PG_URL, run_id)
        assert present is False
        assert event_count > 0
        with pytest.raises(RunNotFoundError) as excinfo:
            Runtime.load(PG_URL, run_id=run_id)
        assert excinfo.value.reason == "orphan_events"
        assert PostgresEventStore.catalog_status(PG_URL, run_id) == (
            False,
            event_count,
        )
    finally:
        for runtime in (created, loaded):
            if runtime is not None and runtime.graph.store is not None:
                runtime.graph.store.close()
        with psycopg.connect(PG_URL, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM events WHERE run_id = %s", (run_id,))
                cur.execute("DELETE FROM runs WHERE run_id = %s", (run_id,))
