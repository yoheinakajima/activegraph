"""CLI happy paths and exit codes — CONTRACT v0.8 #12–#13."""

from __future__ import annotations

import json
import os
import tempfile

import pytest
from click.testing import CliRunner
from pydantic import BaseModel

from activegraph import Graph, Runtime, behavior, clear_registry
from activegraph.cli.main import (
    EXIT_CODES,
    EXIT_CORRUPTION,
    EXIT_DIVERGENCE,
    EXIT_GENERIC_ERROR,
    EXIT_NOT_FOUND,
    EXIT_OK,
    EXIT_USAGE_ERROR,
    cli,
)
from activegraph.packs import Pack, PackSettingsMissingError


def _seed_run(path: str) -> str:
    clear_registry()

    @behavior(name="planner", on=["goal.created"])
    def planner(event, graph, ctx):
        graph.add_object("task", {"x": 1})

    g = Graph()
    rt = Runtime(g, persist_to=path)
    rt.run_goal("test")
    rt.save_state()
    return rt.run_id


def _seed_memo_run(path: str) -> str:
    g = Graph()
    rt = Runtime(g, persist_to=path)
    company = rt.graph.add_object("company", {"name": "Northwind"})
    rt.graph.add_object(
        "memo",
        {
            "company_id": company.id,
            "summary": "Needle-bearing summary for the operator.",
            "key_claims": [
                {"text": "Revenue is growing.", "evidence_ids": ["evidence#1"]}
            ],
            "open_contradictions": [],
            "contradictions_note": "No contradictions in fixture data.",
            "risks": [{"title": "Customer concentration", "severity": "medium"}],
        },
    )
    rt.save_state()
    return rt.run_id


class _CliSettings(BaseModel):
    n: int = 1
    enabled: bool = True


@pytest.fixture
def temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)
    yield path
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(path + suffix)
        except FileNotFoundError:
            pass


@pytest.fixture
def runner():
    return CliRunner()


class TestExitCodes:
    def test_codes_table_documented(self):
        assert EXIT_CODES["ok"] == 0
        assert EXIT_CODES["generic_error"] == 1
        assert EXIT_CODES["usage_error"] == 2
        assert EXIT_CODES["not_found"] == 3
        assert EXIT_CODES["corruption"] == 4
        assert EXIT_CODES["divergence"] == 5


class TestInspect:
    def test_happy_path_text(self, temp_db, runner):
        run_id = _seed_run(temp_db)
        result = runner.invoke(cli, ["inspect", f"sqlite:///{temp_db}"])
        assert result.exit_code == EXIT_OK, result.output
        assert run_id in result.output
        assert "state:" in result.output

    def test_happy_path_json(self, temp_db, runner):
        run_id = _seed_run(temp_db)
        result = runner.invoke(
            cli, ["inspect", f"sqlite:///{temp_db}", "--json"]
        )
        assert result.exit_code == EXIT_OK, result.output
        obj = json.loads(result.output)
        assert obj["run_id"] == run_id
        assert "state" in obj
        assert "budget" in obj
        assert "recent_events" in obj

    def test_not_found_for_missing_store(self, temp_db, runner):
        result = runner.invoke(
            cli, ["inspect", "sqlite:////nonexistent/path.db"]
        )
        assert result.exit_code == EXIT_NOT_FOUND, result.output

    def test_usage_error_for_bare_path(self, temp_db, runner):
        _seed_run(temp_db)
        result = runner.invoke(cli, ["inspect", temp_db])
        assert result.exit_code == EXIT_USAGE_ERROR, result.output
        # Must point operator at the right form.
        assert "sqlite:///" in (result.output or "") + (result.stderr_bytes or b"").decode()

    def test_tail_arg_limits_events(self, temp_db, runner):
        _seed_run(temp_db)
        result = runner.invoke(
            cli,
            ["inspect", f"sqlite:///{temp_db}", "--tail", "2", "--json"],
        )
        assert result.exit_code == EXIT_OK, result.output
        obj = json.loads(result.output)
        assert len(obj["recent_events"]) == 2


class TestReplay:
    def test_happy_path(self, temp_db, runner):
        run_id = _seed_run(temp_db)
        result = runner.invoke(
            cli, ["replay", f"sqlite:///{temp_db}", "--run-id", run_id, "--json"]
        )
        assert result.exit_code == EXIT_OK, result.output
        obj = json.loads(result.output)
        assert obj["run_id"] == run_id
        assert obj["events"] > 0
        assert obj["objects"] >= 1


class TestFork:
    def test_happy_path(self, temp_db, runner):
        run_id = _seed_run(temp_db)
        # Find a fork point
        rt = Runtime.load(f"sqlite:///{temp_db}", run_id=run_id)
        fork_at = next(e.id for e in rt.graph.events if e.type == "object.created")
        result = runner.invoke(
            cli,
            [
                "fork", f"sqlite:///{temp_db}",
                "--run-id", run_id,
                "--at-event", fork_at,
                "--label", "test-fork",
                "--json",
            ],
        )
        assert result.exit_code == EXIT_OK, result.output
        obj = json.loads(result.output)
        assert obj["parent_run_id"] == run_id
        assert obj["new_run_id"]
        assert obj["events_copied"] > 0

    def test_not_found_for_missing_event(self, temp_db, runner):
        run_id = _seed_run(temp_db)
        result = runner.invoke(
            cli,
            [
                "fork", f"sqlite:///{temp_db}",
                "--run-id", run_id,
                "--at-event", "evt_does_not_exist",
            ],
        )
        assert result.exit_code == EXIT_NOT_FOUND, result.output

    def test_record_sets_label_suffix(self, temp_db, runner):
        """v1.0 CLI follow-on: --record stamps the label so operators
        see the fork is intended as a re-recording."""
        run_id = _seed_run(temp_db)
        rt = Runtime.load(f"sqlite:///{temp_db}", run_id=run_id)
        fork_at = next(e.id for e in rt.graph.events if e.type == "object.created")
        result = runner.invoke(
            cli,
            [
                "fork", f"sqlite:///{temp_db}",
                "--run-id", run_id,
                "--at-event", fork_at,
                "--record",
                "--json",
            ],
        )
        assert result.exit_code == EXIT_OK, result.output
        obj = json.loads(result.output)
        assert obj["label"] == "recording"
        assert obj["recording"] is True

    def test_record_composes_with_explicit_label(self, temp_db, runner):
        """--label cautious --record produces label 'cautious-recording'."""
        run_id = _seed_run(temp_db)
        rt = Runtime.load(f"sqlite:///{temp_db}", run_id=run_id)
        fork_at = next(e.id for e in rt.graph.events if e.type == "object.created")
        result = runner.invoke(
            cli,
            [
                "fork", f"sqlite:///{temp_db}",
                "--run-id", run_id,
                "--at-event", fork_at,
                "--label", "cautious",
                "--record",
                "--json",
            ],
        )
        assert result.exit_code == EXIT_OK, result.output
        obj = json.loads(result.output)
        assert obj["label"] == "cautious-recording"

    def test_record_prints_followon_guidance_in_text(self, temp_db, runner):
        run_id = _seed_run(temp_db)
        rt = Runtime.load(f"sqlite:///{temp_db}", run_id=run_id)
        fork_at = next(e.id for e in rt.graph.events if e.type == "object.created")
        result = runner.invoke(
            cli,
            [
                "fork", f"sqlite:///{temp_db}",
                "--run-id", run_id,
                "--at-event", fork_at,
                "--record",
            ],
        )
        assert result.exit_code == EXIT_OK, result.output
        assert "recording fork" in result.output

    def test_set_records_fork_settings_override_event(self, temp_db, runner):
        pack = Pack(name="demo", version="0.1.0", settings_schema=_CliSettings)
        rt = Runtime(Graph(), persist_to=temp_db)
        rt.load_pack(pack, settings=_CliSettings(n=1))
        fork_at = next(e.id for e in rt.graph.events if e.type == "pack.loaded")

        result = runner.invoke(
            cli,
            [
                "fork", f"sqlite:///{temp_db}",
                "--run-id", rt.run_id,
                "--at-event", fork_at,
                "--set", "demo.n=42",
                "--set", "demo.enabled=false",
                "--json",
            ],
        )
        assert result.exit_code == EXIT_OK, result.output
        obj = json.loads(result.output)
        assert obj["settings_overrides"] == {
            "demo": {"n": 42, "enabled": False}
        }
        assert obj["settings_override_events"]

        fork_rt = Runtime.load(f"sqlite:///{temp_db}", run_id=obj["new_run_id"])
        override_events = [
            e for e in fork_rt.graph.events
            if e.type == "pack.settings_overridden"
        ]
        assert len(override_events) == 1
        assert override_events[0].payload["overrides"] == {
            "n": 42,
            "enabled": False,
        }

        fork_rt.load_pack(pack)
        assert fork_rt._pack_state.pack_settings["demo"].n == 42
        assert fork_rt._pack_state.pack_settings["demo"].enabled is False
        latest_load = [
            e for e in fork_rt.graph.events if e.type == "pack.loaded"
        ][-1]
        assert latest_load.payload["settings"]["n"] == 42

    def test_set_rejects_pack_not_loaded_by_fork_point(self, temp_db, runner):
        run_id = _seed_run(temp_db)
        rt = Runtime.load(f"sqlite:///{temp_db}", run_id=run_id)
        fork_at = next(e.id for e in rt.graph.events if e.type == "object.created")

        result = runner.invoke(
            cli,
            [
                "fork", f"sqlite:///{temp_db}",
                "--run-id", run_id,
                "--at-event", fork_at,
                "--set", "demo.n=42",
            ],
        )
        assert result.exit_code == EXIT_USAGE_ERROR, result.output
        assert "no pack.loaded event" in result.output

    def test_set_unknown_setting_fails_when_pack_loads(self, temp_db, runner):
        pack = Pack(name="demo", version="0.1.0", settings_schema=_CliSettings)
        rt = Runtime(Graph(), persist_to=temp_db)
        rt.load_pack(pack, settings=_CliSettings(n=1))
        fork_at = next(e.id for e in rt.graph.events if e.type == "pack.loaded")

        result = runner.invoke(
            cli,
            [
                "fork", f"sqlite:///{temp_db}",
                "--run-id", rt.run_id,
                "--at-event", fork_at,
                "--set", "demo.missing=42",
                "--json",
            ],
        )
        assert result.exit_code == EXIT_OK, result.output
        fork_id = json.loads(result.output)["new_run_id"]
        fork_rt = Runtime.load(f"sqlite:///{temp_db}", run_id=fork_id)
        with pytest.raises(PackSettingsMissingError, match="demo.missing"):
            fork_rt.load_pack(pack)


class TestInspectFlags:
    """v1.0 CLI follow-ons: --event, --behaviors, --pack-version.

    Each is a selector that narrows `activegraph inspect` output to one
    focused section. The selectors are mutually exclusive. Implied by
    the recovery prose of v1.0 PR-A's error messages (the
    `activegraph inspect <run> --event evt_NNN` etc. suggestions); built
    here so the error messages can point at flags that actually exist.
    """

    def test_event_selector_prints_payload(self, temp_db, runner):
        run_id = _seed_run(temp_db)
        rt = Runtime.load(f"sqlite:///{temp_db}", run_id=run_id)
        target = next(e for e in rt.graph.events if e.type == "object.created")
        result = runner.invoke(
            cli,
            [
                "inspect", f"sqlite:///{temp_db}",
                "--run-id", run_id,
                "--event", target.id,
            ],
        )
        assert result.exit_code == EXIT_OK, result.output
        assert target.id in result.output
        assert "type:" in result.output
        assert "payload:" in result.output

    def test_event_selector_json(self, temp_db, runner):
        run_id = _seed_run(temp_db)
        rt = Runtime.load(f"sqlite:///{temp_db}", run_id=run_id)
        target = next(e for e in rt.graph.events if e.type == "object.created")
        result = runner.invoke(
            cli,
            [
                "inspect", f"sqlite:///{temp_db}",
                "--run-id", run_id,
                "--event", target.id,
                "--json",
            ],
        )
        assert result.exit_code == EXIT_OK, result.output
        obj = json.loads(result.output)
        assert obj["id"] == target.id
        assert obj["type"] == target.type
        assert "payload" in obj

    def test_event_selector_not_found(self, temp_db, runner):
        run_id = _seed_run(temp_db)
        result = runner.invoke(
            cli,
            [
                "inspect", f"sqlite:///{temp_db}",
                "--run-id", run_id,
                "--event", "evt_does_not_exist",
            ],
        )
        assert result.exit_code == EXIT_NOT_FOUND, result.output
        assert "evt_does_not_exist" in result.output

    def test_behaviors_selector_text(self, temp_db, runner):
        run_id = _seed_run(temp_db)
        result = runner.invoke(
            cli,
            [
                "inspect", f"sqlite:///{temp_db}",
                "--run-id", run_id,
                "--behaviors",
            ],
        )
        assert result.exit_code == EXIT_OK, result.output
        # Inspect loads without registering behaviors, so the focused
        # output shows the empty-state message rather than the populated
        # behaviors list. Both branches are valid responses; the test
        # asserts the focused command produced output, not full status.
        assert "state:" not in result.output  # focused, not full status
        assert "behaviors" in result.output or "registered" in result.output

    def test_pack_version_selector_empty(self, temp_db, runner):
        """No packs were loaded in the seeded run; the selector reports
        the empty case cleanly."""
        run_id = _seed_run(temp_db)
        result = runner.invoke(
            cli,
            [
                "inspect", f"sqlite:///{temp_db}",
                "--run-id", run_id,
                "--pack-version",
            ],
        )
        assert result.exit_code == EXIT_OK, result.output
        assert "no packs loaded" in result.output

    def test_pack_version_selector_json_empty(self, temp_db, runner):
        run_id = _seed_run(temp_db)
        result = runner.invoke(
            cli,
            [
                "inspect", f"sqlite:///{temp_db}",
                "--run-id", run_id,
                "--pack-version",
                "--json",
            ],
        )
        assert result.exit_code == EXIT_OK, result.output
        obj = json.loads(result.output)
        assert obj == []

    def test_selectors_are_mutually_exclusive(self, temp_db, runner):
        """Focused inspect flags are selectors, not
        filters — combining them is a usage error."""
        run_id = _seed_run(temp_db)
        result = runner.invoke(
            cli,
            [
                "inspect", f"sqlite:///{temp_db}",
                "--run-id", run_id,
                "--behaviors",
                "--memo",
            ],
        )
        assert result.exit_code == EXIT_USAGE_ERROR, result.output
        assert "mutually exclusive" in result.output

    def test_memo_selector_renders_operator_format(self, temp_db, runner):
        run_id = _seed_memo_run(temp_db)
        result = runner.invoke(
            cli,
            [
                "inspect", f"sqlite:///{temp_db}",
                "--run-id", run_id,
                "--memo",
            ],
        )
        assert result.exit_code == EXIT_OK, result.output
        assert "Memo: Northwind" in result.output
        assert "Summary:" in result.output
        assert "Key claims:" in result.output
        assert "Customer concentration" in result.output

    def test_memo_selector_json(self, temp_db, runner):
        run_id = _seed_memo_run(temp_db)
        result = runner.invoke(
            cli,
            [
                "inspect", f"sqlite:///{temp_db}",
                "--run-id", run_id,
                "--memo",
                "--json",
            ],
        )
        assert result.exit_code == EXIT_OK, result.output
        obj = json.loads(result.output)
        assert obj[0]["company"] == "Northwind"
        assert obj[0]["object"]["type"] == "memo"

    def test_search_selector_text(self, temp_db, runner):
        run_id = _seed_memo_run(temp_db)
        result = runner.invoke(
            cli,
            [
                "inspect", f"sqlite:///{temp_db}",
                "--run-id", run_id,
                "--search", "needle-bearing",
            ],
        )
        assert result.exit_code == EXIT_OK, result.output
        assert "matches" in result.output
        assert "object.created" in result.output
        assert "Needle-bearing" in result.output

    def test_search_selector_json(self, temp_db, runner):
        run_id = _seed_memo_run(temp_db)
        result = runner.invoke(
            cli,
            [
                "inspect", f"sqlite:///{temp_db}",
                "--run-id", run_id,
                "--search", "northwind",
                "--json",
            ],
        )
        assert result.exit_code == EXIT_OK, result.output
        obj = json.loads(result.output)
        assert any(m["type"] == "object.created" for m in obj)


class TestDiff:
    def test_happy_path(self, temp_db, runner):
        run_id = _seed_run(temp_db)
        rt = Runtime.load(f"sqlite:///{temp_db}", run_id=run_id)
        fork_at = next(e.id for e in rt.graph.events if e.type == "object.created")
        fork = rt.fork(at_event=fork_at, label="diff-test")
        fork.save_state()
        result = runner.invoke(
            cli,
            [
                "diff", f"sqlite:///{temp_db}",
                "--run-a", run_id,
                "--run-b", fork.run_id,
                "--json",
            ],
        )
        assert result.exit_code == EXIT_OK, result.output
        obj = json.loads(result.output)
        assert obj["run_a"] == run_id
        assert obj["run_b"] == fork.run_id
        for k in (
            "shared_events", "parent_only_events", "fork_only_events",
            "divergent_objects", "divergent_relations",
        ):
            assert k in obj


class TestExportTrace:
    def test_jsonl_format_writes_one_event_per_line(self, temp_db, runner, tmp_path):
        run_id = _seed_run(temp_db)
        out_file = tmp_path / "trace.jsonl"
        result = runner.invoke(
            cli,
            [
                "export-trace", f"sqlite:///{temp_db}",
                "--run-id", run_id,
                "--format", "jsonl",
                "-o", str(out_file),
            ],
        )
        assert result.exit_code == EXIT_OK, result.output
        lines = out_file.read_text().splitlines()
        assert len(lines) > 0
        for ln in lines:
            obj = json.loads(ln)
            assert "id" in obj
            assert "type" in obj


class TestMigrate:
    def test_sqlite_to_sqlite_happy_path(self, temp_db, runner, tmp_path):
        run_id = _seed_run(temp_db)
        dst = str(tmp_path / "dst.db")
        result = runner.invoke(
            cli,
            [
                "migrate",
                "--from", f"sqlite:///{temp_db}",
                "--to", f"sqlite:///{dst}",
                "--json",
            ],
        )
        assert result.exit_code == EXIT_OK, result.output
        obj = json.loads(result.output)
        assert any(r["run_id"] == run_id and r["status"] == "ok" for r in obj["runs"])

    def test_usage_error_on_bare_path(self, temp_db, runner, tmp_path):
        _seed_run(temp_db)
        dst = str(tmp_path / "dst.db")
        result = runner.invoke(
            cli, ["migrate", "--from", temp_db, "--to", dst]
        )
        assert result.exit_code == EXIT_USAGE_ERROR, result.output

    def test_skip_corrupted_recovers_partial_run(self, temp_db, runner, tmp_path):
        """v1.0 CLI follow-on: --skip-corrupted lets a migration recover the
        readable subset of a run with a corrupted-payload row, instead of
        failing the whole run.

        Inject a corruption directly into the source store's events
        table (the only way to produce one — encode_payload refuses
        non-JSON at emit-time). Then migrate with --skip-corrupted and
        confirm the destination run has the readable events minus the
        corrupt one, and the per-run report names the skipped id.
        """
        import sqlite3

        run_id = _seed_run(temp_db)
        # Find a real event id in the run, then corrupt its payload column.
        with sqlite3.connect(temp_db) as conn:
            row = conn.execute(
                "SELECT id FROM events WHERE run_id = ? AND type = 'object.created' LIMIT 1",
                (run_id,),
            ).fetchone()
            assert row is not None
            corrupt_event_id = row[0]
            conn.execute(
                "UPDATE events SET payload = ? WHERE id = ? AND run_id = ?",
                ('{"goal": "x", "broken":', corrupt_event_id, run_id),
            )
            conn.commit()
            total_events = conn.execute(
                "SELECT COUNT(*) FROM events WHERE run_id = ?", (run_id,)
            ).fetchone()[0]

        dst = str(tmp_path / "dst_skip.db")
        result = runner.invoke(
            cli,
            [
                "migrate",
                "--from", f"sqlite:///{temp_db}",
                "--to", f"sqlite:///{dst}",
                "--skip-corrupted",
                "--json",
            ],
        )
        assert result.exit_code == EXIT_OK, result.output
        obj = json.loads(result.output)
        run_report = next(r for r in obj["runs"] if r["run_id"] == run_id)
        assert run_report["status"] == "ok", run_report
        assert run_report["events_migrated"] == total_events - 1
        assert run_report["skipped_events"] == [corrupt_event_id]

    def test_skip_corrupted_text_output_names_skipped_ids(
        self, temp_db, runner, tmp_path
    ):
        """Text mode of --skip-corrupted prints a `skipped (corrupted):
        evt_NNN` line per skipped event so an operator running the
        command interactively sees what was dropped."""
        import sqlite3

        run_id = _seed_run(temp_db)
        with sqlite3.connect(temp_db) as conn:
            row = conn.execute(
                "SELECT id FROM events WHERE run_id = ? AND type = 'object.created' LIMIT 1",
                (run_id,),
            ).fetchone()
            corrupt_event_id = row[0]
            conn.execute(
                "UPDATE events SET payload = ? WHERE id = ? AND run_id = ?",
                ('{"goal": "x", "broken":', corrupt_event_id, run_id),
            )
            conn.commit()

        dst = str(tmp_path / "dst_skip_text.db")
        result = runner.invoke(
            cli,
            [
                "migrate",
                "--from", f"sqlite:///{temp_db}",
                "--to", f"sqlite:///{dst}",
                "--skip-corrupted",
            ],
        )
        assert result.exit_code == EXIT_OK, result.output
        assert corrupt_event_id in result.output
        assert "skipped (corrupted)" in result.output
        assert "skipped=1" in result.output

    def test_without_skip_corrupted_a_bad_row_fails_the_run(
        self, temp_db, runner, tmp_path
    ):
        """Default behavior preserved: without --skip-corrupted, a
        corrupted-payload row fails the run."""
        import sqlite3

        run_id = _seed_run(temp_db)
        with sqlite3.connect(temp_db) as conn:
            row = conn.execute(
                "SELECT id FROM events WHERE run_id = ? AND type = 'object.created' LIMIT 1",
                (run_id,),
            ).fetchone()
            conn.execute(
                "UPDATE events SET payload = ? WHERE id = ? AND run_id = ?",
                ('{"goal": "x", "broken":', row[0], run_id),
            )
            conn.commit()

        dst = str(tmp_path / "dst_strict.db")
        result = runner.invoke(
            cli,
            [
                "migrate",
                "--from", f"sqlite:///{temp_db}",
                "--to", f"sqlite:///{dst}",
                "--json",
            ],
        )
        # Migration exits non-zero when any run fails (existing CLI
        # convention); the per-run report still has the structured
        # error context.
        assert result.exit_code == EXIT_GENERIC_ERROR, result.output
        obj = json.loads(result.output)
        run_report = next(r for r in obj["runs"] if r["run_id"] == run_id)
        assert run_report["status"] == "failed"
        assert "CorruptedEventPayloadError" in run_report["error"]


class TestPromote:
    """CONTRACT v1.3 #4: exit codes, JSON shape, dry-run gating."""

    def _seed_parent_and_fork(self, path, *, conflict=False, warn_pack=False):
        clear_registry()

        @behavior(name="planner", on=["goal.created"])
        def planner(event, graph, ctx):
            graph.add_object("task", {"x": 1})

        g = Graph()
        parent = Runtime(g, persist_to=path)
        parent.run_goal("test")
        parent.save_state()
        fork = parent.fork(at_event=parent.trace.events()[-1].id)
        if warn_pack:
            fork.load_pack(Pack(name="candidate", version="0.1"))
        fork.graph.add_object("note", {"text": "from fork"})
        if conflict:
            task = next(
                o.id for o in parent.graph.all_objects() if o.type == "task"
            )
            fork.graph.patch_object(task, {"x": 2})
            parent.graph.patch_object(task, {"x": 3})
        return parent.run_id, fork.run_id

    def test_dry_run_then_apply_json_shapes(self, tmp_path, runner):
        db = str(tmp_path / "p.db")
        parent, fork = self._seed_parent_and_fork(db, warn_pack=True)
        url = f"sqlite:///{db}"

        r = runner.invoke(
            cli,
            ["promote", url, "--run-id", parent, "--from-run", fork,
             "--dry-run", "--json"],
        )
        assert r.exit_code == EXIT_OK, r.output
        plan = json.loads(r.output)
        assert plan["dry_run"] is True
        assert plan["objects_created"] == ["note#2"]
        assert plan["conflicts"] == []
        assert any("candidate@0.1" in w for w in plan["warnings"])
        assert "marker_event_id" not in plan
        assert plan["computed_against"]

        r = runner.invoke(
            cli,
            ["promote", url, "--run-id", parent, "--from-run", fork, "--json"],
        )
        assert r.exit_code == EXIT_OK, r.output
        applied = json.loads(r.output)
        assert applied["dry_run"] is False
        assert applied["marker_event_id"]
        assert applied["applied_event_ids"]
        # The pack warning survives the CLI path (log-derived, since a
        # loaded runtime has no live pack state).
        assert any("candidate@0.1" in w for w in applied["warnings"])

    def test_conflict_exits_divergence_on_apply_and_dry_run(self, tmp_path, runner):
        db = str(tmp_path / "p.db")
        parent, fork = self._seed_parent_and_fork(db, conflict=True)
        url = f"sqlite:///{db}"

        r = runner.invoke(
            cli, ["promote", url, "--run-id", parent, "--from-run", fork]
        )
        assert r.exit_code == EXIT_DIVERGENCE

        r = runner.invoke(
            cli,
            ["promote", url, "--run-id", parent, "--from-run", fork,
             "--dry-run", "--json"],
        )
        assert r.exit_code == EXIT_DIVERGENCE
        plan = json.loads(r.output)
        assert plan["conflicts"][0]["kind"] == "both_changed"

    def test_unknown_run_ids_exit_not_found_without_phantom_rows(
        self, tmp_path, runner
    ):
        from activegraph.store.sqlite import SQLiteEventStore

        db = str(tmp_path / "p.db")
        parent, fork = self._seed_parent_and_fork(db)
        url = f"sqlite:///{db}"
        runs_before = {r.run_id for r in SQLiteEventStore.list_runs(db)}

        r = runner.invoke(
            cli, ["promote", url, "--run-id", parent, "--from-run", "nope"]
        )
        assert r.exit_code == EXIT_NOT_FOUND
        assert "no such run" in r.output
        r = runner.invoke(
            cli, ["promote", url, "--run-id", "nope", "--from-run", fork]
        )
        assert r.exit_code == EXIT_NOT_FOUND
        # The mistyped ids must not insert phantom run rows.
        assert {r.run_id for r in SQLiteEventStore.list_runs(db)} == runs_before

    def test_bare_path_is_a_usage_error(self, tmp_path, runner):
        db = str(tmp_path / "p.db")
        parent, fork = self._seed_parent_and_fork(db)
        r = runner.invoke(
            cli, ["promote", db, "--run-id", parent, "--from-run", fork]
        )
        assert r.exit_code == EXIT_USAGE_ERROR

    def test_reversed_lineage_exits_not_found(self, tmp_path, runner):
        db = str(tmp_path / "p.db")
        parent, fork = self._seed_parent_and_fork(db)
        url = f"sqlite:///{db}"
        r = runner.invoke(
            cli, ["promote", url, "--run-id", fork, "--from-run", parent]
        )
        assert r.exit_code == EXIT_NOT_FOUND
        assert "not a direct fork" in r.output
