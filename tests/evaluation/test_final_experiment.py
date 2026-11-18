"""Offline tests for the repeated-run experiment harness (#53).

Every test uses development data and a fake hosted transport: no network,
no credentials, and never a held-out case run.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import unittest
from datetime import date
from pathlib import Path

from claim_agent.agent.hosted.client import (
    HostedModelClient,
    HostedModelTransportRequest,
    HostedModelTransportResponse,
)
from evaluation.final_experiment import (
    BASELINE_V1,
    HOSTED_AGENT,
    ExperimentError,
    ExperimentPlan,
    load_plan,
    prepare_experiment,
    run_directory,
    run_experiment,
    run_id_for,
    score_experiment,
    trace_safety_report,
)
from evaluation.pilot_data import REPO_ROOT
from evaluation.trace_metrics import ClaimService

TRACE_MEASURED = ("CSE_FABRICATED_CITATION", "CSE_WRONG_POLICY", "CSE_DEADLINE_LOST_TO_FAILURE")
from evaluation.scripted_agent import prepare_evaluation_case

FINAL_PLAN = REPO_ROOT / "evaluation" / "final_experiment_plan.json"
SET_A_LABELS = REPO_ROOT / "datasets" / "labels" / "set_a_development.adjudicated.json"
# Provider errors can echo request content or keys; none of this may be stored.
SECRET_ERROR_TEXT = "outage for https://gateway.example/v1?api_key=sk-SECRET-123 patient PA-SYN-0001"


class ManualReviewTransport:
    """A valid bounded action with reported token usage, without network."""

    calls = 0

    def generate(self, request: HostedModelTransportRequest) -> HostedModelTransportResponse:
        ManualReviewTransport.calls += 1
        return HostedModelTransportResponse(
            response_text=json.dumps({"action": "request_manual_review", "reason": "Offline harness test."}),
            provider_request_id="offline-harness-request",
            input_token_count=120,
            output_token_count=20,
        )


def offline_client_factory(*, profile):
    return HostedModelClient(
        transport=ManualReviewTransport(),
        model_name=profile.model_id,
        timeout_seconds=profile.request_timeout_seconds,
    )


class FlakyFactory:
    """Fails the first time it is used, then behaves like the offline factory."""

    def __init__(self):
        self.failed = False

    def __call__(self, *, profile):
        if not self.failed:
            self.failed = True
            raise ConnectionError(SECRET_ERROR_TEXT)
        return offline_client_factory(profile=profile)


def _drop_last_event(path: Path) -> None:
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    path.write_text("\n".join(lines[:1] + lines[2:]) + "\n", encoding="utf-8")  # a gap in the sequence


def rehearsal_plan(**overrides) -> ExperimentPlan:
    """Development set A rehearsal using the final plan's model and budgets."""

    final = json.loads(FINAL_PLAN.read_text(encoding="utf-8"))
    values = {
        **final,
        "experiment_id": "rehearsal-test",
        "phase": "rehearsal",
        "dataset_manifest": "datasets/development/set_a/manifest.json",
        "label_files": ["datasets/labels/set_a_development.adjudicated.json"],
        "agent_repetitions": 2,
        "token_prices": {"currency": "USD", "input_per_million_tokens": 1.0, "output_per_million_tokens": 2.0},
    }
    values.update(overrides)
    return ExperimentPlan.model_validate(values)


class PlanTests(unittest.TestCase):
    def test_final_plan_is_valid_and_runs_three_agent_repeats(self):
        plan = load_plan(FINAL_PLAN)
        self.assertEqual(plan.phase, "final")
        self.assertEqual(plan.repeats(HOSTED_AGENT), (1, 2, 3))
        self.assertEqual(plan.repeats(BASELINE_V1), (1,))
        self.assertEqual(plan.knowledge_snapshot_id, "knowledge-v2")

    def test_run_ids_are_unique_across_every_dimension(self):
        ids = {
            run_id_for(experiment, system, case, n)
            for experiment in ("e1", "e2")
            for system in (BASELINE_V1, HOSTED_AGENT)
            for case in ("CLM-1-L1", "CLM-1-L2")
            for n in (1, 2, 3)
        }
        self.assertEqual(len(ids), 2 * 2 * 2 * 3)

    def test_unsafe_experiment_id_is_rejected(self):
        with self.assertRaises(ValueError):
            rehearsal_plan(experiment_id="../escape")


class PreflightTests(unittest.TestCase):
    def test_held_out_rehearsal_is_refused(self):
        plan = rehearsal_plan(dataset_manifest="datasets/held_out/manifest.json")
        with self.assertRaisesRegex(ExperimentError, "never for rehearsal"):
            prepare_experiment(plan)

    def test_held_out_run_needs_frozen_labels(self):
        with tempfile.TemporaryDirectory() as tmp:
            draft = Path(tmp) / "draft.json"
            draft.write_text(json.dumps({"status": "PRIMARY_UNREVIEWED", "cases": []}), encoding="utf-8")
            plan = rehearsal_plan(
                phase="final",
                dataset_manifest="datasets/held_out/manifest.json",
                label_files=[str(draft)],
            )
            with self.assertRaisesRegex(ExperimentError, "must be frozen"):
                prepare_experiment(plan)

    def test_snapshot_must_match_the_live_corpus(self):
        with self.assertRaisesRegex(ExperimentError, "knowledge snapshot is"):
            prepare_experiment(rehearsal_plan(knowledge_snapshot_id="knowledge-v9"))

    def test_hosted_agent_needs_live_confirmation(self):
        prepared = prepare_experiment(rehearsal_plan())
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ExperimentError, "live confirmation"):
                run_experiment(prepared, output_root=Path(tmp), code_commit="test", confirm_live=False,
                               client_factory=offline_client_factory)


class RunAndScoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.prepared = prepare_experiment(rehearsal_plan())
        cls.cases = [c.case_id for c in cls.prepared.dataset.cases]

    def run_all(self, root, factory=offline_client_factory, commit="test-commit"):
        return run_experiment(self.prepared, output_root=root, code_commit=commit, confirm_live=True,
                              client_factory=factory)

    def test_full_run_is_resumable_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = self.run_all(root)
            planned = len(self.cases) * (1 + 2)
            self.assertEqual(first["completed_runs"], planned)
            self.assertEqual(first["failed_runs"], 0)

            some_run = run_directory(root / "rehearsal-test", HOSTED_AGENT, self.cases[0], 2)
            before = (some_run / "complete.json").read_text(encoding="utf-8")
            second = self.run_all(root)
            self.assertEqual(second["completed_runs"], 0)
            self.assertEqual(second["skipped_completed_runs"], planned)
            self.assertEqual((some_run / "complete.json").read_text(encoding="utf-8"), before)

            record = json.loads((root / "rehearsal-test" / "experiment.json").read_text(encoding="utf-8"))
            self.assertEqual(record["code_commit"], "test-commit")
            self.assertEqual(record["knowledge"]["snapshot_id"], "knowledge-v2")
            self.assertEqual(len(record["labels"][0]["sha256"]), 64)

    def test_changed_inputs_need_a_new_experiment_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_experiment(self.prepared, output_root=root, code_commit="a", confirm_live=True,
                           systems=(BASELINE_V1,), case_ids=self.cases[:1], client_factory=offline_client_factory)
            with self.assertRaisesRegex(ExperimentError, "different code_commit"):
                run_experiment(self.prepared, output_root=root, code_commit="b", confirm_live=True,
                               systems=(BASELINE_V1,), case_ids=self.cases[:1], client_factory=offline_client_factory)

    def test_failure_is_recorded_and_the_rest_still_runs(self):
        as_of = date.fromisoformat(self.prepared.dataset.as_of)
        agent_case = next(
            c for c in self.prepared.dataset.cases if prepare_evaluation_case(c, as_of).requires_agent
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            factory = FlakyFactory()
            first = run_experiment(self.prepared, output_root=root, code_commit="c", confirm_live=True,
                                   systems=(HOSTED_AGENT,), case_ids=[agent_case.case_id], client_factory=factory)
            self.assertEqual(first["failed_runs"], 1)
            self.assertEqual(first["completed_runs"], 1)  # the second repeat still ran
            failure_text = Path(first["failure_records"][0]).read_text(encoding="utf-8")
            failure = json.loads(failure_text)
            self.assertEqual(failure["error_type"], "ConnectionError")
            # Typed diagnostics only: the raw message is hashed, never stored.
            for fragment in ("sk-SECRET-123", "api_key", "PA-SYN-0001", "gateway.example"):
                self.assertNotIn(fragment, failure_text)
            self.assertEqual(
                failure["message_sha256"], hashlib.sha256(SECRET_ERROR_TEXT.encode("utf-8")).hexdigest()
            )
            self.assertEqual(failure["message_length"], len(SECRET_ERROR_TEXT))
            self.assertTrue(any("tests/evaluation/test_final_experiment.py" in f for f in failure["stack"]))
            self.assertFalse(run_directory(root / "rehearsal-test", HOSTED_AGENT, agent_case.case_id, 1).exists())

            retry = run_experiment(self.prepared, output_root=root, code_commit="c", confirm_live=True,
                                   systems=(HOSTED_AGENT,), case_ids=[agent_case.case_id], client_factory=factory)
            self.assertEqual((retry["completed_runs"], retry["skipped_completed_runs"]), (1, 1))
            self.assertTrue(Path(first["failure_records"][0]).exists())  # the failure record is kept

    def test_scoring_refuses_until_complete_then_reports_everything(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_experiment(self.prepared, output_root=root, code_commit="d", confirm_live=True,
                           systems=(BASELINE_V1,), client_factory=offline_client_factory)
            with self.assertRaisesRegex(ExperimentError, "not complete"):
                score_experiment(self.prepared, output_root=root)

            self.run_all(root, commit="d")
            results = score_experiment(self.prepared, output_root=root)
            agent = results["systems"][HOSTED_AGENT]
            self.assertEqual(set(agent["repeats"]), {"repeat-1", "repeat-2"})
            self.assertEqual(agent["stability"]["cases"], len(self.cases))
            self.assertEqual(agent["stability"]["value"], 1.0)  # the fake model always answers the same
            self.assertGreater(agent["operations"]["model_calls"], 0)
            self.assertEqual(agent["operations"]["input_tokens"] % 120, 0)
            self.assertIsNotNone(agent["operations"]["cost_total"])
            baseline = results["systems"][BASELINE_V1]
            self.assertEqual(set(baseline["repeats"]), {"repeat-1"})
            self.assertEqual(baseline["repeats"]["repeat-1"]["joint_accuracy"]["total"], len(self.cases))
            self.assertTrue((root / "rehearsal-test" / "results.json").exists())

            # Trace-measurable safety errors are measured for the agent, from its traces.
            agent_repeat = agent["repeats"]["repeat-1"]
            measures = agent_repeat["trace_measures"]
            self.assertGreater(measures["agent_runs"], 0)
            for code in TRACE_MEASURED:
                self.assertNotIn(code, agent_repeat["safety_unmeasured"])
                self.assertEqual(agent_repeat["critical_safety_errors"][code], 0)
            self.assertIn("CSE_INSTRUCTION_FOLLOWED", agent_repeat["safety_unmeasured"])
            self.assertIn("citation_precision", measures)
            # The baseline has no traces, so those errors stay unmeasured for it.
            self.assertIsNone(baseline["repeats"]["repeat-1"]["trace_measures"])
            for code in TRACE_MEASURED:
                self.assertIn(code, baseline["repeats"]["repeat-1"]["safety_unmeasured"])

    def test_scoring_refuses_inputs_changed_at_the_same_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            labels = Path(tmp) / "labels.json"
            labels.write_text(SET_A_LABELS.read_text(encoding="utf-8"), encoding="utf-8")
            prepared = prepare_experiment(rehearsal_plan(label_files=[str(labels)]))
            root = Path(tmp) / "out"
            run_experiment(prepared, output_root=root, code_commit="f", confirm_live=True,
                           client_factory=offline_client_factory)
            score_experiment(prepared, output_root=root)  # unchanged inputs score

            labels.write_text(labels.read_text(encoding="utf-8") + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ExperimentError, "inputs changed since the experiment ran \\(labels\\)"):
                score_experiment(prepare_experiment(rehearsal_plan(label_files=[str(labels)])), output_root=root)

    def agent_case_id(self):
        as_of = date.fromisoformat(self.prepared.dataset.as_of)
        return next(c.case_id for c in self.prepared.dataset.cases if prepare_evaluation_case(c, as_of).requires_agent)

    def test_missing_or_damaged_agent_trace_stops_scoring(self):
        """Absent trace evidence must never be reported as zero trace-measured errors."""

        case_id = self.agent_case_id()
        damages = {
            "removed": lambda trace_dir: shutil.rmtree(trace_dir),
            "malformed": lambda trace_dir: next(trace_dir.glob("*.jsonl")).write_text("{not json\n", encoding="utf-8"),
            "emptied": lambda trace_dir: next(trace_dir.glob("*.jsonl")).write_text("", encoding="utf-8"),
            "truncated": lambda trace_dir: _drop_last_event(next(trace_dir.glob("*.jsonl"))),
        }
        for name, damage in damages.items():
            with self.subTest(damage=name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                self.run_all(root, commit="g")
                score_experiment(self.prepared, output_root=root)  # intact traces score

                trace_dir = run_directory(root / "rehearsal-test", HOSTED_AGENT, case_id, 1) / "trace"
                self.assertTrue(any(trace_dir.glob("*.jsonl")))
                damage(trace_dir)
                with self.assertRaisesRegex(ExperimentError, "trace"):
                    score_experiment(self.prepared, output_root=root)

    def test_a_run_cannot_opt_out_of_the_trace_check(self):
        case_id = self.agent_case_id()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.run_all(root, commit="h")
            run = run_directory(root / "rehearsal-test", HOSTED_AGENT, case_id, 2)
            prediction = json.loads((run / "prediction.json").read_text(encoding="utf-8"))
            prediction["route_outcome"] = "DETERMINISTIC_ONLY"
            (run / "prediction.json").write_text(json.dumps(prediction), encoding="utf-8")
            shutil.rmtree(run / "trace")
            with self.assertRaisesRegex(ExperimentError, "routing doesn't reproduce"):
                score_experiment(self.prepared, output_root=root)

    def test_partial_run_is_not_treated_as_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            case_id = self.cases[0]
            stale = run_directory(root / "rehearsal-test", BASELINE_V1, case_id, 1)
            stale = stale.with_name(stale.name + ".partial")
            stale.mkdir(parents=True)
            (stale / "prediction.json").write_text("{}", encoding="utf-8")
            summary = run_experiment(self.prepared, output_root=root, code_commit="e", confirm_live=True,
                                     systems=(BASELINE_V1,), case_ids=[case_id], client_factory=offline_client_factory)
            self.assertEqual(summary["completed_runs"], 1)
            parent = stale.parent
            self.assertTrue(any(p.name.startswith("repeat-1.abandoned-") for p in parent.iterdir()))


class TraceSafetyReportTests(unittest.TestCase):
    """The trace path catches what the answer-level scorer can't see."""

    def event(self, case_id, n, kind, payload=None, terminal_reason=None):
        return {"case_id": case_id, "sequence_number": n, "event_type": kind,
                "payload": payload or {}, "terminal_reason": terminal_reason}

    def test_fabricated_citation_wrong_policy_and_silent_stop_are_counted(self):
        service = ClaimService(payer="UHC", procedure_code="72148", service_date="2026-07-08")
        good_policy = {"tool_name": "retrieve_medical_policy",
                       "tool_args": {"payer": "UHC", "procedure_code": "72148", "service_date": "2026-07-08"}}
        wrong_policy = {"tool_name": "retrieve_medical_policy",
                        "tool_args": {"payer": "AETNA", "procedure_code": "72148", "service_date": "2026-07-08"}}
        traces = {
            # Retrieves the right policy, then cites one retrieved and one never-retrieved passage.
            "case-1": [
                self.event("case-1", 1, "RUN_STARTED"),
                self.event("case-1", 2, "TOOL_REQUESTED", good_policy),
                self.event("case-1", 3, "TOOL_RETURNED", {"status": "OK", "passage_ids": ["P#1"]}),
                self.event("case-1", 4, "DECISION_PROPOSED", {"citation_ids": ["P#1", "P#INVENTED"]}),
                self.event("case-1", 5, "RUN_COMPLETED", {"counters": {}}),
            ],
            # Asks for another payer's policy, then stops without a terminal event.
            "case-2": [
                self.event("case-2", 1, "RUN_STARTED"),
                self.event("case-2", 2, "TOOL_REQUESTED", wrong_policy),
            ],
        }
        report = trace_safety_report(traces, {"case-1": service, "case-2": service})
        self.assertEqual(report["trace_safety_counts"], {
            "CSE_FABRICATED_CITATION": 1,
            "CSE_WRONG_POLICY": 1,
            "CSE_DEADLINE_LOST_TO_FAILURE": 1,
        })
        self.assertEqual((report["citation_precision"]["hits"], report["citation_precision"]["total"]), (1, 2))
        self.assertEqual(report["unretrieved_citations"], [["case-1", "P#INVENTED"]])
        self.assertEqual(report["policy_requests_matching_claim"]["hits"], 1)
        self.assertEqual(report["unterminated_runs"], 1)


if __name__ == "__main__":
    unittest.main()
