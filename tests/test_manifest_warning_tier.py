"""Loader-side manifest validation, warning tier (CONTRACT v1.6 #1).

When a ``manifest.toml`` sits at the pack root, ``load_pack`` runs
``load_manifest`` + ``verify_surface`` and WARNs on violations —
structured, once per pack per process, never an error before 2.0.
Absent manifest: silent. All offline and deterministic.
"""

from __future__ import annotations

import importlib.util
import logging
import sys

import pytest

from activegraph import Graph, Runtime
from activegraph.packs import loader as pack_loader
from activegraph.packs.manifest import compute_content_hash

PACK_INIT_TEMPLATE = '''
from activegraph.packs import Pack, behavior


@behavior(name="greeter", on=["goal.created"])
def greeter(event, graph, ctx):
    graph.add_object("greeting", {{"text": "hi"}})


pack = Pack(name="{name}", version="0.1.0", behaviors=(greeter,))
'''

MANIFEST_TEMPLATE = """
[pack]
name = "{name}"
version = "0.1.0"
description = "Warning-tier fixture."
license = ""

[pack.provenance]
authored_by = "agent"
generator = "test"

[pack.integrity]
content_hash = "{content_hash}"

[dependencies]
activegraph = ">=1.5,<2.0"
python-deps = []

[surface]
object_types = []
relation_types = []
behaviors = [{behaviors}]
tools = []
settings_schema = ""

[fixtures]
entrypoint = "fixtures/run_fixtures.py"
deterministic = true
"""


@pytest.fixture(autouse=True)
def _fresh_tier():
    """Each test exercises the tier from scratch: the once-per-process
    dedupe set is process state, so tests reset it explicitly."""
    pack_loader._manifest_checked.clear()
    yield
    pack_loader._manifest_checked.clear()


def _make_pack(tmp_path, name, *, declared_behaviors='"greeter"',
               manifest=True, manifest_text=None):
    """Write a real pack package to disk, import it so its module has
    a resolvable ``__file__``, and return the live Pack."""
    root = tmp_path / name
    root.mkdir()
    (root / "__init__.py").write_text(PACK_INIT_TEMPLATE.format(name=name))
    if manifest:
        if manifest_text is None:
            manifest_text = MANIFEST_TEMPLATE.format(
                name=name,
                content_hash=compute_content_hash(root),
                behaviors=declared_behaviors,
            )
        (root / "manifest.toml").write_text(manifest_text)
    spec = importlib.util.spec_from_file_location(
        name, root / "__init__.py", submodule_search_locations=[str(root)]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        del sys.modules[name]
        raise
    return module.pack


def _tier_records(caplog):
    return [
        r
        for r in caplog.records
        if r.name == "activegraph.packs.manifest"
        and r.levelno >= logging.WARNING
    ]


def test_matching_manifest_loads_silently(tmp_path, caplog):
    pack = _make_pack(tmp_path, "warn_tier_clean")
    with caplog.at_level(logging.WARNING, logger="activegraph.packs.manifest"):
        rt = Runtime(Graph(), behaviors=[])
        assert rt.load_pack(pack) is True
    assert _tier_records(caplog) == []


def test_absent_manifest_stays_silent(tmp_path, caplog):
    pack = _make_pack(tmp_path, "warn_tier_bare", manifest=False)
    with caplog.at_level(logging.WARNING, logger="activegraph.packs.manifest"):
        rt = Runtime(Graph(), behaviors=[])
        assert rt.load_pack(pack) is True
    assert _tier_records(caplog) == []


def test_surface_mismatch_warns_once_structured_and_still_loads(
    tmp_path, caplog
):
    # The manifest declares a behavior the Pack never registers: a
    # real violation. The pack STILL loads — warning tier only.
    pack = _make_pack(
        tmp_path, "warn_tier_drift", declared_behaviors='"greeter", "ghost"'
    )
    with caplog.at_level(logging.WARNING, logger="activegraph.packs.manifest"):
        rt = Runtime(Graph(), behaviors=[])
        assert rt.load_pack(pack) is True
        # Loaded despite the violation: the behavior dispatches.
        rt.run_goal("check")
        assert [
            o for o in rt.graph.all_objects() if o.type == "greeting"
        ]
        # A second runtime loading the same pack does not re-warn:
        # once per pack per process.
        rt2 = Runtime(Graph(), behaviors=[])
        assert rt2.load_pack(pack) is True

    records = _tier_records(caplog)
    assert len(records) == 1
    (record,) = records
    # Structured: the record carries the machine-readable fields.
    assert record.pack == "warn_tier_drift"
    assert record.pack_version == "0.1.0"
    assert record.reason == "pack.manifest_invalid"
    assert record.manifest_path.endswith("manifest.toml")
    assert any("ghost" in v for v in record.violations)
    # And the human line says the pack still loads.
    assert "still loads" in record.getMessage()


def test_malformed_manifest_warns_never_raises(tmp_path, caplog):
    pack = _make_pack(
        tmp_path,
        "warn_tier_garbage",
        manifest_text="this is not toml [[[",
    )
    with caplog.at_level(logging.WARNING, logger="activegraph.packs.manifest"):
        rt = Runtime(Graph(), behaviors=[])
        assert rt.load_pack(pack) is True  # never an error before 2.0
    records = _tier_records(caplog)
    assert len(records) == 1
    assert records[0].violations  # the parse failure, verbatim
