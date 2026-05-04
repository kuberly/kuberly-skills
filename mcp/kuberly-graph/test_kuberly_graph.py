#!/usr/bin/env python3
"""
Tests for the orchestration layer in kuberly_graph.py.

Stdlib only (unittest + tempfile). Builds a small synthetic KuberlyGraph
in-memory plus a tempdir "fake repo" — no dependency on the real repo state.

The path-resolution shim below makes this script runnable in two layouts:
  - upstream (kuberly-skills): mcp/kuberly-graph/{kuberly_graph,test_kuberly_graph}.py
  - consumer (post-apm install): scripts/test_kuberly_graph_orchestration.py
    alongside scripts/mcp/kuberly-graph/kuberly_graph.py
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_script_dir = Path(__file__).resolve().parent
_pkg = _script_dir / "mcp" / "kuberly-graph"
if (_pkg / "kuberly_graph.py").is_file():
    sys.path.insert(0, str(_pkg))
else:
    sys.path.insert(0, str(_script_dir))
from kuberly_graph import (  # noqa: E402
    EXPECTED_PERSONAS,
    KuberlyGraph,
    PERSONA_DAGS,
    _slugify,
)


def _fake_repo() -> tempfile.TemporaryDirectory:
    """Make a tempdir that looks enough like a kuberly-stack repo to satisfy
    KuberlyGraph (root.hcl marker, a couple of module dirs, persona stubs)."""
    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name)
    (root / "root.hcl").write_text("# fake\n")

    # One module: aws/loki — so blast_radius and files_likely_changed have something
    loki = root / "clouds" / "aws" / "modules" / "loki"
    loki.mkdir(parents=True)
    (loki / "terragrunt.hcl").write_text('dependency "eks" {}\n')
    (loki / "variables.tf").write_text("# vars\n")
    (loki / "main.tf").write_text("# main\n")
    (loki / "values").mkdir()
    (loki / "values" / "loki.yaml").write_text("# values\n")
    (loki / "kuberly.json").write_text('{"description":"loki module"}\n')

    # Stub upstream module so the dependency edge resolves
    eks = root / "clouds" / "aws" / "modules" / "eks"
    eks.mkdir(parents=True)
    (eks / "terragrunt.hcl").write_text("# eks\n")
    (eks / "kuberly.json").write_text('{"description":"eks"}\n')

    # Persona stubs so personas_synced reports OK
    agents = root / ".claude" / "agents"
    agents.mkdir(parents=True)
    for p in EXPECTED_PERSONAS:
        (agents / f"{p}.md").write_text(f"# {p}\n")

    # Empty openspec/changes/ for openspec_existing tests
    (root / "openspec" / "changes").mkdir(parents=True)

    return tmp


class TaskKindInferenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = _fake_repo()
        self.g = KuberlyGraph(self.tmp.name)
        self.g.build()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_resource_bump_keywords(self) -> None:
        kind, conf = self.g.infer_task_kind("bump loki querier resources")
        self.assertEqual(kind, "resource-bump")
        self.assertIn(conf, {"medium", "high"})

    def test_incident_keywords(self) -> None:
        kind, _ = self.g.infer_task_kind("Loki log queries are slow and timing out")
        self.assertEqual(kind, "incident")

    def test_new_module_keywords(self) -> None:
        kind, _ = self.g.infer_task_kind("scaffold a new sqs queue module")
        self.assertEqual(kind, "new-module")

    def test_unknown_when_no_keywords(self) -> None:
        kind, conf = self.g.infer_task_kind("xyzzy plover")
        self.assertEqual(kind, "unknown")
        self.assertEqual(conf, "low")

    def test_empty_task_is_unknown(self) -> None:
        self.assertEqual(self.g.infer_task_kind(""), ("unknown", "low"))


class PersonaDAGTests(unittest.TestCase):
    def test_resource_bump_dag_shape(self) -> None:
        dag = PERSONA_DAGS["resource-bump"]
        ids = [p["id"] for p in dag]
        self.assertEqual(ids, ["scope", "implement", "review", "reconcile"])
        # Implementation phase requires approval; review is parallel.
        impl = next(p for p in dag if p["id"] == "implement")
        review = next(p for p in dag if p["id"] == "review")
        self.assertTrue(impl["needs_approval"])
        self.assertTrue(review["parallel"])
        self.assertEqual(review["personas"],
                         ["pr-reviewer-in-context", "pr-reviewer-cold"])

    def test_cicd_uses_app_cicd_engineer_not_iac_developer(self) -> None:
        dag = PERSONA_DAGS["cicd"]
        impl = next(p for p in dag if p["id"] == "implement")
        self.assertEqual(impl["personas"], ["app-cicd-engineer"])
        self.assertNotIn("iac-developer", impl["personas"])

    def test_incident_diagnose_phase_is_parallel(self) -> None:
        dag = PERSONA_DAGS["incident"]
        diag = next(p for p in dag if p["id"] == "diagnose")
        self.assertTrue(diag["parallel"])
        self.assertCountEqual(diag["personas"],
                              ["troubleshooter", "infra-scope-planner"])


class PlanPersonaFanoutTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = _fake_repo()
        self.g = KuberlyGraph(self.tmp.name)
        self.g.build()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_loki_query_plan_resolves_module_and_phases(self) -> None:
        plan = self.g.plan_persona_fanout(
            task="Loki log queries are slow in Grafana",
            named_modules=["loki"],
            current_branch="agrishko/some-feature",
        )
        self.assertIn(plan["task_kind"], {"incident", "resource-bump"})
        self.assertIn("module:aws/loki", plan["scope"]["modules"])
        ids = [p["id"] for p in plan["phases"]]
        self.assertIn("review", ids)
        self.assertIn("reconcile", ids)
        # context.md should mention the module
        self.assertIn("module:aws/loki", plan["context_md"])

    def test_branch_gate_blocks_on_integration(self) -> None:
        plan = self.g.plan_persona_fanout(
            task="bump something",
            named_modules=["loki"],
            current_branch="prod",
        )
        self.assertEqual(plan["gates"]["branch"]["verdict"], "block")
        self.assertIn("BLOCKED", plan["context_md"])

    def test_cluster_branch_pattern_blocks(self) -> None:
        plan = self.g.plan_persona_fanout(
            task="anything",
            named_modules=["loki"],
            current_branch="872098898041-eu-central-1-prod",
        )
        self.assertEqual(plan["gates"]["branch"]["verdict"], "block")

    def test_feature_branch_passes_gate(self) -> None:
        plan = self.g.plan_persona_fanout(
            task="anything",
            named_modules=["loki"],
            current_branch="agrishko/orchestrator-mcp-fanout",
        )
        self.assertEqual(plan["gates"]["branch"]["verdict"], "ok")

    def test_openspec_required_for_clouds_path(self) -> None:
        plan = self.g.plan_persona_fanout(
            task="bump loki",
            named_modules=["loki"],
        )
        self.assertTrue(plan["gates"]["openspec"]["required"])

    def test_explicit_task_kind_overrides_inference(self) -> None:
        plan = self.g.plan_persona_fanout(
            task="something ambiguous",
            task_kind="cicd",
        )
        self.assertEqual(plan["task_kind"], "cicd")
        impl = next(p for p in plan["phases"] if p["id"] == "implement")
        self.assertEqual(impl["personas"], ["app-cicd-engineer"])

    def test_personas_synced_reports_ok_with_full_roster(self) -> None:
        plan = self.g.plan_persona_fanout(task="x", named_modules=["loki"])
        synced = plan["gates"]["personas_synced"]
        self.assertEqual(synced["verdict"], "ok")
        self.assertEqual(synced["found"], len(EXPECTED_PERSONAS))


class StopTargetAbsentTests(unittest.TestCase):
    """v0.10.2: planner halts the DAG when named modules don't exist in the
    graph, so the orchestrator can't fan out personas to re-discover the
    absence."""

    def setUp(self) -> None:
        self.tmp = _fake_repo()
        self.g = KuberlyGraph(self.tmp.name)
        self.g.build()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_unknown_module_triggers_stop(self) -> None:
        plan = self.g.plan_persona_fanout(
            task="bump tempo memory in prod",
            named_modules=["tempo"],
            current_branch="agrishko/some-feature",
        )
        self.assertEqual(plan["task_kind"], "stop-target-absent")
        self.assertEqual(plan["confidence"], "high")
        self.assertEqual(plan["unresolved_modules"], ["tempo"])
        # The DAG must have zero personas — the whole point of the guard.
        self.assertEqual(len(plan["phases"]), 1)
        self.assertEqual(plan["phases"][0]["personas"], [])
        self.assertEqual(plan["phases"][0]["id"], "halt")
        # context.md surfaces the halt visibly.
        self.assertIn("Pre-flight halt", plan["context_md"])
        self.assertIn("`tempo`", plan["context_md"])

    def test_resolved_module_does_not_trigger_stop(self) -> None:
        plan = self.g.plan_persona_fanout(
            task="bump loki memory",
            named_modules=["loki"],
            current_branch="agrishko/some-feature",
        )
        self.assertNotEqual(plan["task_kind"], "stop-target-absent")
        self.assertEqual(plan["unresolved_modules"], [])
        # Still has at least one persona phase.
        any_personas = any(ph["personas"] for ph in plan["phases"])
        self.assertTrue(any_personas)

    def test_partial_resolution_keeps_normal_dag(self) -> None:
        """One module resolves, one doesn't — proceed but flag the unresolved."""
        plan = self.g.plan_persona_fanout(
            task="bump loki and unicorn",
            named_modules=["loki", "unicorn"],
            current_branch="agrishko/some-feature",
        )
        self.assertNotEqual(plan["task_kind"], "stop-target-absent")
        self.assertEqual(plan["unresolved_modules"], ["unicorn"])
        any_personas = any(ph["personas"] for ph in plan["phases"])
        self.assertTrue(any_personas)
        self.assertIn("Partial resolution", plan["context_md"])

    def test_no_named_modules_does_not_trigger_stop(self) -> None:
        """The guard only fires when the caller supplied modules to look up."""
        plan = self.g.plan_persona_fanout(
            task="something with no named modules",
            current_branch="agrishko/some-feature",
        )
        self.assertNotEqual(plan["task_kind"], "stop-target-absent")
        self.assertEqual(plan["unresolved_modules"], [])

    def test_stop_target_absent_in_persona_dags_registry(self) -> None:
        self.assertIn("stop-target-absent", PERSONA_DAGS)
        self.assertEqual(PERSONA_DAGS["stop-target-absent"][0]["personas"], [])


class OpenSpecExistingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = _fake_repo()
        self.g = KuberlyGraph(self.tmp.name)
        self.g.build()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_detects_active_change_folder(self) -> None:
        changes = Path(self.tmp.name) / "openspec" / "changes" / "bump-loki-query-resources"
        changes.mkdir()
        (changes / ".openspec.yaml").write_text("schema: spec-driven\n")

        plan = self.g.plan_persona_fanout(
            task="bump loki query resources",
            named_modules=["loki"],
        )
        self.assertEqual(
            plan["gates"]["openspec"]["existing_change_folder"],
            "bump-loki-query-resources",
        )

    def test_ignores_archived_change_folders(self) -> None:
        archive = (Path(self.tmp.name) / "openspec" / "changes" / "archive"
                   / "2026-01-01-bump-loki-query-resources")
        archive.mkdir(parents=True)
        (archive / ".openspec.yaml").write_text("schema: spec-driven\nstatus: archived\n")

        plan = self.g.plan_persona_fanout(
            task="bump loki query resources",
            named_modules=["loki"],
        )
        self.assertIsNone(plan["gates"]["openspec"]["existing_change_folder"])


class SessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = _fake_repo()
        self.g = KuberlyGraph(self.tmp.name)
        self.g.build()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_session_init_creates_layout(self) -> None:
        result = self.g.session_init(
            name="loki-bump",
            task="bump loki",
            modules=["loki"],
        )
        self.assertNotIn("error", result)
        sd = Path(self.tmp.name) / ".agents" / "prompts" / "loki-bump"
        self.assertTrue((sd / "context.md").is_file())
        self.assertTrue((sd / "findings" / ".gitkeep").is_file())
        self.assertTrue((sd / "tasks" / ".gitkeep").is_file())
        # context.md mentions the module
        self.assertIn("loki", (sd / "context.md").read_text())

    def test_session_init_slugifies_name(self) -> None:
        self.g.session_init(name="Bump Loki!  Now")
        self.assertTrue((Path(self.tmp.name) / ".agents" / "prompts"
                         / "bump-loki-now").is_dir())

    def test_session_init_refuses_duplicate(self) -> None:
        self.g.session_init(name="dup")
        again = self.g.session_init(name="dup")
        self.assertIn("error", again)
        self.assertIn("already exists", again["error"])

    def test_session_write_then_read(self) -> None:
        self.g.session_init(name="rw")
        w = self.g.session_write("rw", "decisions.md", "## Decision\nGo with X.\n")
        self.assertEqual(w["bytes"], len("## Decision\nGo with X.\n"))
        r = self.g.session_read("rw", "decisions.md")
        self.assertIn("Decision", r["content"])

    def test_session_write_creates_subdir(self) -> None:
        self.g.session_init(name="sub")
        self.g.session_write("sub", "tasks/01-foo.md", "task body")
        target = (Path(self.tmp.name) / ".agents" / "prompts" / "sub"
                  / "tasks" / "01-foo.md")
        self.assertTrue(target.is_file())

    def test_session_write_rejects_path_traversal(self) -> None:
        self.g.session_init(name="trav")
        out = self.g.session_write("trav", "../escape.md", "nope")
        self.assertIn("error", out)
        self.assertIn("outside session dir", out["error"])
        # Confirm nothing was written
        self.assertFalse((Path(self.tmp.name) / ".agents" / "prompts"
                          / "escape.md").exists())

    def test_session_read_missing_file_returns_clean_error(self) -> None:
        self.g.session_init(name="miss")
        out = self.g.session_read("miss", "nonexistent.md")
        self.assertIn("error", out)
        self.assertIn("not found", out["error"])

    def test_session_list_inventories_files(self) -> None:
        self.g.session_init(name="ls")
        self.g.session_write("ls", "scope.md", "scope body")
        listing = self.g.session_list("ls")
        files = [f["file"] for f in listing["files"]]
        self.assertIn("context.md", files)
        self.assertIn("scope.md", files)


class SlugifyTests(unittest.TestCase):
    def test_handles_punct_and_case(self) -> None:
        self.assertEqual(_slugify("Bump Loki!  Now"), "bump-loki-now")

    def test_empty_falls_back_to_session(self) -> None:
        self.assertEqual(_slugify(""), "session")
        self.assertEqual(_slugify("   "), "session")


class SessionStatusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = _fake_repo()
        self.g = KuberlyGraph(self.tmp.name)
        self.g.build()
        self.g.session_init(name="bump", task="bump loki", modules=["loki"],
                            current_branch="feat/x")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_session_init_seeds_status_json(self) -> None:
        st = self.g.session_status("bump")
        self.assertNotIn("error", st)
        self.assertFalse(st.get("_no_status_yet"))
        # Phases should match the persona DAG for resource-bump
        ids = [p["id"] for p in st["phases"]]
        self.assertIn("scope", ids)
        self.assertIn("review", ids)
        # Every persona starts queued
        for p, info in st["personas"].items():
            self.assertEqual(info["status"], "queued")

    def test_set_persona_running_then_done_flows(self) -> None:
        out = self.g.session_set_status("bump", "infra-scope-planner", "running")
        self.assertEqual(out["status"], "running")
        st = self.g.session_status("bump")
        self.assertEqual(st["personas"]["infra-scope-planner"]["status"], "running")
        # Phase rolls up to running
        scope_ph = next(p for p in st["phases"] if p["id"] == "scope")
        self.assertEqual(scope_ph["status"], "running")
        # Mark done
        self.g.session_set_status("bump", "infra-scope-planner", "done")
        st = self.g.session_status("bump")
        scope_ph = next(p for p in st["phases"] if p["id"] == "scope")
        self.assertEqual(scope_ph["status"], "done")
        # Timestamps written
        self.assertIn("started_at", st["personas"]["infra-scope-planner"])
        self.assertIn("ended_at",   st["personas"]["infra-scope-planner"])

    def test_phase_roll_up_with_two_personas(self) -> None:
        # Diagnose phase doesn't exist for resource-bump; switch to incident
        self.g.session_init.__self__  # noqa: keep ref to silence linters
        # Use an incident-kind session for two-persona phase
        self.g.session_init(name="oom", task="loki ingester OOM",
                            modules=["loki"], current_branch="feat/y")
        # When only one is running, phase should be running
        self.g.session_set_status("oom", "troubleshooter", "running")
        st = self.g.session_status("oom")
        diag = next(p for p in st["phases"] if p["id"] == "diagnose")
        self.assertEqual(diag["status"], "running")
        # Both done → phase done
        self.g.session_set_status("oom", "troubleshooter", "done")
        self.g.session_set_status("oom", "infra-scope-planner", "done")
        st = self.g.session_status("oom")
        diag = next(p for p in st["phases"] if p["id"] == "diagnose")
        self.assertEqual(diag["status"], "done")

    def test_blocked_overrides_running_in_phase_rollup(self) -> None:
        self.g.session_init(name="blk", task="loki ingester OOM",
                            modules=["loki"], current_branch="feat/z")
        self.g.session_set_status("blk", "troubleshooter", "running")
        self.g.session_set_status("blk", "infra-scope-planner", "blocked")
        st = self.g.session_status("blk")
        diag = next(p for p in st["phases"] if p["id"] == "diagnose")
        self.assertEqual(diag["status"], "blocked")

    def test_invalid_status_rejected(self) -> None:
        out = self.g.session_set_status("bump", "iac-developer", "frobnicated")
        self.assertIn("error", out)

    def test_unknown_target_rejected(self) -> None:
        out = self.g.session_set_status("bump", "nonexistent-persona", "running")
        self.assertIn("error", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
