"""v1.0 PR-A: ActiveGraphError format + ReplayError reference category.

CONTRACT v1.0 #3 locks the error message format. This file is the canary
for any drift in that format AND the snapshot baseline for every error
class as it migrates into the hierarchy. Each PR in the v1.0 error
series (PR-B through PR-F) adds new snapshot tests here; the harness
below is the same for every error class.

If the format changes intentionally, run with ``UPDATE_SNAPSHOTS=1`` and
update the doc-site reference page at ``docs/reference/errors/<slug>.md``
in the same commit.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from activegraph import (
    ActiveGraphError,
    AmbiguousBehaviorError,
    AmbiguousToolError,
    ApprovalNotFoundError,
    BehaviorNotFoundError,
    ConfigurationError,
    CorruptedEventPayloadError,
    DuplicateEventError,
    EventNotFoundError,
    ExecutionError,
    IncompatibleRuntimeState,
    InternalEvaluatorError,
    InvalidActivateAfter,
    InvalidArgumentType,
    InvalidPatchLifecycleState,
    InvalidRuntimeConfiguration,
    InvalidStoreURL,
    InvalidToolRegistration,
    LLMBehaviorError,
    MissingOptionalDependency,
    MissingProviderError,
    MissingToolError,
    NonSerializableEventError,
    PackConflictError,
    PackError,
    PackNotFoundError,
    PackSchemaViolation,
    PackVersionConflictError,
    PatternError,
    RegistrationError,
    ReplayDivergenceError,
    ReplayError,
    RuntimeContextRequiredError,
    SchemaVersionMismatch,
    StorageError,
    ToolError,
    ToolNotFoundError,
    UnknownToolError,
    UnsupportedPatternError,
)
from activegraph.errors import GITHUB_NEW_ISSUE_URL, internal_bug_fields


SNAPSHOTS_DIR = Path(__file__).parent / "snapshots" / "errors"


# ---------- harness ------------------------------------------------------


def _check_snapshot(name: str, err: Exception) -> None:
    """Compare ``str(err)`` against ``tests/snapshots/errors/<name>.txt``.

    Run with ``UPDATE_SNAPSHOTS=1`` to write the snapshot. Snapshots are
    byte-identical; trailing newline is part of the file so editors that
    auto-add one don't drift the diff.
    """
    path = SNAPSHOTS_DIR / f"{name}.txt"
    actual = str(err) + "\n"
    if os.environ.get("UPDATE_SNAPSHOTS"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(actual)
    assert path.exists(), (
        f"missing snapshot {path}. Run with UPDATE_SNAPSHOTS=1 to create it."
    )
    expected = path.read_text()
    assert actual == expected, (
        f"{name} drifted from snapshot. If intentional, run with "
        f"UPDATE_SNAPSHOTS=1 and update docs/reference/errors/ in the same "
        f"commit."
    )


# ---------- format invariants -------------------------------------------


_SECTIONS = ("What failed:", "Why:", "How to fix:", "More:")


def _make_dummy(cls: type[ActiveGraphError]) -> ActiveGraphError:
    return cls(
        "summary line for the snapshot",
        what_failed="the specific thing that broke (with a name)",
        why="the root cause, in one sentence",
        how_to_fix="run the canonical fix command",
    )


def _assert_format_compliant(err: Exception) -> None:
    """Verify a rendered error matches CONTRACT v1.0 #3 structurally.

    Structural rather than regex — the body of each section is allowed to
    span multiple lines with internal blank-line paragraph breaks, which
    a strict regex would reject. The check verifies: title line shape,
    every required section header appears in order, every section body
    is at least one indented line, ``More:`` body is a doc URL.
    """
    msg = str(err)
    first, _, rest = msg.partition("\n")
    assert ": " in first, f"missing title-line colon: {first!r}"
    class_name, _, summary = first.partition(": ")
    assert class_name == type(err).__name__, (
        f"title class {class_name!r} != {type(err).__name__!r}"
    )
    assert summary, "summary is empty"

    positions = [msg.find(f"\n{h}\n") for h in _SECTIONS]
    for header, pos in zip(_SECTIONS, positions):
        assert pos != -1, f"missing section {header!r}"
    assert positions == sorted(positions), (
        f"sections out of order: {list(zip(_SECTIONS, positions))}"
    )

    # Every section body has at least one continuation line at 2-space
    # indent (the format's lock-in column).
    for header in _SECTIONS[:-1]:
        body = msg.split(f"\n{header}\n", 1)[1].split("\n\n", 1)[0]
        assert body.startswith("  "), (
            f"section {header!r} body does not start with 2-space indent:\n{body!r}"
        )

    more_body = msg.split("\nMore:\n  ", 1)[1].strip()
    assert more_body.startswith("https://"), (
        f"More: body is not a URL: {more_body!r}"
    )


@pytest.mark.parametrize(
    "cls",
    [
        ConfigurationError,
        RegistrationError,
        ExecutionError,
        ReplayError,
        StorageError,
        PatternError,
        PackError,
    ],
)
def test_every_category_base_obeys_format(cls: type[ActiveGraphError]) -> None:
    """CONTRACT v1.0 #3 — every category base produces a message that
    matches the locked format. Structural check, multi-line bodies
    allowed."""
    _assert_format_compliant(_make_dummy(cls))


def test_every_category_base_exposes_structured_fields() -> None:
    """The four structured fields plus ``.context`` and ``.doc_url`` are
    the programmatic API. Tools (the doc-site error catalog, the
    machine-readable failure log) read these directly instead of
    parsing ``str(err)``."""
    err = _make_dummy(ConfigurationError)
    assert err.what_failed == "the specific thing that broke (with a name)"
    assert err.why == "the root cause, in one sentence"
    assert err.how_to_fix == "run the canonical fix command"
    assert err.context == {}
    assert err.doc_url.startswith("https://")
    assert err.doc_url.endswith("/errors/configuration-error")


def test_active_graph_error_is_the_root() -> None:
    """Every category base inherits from ``ActiveGraphError``. External
    code catching the root catches every framework error."""
    for cls in (
        ConfigurationError,
        RegistrationError,
        ExecutionError,
        ReplayError,
        StorageError,
        PatternError,
        PackError,
    ):
        assert issubclass(cls, ActiveGraphError), cls


def test_doc_slug_is_unique_per_category() -> None:
    """No two category bases share a ``_doc_slug``. Doc URLs must
    disambiguate categories."""
    slugs = [
        ConfigurationError._doc_slug,
        RegistrationError._doc_slug,
        ExecutionError._doc_slug,
        ReplayError._doc_slug,
        StorageError._doc_slug,
        PatternError._doc_slug,
        PackError._doc_slug,
    ]
    assert len(set(slugs)) == len(slugs)


# ---------- reference category: ReplayError ------------------------------


def test_replay_divergence_inherits_from_replay_error() -> None:
    """The reference leaf is wired into the new hierarchy. PR-B+ checks
    the same property for each migrated category."""
    assert issubclass(ReplayDivergenceError, ReplayError)
    assert issubclass(ReplayDivergenceError, ActiveGraphError)


def test_replay_divergence_preserves_legacy_signature() -> None:
    """CONTRACT v0.5 #7 — ``event_id``, ``expected``, ``actual`` are the
    public attributes. Existing tests access them directly. PR-A
    preserves the signature so the 384 v0–v0.9 tests stay valid."""
    err = ReplayDivergenceError(
        event_id="evt_042",
        expected="prompt_hash=a1b2c3d4e5f60718",
        actual="prompt_hash=z9y8x7w6v5u43210",
    )
    assert err.event_id == "evt_042"
    assert err.expected == "prompt_hash=a1b2c3d4e5f60718"
    assert err.actual == "prompt_hash=z9y8x7w6v5u43210"


def test_replay_divergence_prompt_hash_snapshot() -> None:
    """The high-stakes case: an LLM call's prompt hash drifted. The
    most-fired-during-fork-and-diff variant. Snapshot is exact."""
    err = ReplayDivergenceError(
        event_id="evt_042",
        expected="prompt_hash=a1b2c3d4e5f60718",
        actual="prompt_hash=z9y8x7w6v5u43210",
    )
    assert err.kind == "prompt_hash_mismatch"
    assert err.context["kind"] == "prompt_hash_mismatch"
    _assert_format_compliant(err)
    _check_snapshot("replay_divergence__prompt_hash_mismatch", err)


def test_replay_divergence_type_mismatch_snapshot() -> None:
    """The structural case: behaviors produced a different event type at
    the recorded position. Common when a behavior's ``where`` filter
    changed."""
    err = ReplayDivergenceError(
        event_id="evt_113",
        expected="object.created",
        actual="patch.applied",
    )
    assert err.kind == "type_mismatch"
    _assert_format_compliant(err)
    _check_snapshot("replay_divergence__type_mismatch", err)


def test_replay_divergence_short_live_snapshot() -> None:
    """Length mismatch, short live re-run: the recorded log had more
    events than the replay produced. Behavior fires were removed
    or short-circuited."""
    err = ReplayDivergenceError(
        event_id="evt_200",
        expected="behavior.completed",
        actual=None,
    )
    assert err.kind == "length_mismatch"
    _assert_format_compliant(err)
    _check_snapshot("replay_divergence__short_live", err)


def test_replay_divergence_extra_live_snapshot() -> None:
    """Length mismatch, extra live event: the replay produced an event
    the recorded log did not. A behavior was added or loosened."""
    err = ReplayDivergenceError(
        event_id="evt_201",
        expected="<no recorded event>",
        actual="event.emitted",
    )
    assert err.kind == "length_mismatch"
    _assert_format_compliant(err)
    _check_snapshot("replay_divergence__extra_live", err)


def test_replay_divergence_doc_url_uses_slug() -> None:
    """The ``More:`` line in every error points at a real doc page. CI
    will validate the URL resolves once the doc site is live (v1.0
    later PR). For now the URL must at least include the slug."""
    err = ReplayDivergenceError(
        event_id="evt_042",
        expected="prompt_hash=abc",
        actual="prompt_hash=def",
    )
    assert err.doc_url.endswith("/errors/replay-divergence-error")
    assert err.doc_url in str(err)


def test_replay_divergence_names_the_event_id() -> None:
    """CONTRACT v1.0 #7 — errors name names. The event id appears in
    the summary line and in the body, not just hidden in a context
    dict."""
    err = ReplayDivergenceError(
        event_id="evt_042",
        expected="prompt_hash=abc",
        actual="prompt_hash=def",
    )
    msg = str(err)
    # Summary line contains the event id.
    first_line = msg.split("\n", 1)[0]
    assert "evt_042" in first_line
    # Body's "What failed" section contains the event id.
    assert "evt_042" in msg.split("What failed:")[1].split("Why:")[0]
    # "How to fix" includes the event id in the suggested command.
    assert "evt_042" in msg.split("How to fix:")[1].split("More:")[0]


# ---------- PR-B: PatternError reference (UnsupportedPatternError) ------


def _parse(pattern: str):
    """Compile a pattern to force raise; returns the exception."""
    from activegraph.runtime.patterns import parse
    return parse(pattern)


def test_unsupported_pattern_inherits_from_pattern_error() -> None:
    """PR-B reference leaf is wired into the v1.0 hierarchy. Multi-
    inherits SyntaxError so user code that catches SyntaxError around
    pattern compilation continues to work."""
    assert issubclass(UnsupportedPatternError, PatternError)
    assert issubclass(UnsupportedPatternError, ActiveGraphError)
    assert issubclass(UnsupportedPatternError, SyntaxError)


def test_unsupported_pattern_preserves_at_attribute() -> None:
    """CONTRACT v0.7 — every raise carries the offending token in `.at`
    so the IDE / linter / log scrubber can highlight it. PR-B preserves
    the attribute through the v1.0 structured constructor."""
    err = UnsupportedPatternError.refused_feature(
        feature="OR",
        workaround="Register two behaviors.",
        at="OR",
    )
    assert err.at == "OR"
    assert err.context["at"] == "OR"


def test_unsupported_pattern_or_in_where_snapshot() -> None:
    """The canonical refused-feature error. Highest-traffic of the v0.7
    refusals (OR is the first thing Cypher users reach for). Recovery
    points at the 'register two behaviors' workaround."""
    err = UnsupportedPatternError.refused_feature(
        feature="OR",
        workaround=(
            "Register two behaviors, one per branch of the disjunction.\n"
            "Both fire independently; if both branches are true for the\n"
            "same event, both behaviors fire (which is usually what you\n"
            "want — OR-then-dedup is not).\n"
            "\n"
            "Example:\n"
            "  Instead of: WHERE c.confidence > 0.7 OR c.severity = 'high'\n"
            "  Register:   one behavior with WHERE c.confidence > 0.7\n"
            "              one behavior with WHERE c.severity = 'high'"
        ),
        why=(
            "OR in WHERE clauses can produce match-set ambiguity at the "
            "trace level: it's hard to tell, after the fact, which branch "
            "of the OR actually triggered. Registering two behaviors keeps "
            "every fire attributable to a specific pattern in the audit "
            "trail. See CONTRACT v0.7 #8."
        ),
        at="OR",
    )
    _assert_format_compliant(err)
    _check_snapshot("unsupported_pattern__or_in_where", err)


def test_unsupported_pattern_variable_length_path_snapshot() -> None:
    """Refused-feature: -[*]- syntax. Common Cypher idiom; the recovery
    explains the unbounded-cost rationale alongside the workaround."""
    with pytest.raises(UnsupportedPatternError) as excinfo:
        _parse("(a:claim)-[*]->(b:evidence)")
    _assert_format_compliant(excinfo.value)
    _check_snapshot("unsupported_pattern__variable_length_path", excinfo.value)


def test_unsupported_pattern_undirected_relationship_snapshot() -> None:
    """Refused-feature: (a)-[:rel]-(b). The recovery explains that
    direction is needed for the audit trail, not just a style choice."""
    with pytest.raises(UnsupportedPatternError) as excinfo:
        _parse("(a:claim)-[:supports]-(b:evidence)")
    _assert_format_compliant(excinfo.value)
    _check_snapshot("unsupported_pattern__undirected_relationship", excinfo.value)


def test_unsupported_pattern_optional_keyword_snapshot() -> None:
    """Refused-feature: OPTIONAL keyword. Tests the per-keyword
    workaround table."""
    with pytest.raises(UnsupportedPatternError) as excinfo:
        _parse("(a:claim) OPTIONAL")
    _assert_format_compliant(excinfo.value)
    _check_snapshot("unsupported_pattern__optional_keyword", excinfo.value)


def test_unsupported_pattern_unexpected_character_snapshot() -> None:
    """Syntax-error: parser cannot tokenize. Tests the syntax_error
    factory and its 'fix the syntax / see docs' recovery prose."""
    with pytest.raises(UnsupportedPatternError) as excinfo:
        _parse("(a:claim) @")
    _assert_format_compliant(excinfo.value)
    _check_snapshot("unsupported_pattern__unexpected_character", excinfo.value)


def test_unsupported_pattern_relationship_type_required_snapshot() -> None:
    """Syntax-error: a relationship without a type. Common typo —
    `(a)-[]->(b)` instead of `(a)-[:type]->(b)`. The recovery shows
    the expected syntax explicitly."""
    with pytest.raises(UnsupportedPatternError) as excinfo:
        _parse("(a:claim)-[]->(b:evidence)")
    _assert_format_compliant(excinfo.value)
    _check_snapshot("unsupported_pattern__relationship_type_required", excinfo.value)


def test_unsupported_pattern_doc_url_uses_slug() -> None:
    err = UnsupportedPatternError.refused_feature(
        feature="OR", workaround="...", at="OR"
    )
    assert err.doc_url.endswith("/errors/unsupported-pattern-error")
    assert err.doc_url in str(err)


def test_unsupported_pattern_names_the_offending_token() -> None:
    """CONTRACT v1.0 #7 — errors name names. The `at` token appears
    in the rendered message (the format includes context dict access
    indirectly via what_failed prose)."""
    err = UnsupportedPatternError.syntax_error(
        what="unexpected character at position 17",
        at="@!",
    )
    msg = str(err)
    assert "@!" in msg
    assert "position 17" in msg


# ---------- PR-C: StorageError category --------------------------------


def test_storage_leaves_inherit_from_storage_error() -> None:
    """Every PR-C leaf is wired into the v1.0 hierarchy."""
    for cls in (
        InvalidStoreURL,
        NonSerializableEventError,
        CorruptedEventPayloadError,
        SchemaVersionMismatch,
        EventNotFoundError,
        DuplicateEventError,
    ):
        assert issubclass(cls, StorageError), cls
        assert issubclass(cls, ActiveGraphError), cls


def test_storage_leaves_preserve_legacy_base_classes() -> None:
    """Multi-inheritance with stdlib base classes preserves the user
    code that catches the builtin around store operations."""
    # NonSerializableEventError used to be a plain TypeError.
    assert issubclass(NonSerializableEventError, TypeError)
    # InvalidStoreURL used to be a plain ValueError.
    assert issubclass(InvalidStoreURL, ValueError)
    # EventNotFoundError multi-inherits KeyError so `except KeyError`
    # around store lookups keeps working.
    assert issubclass(EventNotFoundError, KeyError)
    # DuplicateEventError multi-inherits ValueError for the same reason.
    assert issubclass(DuplicateEventError, ValueError)


def test_invalid_store_url_bare_path_snapshot() -> None:
    """The most common operator mistake — typing the SQLite file path
    without the scheme prefix. The fix is a one-line edit; the error
    should show the exact corrected URL."""
    from activegraph.store import parse_store_url
    with pytest.raises(InvalidStoreURL) as excinfo:
        parse_store_url("/tmp/run.db")
    _assert_format_compliant(excinfo.value)
    _check_snapshot("invalid_store_url__bare_path", excinfo.value)


def test_invalid_store_url_unsupported_scheme_snapshot() -> None:
    """User tries a database the framework doesn't support. The
    recovery enumerates supported schemes and points at the extension
    path (the EventStore protocol)."""
    from activegraph.store import parse_store_url
    with pytest.raises(InvalidStoreURL) as excinfo:
        parse_store_url("mysql://host/dbname")
    _assert_format_compliant(excinfo.value)
    _check_snapshot("invalid_store_url__unsupported_scheme", excinfo.value)


def test_non_serializable_event_snapshot() -> None:
    """A payload containing a Python value the strict adapter can't
    JSON-encode. The recovery names the offending field path and
    suggests three concrete fixes plus the adapter-extension path."""
    from activegraph.store.serde import encode_payload

    class Custom:
        pass

    payload = {"goal": "x", "nested": {"value": Custom()}}
    with pytest.raises(NonSerializableEventError) as excinfo:
        encode_payload(payload)
    _assert_format_compliant(excinfo.value)
    _check_snapshot("non_serializable_event", excinfo.value)


def test_corrupted_event_payload_snapshot() -> None:
    """Stored JSON that doesn't parse. Recovery prose points at how to
    inspect surrounding events and at the manual-repair path, without
    referencing CLI flags that don't exist yet."""
    from activegraph.store.serde import decode_payload
    with pytest.raises(CorruptedEventPayloadError) as excinfo:
        decode_payload('{"goal": "x", "broken":')
    _assert_format_compliant(excinfo.value)
    _check_snapshot("corrupted_event_payload", excinfo.value)


def test_schema_version_mismatch_snapshot() -> None:
    """The store was written by a different activegraph build. Recovery
    enumerates three concrete actions (upgrade, migrate, drop)."""
    from activegraph import __version__ as _aw_version
    err = SchemaVersionMismatch(
        f"sqlite store schema_version '99' does not match this build's expected '1'",
        what_failed=(
            f"The SQLite store records schema_version='99' in its meta table, "
            f"but activegraph {_aw_version} expects schema_version='1'."
        ),
        why=(
            "The store file format evolves with the framework. The runtime "
            "refuses to read a store with a different schema_version rather "
            "than risk silent data loss — a newer framework might interpret "
            "columns differently than the writer did, and an older framework "
            "might drop fields it doesn't recognize. Either direction would "
            "corrupt the audit trail."
        ),
        how_to_fix=(
            f"One of three actions:\n"
            f"  1. Install the activegraph version that wrote this store\n"
            f"     (whichever shipped schema_version='99').\n"
            f"  2. Migrate the run to a store written by this build:\n"
            f"     activegraph migrate <src-url> <new-dst-url>\n"
            f"     The destination is written with the current schema.\n"
            f"  3. If the store is empty or expendable, delete and re-run.\n"
            f"\n"
            f"Schema version history is documented in CHANGELOG.md."
        ),
        context={
            "found_version": "99",
            "expected_version": "1",
            "activegraph_version": _aw_version,
            "driver": "sqlite",
        },
    )
    _assert_format_compliant(err)
    _check_snapshot("schema_version_mismatch", err)


def test_event_not_found_snapshot() -> None:
    """The high-traffic store lookup error. Fires from inspect, fork,
    causal-chain, basically every operator command that names an event
    id. Recovery points at `activegraph inspect ... --tail` so the
    operator can see which ids actually exist."""
    from activegraph.store import InMemoryEventStore
    store = InMemoryEventStore(run_id="run_test")
    with pytest.raises(EventNotFoundError) as excinfo:
        list(store.iter_events(after="evt_does_not_exist"))
    _assert_format_compliant(excinfo.value)
    _check_snapshot("event_not_found", excinfo.value)


def test_event_not_found_is_a_key_error() -> None:
    """User code catching KeyError around store lookups keeps working."""
    from activegraph.store import InMemoryEventStore
    store = InMemoryEventStore(run_id="run_test")
    with pytest.raises(KeyError):
        list(store.iter_events(after="evt_does_not_exist"))


def test_duplicate_event_snapshot() -> None:
    """Hand-constructed events colliding on id. Voice frames this as
    a programmer error (the id generator is monotonic in normal use)."""
    from activegraph import Event
    from activegraph.store import InMemoryEventStore
    store = InMemoryEventStore(run_id="run_test")
    e1 = Event(id="evt_001", type="goal.created", payload={}, timestamp="2026-05-17T00:00:00Z")
    e2 = Event(id="evt_001", type="goal.created", payload={}, timestamp="2026-05-17T00:00:01Z")
    store.append(e1)
    with pytest.raises(DuplicateEventError) as excinfo:
        store.append(e2)
    _assert_format_compliant(excinfo.value)
    _check_snapshot("duplicate_event", excinfo.value)


def test_duplicate_event_is_a_value_error() -> None:
    """User code catching ValueError around appends keeps working."""
    from activegraph import Event
    from activegraph.store import InMemoryEventStore
    store = InMemoryEventStore(run_id="run_test")
    e1 = Event(id="evt_001", type="goal.created", payload={}, timestamp="2026-05-17T00:00:00Z")
    store.append(e1)
    with pytest.raises(ValueError):
        store.append(e1)


# ---------- PR-D: ExecutionError category ------------------------------


def test_exec_leaves_inherit_from_execution_error() -> None:
    """Every PR-D leaf is wired into the v1.0 hierarchy."""
    for cls in (LLMBehaviorError, ToolError, UnknownToolError, ApprovalNotFoundError):
        assert issubclass(cls, ExecutionError), cls
        assert issubclass(cls, ActiveGraphError), cls


def test_exec_leaves_preserve_legacy_base_classes() -> None:
    """The carriers and lookup error preserve back-compat classes:
    ToolError stays Exception; UnknownToolError keeps RuntimeError;
    ApprovalNotFoundError keeps LookupError."""
    assert issubclass(UnknownToolError, RuntimeError)
    assert issubclass(ApprovalNotFoundError, LookupError)


def test_llm_behavior_error_preserves_reason_signature() -> None:
    """CONTRACT v0.6 #11 — the (reason, message, payload_extras)
    constructor is the wire contract between LLM providers and the
    runtime's behavior.failed event. PR-D preserves the signature so
    the ~8 internal raise sites in providers do not need to change."""
    err = LLMBehaviorError(
        "llm.parse_error",
        "no JSON found",
        payload_extras={"raw_text": "<...>"},
    )
    assert err.reason == "llm.parse_error"
    assert err.payload_extras == {"raw_text": "<...>"}
    assert err.context["reason"] == "llm.parse_error"
    assert err.context["payload_extras"] == {"raw_text": "<...>"}


def test_tool_error_preserves_reason_signature() -> None:
    err = ToolError(
        "tool.timeout",
        "took 31s, exceeds 30s ceiling",
        payload_extras={"elapsed_seconds": 31.0},
    )
    assert err.reason == "tool.timeout"
    assert err.payload_extras == {"elapsed_seconds": 31.0}


def test_llm_behavior_error_parse_error_snapshot() -> None:
    """The highest-traffic LLM failure mode — the model returned
    something that wasn't JSON. Recovery names both fixture and live
    cases."""
    err = LLMBehaviorError(
        "llm.parse_error",
        "Expecting value: line 1 column 1 (char 0)",
    )
    _assert_format_compliant(err)
    _check_snapshot("llm_behavior_error__parse_error", err)


def test_llm_behavior_error_schema_violation_snapshot() -> None:
    """The structural LLM failure — JSON parsed, but didn't match the
    declared output_schema. Recovery walks through the typical fix
    paths."""
    err = LLMBehaviorError(
        "llm.schema_violation",
        "ClaimList: claims.0.confidence: Input should be a valid number",
    )
    _assert_format_compliant(err)
    _check_snapshot("llm_behavior_error__schema_violation", err)


def test_llm_behavior_error_fixture_missing_snapshot() -> None:
    """The fork-and-replay failure — the recorded provider has no
    matching fixture. Recovery walks through the re-recording flow."""
    err = LLMBehaviorError(
        "llm.fixture_missing",
        "no recorded fixture for prompt_hash=a1b2c3d4 in /tmp/fixtures",
    )
    _assert_format_compliant(err)
    _check_snapshot("llm_behavior_error__fixture_missing", err)


def test_llm_behavior_error_unknown_reason_uses_fallback() -> None:
    """A reason code outside the table still produces a format-compliant
    message via the fallback prose."""
    err = LLMBehaviorError(
        "llm.custom_provider_error",
        "model returned 503",
    )
    _assert_format_compliant(err)
    assert "llm.custom_provider_error" in str(err)


def test_tool_error_timeout_snapshot() -> None:
    """The canonical tool failure — exceeded declared timeout. Recovery
    explains the trade-off between raising the timeout vs. retrying in
    the calling behavior."""
    err = ToolError(
        "tool.timeout",
        "fetch_company_docs took 35.2s, exceeds 30s ceiling",
    )
    _assert_format_compliant(err)
    _check_snapshot("tool_error__timeout", err)


def test_tool_error_execution_error_snapshot() -> None:
    """The catch-all tool failure — body raised. Recovery points at
    payload_extras for the original exception."""
    err = ToolError(
        "tool.execution_error",
        "TypeError: unsupported operand type(s) for +: 'NoneType' and 'int'",
    )
    _assert_format_compliant(err)
    _check_snapshot("tool_error__execution_error", err)


def test_unknown_tool_error_snapshot() -> None:
    """The LLM asked for a tool the behavior didn't declare. Recovery
    enumerates the declared tools so the operator can see the mismatch
    without grep-ing source."""
    err = UnknownToolError(
        "LLM called tool 'web_search' which is not declared on @llm_behavior(tools=[...])",
        tool_name="web_search",
        behavior_name="diligence.researcher",
        declared_tools=("diligence.fetch_company_docs", "diligence.fetch_filings"),
    )
    _assert_format_compliant(err)
    _check_snapshot("unknown_tool_error", err)


def test_approval_not_found_snapshot() -> None:
    """Pending-approval lookup miss. Recovery points at rt.pending_approvals()
    and the diligence demo's canonical pattern."""
    err = ApprovalNotFoundError("approval_999", pending_count=2)
    _assert_format_compliant(err)
    _check_snapshot("approval_not_found", err)


def test_approval_not_found_is_a_lookup_error() -> None:
    """User code catching LookupError around the approval API keeps
    working."""
    err = ApprovalNotFoundError("approval_999", pending_count=0)
    assert isinstance(err, LookupError)


# ---------- PR-E: RegistrationError category ---------------------------
#
# Snapshots written in REVERSE AUDIT ORDER per the PR-E discipline
# note: the least-well-understood leaves come first so they get the
# freshest attention. The standard set by the hardest leaf becomes the
# floor for the easier ones.


def test_registration_leaves_inherit_from_registration_error() -> None:
    """Every PR-E leaf — new or re-parented — is in the v1.0 hierarchy."""
    for cls in (
        MissingProviderError,
        MissingToolError,
        MissingOptionalDependency,
        BehaviorNotFoundError,
        AmbiguousBehaviorError,
        ToolNotFoundError,
        AmbiguousToolError,
        InvalidActivateAfter,
        InvalidToolRegistration,
        PackNotFoundError,
        PackConflictError,
        PackVersionConflictError,
    ):
        assert issubclass(cls, RegistrationError), cls
        assert issubclass(cls, ActiveGraphError), cls


def test_pack_registration_leaves_keep_pack_error_lineage() -> None:
    """The Pack* registration leaves multi-inherit RegistrationError +
    PackError so `except PackError` and `except RegistrationError` both
    catch them. CONTRACT v1.0 PR-E backward-compat clause."""
    for cls in (PackConflictError, PackVersionConflictError, PackNotFoundError):
        if cls is PackNotFoundError:
            # PackNotFoundError is new; it doesn't inherit from PackError.
            continue
        assert issubclass(cls, PackError), cls


def test_registration_leaves_preserve_legacy_base_classes() -> None:
    """Multi-inheritance preserves builtin lineage for existing
    catches:
      - MissingProviderError keeps RuntimeError
      - MissingToolError keeps RuntimeError
      - MissingOptionalDependency keeps ImportError
      - BehaviorNotFoundError / ToolNotFoundError / PackNotFoundError
        keep LookupError
      - AmbiguousBehaviorError / AmbiguousToolError keep ValueError
      - InvalidActivateAfter keeps ValueError
      - InvalidToolRegistration keeps TypeError
    """
    assert issubclass(MissingProviderError, RuntimeError)
    assert issubclass(MissingToolError, RuntimeError)
    assert issubclass(MissingOptionalDependency, ImportError)
    assert issubclass(BehaviorNotFoundError, LookupError)
    assert issubclass(ToolNotFoundError, LookupError)
    assert issubclass(PackNotFoundError, LookupError)
    assert issubclass(AmbiguousBehaviorError, ValueError)
    assert issubclass(AmbiguousToolError, ValueError)
    assert issubclass(InvalidActivateAfter, ValueError)
    assert issubclass(InvalidToolRegistration, TypeError)


# --- Reverse-audit-order snapshots (hardest first) ---


def test_pack_version_conflict_snapshot() -> None:
    """A runtime cannot hold two versions of the same pack. The recovery
    walks through the three concrete actions (rename, fresh runtime,
    pick-one)."""
    err = PackVersionConflictError(
        "pack 'diligence': already loaded version '0.1.0', attempted to load version '0.2.0'",
        what_failed=(
            "runtime.load_pack('diligence', version='0.2.0') was rejected "
            "because the runtime already holds 'diligence' version '0.1.0'."
        ),
        why=(
            "A runtime can hold at most one version of any pack. Two "
            "versions would compete for the same canonical names in the "
            "registry — `pack.behavior_name` would resolve differently "
            "depending on dispatch order, which would silently corrupt the "
            "audit trail."
        ),
        how_to_fix=(
            "Pick one version. If you need both behaviors, the older "
            "version's namespace can be retained under a renamed pack: "
            "copy the pack, change its `name=` declaration, and load both. "
            "The two versions then have distinct canonical namespaces.\n"
            "\n"
            "To unload the current version and load the new one, construct "
            "a fresh Runtime — load_pack does not support version swapping "
            "in place."
        ),
        context={
            "pack": "diligence",
            "loaded_version": "0.1.0",
            "attempted_version": "0.2.0",
        },
    )
    _assert_format_compliant(err)
    _check_snapshot("pack_version_conflict", err)


def test_pack_conflict_behavior_snapshot() -> None:
    """Two packs declaring the same canonical behavior name. The hardest
    PR-E case to write recovery prose for because the choice between
    'pick one' / 'rename' / 'separate runtime' depends on the user's
    intent."""
    err = PackConflictError(
        "behavior name conflict: 'diligence.researcher' declared by both pack 'diligence' and pack 'research'",
        what_failed=(
            "runtime.load_pack('research') was rejected: the behavior name "
            "'diligence.researcher' is already registered by pack 'diligence'."
        ),
        why=(
            "Canonical names in the runtime registry are unique across "
            "loaded packs. Two packs claiming the same canonical name "
            "would silently route dispatch one way or the other depending "
            "on pack-load order; the runtime refuses the load instead so "
            "the conflict is visible and the operator decides which pack "
            "to keep."
        ),
        how_to_fix=(
            "One of three actions:\n"
            "  1. Don't load both packs in the same runtime — pick one.\n"
            "  2. Rename one pack: copy its source, change the\n"
            "     `Pack(name=...)` declaration, re-install, and load\n"
            "     under the new name. The behaviors are then under\n"
            "     a different canonical prefix.\n"
            "  3. If both behaviors should run, the second pack's\n"
            "     pyproject can re-export the behavior under a\n"
            "     different name within its declaration."
        ),
        context={
            "kind": "behavior",
            "canonical": "diligence.researcher",
            "owner_pack": "diligence",
            "conflicting_pack": "research",
        },
    )
    _assert_format_compliant(err)
    _check_snapshot("pack_conflict__behavior", err)


def test_ambiguous_behavior_snapshot() -> None:
    """Short name resolves to behaviors in multiple loaded packs. Recovery
    shows the canonical form using one of the conflicting packs as the
    example so the operator can copy-paste."""
    err = AmbiguousBehaviorError(
        "researcher", packs=("diligence", "research"),
    )
    _assert_format_compliant(err)
    _check_snapshot("ambiguous_behavior", err)


def test_ambiguous_tool_snapshot() -> None:
    err = AmbiguousToolError(
        "fetch_docs", packs=("diligence", "research"),
    )
    _assert_format_compliant(err)
    _check_snapshot("ambiguous_tool", err)


def test_pack_not_found_snapshot() -> None:
    """Entry-point discovery turned up nothing. Recovery shows how to
    list discovered packs and the exact pyproject.toml entry-point
    declaration."""
    err = PackNotFoundError(
        "diligence",
        installed=("research", "memory"),
    )
    _assert_format_compliant(err)
    _check_snapshot("pack_not_found", err)


def test_missing_optional_dependency_postgres_snapshot() -> None:
    """The shared MissingOptionalDependency leaf — Postgres case.
    Pattern is uniform across the three call sites (postgres, prometheus,
    pydantic), only the package/feature/extras vary."""
    err = MissingOptionalDependency(
        package="psycopg",
        feature="PostgresEventStore",
        extras="postgres",
    )
    _assert_format_compliant(err)
    _check_snapshot("missing_optional_dependency__postgres", err)


def test_missing_provider_snapshot() -> None:
    """@llm_behavior was registered but the runtime has no provider.
    Re-parented from RuntimeError; recovery shows both real-provider and
    recorded-provider construction."""
    err = MissingProviderError(behavior_name="diligence.researcher")
    _assert_format_compliant(err)
    _check_snapshot("missing_provider", err)


def test_missing_tool_snapshot() -> None:
    """@llm_behavior declares a tool the runtime can't find. Recovery
    enumerates registered tools and points at both Runtime(tools=) and
    load_pack."""
    err = MissingToolError(
        "web_search",
        behavior_name="diligence.researcher",
        registered=("diligence.fetch_company_docs", "diligence.fetch_filings"),
    )
    _assert_format_compliant(err)
    _check_snapshot("missing_tool", err)


def test_behavior_not_found_snapshot() -> None:
    err = BehaviorNotFoundError(
        "extract_claims",
        registered=("diligence.researcher", "diligence.memo_synthesizer"),
        pack_state=True,
    )
    _assert_format_compliant(err)
    _check_snapshot("behavior_not_found", err)


def test_tool_not_found_snapshot() -> None:
    err = ToolNotFoundError(
        "fetch_pdfs",
        registered=("diligence.fetch_company_docs", "diligence.fetch_filings"),
    )
    _assert_format_compliant(err)
    _check_snapshot("tool_not_found", err)


def test_invalid_activate_after_wall_clock_snapshot() -> None:
    """activate_after=`5 seconds` — the most common operator mistake.
    Recovery explains the event-count vs wall-clock distinction
    explicitly because the distinction is load-bearing for replay."""
    from activegraph.runtime.scheduler import parse_activate_after
    with pytest.raises(InvalidActivateAfter) as excinfo:
        parse_activate_after("5 seconds")
    _assert_format_compliant(excinfo.value)
    _check_snapshot("invalid_activate_after__wall_clock", excinfo.value)


def test_invalid_activate_after_unparseable_snapshot() -> None:
    from activegraph.runtime.scheduler import parse_activate_after
    with pytest.raises(InvalidActivateAfter) as excinfo:
        parse_activate_after("abc")
    _assert_format_compliant(excinfo.value)
    _check_snapshot("invalid_activate_after__unparseable", excinfo.value)


def test_invalid_tool_registration_snapshot() -> None:
    """Runtime(tools=[some_function]) — common mistake of forgetting
    @tool decorator. Recovery shows the @tool decorator usage explicitly."""
    def bare_function():
        return 42
    err = InvalidToolRegistration(bare_function)
    _assert_format_compliant(err)
    _check_snapshot("invalid_tool_registration", err)


# ---------- PR-F: ConfigurationError category --------------------------
#
# Snapshots in reverse-audit-order. The hardest leaves per PR-F's audit
# turned out to be the cross-category ones: RuntimeContextRequiredError
# and InvalidPatchLifecycleState are classified as ExecutionError (not
# ConfigurationError) because they fire from within an executing
# behavior or during patch lifecycle, not at static construction time.
# Writing those first set the bar for the more mechanical Configuration
# leaves.


def test_config_leaves_inherit_from_configuration_error() -> None:
    """The three ConfigurationError leaves are in the v1.0 hierarchy."""
    for cls in (
        InvalidRuntimeConfiguration,
        InvalidArgumentType,
        IncompatibleRuntimeState,
    ):
        assert issubclass(cls, ConfigurationError), cls
        assert issubclass(cls, ActiveGraphError), cls


def test_config_leaves_preserve_legacy_base_classes() -> None:
    """Multi-inheritance preserves builtin lineage:
      - InvalidRuntimeConfiguration keeps ValueError
      - InvalidArgumentType keeps TypeError
      - IncompatibleRuntimeState keeps RuntimeError
    """
    assert issubclass(InvalidRuntimeConfiguration, ValueError)
    assert issubclass(InvalidArgumentType, TypeError)
    assert issubclass(IncompatibleRuntimeState, RuntimeError)


def test_pr_f_cross_category_leaves_are_execution() -> None:
    """PR-F audit produced 2 leaves classified as ExecutionError, not
    ConfigurationError. The classification matters because the doc-site
    cross-reference at /concepts/failure-model walks readers from the
    error class to its category; misclassifying would land readers on
    the wrong page."""
    assert issubclass(RuntimeContextRequiredError, ExecutionError)
    assert issubclass(InvalidPatchLifecycleState, ExecutionError)
    assert not issubclass(RuntimeContextRequiredError, ConfigurationError)
    assert not issubclass(InvalidPatchLifecycleState, ConfigurationError)


# --- Reverse-audit-order snapshots (hardest first) ---


def test_runtime_context_required_snapshot() -> None:
    """The cross-category leaf: looked like a configuration error on
    the PR-E hand-off list but classified as ExecutionError on closer
    reading. The recovery prose references /concepts/failure-model as
    the canonical cross-reference for 'when the framework prefers
    exceptions over silent no-ops.'"""
    err = RuntimeContextRequiredError(method="ctx.propose_object")
    _assert_format_compliant(err)
    _check_snapshot("runtime_context_required", err)


def test_invalid_patch_lifecycle_state_snapshot() -> None:
    """The other cross-category leaf: patch lifecycle invariant fires
    during execution. The recovery prose references
    /concepts/failure-model for the patch-lifecycle rules."""
    err = InvalidPatchLifecycleState(patch_id="patch_017", current_status="applied")
    _assert_format_compliant(err)
    _check_snapshot("invalid_patch_lifecycle_state", err)


def test_reserved_field_error_snapshot() -> None:
    """CONTRACT v1.10 #2: the loud replacement for the silent
    provenance strip. The recovery prose names the sanctioned kwargs
    (actor/caused_by/evidence) and the read path (obj.provenance)."""
    from activegraph import ReservedFieldError

    err = ReservedFieldError(field="provenance", api="add_object", param="data")
    _assert_format_compliant(err)
    _check_snapshot("reserved_field__provenance", err)


def test_incompatible_runtime_state_fork_snapshot() -> None:
    """fork() on a non-SQLite runtime. Recovery walks through the
    migrate-then-fork pattern and flags the Postgres-native-fork gap
    as a v1.1 follow-on."""
    err = IncompatibleRuntimeState(
        "runtime.fork() requires a SQLite-backed runtime (current: PostgresEventStore)",
        what_failed=(
            "runtime.fork() was called on a runtime with "
            "PostgresEventStore. The fork primitive currently only "
            "supports SQLite-backed runtimes."
        ),
        why=(
            "Fork copies events up to the fork point using the store's "
            "native primitives (SQLite uses a direct SQL copy under a "
            "single transaction). Postgres has a different transactional "
            "shape and an in-memory store has no copy primitive at all. "
            "v0.8 deliberately scoped the fork command to SQLite first — "
            "the limitation is documented in CONTRACT v0.8 #5."
        ),
        how_to_fix=(
            "Migrate the run to a SQLite store first, then fork:\n"
            "    activegraph migrate --from <current-url> --to sqlite:///fork-source.db\n"
            "    activegraph fork sqlite:///fork-source.db --run-id <run> --at-event <evt>\n"
            "\n"
            "For Postgres-native forking, file an issue — the primitive "
            "shape (transactional copy of events up to a seq cutoff) is "
            "known, and a contributor with Postgres operational experience "
            "could land it as a v1.1 follow-on."
        ),
        context={"current_store_kind": "PostgresEventStore"},
    )
    _assert_format_compliant(err)
    _check_snapshot("incompatible_runtime_state__fork", err)


def test_incompatible_runtime_state_attach_store_snapshot() -> None:
    """The other IncompatibleRuntimeState case — Graph.attach_store
    when a store is already attached. Recovery references migrate as
    the way to copy a run to a different store."""
    err = IncompatibleRuntimeState(
        "graph already has a store attached",
        what_failed=(
            "Graph.attach_store() was called, but this graph already has "
            "a store. Stores attach at most once per graph lifetime."
        ),
        why=(
            "A graph's store is the durability target for every event it "
            "emits. Re-attaching a second store would either (a) split the "
            "event log across two stores, with subsequent events going to "
            "the new one and earlier events stuck in the old, or (b) try "
            "to copy the old log to the new store, which is a migration, "
            "not an attach. The framework refuses re-attach so neither "
            "failure mode is reachable silently."
        ),
        how_to_fix=(
            "If you want to copy the graph's run to a new store, use the "
            "migration primitive on the existing store's URL after the "
            "run completes:\n"
            "    activegraph migrate --from <old-url> --to <new-url>\n"
            "\n"
            "If the graph is fresh and the existing store is a placeholder "
            "(e.g., from a test fixture), construct a new Graph rather "
            "than re-attaching."
        ),
    )
    _assert_format_compliant(err)
    _check_snapshot("incompatible_runtime_state__attach_store", err)


def test_invalid_argument_type_postgres_target_snapshot() -> None:
    """PostgresEventStore target type check. Recovery enumerates the
    three accepted types with concrete usage."""
    err = InvalidArgumentType(
        "PostgresEventStore target has wrong type (got int)",
        what_failed=(
            "PostgresEventStore was constructed with a target of type int:\n"
            "  value: 42\n  type:  int\n"
            "Accepted types are: a `postgres://...` URL string, a "
            "`psycopg.Connection`, or a `psycopg_pool.ConnectionPool`."
        ),
        why=(
            "PostgresEventStore's constructor branches on the target's "
            "type — strings open a fresh connection, Connections are "
            "borrowed without ownership, and ConnectionPools are checked "
            "out per operation. An unknown type has no defined connection "
            "lifecycle, and a fuzzy match would silently leak connections "
            "or double-close them."
        ),
        how_to_fix=(
            "Pass one of:\n"
            "    PostgresEventStore('postgres://host/dbname', run_id=...)\n"
            "    PostgresEventStore(my_psycopg_connection, run_id=...)\n"
            "    PostgresEventStore(my_connection_pool, run_id=...)\n"
            "\n"
            "If you already have a SQLAlchemy engine or another "
            "abstraction, extract a raw psycopg.Connection from it and "
            "pass that."
        ),
        context={"type": "int", "repr": "42"},
    )
    _assert_format_compliant(err)
    _check_snapshot("invalid_argument_type__postgres_target", err)


def test_invalid_runtime_config_conflicting_args_snapshot() -> None:
    """Runtime(persist_to=, store=) — the most common misconfiguration."""
    err = InvalidRuntimeConfiguration(
        "Runtime(...) was passed both `persist_to=` and `store=`",
        what_failed=(
            "Runtime construction received both a `persist_to=` path and "
            "an explicit `store=` instance. The two kwargs are alternative "
            "ways to attach storage — only one can be used per Runtime."
        ),
        why=(
            "`persist_to=` is shorthand for 'open a SQLite store at this "
            "path and attach it.' `store=` is the explicit form for any "
            "EventStore implementation. If both were accepted, the runtime "
            "would have to pick one or merge them, and silent precedence "
            "rules would surface as bugs the first time an operator "
            "switched stores."
        ),
        how_to_fix=(
            "Pass exactly one:\n"
            "    Runtime(graph, persist_to='/path/to/run.db')\n"
            "or:\n"
            "    Runtime(graph, store=SQLiteEventStore('/path/to/run.db'))\n"
            "\n"
            "The two forms produce equivalent runtimes for SQLite. Use "
            "`store=` when you need a non-SQLite backend or want to share "
            "an open store across runtimes."
        ),
    )
    _assert_format_compliant(err)
    _check_snapshot("invalid_runtime_config__conflicting_args", err)


def test_invalid_runtime_config_missing_arg_snapshot() -> None:
    """save_state(path=) required when no store attached. Tests
    InvalidRuntimeConfiguration with the missing-required-arg shape."""
    err = InvalidRuntimeConfiguration(
        "save_state() requires path= when no store is attached",
        what_failed=(
            "runtime.save_state() was called without a `path=` argument, "
            "but this runtime has no store attached. Without either, "
            "save_state has nowhere to write."
        ),
        why=(
            "save_state() is the bridge between an in-memory runtime and "
            "a durable store. It needs either a pre-attached store (from "
            "Runtime construction) or an explicit `path=` argument naming "
            "a SQLite file. Defaulting to a temp file would silently lose "
            "runs the next time the process exited."
        ),
        how_to_fix=(
            "Either attach a store at construction time:\n"
            "    rt = Runtime(graph, persist_to='/path/to/run.db')\n"
            "    rt.run_goal('...')\n"
            "    rt.save_state()\n"
            "or pass a path explicitly:\n"
            "    rt.save_state(path='/path/to/run.db')\n"
            "\n"
            "For ephemeral runs that should not persist, omit save_state() "
            "— the in-memory graph is the run's lifetime."
        ),
    )
    _assert_format_compliant(err)
    _check_snapshot("invalid_runtime_config__missing_arg", err)


def test_invalid_runtime_config_out_of_range_snapshot() -> None:
    """status(recent=-1). Out-of-range argument."""
    err = InvalidRuntimeConfiguration(
        "runtime.status(recent=-1) — recent must be >= 0",
        what_failed=(
            "runtime.status(recent=-1) was called with a negative count. "
            "The `recent` argument controls the length of the "
            "`recent_events` tail in the status snapshot."
        ),
        why=(
            "A negative recent count has no defined semantics — the tail "
            "length is a non-negative integer by construction. The "
            "framework refuses the call rather than silently coerce to "
            "zero, because the caller's intent is ambiguous (did they "
            "mean zero? did they compute the value and end up with a "
            "negative? did they want 'all events' and pass -1 from "
            "another API's convention?)."
        ),
        how_to_fix=(
            "Pass a non-negative integer:\n"
            "    rt.status(recent=20)    # last 20 events\n"
            "    rt.status(recent=0)     # no recent events in the\n"
            "                            # snapshot (just totals)\n"
            "\n"
            "To get every event, read `rt.graph.events` directly rather "
            "than passing a large `recent`."
        ),
        context={"recent": -1},
    )
    _assert_format_compliant(err)
    _check_snapshot("invalid_runtime_config__out_of_range", err)


def test_runtime_context_required_is_a_runtime_error() -> None:
    """User code catching RuntimeError around behavior dispatch keeps
    working."""
    err = RuntimeContextRequiredError()
    assert isinstance(err, RuntimeError)


def test_invalid_patch_lifecycle_state_is_a_value_error() -> None:
    err = InvalidPatchLifecycleState(patch_id="patch_001", current_status="applied")
    assert isinstance(err, ValueError)


def test_config_recovery_prose_references_failure_model() -> None:
    """Per CONTRACT v1.0 #4b addendum, snapshot recovery prose for the
    cross-category ExecutionError leaves references the canonical
    /concepts/failure-model URL so readers land on the right page when
    the doc site builds."""
    ctx_err = RuntimeContextRequiredError()
    patch_err = InvalidPatchLifecycleState(patch_id="p", current_status="applied")
    assert "/concepts/failure-model" in str(ctx_err)
    assert "/concepts/failure-model" in str(patch_err)


# ---------- PR-G subsection 1: PackError category (PackSchemaViolation)


def test_pack_schema_violation_for_object_snapshot() -> None:
    """Object data fails the pack's declared schema. The most common
    PackSchemaViolation case — operator passed wrong-shape data to
    graph.add_object after the pack is loaded."""
    from pydantic import BaseModel, ValidationError

    class Claim(BaseModel):
        text: str
        confidence: float

    try:
        Claim(text="x")  # missing required 'confidence'
    except ValidationError as ve:
        err = PackSchemaViolation.for_object(
            object_type="claim",
            validation_error=ve,
            pack_name="diligence",
        )
    _assert_format_compliant(err)
    _check_snapshot("pack_schema_violation__object", err)


def test_pack_schema_violation_for_relation_source_snapshot() -> None:
    """add_relation source type not in declared allowed list. Recovery
    shows both possible fixes — pass an allowed source, or relax the
    declaration."""
    err = PackSchemaViolation.for_relation_source(
        relation_type="supports",
        source_type="memo",
        allowed=["claim", "evidence"],
        pack_name="diligence",
    )
    _assert_format_compliant(err)
    _check_snapshot("pack_schema_violation__relation_source", err)


def test_pack_schema_violation_for_relation_target_snapshot() -> None:
    """add_relation target type not in declared allowed list. Symmetric
    with the source-type case."""
    err = PackSchemaViolation.for_relation_target(
        relation_type="supports",
        target_type="risk",
        allowed=["evidence"],
        pack_name="diligence",
    )
    _assert_format_compliant(err)
    _check_snapshot("pack_schema_violation__relation_target", err)


def test_pack_schema_violation_is_a_value_error() -> None:
    """User code catching ValueError around graph.add_object / add_relation
    keeps working. CONTRACT v0.9 #5 back-compat clause."""
    err = PackSchemaViolation.for_object(
        object_type="x",
        validation_error="missing field",
    )
    assert isinstance(err, ValueError)
    assert isinstance(err, PackError)


# ---------- PR-G subsection 2: internal-bug consistency pass -----------


def test_internal_bug_fields_helper_shape() -> None:
    """The shared helper produces a uniform field dict that every
    internal-bug raise consumes. Verifies the context-dict keys and
    the recovery-prose pattern that GitHub Issues filed from these
    errors arrive with."""
    fields = internal_bug_fields(
        summary="test summary",
        what_happened="something internal happened",
        why_invariant="the invariant",
        location="tests/test_errors_format.py:test_helper_shape",
        extra_context={"foo": "bar"},
    )
    assert fields["summary"] == "test summary"
    assert fields["what_failed"] == "something internal happened"
    assert fields["why"] == "the invariant"
    ctx = fields["context"]
    # Uniform context-dict shape: every internal-bug carries these keys.
    assert ctx["internal"] is True
    assert "framework_version" in ctx
    assert ctx["internal_error_location"] == "tests/test_errors_format.py:test_helper_shape"
    assert ctx["report_url"] == GITHUB_NEW_ISSUE_URL
    assert ctx["foo"] == "bar"  # extra_context merged
    # Uniform recovery-prose pattern.
    how = fields["how_to_fix"]
    assert "framework bug" in how
    assert "not a problem with your code" in how
    assert GITHUB_NEW_ISSUE_URL in how
    assert "framework version" in how
    assert "internal location" in how


def test_all_three_internal_bug_sites_use_uniform_prose() -> None:
    """The three pre-existing internal-bug sites (PR-B's two in
    patterns.py, PR-F's deferred one in graph.py:797) are unified by
    PR-G to use the same helper. They produce different exception
    classes (UnsupportedPatternError for the pattern cases, the new
    InternalEvaluatorError for the graph case) but identical
    structural prose so issues filed from them are uniform."""
    # Pattern evaluator: unknown comparison operator
    pattern_ev_err = _trigger_internal_pattern_unknown_op()
    # Pattern evaluator: unrecognized AST node
    pattern_ast_err = _trigger_internal_pattern_unknown_ast()
    # Graph view-filter evaluator: unknown where operator
    graph_view_err = _trigger_internal_graph_view_unknown_op()

    for err in (pattern_ev_err, pattern_ast_err, graph_view_err):
        msg = str(err)
        assert "framework bug" in msg
        assert "not a problem with your code" in msg
        assert GITHUB_NEW_ISSUE_URL in msg
        assert "framework version" in msg
        assert "internal location" in msg
        # Context dict has uniform keys.
        assert err.context["internal"] is True
        assert "framework_version" in err.context
        assert "internal_error_location" in err.context
        assert err.context["report_url"] == GITHUB_NEW_ISSUE_URL


def _trigger_internal_pattern_unknown_op() -> UnsupportedPatternError:
    """Synthesize the pattern-evaluator unknown-operator case by
    constructing a Comparison AST with an operator that _OPS doesn't
    know about, then feeding it to _eval_where directly."""
    from activegraph.runtime.patterns import Comparison, _eval_where
    expr = Comparison(left_path=["a", "x"], op="<>>", right_value=0)
    try:
        _eval_where(expr, bindings={"a": "obj#1"}, graph=_NullGraph())
    except UnsupportedPatternError as e:
        return e
    raise AssertionError("expected UnsupportedPatternError")


def _trigger_internal_pattern_unknown_ast() -> UnsupportedPatternError:
    """Synthesize the pattern-evaluator unrecognized-AST-node case by
    passing a bogus object into _eval_where."""
    from activegraph.runtime.patterns import _eval_where
    class _BogusAst:
        pass
    try:
        _eval_where(_BogusAst(), bindings={}, graph=_NullGraph())
    except UnsupportedPatternError as e:
        return e
    raise AssertionError("expected UnsupportedPatternError")


def _trigger_internal_graph_view_unknown_op() -> InternalEvaluatorError:
    """Synthesize the graph view-filter unknown-operator case by
    calling evaluate_where with a bogus operator."""
    from activegraph.core.graph import evaluate_where
    try:
        evaluate_where(
            where={"text": {"NOT_A_REAL_OP": "anything"}},
            root={"text": "anything"},
        )
    except InternalEvaluatorError as e:
        return e
    raise AssertionError("expected InternalEvaluatorError")


class _NullGraph:
    """Minimal stub for _eval_where — these internal-bug raises fire
    before the graph is read, so a no-op stub is enough."""
    def get_object(self, _id):
        return None


def test_internal_pattern_unknown_op_snapshot() -> None:
    err = _trigger_internal_pattern_unknown_op()
    _assert_format_compliant(err)
    _check_snapshot("internal_bug__pattern_unknown_op", err)


def test_internal_graph_view_unknown_op_snapshot() -> None:
    err = _trigger_internal_graph_view_unknown_op()
    _assert_format_compliant(err)
    _check_snapshot("internal_bug__graph_view_unknown_op", err)


def test_internal_evaluator_error_inherits_correctly() -> None:
    """InternalEvaluatorError is an ExecutionError and a ValueError."""
    assert issubclass(InternalEvaluatorError, ExecutionError)
    assert issubclass(InternalEvaluatorError, ActiveGraphError)
    assert issubclass(InternalEvaluatorError, ValueError)
