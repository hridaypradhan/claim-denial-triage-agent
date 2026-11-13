"""Build the committed evidence-bundle fixtures from real retrievals.

The fixtures let verification (#60) and drafting (#61) be developed against a
real same-run bundle before the final corpus exists. Regenerate with:

    PYTHONPATH=src:. python tests/tools/evidence_bundle_fixtures.py
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

from claim_agent.tools.dispatcher import ENCOUNTER_NOTE_TOOL, MEDICAL_POLICY_TOOL, ToolDispatcher
from claim_agent.tools.evidence_bundle import (
    ClaimEvidenceScope,
    EvidenceBundle,
    EvidenceBundleRecorder,
)
from claim_agent.tools.knowledge_store import KnowledgeStore

REPO_ROOT = Path(__file__).resolve().parents[2]
KNOWLEDGE = REPO_ROOT / "knowledge"
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "evidence_bundles"


def _record(run_id, scope, calls):
    store = KnowledgeStore(KNOWLEDGE)
    recorder = EvidenceBundleRecorder(ToolDispatcher(store), run_id, store.snapshot_id, scope)
    for index, (tool, arguments) in enumerate(calls, start=1):
        recorder.dispatch(f"REQ-{index}", tool, arguments)
    return recorder.freeze()


def appeal_bundle() -> EvidenceBundle:
    """CLM-2026-005097: brain MRI appeal with a policy and two notes."""

    scope = ClaimEvidenceScope(
        claim_id="CLM-2026-005097", service_line_id=1, payer="BCBS_AZ",
        procedure_code="70553", service_date=date(2026, 6, 2),
    )
    return _record("FIXTURE-APPEAL-005097", scope, [
        (MEDICAL_POLICY_TOOL, {"payer": "BCBS_AZ", "procedure_code": "70553",
                               "service_date": "2026-06-02", "query": "medical necessity criteria for procedure 70553"}),
        (ENCOUNTER_NOTE_TOOL, {"claim_id": "CLM-2026-005097", "document_id": "ENC-CLM-2026-005097-CN"}),
        (ENCOUNTER_NOTE_TOOL, {"claim_id": "CLM-2026-005097", "document_id": "ENC-CLM-2026-005097-PN"}),
    ])


def no_policy_bundle() -> EvidenceBundle:
    """CLM-2026-620111: UHC has no policy in the corpus, so retrieval returns NO_MATCH."""

    scope = ClaimEvidenceScope(
        claim_id="CLM-2026-620111", service_line_id=1, payer="UHC",
        procedure_code="70553", service_date=date(2026, 6, 30),
    )
    return _record("FIXTURE-NO-POLICY-620111", scope, [
        (MEDICAL_POLICY_TOOL, {"payer": "UHC", "procedure_code": "70553",
                               "service_date": "2026-06-30", "query": "medical necessity criteria for procedure 70553"}),
    ])


FIXTURE_BUILDERS = {
    "appeal_005097.json": appeal_bundle,
    "no_policy_620111.json": no_policy_bundle,
}


def render(bundle: EvidenceBundle) -> str:
    return bundle.model_dump_json(indent=2) + "\n"


if __name__ == "__main__":
    FIXTURES.mkdir(parents=True, exist_ok=True)
    for name, build in FIXTURE_BUILDERS.items():
        (FIXTURES / name).write_text(render(build()), encoding="utf-8")
        sys.stdout.write(f"wrote {FIXTURES / name}\n")
