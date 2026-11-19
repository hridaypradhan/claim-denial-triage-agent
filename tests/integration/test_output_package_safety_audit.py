from __future__ import annotations

import json
import unittest
from datetime import date
from pathlib import Path

from baseline.triage.claim import Claim, Denial
from claim_agent.agent.drafting.interface import DraftingRequest, DraftingResponse
from claim_agent.agent.drafting.post_decision import (
    DETERMINISTIC_VALIDATION_REJECTED,
    DRAFTING_ATTEMPTED,
    VERIFICATION_NOT_AGREED,
    run_post_decision_drafting,
)
from claim_agent.agent.integrated_runtime import run_integrated_case
from claim_agent.agent.router import analyze_and_route_claim
from claim_agent.agent.runtime_budget import RuntimeBudgetLimits
from claim_agent.agent.scripted_model import (
    ScriptedModelClient,
    ScriptedModelOutput,
)
from claim_agent.agent.tool_request_guard import ClaimToolContext
from claim_agent.agent.verification.interface import (
    VerifierRequest,
    VerifierResponse,
)
from claim_agent.contracts import (
    AgentDecision,
    AgentStatus,
    AppealDraft,
    BaselineFinding,
    CriterionAssessment,
    CriterionStatus,
    Disposition,
    DraftingOutcome,
    ESCALATION_DEADLINE_EXPIRED,
    ESCALATION_PROHIBITED_APPEAL,
    ESCALATION_PROHIBITED_BILLING,
    ESCALATION_UNAUTHORIZED_DISPOSITION,
    ESCALATION_UNAUTHORIZED_OVERRIDE,
    RootCause,
    RouteDecision,
    RouteOutcome,
    ToolStatus,
    ValidationOutcome,
    VerificationOutcome,
)
from claim_agent.contracts.evidence_bundle import (
    BundledPassage,
    ClaimEvidenceScope,
    EvidenceBundle,
    RetrievalRecord,
)
from claim_agent.output.package import create_output_package
from claim_agent.safety.validator import validate_agent_decision
from claim_agent.tools.dispatcher import (
    ENCOUNTER_NOTE_TOOL,
    MEDICAL_POLICY_TOOL,
    ToolDispatcher,
)
from claim_agent.tools.knowledge_store import KnowledgeStore
from tests.integration.test_production_runtime_flow import (
    CLAIM_ID,
    ENCOUNTER_PASSAGE_IDS,
    KNOWLEDGE_ROOT,
    POLICY_PASSAGE_IDS,
    make_baseline_finding,
    make_claim_tool_context,
    make_route_decision,
    make_scripted_model,
)

AUDIT_AS_OF = date(2026, 11, 6)
AUDIT_BUDGETS = RuntimeBudgetLimits(
    max_model_calls=3,
    max_tool_calls=2,
    max_repair_attempts=1,
    max_total_steps=5,
    max_runtime_seconds=30.0,
)


class _RecordingTrace:
    """In-memory trace recorder for post-decision audit assertions."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    def record(
        self,
        event_name: str,
        details: dict[str, object] | None = None,
    ) -> None:
        self.events.append((event_name, dict(details or {})))


class _StubVerifierClient:
    """Deterministic verifier client that counts invocations."""

    def __init__(self, handler) -> None:
        self._handler = handler
        self.calls: list[VerifierRequest] = []

    def verify(self, request: VerifierRequest) -> VerifierResponse:
        self.calls.append(request)
        return self._handler(request)


class _StubDraftingClient:
    """Deterministic drafting client that counts invocations."""

    def __init__(self, handler) -> None:
        self._handler = handler
        self.calls: list[DraftingRequest] = []

    def draft(self, request: DraftingRequest) -> DraftingResponse:
        self.calls.append(request)
        return self._handler(request)


def _build_same_run_bundle(
    *,
    run_id: str,
    knowledge_snapshot_id: str,
    retrieved_evidence: tuple,
    policy_formula: str | None,
) -> EvidenceBundle:
    """Construct a frozen same-run EvidenceBundle from integrated outputs."""

    by_id = {p.passage_id: p for p in retrieved_evidence}
    policy_passages = [by_id[pid] for pid in POLICY_PASSAGE_IDS if pid in by_id]
    encounter_passages = [
        by_id[pid] for pid in ENCOUNTER_PASSAGE_IDS if pid in by_id
    ]

    retrievals: list[RetrievalRecord] = []
    bundled: list[BundledPassage] = []
    call_idx = 0

    if policy_passages:
        call_idx += 1
        retrievals.append(
            RetrievalRecord(
                call_index=call_idx,
                request_id=f"{run_id}-req-{call_idx}",
                tool_name=MEDICAL_POLICY_TOOL,
                status=ToolStatus.OK,
                source_ids=("POL-BCBSAZ-MRI-BRAIN@v1",),
                passage_ids=tuple(p.passage_id for p in policy_passages),
            )
        )
        for passage in policy_passages:
            bundled.append(
                BundledPassage(
                    passage=passage,
                    source_type=passage.source_type,
                    claim_id=CLAIM_ID,
                    payer="BCBS_AZ",
                    procedure_code="70553",
                    service_date=date(2026, 6, 2),
                    tool_name=MEDICAL_POLICY_TOOL,
                    request_id=f"{run_id}-req-{call_idx}",
                    call_index=call_idx,
                )
            )

    if encounter_passages:
        call_idx += 1
        retrievals.append(
            RetrievalRecord(
                call_index=call_idx,
                request_id=f"{run_id}-req-{call_idx}",
                tool_name=ENCOUNTER_NOTE_TOOL,
                status=ToolStatus.OK,
                source_ids=("ENC-CLM-2026-005097-PN@v1",),
                passage_ids=tuple(p.passage_id for p in encounter_passages),
            )
        )
        for passage in encounter_passages:
            bundled.append(
                BundledPassage(
                    passage=passage,
                    source_type=passage.source_type,
                    claim_id=CLAIM_ID,
                    payer="BCBS_AZ",
                    procedure_code="70553",
                    service_date=date(2026, 6, 2),
                    tool_name=ENCOUNTER_NOTE_TOOL,
                    request_id=f"{run_id}-req-{call_idx}",
                    call_index=call_idx,
                )
            )

    return EvidenceBundle(
        run_id=run_id,
        knowledge_snapshot_id=knowledge_snapshot_id,
        scope=ClaimEvidenceScope(
            claim_id=CLAIM_ID,
            service_line_id=1,
            payer="BCBS_AZ",
            procedure_code="70553",
            service_date=date(2026, 6, 2),
        ),
        policy_formula=policy_formula,
        passages=tuple(bundled),
        retrievals=tuple(retrievals),
    )


def _make_claim(
    *,
    claim_id: str = "CLM-AUDIT-001",
    group_code: str = "CO",
    carc: str = "97",
    rarc: list[str] | None = None,
    encounter: dict | None = None,
    date_of_service: date = date(2026, 10, 15),
) -> Claim:
    return Claim(
        claim_id=claim_id,
        payer="BCBS_AZ",
        patient_account="PA-AUDIT-01",
        date_of_service=date_of_service,
        remit_date=date(2026, 10, 25),
        billed_amount=150.0,
        paid_amount=0.0,
        service_lines=[{"line": 1, "cpt": "99213", "billed": 150.0}],
        denials=[
            Denial(
                service_line=1,
                group_code=group_code,
                carc=carc,
                rarc=rarc or [],
            )
        ],
        encounter=encounter or {},
    )


class OutputPackageSafetyAuditTests(unittest.TestCase):
    """Comprehensive safety audit across routing, verification, drafting, and packaging."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.knowledge_store = KnowledgeStore(KNOWLEDGE_ROOT)
        cls.snapshot_id = cls.knowledge_store.snapshot_id

    def _run_happy_integrated_case(self, run_id: str = "audit-happy-run"):
        return run_integrated_case(
            run_id=run_id,
            system_instructions=(
                "Use only retrieved evidence. Return one structured runtime action per turn."
            ),
            model_client=make_scripted_model(),
            dispatcher=ToolDispatcher(self.knowledge_store),
            baseline_finding=make_baseline_finding(),
            claim_context=make_claim_tool_context(),
            route_decision=make_route_decision(),
            budget_limits=AUDIT_BUDGETS,
            as_of=AUDIT_AS_OF,
            package_version="audit-v1",
            knowledge_snapshot_id=self.snapshot_id,
        )

    # ------------------------------------------------------------------
    # Task 1: Routing audit after #57 (required-evidence safety)
    # ------------------------------------------------------------------
    def test_missing_decision_critical_evidence_escalates_to_manual_review_package(self) -> None:
        """Missing ncci_edit_lookup or primary_eob must escalate to MANUAL_REVIEW."""
        bundling_claim = _make_claim(
            claim_id="CLM-AUDIT-NCCI",
            group_code="CO",
            carc="97",
            encounter={},
        )
        finding, route = analyze_and_route_claim(bundling_claim, AUDIT_AS_OF)[0]

        self.assertEqual(RouteOutcome.MANUAL_REVIEW, route.outcome)
        self.assertEqual("MISSING_DECISION_CRITICAL_EVIDENCE", route.reason_code)

        pkg = create_output_package(
            run_id="audit-ncci-missing",
            package_version="audit-v1",
            knowledge_snapshot_id=self.snapshot_id,
            finding=finding,
            route_outcome=route.outcome,
        )
        self.assertEqual(Disposition.MANUAL_REVIEW, pkg.final_disposition)
        self.assertIsNone(pkg.proposed_decision)
        self.assertIsNone(pkg.validation_result)

    def test_correction_input_and_nonessential_evidence_preserve_deterministic_route(self) -> None:
        """Correction-supplied inputs and nonessential documents must not false-escalate."""
        cases = (
            (
                "nonessential-insurance-card",
                _make_claim(
                    claim_id="CLM-AUDIT-NONESSENTIAL",
                    group_code="CO",
                    carc="4",
                    rarc=["MA130"],
                    encounter={
                        "corrected_modifier": "25",
                        "updated_insurance_card_needed": True,
                    },
                ),
            ),
            (
                "correction-supplied-modifier",
                _make_claim(
                    claim_id="CLM-AUDIT-CORRECTION",
                    group_code="CO",
                    carc="4",
                    encounter={"corrected_modifier": "25"},
                ),
            ),
        )
        for case_slug, claim in cases:
            with self.subTest(case=case_slug):
                finding, route = analyze_and_route_claim(claim, AUDIT_AS_OF)[0]
                self.assertEqual(RouteOutcome.DETERMINISTIC_ONLY, route.outcome)

                pkg = create_output_package(
                    run_id=f"audit-{case_slug}",
                    package_version="audit-v1",
                    knowledge_snapshot_id=self.snapshot_id,
                    finding=finding,
                    route_outcome=route.outcome,
                )
                self.assertEqual(finding.baseline_disposition, pkg.final_disposition)
                self.assertEqual([], pkg.manual_review_reasons)

    # ------------------------------------------------------------------
    # Task 2 & Task 5: Verifier negative paths (#60)
    # ------------------------------------------------------------------
    def test_verifier_negative_paths_fail_closed_and_block_drafting(self) -> None:
        """DISAGREE, UNCERTAIN, invalid JSON, unretrieved citations, timeout, and errors block drafting."""
        integrated = self._run_happy_integrated_case("audit-verifier-negatives")
        bundle = _build_same_run_bundle(
            run_id="audit-verifier-negatives",
            knowledge_snapshot_id=self.snapshot_id,
            retrieved_evidence=integrated.retrieved_evidence,
            policy_formula=integrated.policy_formula,
        )
        decision = integrated.proposed_decision
        det_validation = integrated.validation_result
        self.assertIsNotNone(decision)
        self.assertIsNotNone(det_validation)

        def _agree_json() -> str:
            return json.dumps(
                {
                    "decision_id": decision.decision_id,
                    "claim_id": decision.claim_id,
                    "service_line_id": decision.service_line_id,
                    "outcome": "AGREE",
                    "rationale": "All criteria supported by same-run passages.",
                    "supported_criterion_ids": ["A", "B", "C"],
                    "disputed_criterion_ids": [],
                    "unresolved_questions": [],
                    "citation_ids": list(POLICY_PASSAGE_IDS + ENCOUNTER_PASSAGE_IDS),
                }
            )

        scenarios: list[tuple[str, object, VerificationOutcome]] = [
            (
                "disagree",
                lambda req: VerifierResponse(
                    request_id=req.request_id,
                    response_text=json.dumps(
                        {
                            "decision_id": decision.decision_id,
                            "claim_id": decision.claim_id,
                            "service_line_id": decision.service_line_id,
                            "outcome": "DISAGREE",
                            "rationale": "Criterion C is not met by the encounter note.",
                            "supported_criterion_ids": ["A", "B"],
                            "disputed_criterion_ids": ["C"],
                            "unresolved_questions": [],
                            "citation_ids": ["ENC-CLM-2026-005097-PN@v1#L4"],
                        }
                    ),
                ),
                VerificationOutcome.DISAGREE,
            ),
            (
                "uncertain",
                lambda req: VerifierResponse(
                    request_id=req.request_id,
                    response_text=json.dumps(
                        {
                            "decision_id": decision.decision_id,
                            "claim_id": decision.claim_id,
                            "service_line_id": decision.service_line_id,
                            "outcome": "UNCERTAIN",
                            "rationale": "Symptom duration is ambiguous.",
                            "supported_criterion_ids": ["A"],
                            "disputed_criterion_ids": [],
                            "unresolved_questions": ["Exact onset date of diplopia?"],
                            "citation_ids": ["ENC-CLM-2026-005097-PN@v1#L1"],
                        }
                    ),
                ),
                VerificationOutcome.UNCERTAIN,
            ),
            (
                "malformed_output",
                lambda req: VerifierResponse(
                    request_id=req.request_id,
                    response_text="{not-valid-json",
                ),
                VerificationOutcome.INVALID_OUTPUT,
            ),
            (
                "unretrieved_citation",
                lambda req: VerifierResponse(
                    request_id=req.request_id,
                    response_text=json.dumps(
                        {
                            "decision_id": decision.decision_id,
                            "claim_id": decision.claim_id,
                            "service_line_id": decision.service_line_id,
                            "outcome": "AGREE",
                            "rationale": "Supported by fabricated passage.",
                            "supported_criterion_ids": ["A", "B", "C"],
                            "disputed_criterion_ids": [],
                            "unresolved_questions": [],
                            "citation_ids": ["POL-FAKE-DOC@v1#P99"],
                        }
                    ),
                ),
                VerificationOutcome.INVALID_OUTPUT,
            ),
            (
                "timeout",
                lambda req: (_ for _ in ()).throw(TimeoutError("verifier timed out")),
                VerificationOutcome.TIMEOUT,
            ),
            (
                "provider_failure",
                lambda req: (_ for _ in ()).throw(RuntimeError("upstream 502")),
                VerificationOutcome.PROVIDER_FAILURE,
            ),
        ]

        for label, verifier_handler, expected_outcome in scenarios:
            with self.subTest(scenario=label):
                trace = _RecordingTrace()
                verifier_client = _StubVerifierClient(verifier_handler)
                drafter_client = _StubDraftingClient(
                    lambda req: DraftingResponse(
                        request_id=req.request_id,
                        response_text=_agree_json(),
                    )
                )

                post_result = run_post_decision_drafting(
                    verifier_client=verifier_client,
                    drafting_client=drafter_client,
                    verification_request_id=f"ver-{label}",
                    drafting_request_id=f"drf-{label}",
                    proposed_decision=decision,
                    evidence_bundle=bundle,
                    deterministic_validation=det_validation,
                    runtime_trace=trace,
                )

                self.assertEqual(VERIFICATION_NOT_AGREED, post_result.stop_reason)
                self.assertIsNotNone(post_result.verification_execution)
                ver_val = post_result.verification_execution.validation_result
                self.assertEqual(expected_outcome, ver_val.outcome)
                self.assertTrue(ver_val.requires_manual_review)
                self.assertTrue(bool(ver_val.reason_code))
                self.assertIsNone(post_result.drafting_execution)
                self.assertIsNone(post_result.validated_draft)
                self.assertEqual(0, len(drafter_client.calls))

                # Escalated OutputPackage must resolve to MANUAL_REVIEW with explicit reason
                escalated_pkg = create_output_package(
                    run_id=f"audit-ver-{label}",
                    package_version="audit-v1",
                    knowledge_snapshot_id=self.snapshot_id,
                    finding=make_baseline_finding(),
                    route_outcome=RouteOutcome.AGENT_REQUIRED,
                    proposed_decision=None,
                    validation_result=None,
                    agent_terminal_reason=ver_val.reason_code,
                )
                self.assertEqual(Disposition.MANUAL_REVIEW, escalated_pkg.final_disposition)
                self.assertIn(ver_val.reason_code, escalated_pkg.manual_review_reasons)

    # ------------------------------------------------------------------
    # Task 3, Task 5 & Task 6: Drafting authorization, groundedness & PENDING_REVIEW (#61)
    # ------------------------------------------------------------------
    def test_rejected_deterministic_validation_blocks_verifier_and_drafter(self) -> None:
        """Rejected deterministic validation must invoke neither verifier nor drafter."""
        integrated = self._run_happy_integrated_case("audit-det-rejected")
        bundle = _build_same_run_bundle(
            run_id="audit-det-rejected",
            knowledge_snapshot_id=self.snapshot_id,
            retrieved_evidence=integrated.retrieved_evidence,
            policy_formula=integrated.policy_formula,
        )
        bad_decision = integrated.proposed_decision.model_copy(
            update={"recommended_disposition": Disposition.BILL_PATIENT}
        )
        rejected_validation = validate_agent_decision(
            decision=bad_decision,
            finding=make_baseline_finding(),
            retrieved_evidence=list(integrated.retrieved_evidence),
            as_of=AUDIT_AS_OF,
            policy_formula=integrated.policy_formula,
        )
        self.assertEqual(ValidationOutcome.REJECTED_MANUAL_REVIEW, rejected_validation.outcome)

        verifier_client = _StubVerifierClient(lambda req: None)
        drafter_client = _StubDraftingClient(lambda req: None)
        post_result = run_post_decision_drafting(
            verifier_client=verifier_client,
            drafting_client=drafter_client,
            verification_request_id="ver-blocked",
            drafting_request_id="drf-blocked",
            proposed_decision=bad_decision,
            evidence_bundle=bundle,
            deterministic_validation=rejected_validation,
            runtime_trace=_RecordingTrace(),
        )

        self.assertEqual(DETERMINISTIC_VALIDATION_REJECTED, post_result.stop_reason)
        self.assertEqual(0, len(verifier_client.calls))
        self.assertEqual(0, len(drafter_client.calls))
        self.assertIsNone(post_result.validated_draft)

    def test_drafting_negative_and_accepted_paths_enforce_groundedness_and_pending_review(self) -> None:
        """Drafting failures close safely; accepted drafts resolve all citations and stay PENDING_REVIEW."""
        integrated = self._run_happy_integrated_case("audit-drafting-end-to-end")
        bundle = _build_same_run_bundle(
            run_id="audit-drafting-end-to-end",
            knowledge_snapshot_id=self.snapshot_id,
            retrieved_evidence=integrated.retrieved_evidence,
            policy_formula=integrated.policy_formula,
        )
        decision = integrated.proposed_decision
        det_validation = integrated.validation_result

        agree_verifier = _StubVerifierClient(
            lambda req: VerifierResponse(
                request_id=req.request_id,
                response_text=json.dumps(
                    {
                        "decision_id": decision.decision_id,
                        "claim_id": decision.claim_id,
                        "service_line_id": decision.service_line_id,
                        "outcome": "AGREE",
                        "rationale": "All criteria supported by same-run passages.",
                        "supported_criterion_ids": ["A", "B", "C"],
                        "disputed_criterion_ids": [],
                        "unresolved_questions": [],
                        "citation_ids": list(POLICY_PASSAGE_IDS + ENCOUNTER_PASSAGE_IDS),
                    }
                ),
            )
        )

        # Extract exact passage quote from the same-run bundle for grounded drafting
        l1_passage = bundle.resolve(["ENC-CLM-2026-005097-PN@v1#L1"])[0].passage

        negative_draft_cases: list[tuple[str, object, DraftingOutcome]] = [
            (
                "ungrounded_statement",
                lambda req: DraftingResponse(
                    request_id=req.request_id,
                    response_text=json.dumps(
                        {
                            "draft_id": "DRF-UNGROUNDED-1",
                            "decision_id": decision.decision_id,
                            "claim_id": decision.claim_id,
                            "service_line_id": decision.service_line_id,
                            "disposition": "APPEAL_WITH_DOCUMENTATION",
                            "approval_state": "PENDING_REVIEW",
                            "subject": "Appeal for MRI Brain",
                            "body": "Placeholder body.",
                            "grounded_claims": [
                                {
                                    "statement": "This claim was pre-approved by phone on May 1.",
                                    "citation_ids": ["ENC-CLM-2026-005097-PN@v1#L1"],
                                }
                            ],
                            "citation_ids": ["ENC-CLM-2026-005097-PN@v1#L1"],
                        }
                    ),
                ),
                DraftingOutcome.REJECTED_UNGROUNDED,
            ),
            (
                "unretrieved_citation",
                lambda req: DraftingResponse(
                    request_id=req.request_id,
                    response_text=json.dumps(
                        {
                            "draft_id": "DRF-BADCIT-1",
                            "decision_id": decision.decision_id,
                            "claim_id": decision.claim_id,
                            "service_line_id": decision.service_line_id,
                            "disposition": "APPEAL_WITH_DOCUMENTATION",
                            "approval_state": "PENDING_REVIEW",
                            "subject": "Appeal for MRI Brain",
                            "body": "Placeholder body.",
                            "grounded_claims": [
                                {
                                    "statement": l1_passage.text,
                                    "citation_ids": ["ENC-OTHER-CLAIM@v1#L1"],
                                }
                            ],
                            "citation_ids": ["ENC-OTHER-CLAIM@v1#L1"],
                        }
                    ),
                ),
                DraftingOutcome.REJECTED_UNGROUNDED,
            ),
            (
                "malformed_output",
                lambda req: DraftingResponse(
                    request_id=req.request_id,
                    response_text="not-json",
                ),
                DraftingOutcome.INVALID_OUTPUT,
            ),
            (
                "timeout",
                lambda req: (_ for _ in ()).throw(TimeoutError("drafter timed out")),
                DraftingOutcome.TIMEOUT,
            ),
            (
                "provider_failure",
                lambda req: (_ for _ in ()).throw(RuntimeError("drafter 503")),
                DraftingOutcome.PROVIDER_FAILURE,
            ),
        ]

        for label, drafter_handler, expected_draft_outcome in negative_draft_cases:
            with self.subTest(drafting_negative=label):
                post_result = run_post_decision_drafting(
                    verifier_client=agree_verifier,
                    drafting_client=_StubDraftingClient(drafter_handler),
                    verification_request_id=f"ver-{label}",
                    drafting_request_id=f"drf-{label}",
                    proposed_decision=decision,
                    evidence_bundle=bundle,
                    deterministic_validation=det_validation,
                    runtime_trace=_RecordingTrace(),
                )
                self.assertEqual(DRAFTING_ATTEMPTED, post_result.stop_reason)
                self.assertIsNotNone(post_result.drafting_execution)
                drf_val = post_result.drafting_execution.validation_result
                self.assertEqual(expected_draft_outcome, drf_val.outcome)
                self.assertTrue(drf_val.requires_manual_review)
                self.assertEqual("PENDING_REVIEW", drf_val.approval_state)
                self.assertIsNone(post_result.validated_draft)

        # Positive grounded drafting path: all citations resolve to same-run bundle and remain PENDING_REVIEW
        accepted_drafter = _StubDraftingClient(
            lambda req: DraftingResponse(
                request_id=req.request_id,
                response_text=json.dumps(
                    {
                        "draft_id": "DRF-ACCEPTED-1",
                        "decision_id": decision.decision_id,
                        "claim_id": decision.claim_id,
                        "service_line_id": decision.service_line_id,
                        "disposition": "APPEAL_WITH_DOCUMENTATION",
                        "approval_state": "PENDING_REVIEW",
                        "subject": "Medical Necessity Appeal - CLM-2026-005097",
                        "body": "Provider prose replaced by canonical renderer.",
                        "grounded_claims": [
                            {
                                "statement": l1_passage.text,
                                "citation_ids": [l1_passage.passage_id],
                            }
                        ],
                        "citation_ids": [l1_passage.passage_id],
                    }
                ),
            )
        )
        accepted_post = run_post_decision_drafting(
            verifier_client=agree_verifier,
            drafting_client=accepted_drafter,
            verification_request_id="ver-accepted",
            drafting_request_id="drf-accepted",
            proposed_decision=decision,
            evidence_bundle=bundle,
            deterministic_validation=det_validation,
            runtime_trace=_RecordingTrace(),
        )
        self.assertIsNotNone(accepted_post.validated_draft)
        self.assertIsInstance(accepted_post.validated_draft, AppealDraft)
        self.assertEqual("PENDING_REVIEW", accepted_post.validated_draft.approval_state)
        self.assertEqual("PENDING_REVIEW", integrated.output_package.validation_result.approval_state)

        # Verify every citation in both the OutputPackage decision and accepted draft resolves to same-run bundle
        self.assertEqual([], bundle.check_citations(integrated.output_package.validation_result.validated_decision.citation_ids))
        self.assertEqual([], bundle.check_citations(accepted_post.validated_draft.citation_ids))

    # ------------------------------------------------------------------
    # Task 4: Citation resolution, deadlines, patient billing, disposition allowlists
    # ------------------------------------------------------------------
    def test_hard_safety_invariants_escalate_to_manual_review_package(self) -> None:
        """Unresolved citations, expired deadlines, CO patient billing, contractual appeals, and disallowed dispositions fail closed."""
        integrated = self._run_happy_integrated_case("audit-invariants-base")
        evidence = list(integrated.retrieved_evidence)
        formula = integrated.policy_formula
        base_decision = integrated.proposed_decision

        # 1. Expired deadline -> ESCALATION_DEADLINE_EXPIRED
        expired_finding = make_baseline_finding().model_copy(
            update={"deadline_status": "EXPIRED", "filing_deadline": date(2026, 9, 1)}
        )
        expired_res = validate_agent_decision(
            decision=base_decision,
            finding=expired_finding,
            retrieved_evidence=evidence,
            as_of=AUDIT_AS_OF,
            policy_formula=formula,
        )
        self.assertEqual(ValidationOutcome.REJECTED_MANUAL_REVIEW, expired_res.outcome)
        self.assertIn(ESCALATION_DEADLINE_EXPIRED, expired_res.manual_review_reasons)

        # 2. Patient-billing restriction on CO denial -> ESCALATION_PROHIBITED_BILLING
        bill_patient_decision = base_decision.model_copy(
            update={"recommended_disposition": Disposition.BILL_PATIENT}
        )
        billing_res = validate_agent_decision(
            decision=bill_patient_decision,
            finding=make_baseline_finding(),
            retrieved_evidence=evidence,
            as_of=AUDIT_AS_OF,
            policy_formula=formula,
        )
        self.assertEqual(ValidationOutcome.REJECTED_MANUAL_REVIEW, billing_res.outcome)
        self.assertIn(ESCALATION_PROHIBITED_BILLING, billing_res.manual_review_reasons)

        # 3. Contractual appeal prohibition -> ESCALATION_PROHIBITED_APPEAL
        contractual_finding = make_baseline_finding().model_copy(
            update={
                "root_cause": RootCause.CONTRACTUAL,
                "baseline_disposition": Disposition.NO_ACTION_CONTRACTUAL,
            }
        )
        contractual_decision = base_decision.model_copy(
            update={"root_cause": RootCause.CONTRACTUAL}
        )
        contractual_res = validate_agent_decision(
            decision=contractual_decision,
            finding=contractual_finding,
            retrieved_evidence=evidence,
            as_of=AUDIT_AS_OF,
            policy_formula=formula,
        )
        self.assertEqual(ValidationOutcome.REJECTED_MANUAL_REVIEW, contractual_res.outcome)
        self.assertIn(ESCALATION_PROHIBITED_APPEAL, contractual_res.manual_review_reasons)

        # 4. Baseline disposition allowlist / override prohibition -> ESCALATION_UNAUTHORIZED_OVERRIDE & ESCALATION_UNAUTHORIZED_DISPOSITION
        mr_finding = make_baseline_finding().model_copy(
            update={"baseline_disposition": Disposition.MANUAL_REVIEW}
        )
        override_res = validate_agent_decision(
            decision=base_decision,
            finding=mr_finding,
            retrieved_evidence=evidence,
            as_of=AUDIT_AS_OF,
            policy_formula=formula,
        )
        self.assertEqual(ValidationOutcome.REJECTED_MANUAL_REVIEW, override_res.outcome)
        self.assertIn(ESCALATION_UNAUTHORIZED_OVERRIDE, override_res.manual_review_reasons)

        writeoff_finding = make_baseline_finding().model_copy(
            update={"baseline_disposition": Disposition.WRITE_OFF}
        )
        writeoff_res = validate_agent_decision(
            decision=base_decision,
            finding=writeoff_finding,
            retrieved_evidence=evidence,
            as_of=AUDIT_AS_OF,
            policy_formula=formula,
        )
        self.assertEqual(ValidationOutcome.REJECTED_MANUAL_REVIEW, writeoff_res.outcome)
        self.assertIn(ESCALATION_UNAUTHORIZED_DISPOSITION, writeoff_res.manual_review_reasons)

        # 5. Unresolved citation in runtime loop exhausts repair budget and produces MANUAL_REVIEW package
        bad_cit_decision = base_decision.model_dump(mode="json")
        bad_cit_decision["citation_ids"] = ["POL-UNRETRIEVED@v1#P1"]
        repair_script = ScriptedModelClient(
            [
                ScriptedModelOutput(
                    response_text=json.dumps(
                        {
                            "action": "call_tool",
                            "tool_name": MEDICAL_POLICY_TOOL,
                            "tool_arguments": {
                                "payer": "BCBS_AZ",
                                "procedure_code": "70553",
                                "service_date": "2026-06-02",
                                "query": "headache diplopia neurologic examination",
                            },
                        }
                    )
                ),
                ScriptedModelOutput(
                    response_text=json.dumps(
                        {
                            "action": "call_tool",
                            "tool_name": ENCOUNTER_NOTE_TOOL,
                            "tool_arguments": {
                                "claim_id": CLAIM_ID,
                                "document_id": "ENC-CLM-2026-005097-PN",
                            },
                        }
                    )
                ),
                ScriptedModelOutput(
                    response_text=json.dumps(
                        {"action": "propose_decision", "decision": bad_cit_decision}
                    )
                ),
                ScriptedModelOutput(
                    response_text=json.dumps(
                        {"action": "propose_decision", "decision": bad_cit_decision}
                    )
                ),
            ]
        )
        unresolved_run = run_integrated_case(
            run_id="audit-unresolved-citation",
            system_instructions="Use only retrieved evidence.",
            model_client=repair_script,
            dispatcher=ToolDispatcher(self.knowledge_store),
            baseline_finding=make_baseline_finding(),
            claim_context=make_claim_tool_context(),
            route_decision=make_route_decision(),
            budget_limits=RuntimeBudgetLimits(
                max_model_calls=4,
                max_tool_calls=2,
                max_repair_attempts=1,
                max_total_steps=6,
                max_runtime_seconds=30.0,
            ),
            as_of=AUDIT_AS_OF,
            package_version="audit-v1",
            knowledge_snapshot_id=self.snapshot_id,
        )
        self.assertEqual(
            Disposition.MANUAL_REVIEW,
            unresolved_run.output_package.final_disposition,
        )
        self.assertEqual(
            "PENDING_REVIEW",
            unresolved_run.output_package.validation_result.approval_state,
        )


if __name__ == "__main__":
    unittest.main()