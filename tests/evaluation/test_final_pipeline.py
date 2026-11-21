"""The final run's full hosted-agent pipeline: verifier and drafter (#60, #61, #66).

Development fixtures only. The appeal case is development set B's
CLM-2026-005097-L1. The planning model, verifier, and drafter are offline fakes,
so nothing here calls a provider or reads held-out data.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from claim_agent.agent.drafting.interface import DraftingResponse
from claim_agent.agent.model_interface import ModelResponse
from claim_agent.agent.scripted_model import ScriptedModelClient, ScriptedModelOutput
from claim_agent.agent.verification.interface import VerifierResponse
from claim_agent.contracts import Disposition
from claim_agent.tools.dispatcher import ENCOUNTER_NOTE_TOOL, ToolDispatcher
from claim_agent.tools.knowledge_store import KnowledgeStore
from claim_agent.agent.hosted.factory import (
    HOSTED_API_KEY_ENVIRONMENT_VARIABLE,
    build_hosted_drafting_client,
    build_hosted_model_client,
    build_hosted_verifier_client,
)
from evaluation.final_experiment import (
    load_plan,
    model_profiles,
    _measure_trace,
    _post_decision_measurements,
    _run_measurements,
    post_decision_summary,
    BASELINE_V1,
    HOSTED_AGENT,
    ExperimentError,
    ExperimentPlan,
    prepare_experiment,
    run_directory,
    run_experiment,
    score_experiment,
)
from evaluation.hosted_execution import PostDecisionClients, execute_hosted_case
from evaluation.pilot_data import REPO_ROOT, load_dataset
from evaluation.scripted_agent import prepare_evaluation_case

# Model prose that must never reach a persisted trace (ADR 0002).
VERIFIER_PROSE = "VERIFIER-PROSE-MARKER the verifier explains its reasoning here."
DRAFT_PROSE = "DRAFT-PROSE-MARKER dear reviewer, the appeal letter body."
INVALID_PROSE = "INVALID-PROSE-MARKER not a draft at all"
FINAL_PLAN = REPO_ROOT / "evaluation" / "final_experiment_plan.json"
SET_B = REPO_ROOT / "datasets" / "development" / "set_b" / "manifest.json"
APPEAL_CASE = "CLM-2026-005097-L1"
CLAIM_ID = "CLM-2026-005097"
DECISION_ID = "DEC-PIPELINE-005097"
PN = "ENC-CLM-2026-005097-PN@v1"
CITATIONS = [
    "POL-BCBSAZ-MRI-BRAIN@v1#P3", "POL-BCBSAZ-MRI-BRAIN@v1#P4", "POL-BCBSAZ-MRI-BRAIN@v1#P5",
    f"{PN}#L1", f"{PN}#L2", f"{PN}#L4",
]


def _appeal_actions() -> list:
    """Policy, both encounter notes, then a supported appeal."""

    return [
        {"action": "call_tool", "tool_name": "retrieve_medical_policy", "tool_arguments": {
            "payer": "BCBS_AZ", "procedure_code": "70553", "service_date": "2026-06-02",
            "query": "headache diplopia neurologic examination possible mass contrast"}},
        {"action": "call_tool", "tool_name": ENCOUNTER_NOTE_TOOL,
         "tool_arguments": {"claim_id": CLAIM_ID, "document_id": "ENC-CLM-2026-005097-CN"}},
        {"action": "call_tool", "tool_name": ENCOUNTER_NOTE_TOOL,
         "tool_arguments": {"claim_id": CLAIM_ID, "document_id": "ENC-CLM-2026-005097-PN"}},
        {"action": "propose_decision", "decision": {
            "decision_id": DECISION_ID, "claim_id": CLAIM_ID, "service_line_id": 1,
            "root_cause": "MEDICAL_NECESSITY", "recommended_disposition": "APPEAL_WITH_DOCUMENTATION",
            "actions": ["Prepare a documented appeal for human review."],
            "criterion_assessments": [
                {"criterion_id": "A", "status": "SUPPORTED", "explanation": "Headache with diplopia.",
                 "citation_ids": [f"{PN}#L1"]},
                {"criterion_id": "B", "status": "SUPPORTED", "explanation": "Cranial nerve finding.",
                 "citation_ids": [f"{PN}#L2"]},
                {"criterion_id": "C", "status": "SUPPORTED", "explanation": "Possible intracranial mass.",
                 "citation_ids": [f"{PN}#L4"]},
            ],
            "evidence_gaps": [], "agent_status": "COMPLETED", "manual_review_reasons": [],
            "rationale": "The retrieved policy criteria are supported by the encounter note.",
            "citation_ids": CITATIONS,
        }},
    ]


def appeal_model() -> ScriptedModelClient:
    return ScriptedModelClient([ScriptedModelOutput(response_text=json.dumps(a)) for a in _appeal_actions()])


def manual_review_model() -> ScriptedModelClient:
    return ScriptedModelClient([ScriptedModelOutput(response_text=json.dumps(
        {"action": "request_manual_review", "reason": "Offline pipeline test."}))])


class CaseAwarePlanner:
    """Plays the appeal script for the appeal case and asks for manual review otherwise.

    The harness builds one planning client per run without telling it the case,
    so the fake reads the claim ID from the first request it receives.
    """

    def __init__(self):
        self._client = None

    def generate_response(self, request) -> ModelResponse:
        if self._client is None:
            self._client = appeal_model() if CLAIM_ID in request.task_context else manual_review_model()
        response = self._client.generate_response(request)
        return ModelResponse(
            request_id=response.request_id,
            response_text=response.response_text,
            input_token_count=200,
            output_token_count=40,
        )


def _passage_text(passage_id: str) -> str:
    result = ToolDispatcher(KnowledgeStore(REPO_ROOT / "knowledge")).dispatch(
        request_id="fixture", tool_name=ENCOUNTER_NOTE_TOOL,
        tool_arguments={"claim_id": CLAIM_ID, "document_id": "ENC-CLM-2026-005097-PN"},
    )
    return next(p.text for p in result.evidence_passages if p.passage_id == passage_id)


def _verifier_json(outcome: str = "AGREE") -> str:
    return json.dumps({
        "decision_id": DECISION_ID, "claim_id": CLAIM_ID, "service_line_id": 1, "outcome": outcome,
        "rationale": VERIFIER_PROSE,
        "supported_criterion_ids": ["A", "B", "C"] if outcome == "AGREE" else ["A", "B"],
        "disputed_criterion_ids": [] if outcome == "AGREE" else ["C"],
        "unresolved_questions": [],
        "citation_ids": [f"{PN}#L1"] if outcome == "AGREE" else [f"{PN}#L4"],
    })


def _draft_json(statement: str, citation: str = f"{PN}#L1") -> str:
    return json.dumps({
        "draft_id": "DRF-PIPELINE-1", "decision_id": DECISION_ID, "claim_id": CLAIM_ID, "service_line_id": 1,
        "disposition": "APPEAL_WITH_DOCUMENTATION", "approval_state": "PENDING_REVIEW",
        "subject": "Medical necessity appeal", "body": DRAFT_PROSE,
        "grounded_claims": [{"statement": statement, "citation_ids": [citation]}],
        "citation_ids": [citation],
    })


class FakeVerifier:
    def __init__(self, behaviour="AGREE"):
        self.behaviour = behaviour
        self.calls = 0

    def verify(self, request):
        self.calls += 1
        if self.behaviour == "RAISE":
            raise ConnectionError("provider outage")
        if self.behaviour == "TIMEOUT":
            raise TimeoutError("verifier timed out")
        return VerifierResponse(request_id=request.request_id, response_text=_verifier_json(self.behaviour),
                                input_token_count=300, output_token_count=30)


class FakeDrafter:
    def __init__(self, grounded=True, invalid=False):
        self.grounded = grounded
        self.invalid = invalid
        self.calls = 0

    def draft(self, request):
        self.calls += 1
        if self.invalid:
            return DraftingResponse(request_id=request.request_id, response_text=INVALID_PROSE,
                                    input_token_count=500, output_token_count=5)
        statement = _passage_text(f"{PN}#L1") if self.grounded else "This claim was pre-approved by phone."
        return DraftingResponse(request_id=request.request_id, response_text=_draft_json(statement),
                                input_token_count=500, output_token_count=80)


class AppealCaseRunner(unittest.TestCase):
    """Runs execute_hosted_case on the set B appeal case; holds no tests itself."""

    @classmethod
    def setUpClass(cls):
        cls.dataset = load_dataset(SET_B)
        cls.as_of = date.fromisoformat(cls.dataset.as_of)
        case = next(c for c in cls.dataset.cases if c.case_id == APPEAL_CASE)
        cls.prepared = prepare_evaluation_case(case, cls.as_of)
        cls.store = KnowledgeStore(REPO_ROOT / "knowledge")
        cls.plan = ExperimentPlan.model_validate(json.loads(FINAL_PLAN.read_text(encoding="utf-8")))

    def run_case(self, verifier=None, drafter=None, model=None, clients="default"):
        verifier = verifier or FakeVerifier()
        drafter = drafter or FakeDrafter()
        if clients == "default":
            clients = PostDecisionClients(lambda: verifier, lambda: drafter)
        execution = execute_hosted_case(
            prepared_case=self.prepared, model_client=model or appeal_model(),
            profile=self.plan.candidate.hosted_profile(), budgets=self.plan.budgets,
            knowledge_store=self.store, run_id="pipeline-test", as_of=self.as_of,
            code_commit="test", plan_version="test", repeat_index=1, post_decision_clients=clients,
        )
        return execution, verifier, drafter


class HostedCaseGateTests(AppealCaseRunner):
    """execute_hosted_case with the verifier and drafter, on the appeal case."""

    def test_verified_appeal_with_grounded_draft_stays_an_appeal(self):
        execution, verifier, drafter = self.run_case()
        post = execution.post_decision
        self.assertEqual(execution.output_package.final_disposition, Disposition.APPEAL_WITH_DOCUMENTATION)
        self.assertIs(execution.output_package, post.decision_package)
        self.assertIsNone(post.escalation_reason)
        self.assertEqual((verifier.calls, drafter.calls), (1, 1))
        draft = post.result.validated_draft
        self.assertEqual(draft.approval_state, "PENDING_REVIEW")
        self.assertEqual(post.evidence_bundle.check_citations(draft.citation_ids), [])
        self.assertEqual(
            [e.event_name for e in post.trace_entries],
            ["MODEL_REQUESTED", "MODEL_RESPONDED", "VERIFICATION_RETURNED",
             "MODEL_REQUESTED", "MODEL_RESPONDED", "DRAFTING_RETURNED"],
        )

    def test_bundle_holds_exactly_this_runs_retrievals(self):
        execution, _, _ = self.run_case()
        bundle = execution.post_decision.evidence_bundle
        self.assertEqual(bundle.run_id, "pipeline-test")
        self.assertEqual(bundle.scope.claim_id, CLAIM_ID)
        self.assertEqual(len(bundle.retrievals), 3)
        self.assertEqual(bundle.check_citations(CITATIONS), [])

    def test_verifier_disagreement_escalates_and_skips_drafting(self):
        execution, _, drafter = self.run_case(verifier=FakeVerifier("DISAGREE"))
        post = execution.post_decision
        self.assertEqual(execution.output_package.final_disposition, Disposition.MANUAL_REVIEW)
        self.assertIn("VERIFIER_DISAGREED", execution.output_package.manual_review_reasons)
        self.assertEqual(post.decision_package.final_disposition, Disposition.APPEAL_WITH_DOCUMENTATION)
        self.assertEqual(drafter.calls, 0)

    def test_verifier_provider_failure_escalates(self):
        execution, _, drafter = self.run_case(verifier=FakeVerifier("RAISE"))
        self.assertEqual(execution.output_package.final_disposition, Disposition.MANUAL_REVIEW)
        self.assertIn("VERIFIER_PROVIDER_FAILURE", execution.output_package.manual_review_reasons)
        self.assertEqual(drafter.calls, 0)

    def test_ungrounded_draft_escalates_the_appeal(self):
        execution, _, _ = self.run_case(drafter=FakeDrafter(grounded=False))
        post = execution.post_decision
        self.assertEqual(execution.output_package.final_disposition, Disposition.MANUAL_REVIEW)
        self.assertEqual(post.result.drafting_execution.validation_result.outcome.value, "REJECTED_UNGROUNDED")
        self.assertIn(post.escalation_reason, execution.output_package.manual_review_reasons)
        self.assertIsNone(post.result.validated_draft)

    def test_no_accepted_decision_calls_neither_model(self):
        def unused():
            raise AssertionError("no client should be built")

        execution, _, _ = self.run_case(model=manual_review_model(), clients=PostDecisionClients(unused, unused))
        self.assertEqual(execution.output_package.final_disposition, Disposition.MANUAL_REVIEW)
        self.assertIsNone(execution.post_decision.result)

    def test_client_setup_error_fails_the_run_instead_of_counting_as_a_verifier_failure(self):
        def missing_key():
            raise RuntimeError("Hosted execution requires the API key")

        with self.assertRaises(RuntimeError):
            self.run_case(clients=PostDecisionClients(missing_key, lambda: FakeDrafter()))

    def test_without_gates_the_decision_stage_is_unchanged(self):
        execution, verifier, drafter = self.run_case(clients=None)
        self.assertIsNone(execution.post_decision)
        self.assertEqual(execution.output_package.final_disposition, Disposition.APPEAL_WITH_DOCUMENTATION)
        self.assertEqual((verifier.calls, drafter.calls), (0, 0))


def _serialized(events) -> str:
    return "\n".join(json.dumps(e.model_dump(mode="json"), sort_keys=True) for e in events)


class RedactionAndUsageTests(AppealCaseRunner):
    """Hriday's #93 blockers: redacted post-decision traces and honest token completeness."""

    PRICES = ExperimentPlan.model_validate({
        **json.loads(FINAL_PLAN.read_text(encoding="utf-8")),
        "token_prices": {"currency": "USD", "input_per_million_tokens": 1.0, "output_per_million_tokens": 2.0},
    }).token_prices

    def test_persisted_post_decision_trace_has_no_model_prose(self):
        execution, _, _ = self.run_case()
        post = execution.post_decision
        raw = "\n".join(json.dumps(dict(e.details), default=str) for e in post.trace_entries)
        self.assertIn("VERIFIER-PROSE-MARKER", raw)  # the in-memory entries do hold it
        text = _serialized(post.canonical_trace_events)
        for marker in ("VERIFIER-PROSE-MARKER", "DRAFT-PROSE-MARKER", "response_text"):
            self.assertNotIn(marker, text)
        roles = {e.payload.get("model_role") for e in post.canonical_trace_events if e.payload}
        self.assertEqual(roles, {"VERIFIER", "DRAFTER"})
        self.assertTrue(all(e.run_id == "pipeline-test" for e in post.canonical_trace_events))

    def test_invalid_drafter_output_is_redacted(self):
        execution, _, _ = self.run_case(drafter=FakeDrafter(invalid=True))
        self.assertEqual(execution.output_package.final_disposition, Disposition.MANUAL_REVIEW)
        text = _serialized(execution.post_decision.canonical_trace_events)
        self.assertNotIn("INVALID-PROSE-MARKER", text)
        self.assertIn("raw_output_sha256", text)

    def assert_incomplete(self, execution):
        summary = post_decision_summary(execution.post_decision, execution.output_package)
        usage = _post_decision_measurements(summary, self.PRICES)
        self.assertEqual(usage["model_calls"], 1)
        self.assertEqual(usage["model_responses"], 0)
        self.assertFalse(usage["token_usage_complete"])
        self.assertIsNone(usage["cost"])

    def test_verifier_timeout_makes_usage_incomplete(self):
        execution, _, _ = self.run_case(verifier=FakeVerifier("TIMEOUT"))
        self.assertIn("VERIFIER_TIMEOUT", execution.output_package.manual_review_reasons)
        self.assert_incomplete(execution)

    def test_verifier_provider_failure_makes_usage_incomplete(self):
        execution, _, _ = self.run_case(verifier=FakeVerifier("RAISE"))
        self.assert_incomplete(execution)

    def test_pipeline_cost_is_unknown_when_any_call_lacks_usage(self):
        execution, _, _ = self.run_case(verifier=FakeVerifier("RAISE"))
        summary = post_decision_summary(execution.post_decision, execution.output_package)
        prediction = type("P", (), {"trace_events": execution.canonical_trace_events})()
        measured = _run_measurements(prediction, summary, 10, self.PRICES)
        self.assertFalse(measured["pipeline"]["token_usage_complete"])
        self.assertIsNone(measured["pipeline"]["cost"])

    def test_failed_planning_call_makes_decision_usage_incomplete(self):
        class FailingPlanner:
            def generate_response(self, request):
                raise ConnectionError("planner outage")

        execution, _, _ = self.run_case(model=FailingPlanner())
        measured = _measure_trace(execution.canonical_trace_events, self.PRICES)
        self.assertGreaterEqual(measured["model_calls"], 1)
        self.assertFalse(measured["token_usage_complete"])
        self.assertIsNone(measured["cost"])


class EffectiveTimeoutTests(unittest.TestCase):
    """#66: only the drafter's request timeout changes; planner and verifier stay at 60 s."""

    def test_frozen_plan_sets_planner_60_verifier_60_drafter_180(self):
        plan = load_plan(FINAL_PLAN)
        profiles = model_profiles(plan)
        self.assertEqual(
            {role: p.request_timeout_seconds for role, p in profiles.items()},
            {"planner": 60.0, "verifier": 60.0, "drafter": 180.0},
        )
        # Nothing else about the drafter's model settings differs.
        for field in ("model_id", "base_url", "max_completion_tokens", "temperature",
                      "reasoning_effort", "response_format_mode", "disable_gateway_addons"):
            self.assertEqual(getattr(profiles["drafter"], field), getattr(profiles["planner"], field), field)
        self.assertEqual(profiles["planner"].model_id, "beam-1-0")

    def test_built_clients_use_those_limits(self):
        profiles = model_profiles(load_plan(FINAL_PLAN))
        env = {HOSTED_API_KEY_ENVIRONMENT_VARIABLE: "offline-test-key"}  # nothing is sent
        built = {
            "planner": build_hosted_model_client(profile=profiles["planner"], environment=env),
            "verifier": build_hosted_verifier_client(profile=profiles["verifier"], environment=env),
            "drafter": build_hosted_drafting_client(profile=profiles["drafter"], environment=env),
        }
        self.assertEqual(
            {role: client._timeout_seconds for role, client in built.items()},
            {"planner": 60.0, "verifier": 60.0, "drafter": 180.0},
        )

    def test_harness_passes_each_role_its_own_profile(self):
        seen = {}

        def record(role, client):
            def factory(*, profile):
                seen.setdefault(role, set()).add(profile.request_timeout_seconds)
                return client()
            return factory

        prepared = prepare_experiment(set_b_plan(drafter_request_timeout_seconds=180.0))
        with tempfile.TemporaryDirectory() as tmp:
            run_experiment(
                prepared, output_root=Path(tmp), code_commit="test", confirm_live=True,
                systems=(HOSTED_AGENT,), case_ids=[APPEAL_CASE],
                client_factory=record("planner", CaseAwarePlanner),
                verifier_factory=record("verifier", FakeVerifier),
                drafting_factory=record("drafter", FakeDrafter),
            )
        self.assertEqual(seen, {"planner": {60.0}, "verifier": {60.0}, "drafter": {180.0}})

    def test_without_the_field_the_drafter_keeps_the_candidate_timeout(self):
        profiles = model_profiles(set_b_plan(drafter_request_timeout_seconds=None))
        self.assertEqual(profiles["drafter"].request_timeout_seconds, 60.0)

    def test_non_positive_drafter_timeout_is_rejected(self):
        with self.assertRaises(ValueError):
            set_b_plan(drafter_request_timeout_seconds=0)


def set_b_plan(**overrides) -> ExperimentPlan:
    values = {
        **json.loads(FINAL_PLAN.read_text(encoding="utf-8")),
        "experiment_id": "pipeline-rehearsal",
        "phase": "rehearsal",
        "dataset_manifest": "datasets/development/set_b/manifest.json",
        "label_files": ["datasets/labels/set_b_development.adjudicated.json"],
        "label_lock": None,
        "agent_repetitions": 2,
        "token_prices": {"currency": "USD", "input_per_million_tokens": 1.0, "output_per_million_tokens": 2.0},
    }
    values.update(overrides)
    return ExperimentPlan.model_validate(values)


class HarnessPipelineTests(unittest.TestCase):
    """The final harness writes, measures, and scores the verifier and drafter."""

    @classmethod
    def setUpClass(cls):
        cls.prepared = prepare_experiment(set_b_plan())
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        cls.summary = run_experiment(
            cls.prepared, output_root=cls.root, code_commit="test", confirm_live=True,
            client_factory=lambda *, profile: CaseAwarePlanner(),
            verifier_factory=lambda *, profile: FakeVerifier(),
            drafting_factory=lambda *, profile: FakeDrafter(),
        )
        cls.experiment = cls.root / "pipeline-rehearsal"

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_every_run_completes(self):
        self.assertEqual(self.summary["failed_runs"], 0)
        self.assertEqual(self.summary["completed_runs"], len(self.prepared.dataset.cases) * 3)

    def test_agent_run_keeps_every_post_decision_artifact(self):
        run = run_directory(self.experiment, HOSTED_AGENT, APPEAL_CASE, 1)
        for name in ("post-decision.json", "decision-package.json", "evidence-bundle.json",
                     "post-decision-trace.jsonl", "appeal-draft.json", "output-package.json"):
            self.assertTrue((run / name).is_file(), name)
        # Model prose is redacted everywhere except the accepted draft itself,
        # which is the PENDING_REVIEW output a human reviews.
        for path in run.rglob("*"):
            if path.is_file() and path.name != "appeal-draft.json":
                text = path.read_text(encoding="utf-8")
                self.assertNotIn("VERIFIER-PROSE-MARKER", text, path.name)
                self.assertNotIn("DRAFT-PROSE-MARKER", text, path.name)
        summary = json.loads((run / "post-decision.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["verifier"]["outcome"], "AGREE")
        self.assertTrue(summary["drafting"]["draft_accepted"])
        self.assertEqual(summary["final_disposition"], "APPEAL_WITH_DOCUMENTATION")
        draft = json.loads((run / "appeal-draft.json").read_text(encoding="utf-8"))
        self.assertEqual(draft["approval_state"], "PENDING_REVIEW")

    def test_measurements_add_the_verifier_and_drafter_to_the_pipeline(self):
        run = run_directory(self.experiment, HOSTED_AGENT, APPEAL_CASE, 1)
        measured = json.loads((run / "measurements.json").read_text(encoding="utf-8"))
        post, pipeline = measured["post_decision"], measured["pipeline"]
        self.assertEqual((post["model_calls"], post["input_tokens"], post["output_tokens"]), (2, 800, 110))
        self.assertEqual(pipeline["model_calls"], measured["model_calls"] + 2)
        self.assertEqual(pipeline["input_tokens"], measured["input_tokens"] + 800)
        self.assertAlmostEqual(pipeline["cost"], measured["cost"] + post["cost"], places=6)

    def test_baseline_runs_are_unchanged(self):
        for case in self.prepared.dataset.cases:
            run = run_directory(self.experiment, BASELINE_V1, case.case_id, 1)
            self.assertFalse((run / "post-decision.json").exists())
            self.assertEqual(json.loads((run / "measurements.json").read_text(encoding="utf-8"))["pipeline"]["model_calls"], 0)

    def test_scoring_reports_verifier_drafter_and_decision_stage(self):
        results = score_experiment(self.prepared, output_root=self.root)
        agent = results["systems"][HOSTED_AGENT]
        repeat = agent["repeats"]["repeat-1"]["post_decision"]
        self.assertEqual(repeat["decisions_accepted_by_validation"], 1)
        self.assertEqual(repeat["verifier_outcomes"], {"AGREE": 1})
        self.assertEqual((repeat["appeals_drafted"], repeat["drafts_accepted"]), (1, 1))
        self.assertEqual((repeat["escalated_by_verifier"], repeat["escalated_by_drafting"]), (0, 0))
        self.assertEqual(repeat["provider_failures"], {"verifier": 0, "drafter": 0})
        self.assertIn("joint_accuracy", repeat["decision_stage"])
        self.assertEqual(agent["operations"]["post_decision"]["model_calls"], 2 * 2)
        self.assertIsNone(results["systems"][BASELINE_V1]["repeats"]["repeat-1"]["post_decision"])

    def test_scoring_stops_if_a_post_decision_record_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_experiment(
                self.prepared, output_root=root, code_commit="test", confirm_live=True,
                client_factory=lambda *, profile: CaseAwarePlanner(),
                verifier_factory=lambda *, profile: FakeVerifier(),
                drafting_factory=lambda *, profile: FakeDrafter(),
            )
            (run_directory(root / "pipeline-rehearsal", HOSTED_AGENT, APPEAL_CASE, 2) / "post-decision.json").unlink()
            with self.assertRaisesRegex(ExperimentError, "post-decision.json"):
                score_experiment(self.prepared, output_root=root)


if __name__ == "__main__":
    unittest.main()
