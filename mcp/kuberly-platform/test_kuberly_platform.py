#!/usr/bin/env python3
"""
Tests for the orchestration layer in kuberly_platform.py.

Stdlib only (unittest + tempfile). Builds a small synthetic KuberlyPlatform
in-memory plus a tempdir "fake repo" — no dependency on the real repo state.

The path-resolution shim below makes this script runnable in two layouts:
  - upstream (kuberly-skills): mcp/kuberly-platform/{kuberly_platform,test_kuberly_platform}.py
  - consumer (post-apm install): scripts/test_kuberly_platform_orchestration.py
    alongside scripts/mcp/kuberly-platform/kuberly_platform.py
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

_script_dir = Path(__file__).resolve().parent
_pkg = _script_dir / "mcp" / "kuberly-platform"
if (_pkg / "kuberly_platform.py").is_file():
    sys.path.insert(0, str(_pkg))
else:
    sys.path.insert(0, str(_script_dir))
from kuberly_platform import (  # noqa: E402
    EXPECTED_PERSONAS,
    KuberlyPlatform,
    PERSONA_DAGS,
    _slugify,
)


def _fake_repo() -> tempfile.TemporaryDirectory:
    """Make a tempdir that looks enough like a kuberly-stack repo to satisfy
    KuberlyPlatform (root.hcl marker, a couple of module dirs, persona stubs)."""
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

    # v0.15.0: a component instance invoking loki — needed so the
    # actionability pre-flight (in plan_persona_fanout / quick_scope)
    # treats loki as a real, tune-able deployment, not a graph leaf.
    comp = root / "components" / "prod" / "loki.json"
    comp.parent.mkdir(parents=True, exist_ok=True)
    comp.write_text('{"name":"loki","module":"loki","provider":"aws"}\n')
    si = root / "components" / "prod" / "shared-infra.json"
    si.write_text('{"shared-infra":{"target":{"cluster":{"name":"prod"}}}}\n')

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
        self.g = KuberlyPlatform(self.tmp.name)
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

    def test_new_application_keywords(self) -> None:
        kind, _ = self.g.infer_task_kind("add new application called billing")
        self.assertEqual(kind, "new-application")

    def test_new_database_keywords(self) -> None:
        kind, _ = self.g.infer_task_kind("add database for the orders service")
        self.assertEqual(kind, "new-database")

    def test_plan_review_keywords(self) -> None:
        kind, _ = self.g.infer_task_kind("review the terragrunt plan output on PR 42")
        self.assertEqual(kind, "plan-review")

    def test_cicd_yaml_phrasing(self) -> None:
        kind, _ = self.g.infer_task_kind("create ci yaml for my backend")
        self.assertEqual(kind, "cicd")


class PersonaDAGTests(unittest.TestCase):
    def test_resource_bump_dag_shape(self) -> None:
        # v0.14.0: review phase removed from default DAGs.
        dag = PERSONA_DAGS["resource-bump"]
        ids = [p["id"] for p in dag]
        self.assertEqual(ids, ["scope", "implement"])
        impl = next(p for p in dag if p["id"] == "implement")
        self.assertTrue(impl["needs_approval"])

    def test_default_dags_have_no_review_phase(self) -> None:
        # v0.14.0: every default DAG ends at implement (or has its own
        # bespoke shape — plan-review, stop-target-absent, unknown).
        special = {"plan-review", "stop-target-absent", "unknown"}
        for kind, dag in PERSONA_DAGS.items():
            if kind in special:
                continue
            ids = [p["id"] for p in dag]
            self.assertNotIn("review", ids,
                             f"{kind} DAG should NOT include review by default; got {ids}")
            self.assertNotIn("reconcile", ids,
                             f"{kind} DAG should NOT include reconcile by default; got {ids}")

    def test_cicd_uses_app_cicd_engineer_not_iac_developer(self) -> None:
        dag = PERSONA_DAGS["cicd"]
        impl = next(p for p in dag if p["id"] == "implement")
        self.assertEqual(impl["personas"], ["agent-cicd"])
        self.assertNotIn("agent-infra-ops", impl["personas"])

    def test_incident_diagnose_phase_is_parallel(self) -> None:
        dag = PERSONA_DAGS["incident"]
        diag = next(p for p in dag if p["id"] == "diagnose")
        self.assertTrue(diag["parallel"])
        self.assertCountEqual(diag["personas"],
                              ["agent-sre", "agent-k8s-ops", "agent-planner"])

    def test_v0_11_new_task_kinds_present(self) -> None:
        for kind in ("new-application", "new-database", "plan-review"):
            self.assertIn(kind, PERSONA_DAGS, f"missing DAG for {kind}")

    def test_plan_review_dispatches_only_terragrunt_plan_reviewer(self) -> None:
        dag = PERSONA_DAGS["plan-review"]
        self.assertEqual(len(dag), 1)
        self.assertEqual(dag[0]["personas"], ["terragrunt-plan-reviewer"])
        self.assertFalse(dag[0]["needs_approval"])

    def test_terragrunt_plan_reviewer_in_expected_set(self) -> None:
        from kuberly_platform import EXPECTED_PERSONAS
        self.assertIn("terragrunt-plan-reviewer", EXPECTED_PERSONAS)

    def test_v0_14_legacy_reviewers_not_in_expected_set(self) -> None:
        from kuberly_platform import EXPECTED_PERSONAS
        self.assertNotIn("pr-reviewer-in-context", EXPECTED_PERSONAS)
        self.assertNotIn("pr-reviewer-cold", EXPECTED_PERSONAS)
        self.assertIn("pr-reviewer", EXPECTED_PERSONAS)

    def test_v0_15_stop_no_instance_in_dags(self) -> None:
        self.assertIn("stop-no-instance", PERSONA_DAGS)
        self.assertEqual(PERSONA_DAGS["stop-no-instance"][0]["personas"], [])


class QuickScopeTests(unittest.TestCase):
    """v0.15.0: quick_scope replaces scope-planner agent for typical tasks."""

    def setUp(self) -> None:
        self.tmp = _fake_repo()
        self.g = KuberlyPlatform(self.tmp.name)
        self.g.build()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_actionable_loki_returns_dispatch_recommendation(self) -> None:
        # _fake_repo now wires components/prod/loki.json -> loki module.
        r = self.g.quick_scope(task="bump loki memory", named_modules=["loki"])
        self.assertEqual(r["recommendation"], "dispatch-agent-infra-ops")
        self.assertTrue(r["actionable"])
        self.assertIn("module:aws/loki", r["modules"])
        self.assertIn("# Scope:", r["scope_md"])
        self.assertIn("## Affected", r["scope_md"])
        self.assertIn("## Blast", r["scope_md"])

    def test_unresolved_target_returns_stop_target_absent(self) -> None:
        r = self.g.quick_scope(task="bump tempo memory", named_modules=["tempo"])
        self.assertEqual(r["recommendation"], "stop-target-absent")
        self.assertFalse(r["actionable"])
        self.assertEqual(r["unresolved"], ["tempo"])
        self.assertIn("STOP", r["scope_md"])

    def test_no_named_modules_falls_back(self) -> None:
        # Without named_modules, quick_scope can't infer scope reliably —
        # tell the orchestrator to dispatch the full scope-planner agent.
        r = self.g.quick_scope(task="something vague")
        self.assertEqual(r["recommendation"], "fall-back-to-scope-planner")


class StopNoInstanceTests(unittest.TestCase):
    """v0.15.0: actionability pre-flight in plan_persona_fanout."""

    def _build_module_only_repo(self) -> tempfile.TemporaryDirectory:
        # No component, no app — pure module. Should trigger stop-no-instance
        # for resource-bump-style tasks.
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        (root / "root.hcl").write_text("# fake\n")
        m = root / "clouds" / "aws" / "modules" / "tempo"
        m.mkdir(parents=True)
        (m / "terragrunt.hcl").write_text("\n")
        (m / "kuberly.json").write_text('{"description":"tempo"}\n')
        agents = root / ".claude" / "agents"
        agents.mkdir(parents=True)
        for p in EXPECTED_PERSONAS:
            (agents / f"{p}.md").write_text(f"# {p}\n")
        return tmp

    def test_resource_bump_on_orphan_module_halts(self) -> None:
        with self._build_module_only_repo() as repo:
            g = KuberlyPlatform(repo)
            g.build()
            plan = g.plan_persona_fanout(
                task="bump tempo memory",
                named_modules=["tempo"],
                current_branch="agrishko/feature",
            )
            self.assertEqual(plan["task_kind"], "stop-no-instance")
            self.assertIn("tempo", plan["unactionable_modules"])
            self.assertEqual(plan["phases"][0]["personas"], [])

    def test_incident_on_orphan_does_not_halt(self) -> None:
        # Investigations should still go through even for leaf modules.
        with self._build_module_only_repo() as repo:
            g = KuberlyPlatform(repo)
            g.build()
            plan = g.plan_persona_fanout(
                task="tempo is throwing 5xx errors",
                named_modules=["tempo"],
                current_branch="agrishko/feature",
            )
            self.assertEqual(plan["task_kind"], "incident")
            # Actionability is recorded informationally even when not halting.
            self.assertEqual(plan["unactionable_modules"], [])

    def test_new_application_does_not_halt(self) -> None:
        # new-* task_kinds CREATE the first instance — unactionable is normal.
        with self._build_module_only_repo() as repo:
            g = KuberlyPlatform(repo)
            g.build()
            plan = g.plan_persona_fanout(
                task="add new application using tempo",
                named_modules=["tempo"],
                current_branch="agrishko/feature",
            )
            self.assertNotEqual(plan["task_kind"], "stop-no-instance")


class PlanPersonaFanoutTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = _fake_repo()
        self.g = KuberlyPlatform(self.tmp.name)
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
        # v0.14.0: default DAG ends at implement; review is opt-in.
        self.assertIn("implement", ids)
        self.assertNotIn("review", ids)
        self.assertNotIn("reconcile", ids)
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
        self.assertEqual(impl["personas"], ["agent-cicd"])

    def test_personas_synced_reports_ok_with_full_roster(self) -> None:
        plan = self.g.plan_persona_fanout(task="x", named_modules=["loki"])
        synced = plan["gates"]["personas_synced"]
        self.assertEqual(synced["verdict"], "ok")
        self.assertEqual(synced["found"], len(EXPECTED_PERSONAS))

    def test_review_opt_out_by_default(self) -> None:
        # v0.14.0: review phase is OFF by default to save tokens.
        plan = self.g.plan_persona_fanout(
            task="bump loki memory",
            named_modules=["loki"],
            current_branch="agrishko/feature",
        )
        ids = [p["id"] for p in plan["phases"]]
        self.assertNotIn("review", ids)
        self.assertFalse(plan["with_review"])

    def test_review_opt_in_via_parameter(self) -> None:
        plan = self.g.plan_persona_fanout(
            task="bump loki memory",
            named_modules=["loki"],
            current_branch="agrishko/feature",
            with_review=True,
        )
        ids = [p["id"] for p in plan["phases"]]
        self.assertIn("review", ids)
        review = next(p for p in plan["phases"] if p["id"] == "review")
        self.assertEqual(review["personas"], ["pr-reviewer"])
        self.assertTrue(plan["with_review"])

    def test_review_auto_enabled_when_task_says_review(self) -> None:
        plan = self.g.plan_persona_fanout(
            task="bump loki memory and review the diff",
            named_modules=["loki"],
            current_branch="agrishko/feature",
        )
        # The 'review' word in the task should auto-opt in.
        ids = [p["id"] for p in plan["phases"]]
        self.assertIn("review", ids)
        self.assertTrue(plan["with_review"])

    def test_with_review_no_op_for_plan_review_kind(self) -> None:
        # plan-review has its own DAG (terragrunt-plan-reviewer); don't
        # tack a redundant pr-reviewer phase onto it.
        plan = self.g.plan_persona_fanout(
            task="review the terragrunt plan output on PR 42",
            current_branch="agrishko/feature",
            with_review=True,
        )
        self.assertEqual(plan["task_kind"], "plan-review")
        ids = [p["id"] for p in plan["phases"]]
        self.assertEqual(ids, ["plan-review"])  # not [plan-review, review]


class StopTargetAbsentTests(unittest.TestCase):
    """v0.10.2: planner halts the DAG when named modules don't exist in the
    graph, so the orchestrator can't fan out personas to re-discover the
    absence."""

    def setUp(self) -> None:
        self.tmp = _fake_repo()
        self.g = KuberlyPlatform(self.tmp.name)
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
        self.g = KuberlyPlatform(self.tmp.name)
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
        self.g = KuberlyPlatform(self.tmp.name)
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
        self.g = KuberlyPlatform(self.tmp.name)
        self.g.build()
        self.g.session_init(name="bump", task="bump loki", modules=["loki"],
                            current_branch="feat/x")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_session_init_seeds_status_json(self) -> None:
        st = self.g.session_status("bump")
        self.assertNotIn("error", st)
        self.assertFalse(st.get("_no_status_yet"))
        # Phases should match the persona DAG for resource-bump.
        # v0.14.0: default ends at implement; review is opt-in via with_review.
        ids = [p["id"] for p in st["phases"]]
        self.assertIn("scope", ids)
        self.assertIn("implement", ids)
        # Every persona starts queued
        for p, info in st["personas"].items():
            self.assertEqual(info["status"], "queued")

    def test_set_persona_running_then_done_flows(self) -> None:
        out = self.g.session_set_status("bump", "agent-planner", "running")
        self.assertEqual(out["status"], "running")
        st = self.g.session_status("bump")
        self.assertEqual(st["personas"]["agent-planner"]["status"], "running")
        # Phase rolls up to running
        scope_ph = next(p for p in st["phases"] if p["id"] == "scope")
        self.assertEqual(scope_ph["status"], "running")
        # Mark done
        self.g.session_set_status("bump", "agent-planner", "done")
        st = self.g.session_status("bump")
        scope_ph = next(p for p in st["phases"] if p["id"] == "scope")
        self.assertEqual(scope_ph["status"], "done")
        # Timestamps written
        self.assertIn("started_at", st["personas"]["agent-planner"])
        self.assertIn("ended_at",   st["personas"]["agent-planner"])

    def test_phase_roll_up_with_two_personas(self) -> None:
        # Diagnose phase doesn't exist for resource-bump; switch to incident
        self.g.session_init.__self__  # noqa: keep ref to silence linters
        # Use an incident-kind session for multi-persona phase. v0.22.0+:
        # diagnose phase has three personas (agent-sre, agent-k8s-ops,
        # agent-planner).
        self.g.session_init(name="oom", task="loki ingester OOM",
                            modules=["loki"], current_branch="feat/y")
        # When only one is running, phase should be running
        self.g.session_set_status("oom", "agent-sre", "running")
        st = self.g.session_status("oom")
        diag = next(p for p in st["phases"] if p["id"] == "diagnose")
        self.assertEqual(diag["status"], "running")
        # All three done → phase done
        self.g.session_set_status("oom", "agent-sre", "done")
        self.g.session_set_status("oom", "agent-k8s-ops", "done")
        self.g.session_set_status("oom", "agent-planner", "done")
        st = self.g.session_status("oom")
        diag = next(p for p in st["phases"] if p["id"] == "diagnose")
        self.assertEqual(diag["status"], "done")

    def test_blocked_overrides_running_in_phase_rollup(self) -> None:
        self.g.session_init(name="blk", task="loki ingester OOM",
                            modules=["loki"], current_branch="feat/z")
        self.g.session_set_status("blk", "agent-sre", "running")
        self.g.session_set_status("blk", "agent-planner", "blocked")
        st = self.g.session_status("blk")
        diag = next(p for p in st["phases"] if p["id"] == "diagnose")
        self.assertEqual(diag["status"], "blocked")

    def test_invalid_status_rejected(self) -> None:
        out = self.g.session_set_status("bump", "agent-infra-ops", "frobnicated")
        self.assertIn("error", out)

    def test_unknown_target_rejected(self) -> None:
        out = self.g.session_set_status("bump", "nonexistent-persona", "running")
        self.assertIn("error", out)


class StateOverlayTests(unittest.TestCase):
    """The state overlay turns "module exists in clouds/aws/modules but no
    components/<env>/<m>.json" from a stop-no-instance into actionable —
    when the module IS actually deployed (state confirms it). These tests
    pin the overlay-loader behavior."""

    def setUp(self) -> None:
        self.tmp = _fake_repo()
        # Add a `grafana` module to the fake repo — there's no
        # components/prod/grafana.json (no JSON sidecar), but the overlay
        # will declare it as deployed.
        graf = Path(self.tmp.name) / "clouds" / "aws" / "modules" / "grafana"
        graf.mkdir(parents=True)
        (graf / "terragrunt.hcl").write_text("# grafana\n")
        (graf / "kuberly.json").write_text('{"description":"grafana"}\n')

        # Place a state overlay declaring grafana + loki as deployed in prod.
        # loki ALSO has a JSON sidecar (created by _fake_repo) → should be
        # annotated `also_in_state=True`. grafana is overlay-only → synthetic.
        overlay = Path(self.tmp.name) / ".kuberly" / "state_overlay_prod.json"
        overlay.parent.mkdir(parents=True, exist_ok=True)
        overlay.write_text(
            '{\n'
            '  "schema_version": 1,\n'
            '  "generated_at": "2026-05-05T00:00:00Z",\n'
            '  "generator": "test",\n'
            '  "cluster": {"env": "prod", "name": "prod", "region": "us-east-1",\n'
            '              "account_id": "111111111111",\n'
            '              "state_bucket": "111111111111-us-east-1-prod-tf-states"},\n'
            '  "deployed_modules": [\n'
            '    {"name": "loki",    "state_key": "aws/loki/terraform.tfstate"},\n'
            '    {"name": "grafana", "state_key": "aws/grafana/terraform.tfstate"}\n'
            '  ],\n'
            '  "deployed_applications": []\n'
            '}\n'
        )

        self.g = KuberlyPlatform(self.tmp.name)
        self.g.build()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_overlay_synthesizes_missing_component_node(self) -> None:
        node = self.g.nodes.get("component:prod/grafana")
        self.assertIsNotNone(node, "overlay should synthesize component:prod/grafana")
        self.assertEqual(node["type"], "component")
        self.assertEqual(node["source"], "state")

    def test_overlay_annotates_existing_json_sidecar(self) -> None:
        node = self.g.nodes.get("component:prod/loki")
        self.assertIsNotNone(node)
        # Loki has a JSON sidecar in _fake_repo, so source stays default
        # ("json"-implicit, i.e. no `source` attr or "json"); state overlay
        # must NOT overwrite it but should mark it confirmed-by-state.
        self.assertNotEqual(node.get("source"), "state")
        self.assertTrue(node.get("also_in_state"))

    def test_overlay_unblocks_actionability_check(self) -> None:
        # Before overlay: grafana would have been stop-no-instance.
        self.assertTrue(self.g._has_json_sidecar("prod", "grafana"))
        res = self.g.quick_scope("bump grafana memory", named_modules=["grafana"])
        self.assertEqual(res["recommendation"], "dispatch-agent-infra-ops")
        self.assertTrue(res["actionable"])
        self.assertEqual(res["unactionable"], [])

    def test_overlay_missing_dir_is_noop(self) -> None:
        # Build against a repo that has no kuberly dir at all — must not error.
        tmp2 = _fake_repo()
        try:
            shutil = __import__("shutil")
            claude = Path(tmp2.name) / ".kuberly"
            if claude.exists():
                shutil.rmtree(claude)
            g = KuberlyPlatform(tmp2.name)
            g.build()  # must not raise
            self.assertIn("component:prod/loki", g.nodes)  # JSON sidecar still works
        finally:
            tmp2.cleanup()

    def test_overlay_with_bad_schema_is_skipped(self) -> None:
        tmp2 = _fake_repo()
        try:
            bad = Path(tmp2.name) / ".kuberly" / "state_overlay_prod.json"
            bad.parent.mkdir(parents=True, exist_ok=True)
            # schema_version != 1 → silently skipped
            bad.write_text('{"schema_version": 99, "deployed_modules": [{"name":"x"}]}\n')
            g = KuberlyPlatform(tmp2.name)
            g.build()
            self.assertNotIn("component:prod/x", g.nodes)
        finally:
            tmp2.cleanup()


class StateOnlyActionabilityTests(unittest.TestCase):
    """v0.22.0: a module deployed via terragrunt apply (state_overlay-only,
    NO components/<env>/<x>.json) should be reported actionable. Before
    v0.22.0 the actionability predicate only walked component/application
    edges and missed source="state" component nodes synthesized by the
    state overlay when link_components_to_modules failed to bridge them.
    """

    def _build_state_only_repo(self) -> tempfile.TemporaryDirectory:
        # No components/<env>/loki.json. State overlay declares loki deployed.
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        (root / "root.hcl").write_text("# fake\n")
        m = root / "clouds" / "aws" / "modules" / "loki"
        m.mkdir(parents=True)
        (m / "terragrunt.hcl").write_text("\n")
        (m / "kuberly.json").write_text('{"description":"loki"}\n')
        agents = root / ".claude" / "agents"
        agents.mkdir(parents=True)
        for p in EXPECTED_PERSONAS:
            (agents / f"{p}.md").write_text(f"# {p}\n")
        # State overlay: declares loki deployed under env=prod, but with a
        # mismatched-name component synthesis so link_components_to_modules
        # cannot auto-bridge it. The patched actionability predicate must
        # still recognize source="state" as a valid invoker.
        overlay = root / ".kuberly" / "state_overlay_prod.json"
        overlay.parent.mkdir(parents=True, exist_ok=True)
        overlay.write_text(
            '{\n'
            '  "schema_version": 1,\n'
            '  "generated_at": "2026-05-05T00:00:00Z",\n'
            '  "generator": "test",\n'
            '  "cluster": {"env": "prod", "name": "prod", "region": "us-east-1",\n'
            '              "account_id": "111111111111",\n'
            '              "state_bucket": "111111111111-us-east-1-prod-tf-states"},\n'
            '  "deployed_modules": [\n'
            '    {"name": "loki", "state_key": "aws/loki/terraform.tfstate"}\n'
            '  ],\n'
            '  "deployed_applications": []\n'
            '}\n'
        )
        return tmp

    @staticmethod
    def _strip_link_edges(g: KuberlyPlatform) -> None:
        """Drop the `configures_module` edges link_components_to_modules
        adds. Simulates the failure mode where label-matching can't bridge
        a state-overlay-synthesized component to its module (e.g., when
        state name and module label differ in casing/punctuation). After
        stripping, only source="state" recognition can save actionability.
        """
        g.edges = [e for e in g.edges if e.get("relation") != "configures_module"]

    def test_quick_scope_recognizes_state_only_component(self) -> None:
        with self._build_state_only_repo() as repo:
            g = KuberlyPlatform(repo)
            g.build()
            # Sanity: the synthesized component node has source="state"
            comp = g.nodes.get("component:prod/loki")
            self.assertIsNotNone(comp)
            self.assertEqual(comp.get("source"), "state")
            # Force the failure mode: drop bridge edges so only the v0.22.0
            # source="state" check can save actionability.
            self._strip_link_edges(g)
            res = g.quick_scope(task="bump loki memory",
                                named_modules=["loki"])
            self.assertEqual(res["recommendation"], "dispatch-agent-infra-ops")
            self.assertTrue(res["actionable"])
            self.assertEqual(res["unactionable"], [])

    def test_plan_persona_fanout_no_stop_no_instance_for_state_only(self) -> None:
        with self._build_state_only_repo() as repo:
            g = KuberlyPlatform(repo)
            g.build()
            self._strip_link_edges(g)
            plan = g.plan_persona_fanout(
                task="bump loki memory",
                named_modules=["loki"],
                current_branch="agrishko/feature",
            )
            self.assertNotEqual(plan["task_kind"], "stop-no-instance")
            self.assertEqual(plan["unactionable_modules"], [])


class StateGraphParseTests(unittest.TestCase):
    """Unit tests for state_graph.py's pure functions — no AWS calls."""

    def setUp(self) -> None:
        sg_path = _pkg if (_pkg / "state_graph.py").is_file() else _script_dir
        if str(sg_path) not in sys.path:
            sys.path.insert(0, str(sg_path))
        import state_graph  # noqa: E402
        self.sg = state_graph

    def test_parse_keeps_module_state(self) -> None:
        mods, apps = self.sg._parse_state_keys([
            "aws/loki/terraform.tfstate",
            "aws/grafana/terraform.tfstate",
        ])
        names = sorted(m["name"] for m in mods)
        self.assertEqual(names, ["grafana", "loki"])
        self.assertEqual(apps, [])

    def test_parse_skips_init_module(self) -> None:
        mods, _ = self.sg._parse_state_keys(["aws/init/terraform.tfstate"])
        self.assertEqual(mods, [])

    def test_parse_skips_non_state_keys(self) -> None:
        mods, _ = self.sg._parse_state_keys([
            "aws/loki/terraform.tfstate.tflock",
            "aws/loki/.terragrunt-cache/x",
            "aws/loki/some_other_file.json",
            "outside/aws/loki/terraform.tfstate",
        ])
        self.assertEqual(mods, [])

    def test_parse_per_app_modules(self) -> None:
        mods, apps = self.sg._parse_state_keys([
            "aws/ecs_app/prod/backend/terraform.tfstate",
            "aws/lambda_app/prod/worker/terraform.tfstate",
        ])
        self.assertEqual(sorted(m["name"] for m in mods), ["ecs_app", "lambda_app"])
        self.assertEqual(
            sorted((a["module"], a["env"], a["name"]) for a in apps),
            [("ecs_app", "prod", "backend"), ("lambda_app", "prod", "worker")],
        )

    def test_validator_rejects_command_injection(self) -> None:
        bad = {
            "schema_version": 1,
            "generated_at": "2026-05-05T00:00:00Z",
            "cluster": {
                "env": "prod", "name": "evil; rm -rf /",
                "region": "us-east-1", "account_id": "111111111111",
                "state_bucket": "111111111111-us-east-1-prod-tf-states",
            },
            "deployed_modules": [],
            "deployed_applications": [],
        }
        with self.assertRaises(ValueError):
            self.sg._validate_overlay(bad)

    def test_validator_rejects_long_strings(self) -> None:
        bad = {
            "schema_version": 1,
            "generated_at": "2026-05-05T00:00:00Z",
            "cluster": {
                "env": "prod", "name": "prod",
                "region": "us-east-1",
                "account_id": "1" * 200,  # implausibly long → leak suspicion
                "state_bucket": "x-x-x-tf-states",
            },
            "deployed_modules": [],
            "deployed_applications": [],
        }
        with self.assertRaises(ValueError):
            self.sg._validate_overlay(bad)

    def test_validator_rejects_unknown_schema_version(self) -> None:
        with self.assertRaises(ValueError):
            self.sg._validate_overlay({"schema_version": 99})

    def test_validator_dedupes_modules(self) -> None:
        good = {
            "schema_version": 1,
            "generated_at": "2026-05-05T00:00:00Z",
            "cluster": {
                "env": "prod", "name": "prod",
                "region": "us-east-1", "account_id": "111111111111",
                "state_bucket": "111111111111-us-east-1-prod-tf-states",
            },
            "deployed_modules": [
                {"name": "loki", "state_key": "aws/loki/terraform.tfstate"},
                {"name": "loki", "state_key": "aws/loki/terraform.tfstate"},
            ],
            "deployed_applications": [],
        }
        out = self.sg._validate_overlay(good)
        self.assertEqual(len(out["deployed_modules"]), 1)


class StateGraphResourceExtractTests(unittest.TestCase):
    """Schema 2 (resource graph) — pure-function extractor tests. No AWS."""

    def setUp(self) -> None:
        sg_path = _pkg if (_pkg / "state_graph.py").is_file() else _script_dir
        if str(sg_path) not in sys.path:
            sys.path.insert(0, str(sg_path))
        import state_graph
        self.sg = state_graph

    def _state(self, resources, outputs=None):
        return {"version": 4, "resources": resources, "outputs": outputs or {}}

    def test_extract_skips_data_sources(self) -> None:
        state = self._state([
            {"mode": "managed", "type": "aws_iam_role", "name": "loki",
             "provider": 'provider["registry.terraform.io/hashicorp/aws"]',
             "instances": [{"attributes": {"arn": "arn:secret"}}]},
            {"mode": "data", "type": "aws_caller_identity", "name": "current",
             "instances": [{"attributes": {"account_id": "123"}}]},
        ])
        out = self.sg._extract_module_resources(state)
        self.assertEqual(out["resource_count"], 1)
        self.assertEqual(out["resources"][0]["type"], "aws_iam_role")

    def test_extract_drops_all_attribute_values(self) -> None:
        state = self._state([
            {"mode": "managed", "type": "kubernetes_secret", "name": "creds",
             "provider": 'provider["registry.terraform.io/hashicorp/kubernetes"]',
             "instances": [{"attributes": {"data": {"password": "VEhJU0lTU0VDUkVU"},
                                            "metadata": [{"name": "loki-creds"}]}}]},
        ], outputs={"endpoint": {"value": "http://internal-uri", "sensitive": True}})
        out = self.sg._extract_module_resources(state)
        blob = json.dumps(out)  # full serialized output
        # No attribute or output VALUE may appear in serialized output.
        for forbidden in ("VEhJU0lTU0VDUkVU", "http://internal-uri", "loki-creds"):
            self.assertNotIn(forbidden, blob,
                             f"leaked attribute/output value: {forbidden!r}")
        # But the resource itself IS visible (existence preserved).
        self.assertEqual(out["resource_count"], 1)
        self.assertEqual(out["resources"][0]["type"], "kubernetes_secret")
        # Output NAME is kept; output value is not.
        self.assertEqual(out["output_names"], ["endpoint"])

    def test_extract_captures_dependencies(self) -> None:
        state = self._state([
            {"mode": "managed", "type": "helm_release", "name": "loki",
             "provider": 'provider["registry.terraform.io/hashicorp/helm"]',
             "instances": [{"dependencies": ["module.iam.aws_iam_role.loki",
                                              "kubernetes_namespace.monitoring"]}]},
        ])
        out = self.sg._extract_module_resources(state)
        self.assertEqual(out["resources"][0]["depends_on"],
                         ["kubernetes_namespace.monitoring",
                          "module.iam.aws_iam_role.loki"])

    def test_extract_provider_cleaning(self) -> None:
        cases = [
            ('provider["registry.terraform.io/hashicorp/aws"]', "hashicorp/aws"),
            ('provider["registry.terraform.io/hashicorp/kubernetes"]', "hashicorp/kubernetes"),
            ('provider["registry.terraform.io/grafana/grafana"]', "grafana/grafana"),
            ("", ""),
            ("garbage", ""),
        ]
        for raw, expected in cases:
            self.assertEqual(self.sg._clean_provider(raw), expected, raw)

    def test_validator_accepts_schema_2(self) -> None:
        doc = {
            "schema_version": 2,
            "generated_at": "2026-05-05T00:00:00Z",
            "cluster": {
                "env": "prod", "name": "prod",
                "region": "us-east-1", "account_id": "111111111111",
                "state_bucket": "111111111111-us-east-1-prod-tf-states",
            },
            "deployed_modules": [{"name": "loki", "state_key": "aws/loki/terraform.tfstate"}],
            "deployed_applications": [],
            "modules": {
                "loki": {
                    "resource_count": 1,
                    "resources": [
                        {"address": "module.helm.helm_release.loki",
                         "type": "helm_release", "name": "loki",
                         "provider": "hashicorp/helm", "instance_count": 1,
                         "depends_on": []}
                    ],
                    "output_names": ["loki_endpoint"],
                }
            },
        }
        out = self.sg._validate_overlay(doc)
        self.assertEqual(out["schema_version"], 2)
        self.assertIn("modules", out)
        self.assertEqual(len(out["modules"]["loki"]["resources"]), 1)

    def test_validator_rejects_resource_address_with_injection(self) -> None:
        bad = {
            "schema_version": 2,
            "generated_at": "2026-05-05T00:00:00Z",
            "cluster": {
                "env": "prod", "name": "prod",
                "region": "us-east-1", "account_id": "111111111111",
                "state_bucket": "111111111111-us-east-1-prod-tf-states",
            },
            "deployed_modules": [],
            "deployed_applications": [],
            "modules": {
                "loki": {
                    "resource_count": 1,
                    "resources": [
                        {"address": "$(rm -rf /)", "type": "helm_release",
                         "name": "loki", "instance_count": 1, "depends_on": []}
                    ],
                    "output_names": [],
                }
            },
        }
        with self.assertRaises(ValueError):
            self.sg._validate_overlay(bad)


class StateOverlaySchema2Tests(unittest.TestCase):
    """End-to-end: overlay file with schema 2 -> resource nodes + edges + redaction."""

    def setUp(self) -> None:
        self.tmp = _fake_repo()
        overlay = Path(self.tmp.name) / ".kuberly" / "state_overlay_prod.json"
        overlay.parent.mkdir(parents=True, exist_ok=True)
        overlay.write_text(json.dumps({
            "schema_version": 2,
            "generated_at": "2026-05-05T00:00:00Z",
            "generator": "test",
            "cluster": {"env": "prod", "name": "prod", "region": "us-east-1",
                        "account_id": "111111111111",
                        "state_bucket": "111111111111-us-east-1-prod-tf-states"},
            "deployed_modules": [{"name": "loki", "state_key": "aws/loki/terraform.tfstate"}],
            "deployed_applications": [],
            "modules": {
                "loki": {
                    "resource_count": 3,
                    "resources": [
                        {"address": "module.helm.helm_release.loki",
                         "type": "helm_release", "name": "loki",
                         "provider": "hashicorp/helm", "instance_count": 1,
                         "depends_on": ["module.iam.aws_iam_role.loki"]},
                        {"address": "module.iam.aws_iam_role.loki",
                         "type": "aws_iam_role", "name": "loki",
                         "provider": "hashicorp/aws", "instance_count": 1,
                         "depends_on": []},
                        {"address": "kubernetes_secret.creds",
                         "type": "kubernetes_secret", "name": "creds",
                         "provider": "hashicorp/kubernetes", "instance_count": 1,
                         "depends_on": []},
                    ],
                    "output_names": ["loki_endpoint"],
                }
            },
        }) + "\n")
        self.g = KuberlyPlatform(self.tmp.name)
        self.g.build()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_resource_nodes_synthesized(self) -> None:
        rids = [n["id"] for n in self.g.nodes.values() if n.get("type") == "resource"]
        self.assertEqual(len(rids), 3)
        self.assertIn("resource:prod/loki/module.helm.helm_release.loki", rids)
        self.assertIn("resource:prod/loki/module.iam.aws_iam_role.loki", rids)
        self.assertIn("resource:prod/loki/kubernetes_secret.creds", rids)

    def test_sensitive_resources_tagged_redacted(self) -> None:
        helm = self.g.nodes["resource:prod/loki/module.helm.helm_release.loki"]
        secret = self.g.nodes["resource:prod/loki/kubernetes_secret.creds"]
        iam = self.g.nodes["resource:prod/loki/module.iam.aws_iam_role.loki"]
        self.assertTrue(helm.get("redacted"))
        self.assertTrue(secret.get("redacted"))
        self.assertFalse(iam.get("redacted", False))

    def test_depends_on_edges_emitted(self) -> None:
        deps = [(e["source"], e["target"]) for e in self.g.edges
                if e.get("relation") == "depends_on"]
        self.assertIn(
            ("resource:prod/loki/module.helm.helm_release.loki",
             "resource:prod/loki/module.iam.aws_iam_role.loki"),
            deps,
        )

    def test_component_enriched_with_resource_count(self) -> None:
        comp = self.g.nodes["component:prod/loki"]
        self.assertEqual(comp.get("resource_count"), 3)
        self.assertEqual(comp.get("output_names"), ["loki_endpoint"])

    def test_query_resources_filter_by_type(self) -> None:
        out = self.g.query_resources(resource_type="helm_release")
        self.assertEqual(out["count"], 1)
        self.assertEqual(out["matches"][0]["resource_type"], "helm_release")
        self.assertTrue(out["matches"][0]["redacted"])

    def test_query_resources_exclude_redacted(self) -> None:
        out = self.g.query_resources(include_redacted=False)
        types = {m["resource_type"] for m in out["matches"]}
        self.assertEqual(types, {"aws_iam_role"})

    def test_query_resources_filter_by_module(self) -> None:
        out = self.g.query_resources(module="loki", environment="prod")
        self.assertEqual(out["count"], 3)


class K8sGraphExtractTests(unittest.TestCase):
    """Per-kind extractor tests for k8s_graph.py — no kubectl, no cluster."""

    def setUp(self) -> None:
        sg_path = _pkg if (_pkg / "k8s_graph.py").is_file() else _script_dir
        if str(sg_path) not in sys.path:
            sys.path.insert(0, str(sg_path))
        import k8s_graph
        self.kg = k8s_graph

    def test_deployment_extraction_drops_secrets(self) -> None:
        deploy = {
            "kind": "Deployment", "apiVersion": "apps/v1",
            "metadata": {"name": "loki", "namespace": "monitoring",
                         "labels": {"app": "loki"},
                         "annotations": {"deployment.kubernetes.io/revision": "5",
                                         "secret.payload": "leak-me"}},
            "spec": {
                "replicas": 3,
                "template": {"spec": {
                    "serviceAccountName": "loki",
                    "containers": [{
                        "name": "loki", "image": "grafana/loki:3.0.0",
                        "command": ["sh", "-c", "rm -rf /"],
                        "args": ["--password=topsecret"],
                        "env": [{"name": "DB_PASS", "value": "leakme"}],
                        "envFrom": [{"configMapRef": {"name": "loki-cfg"}}],
                    }],
                    "volumes": [
                        {"name": "creds", "secret": {"secretName": "loki-creds"}},
                    ],
                }},
            },
            "status": {"readyReplicas": 3},
        }
        out = self.kg._extract_resource(deploy)
        blob = json.dumps(out)
        for forbidden in ("topsecret", "leakme", "rm -rf",
                          "leak-me", "deployment.kubernetes.io/revision",
                          "readyReplicas"):
            self.assertNotIn(forbidden, blob, f"leaked: {forbidden!r}")
        self.assertEqual(out["replicas"], 3)
        self.assertEqual(out["service_account"], "loki")
        self.assertIn("loki-cfg", out["config_refs"])
        self.assertIn("loki-creds", out["secret_refs"])

    def test_secret_extraction_keeps_keys_drops_values(self) -> None:
        secret = {
            "kind": "Secret", "apiVersion": "v1",
            "metadata": {"name": "creds", "namespace": "monitoring"},
            "type": "Opaque",
            "data": {"password": "VEhJU0lTU0VDUkVU", "token": "WVlZ"},
            "stringData": {"raw": "RAW-SECRET"},
        }
        out = self.kg._extract_resource(secret)
        blob = json.dumps(out)
        for forbidden in ("VEhJU0lTU0VDUkVU", "WVlZ", "RAW-SECRET"):
            self.assertNotIn(forbidden, blob)
        self.assertEqual(out["secret_type"], "Opaque")
        self.assertEqual(sorted(out["data_keys"]), ["password", "token"])

    def test_configmap_extraction_keeps_keys_drops_values(self) -> None:
        cm = {
            "kind": "ConfigMap", "apiVersion": "v1",
            "metadata": {"name": "cfg", "namespace": "monitoring"},
            "data": {"loki.yaml": "auth_enabled: false\nadmin_password: TOPSECRET"},
        }
        out = self.kg._extract_resource(cm)
        self.assertNotIn("TOPSECRET", json.dumps(out))
        self.assertEqual(out["data_keys"], ["loki.yaml"])

    def test_serviceaccount_irsa_lift(self) -> None:
        sa = {
            "kind": "ServiceAccount", "apiVersion": "v1",
            "metadata": {"name": "loki", "namespace": "monitoring",
                         "annotations": {
                             "eks.amazonaws.com/role-arn": "arn:aws:iam::111111111111:role/loki",
                             "kubectl.kubernetes.io/last-applied": "{...}",  # NOT in keep set
                         }},
        }
        out = self.kg._extract_resource(sa)
        self.assertEqual(out["irsa_role_arn"], "arn:aws:iam::111111111111:role/loki")
        self.assertIn("eks.amazonaws.com/role-arn", out["annotations"])
        self.assertNotIn("kubectl.kubernetes.io/last-applied", out["annotations"])

    def test_unknown_annotations_dropped(self) -> None:
        deploy = {
            "kind": "Deployment", "apiVersion": "apps/v1",
            "metadata": {"name": "x", "namespace": "y",
                         "annotations": {"random.example.com/payload": "DO-NOT-LEAK"}},
            "spec": {"replicas": 1, "template": {"spec": {}}},
        }
        out = self.kg._extract_resource(deploy)
        self.assertNotIn("DO-NOT-LEAK", json.dumps(out))


class K8sOverlayConsumerTests(unittest.TestCase):
    """End-to-end: k8s overlay file -> graph nodes/edges + IRSA bridge."""

    def setUp(self) -> None:
        self.tmp = _fake_repo()
        # State overlay creates aws_iam_role.loki resource node — IRSA target
        Path(self.tmp.name, ".kuberly", "state_overlay_prod.json").parent.mkdir(
            parents=True, exist_ok=True)
        Path(self.tmp.name, ".kuberly", "state_overlay_prod.json").write_text(json.dumps({
            "schema_version": 2, "generated_at": "2026-05-05T00:00:00Z",
            "generator": "test",
            "cluster": {"env": "prod", "name": "prod", "region": "us-east-1",
                        "account_id": "111111111111",
                        "state_bucket": "111111111111-us-east-1-prod-tf-states"},
            "deployed_modules": [{"name": "loki", "state_key": "aws/loki/terraform.tfstate"}],
            "deployed_applications": [],
            "modules": {"loki": {"resource_count": 1, "output_names": [],
                "resources": [{"address": "module.iam.aws_iam_role.loki",
                               "type": "aws_iam_role", "name": "loki",
                               "provider": "hashicorp/aws", "instance_count": 1,
                               "depends_on": []}]}},
        }) + "\n")
        # K8s overlay
        Path(self.tmp.name, ".kuberly", "k8s_overlay_prod.json").write_text(json.dumps({
            "schema_version": 1, "generated_at": "2026-05-05T00:00:00Z",
            "generator": "test",
            "cluster": {"env": "prod", "name": "prod", "context": ""},
            "namespaces": ["monitoring"],
            "resources": [
                {"kind": "Deployment", "apiVersion": "apps/v1",
                 "namespace": "monitoring", "name": "loki",
                 "labels": {"app.kubernetes.io/name": "loki"},
                 "owner_refs": [],
                 "service_account": "loki", "replicas": 2,
                 "containers": ["loki"], "images": ["grafana/loki:3.0.0"],
                 "config_refs": ["loki-cfg"], "secret_refs": ["loki-creds"],
                 "pvc_refs": []},
                {"kind": "Service", "apiVersion": "v1",
                 "namespace": "monitoring", "name": "loki",
                 "labels": {}, "owner_refs": [],
                 "selector": {"app.kubernetes.io/name": "loki"},
                 "ports": [{"port": 3100, "protocol": "TCP"}],
                 "service_type": "ClusterIP"},
                {"kind": "ServiceAccount", "apiVersion": "v1",
                 "namespace": "monitoring", "name": "loki",
                 "labels": {}, "owner_refs": [],
                 "annotations": {"eks.amazonaws.com/role-arn":
                                 "arn:aws:iam::111111111111:role/loki"},
                 "irsa_role_arn": "arn:aws:iam::111111111111:role/loki"},
                {"kind": "Secret", "apiVersion": "v1",
                 "namespace": "monitoring", "name": "loki-creds",
                 "labels": {}, "owner_refs": [],
                 "secret_type": "Opaque", "data_keys": ["password"]},
            ],
        }) + "\n")
        self.g = KuberlyPlatform(self.tmp.name)
        self.g.build()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_k8s_nodes_synthesized(self) -> None:
        kinds = sorted({n.get("k8s_kind") for n in self.g.nodes.values()
                        if n.get("type") == "k8s_resource"})
        self.assertEqual(kinds, ["Deployment", "Secret", "Service", "ServiceAccount"])

    def test_workload_uses_sa_edge(self) -> None:
        edges = [(e["source"], e["target"]) for e in self.g.edges
                 if e.get("relation") == "uses_sa"]
        self.assertIn(("k8s:prod/monitoring/Deployment/loki",
                       "k8s:prod/monitoring/ServiceAccount/loki"), edges)

    def test_service_selects_workload_via_label_match(self) -> None:
        edges = [(e["source"], e["target"]) for e in self.g.edges
                 if e.get("relation") == "selects"]
        self.assertIn(("k8s:prod/monitoring/Service/loki",
                       "k8s:prod/monitoring/Deployment/loki"), edges)

    def test_irsa_bridge_to_state_iam_role(self) -> None:
        edges = [(e["source"], e["target"]) for e in self.g.edges
                 if e.get("relation") == "irsa_bound"]
        self.assertIn(("k8s:prod/monitoring/ServiceAccount/loki",
                       "resource:prod/loki/module.iam.aws_iam_role.loki"), edges)

    def test_secret_node_redacted(self) -> None:
        secret = self.g.nodes["k8s:prod/monitoring/Secret/loki-creds"]
        self.assertTrue(secret.get("redacted"))
        # No value-bearing fields in the node either.
        forbidden = {"data", "stringData", "value"}
        self.assertEqual(forbidden & set(secret.keys()), set())

    def test_query_k8s_filter_by_kind(self) -> None:
        out = self.g.query_k8s(kind="Deployment")
        self.assertEqual(out["count"], 1)

    def test_query_k8s_label_selector(self) -> None:
        out = self.g.query_k8s(label_selector={"app.kubernetes.io/name": "loki"})
        # Only the Deployment carries that label in the fixture.
        self.assertEqual(out["count"], 1)
        self.assertEqual(out["matches"][0]["k8s_kind"], "Deployment")

    def test_query_k8s_exclude_redacted(self) -> None:
        out = self.g.query_k8s(include_redacted=False)
        kinds = {m["k8s_kind"] for m in out["matches"]}
        self.assertNotIn("Secret", kinds)
        self.assertNotIn("ConfigMap", kinds)


class K8sCRDExtractTests(unittest.TestCase):
    """Per-CRD extractor coverage — Karpenter, ArgoCD, Istio."""

    def setUp(self) -> None:
        sg_path = _pkg if (_pkg / "k8s_graph.py").is_file() else _script_dir
        if str(sg_path) not in sys.path:
            sys.path.insert(0, str(sg_path))
        import k8s_graph
        self.kg = k8s_graph

    def test_karpenter_nodepool_keeps_keys_drops_values(self) -> None:
        np = {
            "apiVersion": "karpenter.sh/v1", "kind": "NodePool",
            "metadata": {"name": "default"},
            "spec": {
                "limits": {"cpu": "1000", "memory": "2000Gi"},
                "disruption": {"consolidationPolicy": "WhenUnderutilized"},
                "template": {"spec": {
                    "nodeClassRef": {"kind": "EC2NodeClass", "name": "default"},
                    "requirements": [
                        {"key": "karpenter.k8s.aws/instance-family",
                         "operator": "In", "values": ["m5", "SECRET-BOX"]},
                    ],
                }},
            },
            "status": {"resources": {}},
        }
        out = self.kg._extract_resource(np)
        # Requirement VALUES never kept — just keys.
        self.assertNotIn("SECRET-BOX", json.dumps(out))
        self.assertEqual(out["node_class_name"], "default")
        self.assertEqual(out["limits_cpu"], "1000")
        self.assertEqual(out["consolidation_policy"], "WhenUnderutilized")
        self.assertIn("karpenter.k8s.aws/instance-family", out["requirement_keys"])

    def test_argocd_application_strips_credentials_in_repo_url(self) -> None:
        app = {
            "apiVersion": "argoproj.io/v1alpha1", "kind": "Application",
            "metadata": {"name": "loki", "namespace": "argocd"},
            "spec": {
                "project": "default",
                "source": {
                    "repoURL": "https://user:LEAK_PWD@github.com/foo/bar",
                    "path": "charts/loki",
                    "targetRevision": "main",
                },
                "destination": {"server": "https://kubernetes.default.svc",
                                "namespace": "monitoring"},
            },
            "status": {"sync": {"status": "Synced"}},
        }
        out = self.kg._extract_resource(app)
        blob = json.dumps(out)
        self.assertNotIn("LEAK_PWD", blob)
        self.assertNotIn("user:", blob)
        self.assertNotIn("Synced", blob)
        self.assertEqual(out["source_repo"], "https://github.com/foo/bar")
        self.assertEqual(out["dest_namespace"], "monitoring")

    def test_istio_virtualservice_routes_extracted(self) -> None:
        vs = {
            "apiVersion": "networking.istio.io/v1", "kind": "VirtualService",
            "metadata": {"name": "loki-vs", "namespace": "monitoring"},
            "spec": {
                "hosts": ["loki.example.com"],
                "gateways": ["istio-system/main-gw"],
                "http": [{"route": [{"destination": {
                    "host": "loki-distributor.monitoring.svc.cluster.local",
                    "port": {"number": 3100}}}]}],
            },
        }
        out = self.kg._extract_resource(vs)
        self.assertEqual(out["hosts"], ["loki.example.com"])
        self.assertEqual(out["gateways"], ["istio-system/main-gw"])
        self.assertEqual(out["routes"][0]["host"],
                         "loki-distributor.monitoring.svc.cluster.local")
        self.assertEqual(out["routes"][0]["port"], 3100)

    def test_istio_gateway_drops_tls_secret_ref(self) -> None:
        gw = {
            "apiVersion": "networking.istio.io/v1", "kind": "Gateway",
            "metadata": {"name": "main", "namespace": "istio-system"},
            "spec": {
                "selector": {"istio": "ingressgateway"},
                "servers": [{
                    "port": {"number": 443, "protocol": "HTTPS"},
                    "hosts": ["*.example.com"],
                    "tls": {"mode": "SIMPLE", "credentialName": "MUST-NOT-LEAK"},
                }],
            },
        }
        out = self.kg._extract_resource(gw)
        self.assertNotIn("MUST-NOT-LEAK", json.dumps(out))
        self.assertEqual(out["selector"], {"istio": "ingressgateway"})
        self.assertEqual(out["servers"][0]["port"], 443)
        self.assertEqual(out["servers"][0]["protocol"], "HTTPS")


class K8sCRDConsumerTests(unittest.TestCase):
    """End-to-end: CRD nodes + edges in the consumer graph."""

    def setUp(self) -> None:
        self.tmp = _fake_repo()
        # Service in monitoring ns so VirtualService route_to has a target
        Path(self.tmp.name, ".kuberly", "k8s_overlay_prod.json").parent.mkdir(
            parents=True, exist_ok=True)
        Path(self.tmp.name, ".kuberly", "k8s_overlay_prod.json").write_text(json.dumps({
            "schema_version": 1, "generated_at": "2026-05-05T00:00:00Z",
            "generator": "test",
            "cluster": {"env": "prod", "name": "prod", "context": ""},
            "namespaces": ["monitoring", "argocd", "istio-system"],
            "resources": [
                {"kind": "Service", "apiVersion": "v1",
                 "namespace": "monitoring", "name": "loki-distributor",
                 "labels": {}, "owner_refs": [],
                 "selector": {"app": "loki"},
                 "ports": [{"port": 3100, "protocol": "TCP"}],
                 "service_type": "ClusterIP"},
                # Karpenter — cluster-scoped (ns="")
                {"kind": "NodePool", "apiVersion": "karpenter.sh/v1",
                 "namespace": "", "name": "default",
                 "labels": {}, "owner_refs": [],
                 "node_class_kind": "EC2NodeClass", "node_class_name": "default",
                 "limits_cpu": "1000", "limits_memory": "2000Gi",
                 "consolidation_policy": "WhenUnderutilized",
                 "requirement_keys": ["kubernetes.io/arch"]},
                {"kind": "EC2NodeClass", "apiVersion": "karpenter.k8s.aws/v1",
                 "namespace": "", "name": "default",
                 "labels": {}, "owner_refs": [],
                 "ami_family": "Bottlerocket", "iam_role": "KarpenterNodeRole"},
                # ArgoCD
                {"kind": "Application", "apiVersion": "argoproj.io/v1alpha1",
                 "namespace": "argocd", "name": "loki",
                 "labels": {}, "owner_refs": [],
                 "argocd_project": "default",
                 "source_repo": "https://github.com/foo/bar",
                 "source_path": "charts/loki", "source_revision": "main",
                 "dest_server": "https://kubernetes.default.svc",
                 "dest_namespace": "monitoring"},
                # Istio
                {"kind": "VirtualService", "apiVersion": "networking.istio.io/v1",
                 "namespace": "monitoring", "name": "loki-vs",
                 "labels": {}, "owner_refs": [],
                 "hosts": ["loki.example.com"],
                 "gateways": ["istio-system/main-gw"],
                 "routes": [{"host": "loki-distributor.monitoring.svc.cluster.local",
                             "port": 3100}]},
                {"kind": "Gateway", "apiVersion": "networking.istio.io/v1",
                 "namespace": "istio-system", "name": "main-gw",
                 "labels": {}, "owner_refs": [],
                 "selector": {"istio": "ingressgateway"},
                 "servers": [{"port": 443, "protocol": "HTTPS",
                              "hosts": ["*.example.com"]}]},
            ],
        }) + "\n")
        self.g = KuberlyPlatform(self.tmp.name)
        self.g.build()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_crd_nodes_synthesized(self) -> None:
        kinds = sorted({n.get("k8s_kind") for n in self.g.nodes.values()
                        if n.get("type") == "k8s_resource"})
        self.assertIn("NodePool", kinds)
        self.assertIn("EC2NodeClass", kinds)
        self.assertIn("Application", kinds)
        self.assertIn("VirtualService", kinds)
        self.assertIn("Gateway", kinds)

    def test_nodepool_to_ec2nodeclass_edge(self) -> None:
        edges = [(e["source"], e["target"]) for e in self.g.edges
                 if e.get("relation") == "uses_node_class"]
        self.assertIn(("k8s:prod//NodePool/default",
                       "k8s:prod//EC2NodeClass/default"), edges)

    def test_argocd_app_targets_namespace(self) -> None:
        edges = [(e["source"], e["target"]) for e in self.g.edges
                 if e.get("relation") == "targets_namespace"]
        self.assertIn(("k8s:prod/argocd/Application/loki",
                       "k8s_namespace:prod/monitoring"), edges)

    def test_virtualservice_bound_to_gateway(self) -> None:
        edges = [(e["source"], e["target"]) for e in self.g.edges
                 if e.get("relation") == "bound_to_gateway"]
        self.assertIn(("k8s:prod/monitoring/VirtualService/loki-vs",
                       "k8s:prod/istio-system/Gateway/main-gw"), edges)

    def test_virtualservice_routes_to_service(self) -> None:
        edges = [(e["source"], e["target"]) for e in self.g.edges
                 if e.get("relation") == "routes_to"]
        # FQDN -> short svc name + ns extracted
        self.assertIn(("k8s:prod/monitoring/VirtualService/loki-vs",
                       "k8s:prod/monitoring/Service/loki-distributor"), edges)


class DocsOverlayTests(unittest.TestCase):
    """Docs overlay (knowledge layer) — extraction + cross-link edges +
    find_docs + graph_index meta tool."""

    def setUp(self) -> None:
        self.tmp = _fake_repo()
        # The fake repo already has a `loki` module + `prod/loki` component.
        # Add an OpenSpec change + a skill + an agent that mention loki.
        Path(self.tmp.name, ".apm/skills/test-skill").mkdir(parents=True, exist_ok=True)
        Path(self.tmp.name, ".apm/skills/test-skill/SKILL.md").write_text(
            "---\nname: test-skill\ndescription: Test skill that talks about `loki` and `eks`\n---\n"
            "# Test skill\n## Section\nThis skill mentions `loki` and `prod/loki` and `nonexistent`.\n"
            "Link: [other doc](../../docs/HELLO.md)\n"
        )
        Path(self.tmp.name, "agents").mkdir(exist_ok=True)
        Path(self.tmp.name, "agents/foo.md").write_text(
            "---\nname: foo\ndescription: Test agent\ntools: Read, Write, mcp__kuberly-platform__query_nodes\n---\n"
            "# Foo agent\n## How\nUse `loki` for logs.\n"
        )
        Path(self.tmp.name, "docs").mkdir(exist_ok=True)
        Path(self.tmp.name, "docs/HELLO.md").write_text("# Hello\nA test doc.\n")

        # Build the docs overlay file directly (no need to run docs_graph.py)
        overlay = Path(self.tmp.name) / ".kuberly" / "docs_overlay.json"
        overlay.parent.mkdir(parents=True, exist_ok=True)
        overlay.write_text(json.dumps({
            "schema_version": 1,
            "generated_at": "2026-05-05T00:00:00Z",
            "generator": "kuberly-skills/docs_graph.py",
            "embed_provider": "",
            "docs": [
                {"id": "skill/test-skill", "kind": "skill",
                 "path": ".apm/skills/test-skill/SKILL.md",
                 "title": "test-skill",
                 "description": "Test skill that talks about loki and eks",
                 "headings": ["Test skill", "Section"],
                 "tools": [], "linked_docs": ["docs/HELLO.md"],
                 "mentions": {"modules": ["loki"], "components": ["loki"], "applications": []},
                 "content_sha": "sha256:abc123"},
                {"id": "agent/foo", "kind": "agent",
                 "path": "agents/foo.md",
                 "title": "foo",
                 "description": "Test agent that handles loki incidents",
                 "headings": ["Foo agent", "How"],
                 "tools": ["Read", "mcp__kuberly-platform__query_nodes"],
                 "linked_docs": [],
                 "mentions": {"modules": ["loki"], "components": [], "applications": []},
                 "content_sha": "sha256:def456"},
                {"id": "doc/HELLO", "kind": "doc",
                 "path": "docs/HELLO.md",
                 "title": "Hello", "description": "",
                 "headings": ["Hello"], "tools": [], "linked_docs": [],
                 "mentions": {"modules": [], "components": [], "applications": []},
                 "content_sha": "sha256:ghi789"},
            ],
        }) + "\n")
        self.g = KuberlyPlatform(self.tmp.name)
        self.g.build()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_doc_nodes_synthesized(self) -> None:
        ids = sorted(n["id"] for n in self.g.nodes.values() if n.get("type") == "doc")
        self.assertEqual(ids, ["doc:agent/foo", "doc:doc/HELLO", "doc:skill/test-skill"])

    def test_links_to_edge_emitted(self) -> None:
        edges = [(e["source"], e["target"]) for e in self.g.edges
                 if e.get("relation") == "links_to"]
        self.assertIn(("doc:skill/test-skill", "doc:doc/HELLO"), edges)

    def test_mentions_edges_to_module(self) -> None:
        edges = [(e["source"], e["target"]) for e in self.g.edges
                 if e.get("relation") == "mentions"]
        # Both skill and agent mention `loki` -> edges to module:aws/loki
        self.assertIn(("doc:skill/test-skill", "module:aws/loki"), edges)
        self.assertIn(("doc:agent/foo", "module:aws/loki"), edges)

    def test_uses_tool_edges(self) -> None:
        edges = [(e["source"], e["target"]) for e in self.g.edges
                 if e.get("relation") == "uses_tool"]
        self.assertIn(("doc:agent/foo", "tool:Read"), edges)
        self.assertIn(("doc:agent/foo", "tool:mcp__kuberly-platform__query_nodes"), edges)

    def test_find_docs_keyword_match(self) -> None:
        res = self.g.find_docs(query="loki", limit=10)
        self.assertGreater(res["count"], 0)
        ids = {m["id"] for m in res["matches"]}
        # Both docs that mention loki should rank above the empty doc.
        self.assertIn("doc:skill/test-skill", ids)
        self.assertIn("doc:agent/foo", ids)

    def test_find_docs_kind_filter(self) -> None:
        res = self.g.find_docs(query="loki", kind="skill", limit=10)
        for m in res["matches"]:
            self.assertEqual(m["doc_kind"], "skill")

    def test_graph_index_reports_layers(self) -> None:
        idx = self.g.graph_index()
        self.assertGreater(idx["node_counts_by_type"].get("doc", 0), 0)
        self.assertGreater(idx["edge_counts_by_relation"].get("uses_tool", 0), 0)
        meta = idx.get("docs_overlay_meta") or {}
        self.assertEqual(meta.get("doc_count"), 3)


class DocsGraphProducerTests(unittest.TestCase):
    """Pure tests for the offline docs_graph.py extractor — no network."""

    def setUp(self) -> None:
        sg_path = _pkg if (_pkg / "docs_graph.py").is_file() else _script_dir
        if str(sg_path) not in sys.path:
            sys.path.insert(0, str(sg_path))
        import docs_graph
        self.dg = docs_graph

    def test_classify_recognises_skill(self) -> None:
        self.assertEqual(self.dg._classify(".apm/skills/foo/SKILL.md"), "skill")
        self.assertEqual(self.dg._classify("agents/agent-sre.md"), "agent")
        self.assertEqual(self.dg._classify("docs/AGENT_SESSIONS.md"), "doc")
        self.assertIsNone(self.dg._classify("apm_modules/x/y.md"))
        self.assertIsNone(self.dg._classify("random/file.txt"))

    def test_doc_id_stable_human(self) -> None:
        self.assertEqual(self.dg._doc_id(".apm/skills/foo/SKILL.md", "skill"),
                         "skill/foo")
        self.assertEqual(self.dg._doc_id("agents/agent-sre.md", "agent"),
                         "agent/agent-sre")

    def test_frontmatter_parses_inline_list(self) -> None:
        text = (
            "---\n"
            "name: x\n"
            "description: 'A thing'\n"
            "tools: Read, Write, Bash\n"
            "---\n# body\n"
        )
        fm = self.dg._parse_frontmatter(text)
        self.assertEqual(fm["name"], "x")
        self.assertEqual(fm["description"], "A thing")
        self.assertEqual(fm["tools"], ["Read", "Write", "Bash"])

    def test_mentions_match_known_modules(self) -> None:
        text = "Backticks `loki` and `unknown_module` and prose loki."
        out = self.dg._detect_mentions(text, {"loki"}, set(), set())
        self.assertEqual(out["modules"], ["loki"])

    def test_validator_rejects_bad_doc_id(self) -> None:
        bad = {
            "schema_version": 1,
            "generated_at": "2026-05-05T00:00:00Z",
            "embed_provider": "",
            "docs": [{"id": "$(rm -rf /)", "kind": "skill",
                      "path": "x.md", "content_sha": "sha256:ab"}],
        }
        out = self.dg._validate_overlay(bad)
        self.assertEqual(len(out["docs"]), 0)


class GraphHtmlVizTests(unittest.TestCase):
    """v0.23.0: cytoscape compound-node graph viz.

    Builds a tiny in-memory graph with one node per source layer
    (static / state / k8s / docs), renders the HTML, and asserts:
      - the file exists and contains the cytoscape script tag
      - the embedded NODES json parses and every leaf has source_layer
        in {static, state, k8s, docs}
      - every k8s node has a parent compound id, and every parent id
        also exists as a node (cytoscape requires this)
    """

    def _build_graph(self):
        from kuberly_platform import KuberlyPlatform

        tmp = _fake_repo()
        g = KuberlyPlatform(tmp.name)
        g.build()
        # Inject one synthetic node per source layer so the test
        # exercises every classification branch even if the fake repo
        # is missing overlays.
        g.add_node("k8s:prod/monitoring/Deployment/loki",
                   type="k8s_resource", label="Deployment/loki",
                   environment="prod", k8s_kind="Deployment",
                   k8s_namespace="monitoring", k8s_name="loki")
        g.add_node("k8s:prod/argocd/Deployment/argocd-server",
                   type="k8s_resource", label="Deployment/argocd-server",
                   environment="prod", k8s_kind="Deployment",
                   k8s_namespace="argocd", k8s_name="argocd-server")
        g.add_node("doc:skill/loki-troubleshoot",
                   type="doc", label="loki-troubleshoot",
                   doc_kind="skill", path="docs/loki.md")
        g.add_node("resource:prod/loki/aws_s3_bucket.ingester",
                   type="resource", label="aws_s3_bucket.ingester",
                   environment="prod")
        return tmp, g

    def _extract_elements(self, html: str):
        # The template renders `const NODES = [...]; const EDGES = [...];`
        m = re.search(r"const NODES = (\[.*?\]);\s*const EDGES = (\[.*?\]);",
                      html, re.DOTALL)
        self.assertIsNotNone(m, "expected NODES/EDGES JSON in graph.html")
        nodes = json.loads(m.group(1))
        edges = json.loads(m.group(2))
        return nodes, edges

    def test_write_graph_html_produces_valid_file(self):
        from kuberly_platform import write_graph_html

        tmp, g = self._build_graph()
        try:
            with tempfile.TemporaryDirectory() as out:
                out_path = Path(out)
                write_graph_html(g, out_path)
                html_file = out_path / "graph.html"
                self.assertTrue(html_file.is_file())
                html = html_file.read_text(encoding="utf-8")
                # Cytoscape + fcose CDN refs are present.
                self.assertIn("cytoscape", html)
                self.assertIn("fcose", html)
                # No leftover vis.js references.
                self.assertNotIn("vis-network", html)
                self.assertNotIn("vis.Network", html)
                # The substitution happened — no raw $NODES_JSON literal.
                self.assertNotIn("$NODES_JSON", html)
                # Embedded JSON parses.
                nodes, edges = self._extract_elements(html)
                self.assertGreater(len(nodes), 0)
                self.assertIsInstance(edges, list)
        finally:
            tmp.cleanup()

    def test_graph_html_compound_hierarchy(self):
        from kuberly_platform import write_graph_html

        tmp, g = self._build_graph()
        try:
            with tempfile.TemporaryDirectory() as out:
                out_path = Path(out)
                write_graph_html(g, out_path)
                html = (out_path / "graph.html").read_text(encoding="utf-8")
                nodes, _ = self._extract_elements(html)

                ids = {n["data"]["id"] for n in nodes}
                # Every k8s leaf has a parent compound id.
                k8s_leaves = [n for n in nodes
                              if n["data"].get("source_layer") == "k8s"
                              and not n["data"].get("compound")]
                self.assertGreater(len(k8s_leaves), 0)
                for n in k8s_leaves:
                    parent = n["data"].get("parent")
                    self.assertIsNotNone(parent,
                        f"k8s leaf {n['data']['id']} has no parent")
                    self.assertIn(parent, ids,
                        f"compound parent {parent} not present as a node")

                # Every compound parent that any node references must
                # itself exist as a node (cytoscape invariant).
                referenced_parents = {n["data"].get("parent") for n in nodes
                                      if n["data"].get("parent")}
                for p in referenced_parents:
                    self.assertIn(p, ids, f"missing compound: {p}")
        finally:
            tmp.cleanup()

    def test_graph_html_source_layer_attr(self):
        from kuberly_platform import write_graph_html

        tmp, g = self._build_graph()
        try:
            with tempfile.TemporaryDirectory() as out:
                out_path = Path(out)
                write_graph_html(g, out_path)
                html = (out_path / "graph.html").read_text(encoding="utf-8")
                nodes, _ = self._extract_elements(html)

                allowed = {"static", "state", "k8s", "docs"}
                for n in nodes:
                    layer = n["data"].get("source_layer")
                    self.assertIn(layer, allowed,
                        f"node {n['data']['id']} has bad source_layer={layer}")

                # All four layers are represented (we injected one of each).
                seen = {n["data"].get("source_layer") for n in nodes
                        if not n["data"].get("compound")}
                self.assertEqual(allowed, allowed & seen,
                    f"missing layers — saw {seen}")
        finally:
            tmp.cleanup()

    def test_graph_html_contains_brand_logo(self):
        """v0.24.0: rendered HTML embeds the kuberly LogoMark inline SVG."""
        from kuberly_platform import write_graph_html

        tmp, g = self._build_graph()
        try:
            with tempfile.TemporaryDirectory() as out:
                out_path = Path(out)
                write_graph_html(g, out_path)
                html = (out_path / "graph.html").read_text(encoding="utf-8")
                # First path of the LogoMark SVG (kuberly-web).
                self.assertIn("M11.3647 2.92733", html,
                    "expected kuberly LogoMark SVG path data in graph.html")
                # Brand wordmark + version eyebrow.
                self.assertIn("kuberly", html)
                self.assertIn("Dashboard", html)
                self.assertIn("kuberly-dashboard-json", html)
        finally:
            tmp.cleanup()

    def test_graph_html_has_initial_layout_call(self):
        """v0.25.0 / v0.29.0: a layout must run once cytoscape is constructed.

        v0.29 lazily builds the graph on the Graph tab; `runLayout(initialLayout)`
        runs immediately inside `buildCy()` so nodes are not stacked at
        (0,0) when the canvas first appears. Large graphs use cose; small use fcose.
        """
        from kuberly_platform import write_graph_html

        tmp, g = self._build_graph()
        try:
            with tempfile.TemporaryDirectory() as out:
                out_path = Path(out)
                write_graph_html(g, out_path)
                html = (out_path / "graph.html").read_text(encoding="utf-8")
                self.assertIn("function buildCy", html)
                self.assertIn("initialLayout", html)
                self.assertTrue(
                    "runLayout(initialLayout)" in html,
                    "expected runLayout(initialLayout) after cytoscape init",
                )
        finally:
            tmp.cleanup()

    def test_graph_html_uses_parent_pseudoclass(self):
        """v0.26.0: the cytoscape style array uses the built-in `:parent`
        pseudo-class to target compound containers. Class-binding
        (`node.compound`) is fragile because the python builder does not
        emit that class on every code path — `:parent` matches any node
        with children, no class needed.
        """
        from kuberly_platform import write_graph_html

        tmp, g = self._build_graph()
        try:
            with tempfile.TemporaryDirectory() as out:
                out_path = Path(out)
                write_graph_html(g, out_path)
                html = (out_path / "graph.html").read_text(encoding="utf-8")
                self.assertIn('"node:parent"', html,
                    "rendered HTML missing `node:parent` style selector — "
                    "compound parents will fall through to default fill")
                # Make sure no leftover class-binding selector remains.
                self.assertNotIn('"node.compound"', html,
                    "rendered HTML still references the class-binding "
                    "`node.compound` selector — should be `:parent`")
        finally:
            tmp.cleanup()

    def test_to_json_strips_orphan_edges(self):
        """Regression for v0.27.0: serialized output must be cytoscape-safe.

        HCL `component_type:*` refs, agent doc `tool:*` refs,
        `k8s_namespace:*` refs, and state-overlay refs to redacted
        resources all emit edges to non-materialized targets. In-memory
        `self.edges` keeps them (existing query tests assert on them),
        but `to_json()` — the single chokepoint feeding both
        `write_graph_json` and `write_graph_html` — must filter them.
        Cytoscape aborts on the first orphan and renders nothing.
        """
        from kuberly_platform import KuberlyPlatform

        tmp = _fake_repo()
        try:
            g = KuberlyPlatform(tmp.name)
            g.build()
            # Inject a deliberate orphan and confirm in-memory state
            # retains it but to_json() filters it out.
            g.add_edge("env:prod", "component_type:does-not-exist",
                       relation="reads_config")
            self.assertTrue(any(
                e["target"] == "component_type:does-not-exist"
                for e in g.edges
            ), "in-memory edges should retain the orphan")

            data = g.to_json()
            node_ids = {n["id"] for n in data["nodes"]}
            for e in data["edges"]:
                self.assertIn(e["source"], node_ids,
                    f"serialized orphan edge source: {e}")
                self.assertIn(e["target"], node_ids,
                    f"serialized orphan edge target: {e}")
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main(verbosity=2)
