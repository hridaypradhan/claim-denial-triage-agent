"""Repeated-run evaluation harness for the final experiment (#53).

One frozen experiment plan names the dataset, the label files, the knowledge
snapshot, the hosted model, and the budgets. The harness runs the baseline
once and the hosted agent ``agent_repetitions`` times on every case, and keeps
every run as its own artifact directory:

    <output_root>/<experiment_id>/
        experiment.json                     inputs, hashes, and code commit
        runs/<system>/<case_id>/repeat-<n>/
            prediction.json                 scored fields
            output-package.json             agent system only
            trace/                          canonical trace, agent runs only
            measurements.json               latency, model calls, tokens, cost
            complete.json                   written last; marks the run done
        failures/<system>/<case_id>/repeat-<n>/attempt-<k>.json

A run is written to a temporary directory and renamed into place only when
complete, so a crash never leaves a half-written run that looks finished.
Completed runs are never rerun or overwritten. A failed run is recorded under
``failures/`` and the experiment continues. Rerunning the same experiment
retries only runs that have no ``complete.json``, and keeps every failure
record. Changing any input requires a new experiment ID.

Failure records keep typed diagnostics only (exception class, a hash and the
length of the message, and code locations), never raw exception text.

``score_experiment`` re-hashes the dataset, label, and knowledge files and
refuses if any differs from ``experiment.json``, or if any planned run is
incomplete. It then scores each system and repeat against the labels, adds the
trace-measured safety errors and citation precision for the agent, and reports
stability and operational measurements. Normal tests use fake hosted responses and need no
network or credentials.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import time
import traceback
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Literal, Mapping, Optional, Sequence, Tuple

from pydantic import BaseModel, ConfigDict, field_validator

from claim_agent.agent.canonical_trace import TraceEvent, TraceEventType
from claim_agent.agent.hosted.factory import HostedModelProfile, build_hosted_model_client
from claim_agent.agent.model_interface import ModelClient
from claim_agent.agent.trace_writer import JsonLinesTraceWriter
from claim_agent.tools.knowledge_store import KnowledgeStore
from claim_agent.tools.snapshot_freeze import check_lock
from evaluation.hosted_comparison import HostedCandidate, HostedComparisonBudgets
from evaluation.hosted_execution import execute_hosted_case
from evaluation.label_lock import check_label_lock
from baseline.triage.claim import load_claim
from evaluation.metrics import ALL_SAFETY_ERRORS, Rate, score_system
from evaluation.trace_metrics import ClaimService, measure_traces, trace_safety_counts
from evaluation.pilot_data import REPO_ROOT, Case, Dataset, Label, load_dataset, load_labels
from evaluation.run_pilot import case_facts
from evaluation.scripted_agent import build_non_agent_output_package, prepare_evaluation_case
from evaluation.systems import (
    BASELINE_V1,
    PACKAGE_VERSION,
    Prediction,
    predict_baseline_v1,
    prediction_from_output_package,
)

HOSTED_AGENT = "hosted-agent"
SYSTEMS = (BASELINE_V1, HOSTED_AGENT)
KNOWLEDGE_ROOT = REPO_ROOT / "knowledge"
DEFAULT_OUTPUT_ROOT = REPO_ROOT / ".local-output" / "final-experiment"
FROZEN_LABEL_STATUSES = {"ADJUDICATED", "FROZEN"}


class ExperimentError(RuntimeError):
    """An unsafe, inconsistent, or incomplete experiment request."""


class TokenPrices(BaseModel):
    """Optional prices for cost estimates. Without them cost is reported as unavailable."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    currency: str
    input_per_million_tokens: float
    output_per_million_tokens: float


class ExperimentPlan(BaseModel):
    """Frozen inputs for one experiment. Any change needs a new experiment_id."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    experiment_id: str
    phase: Literal["rehearsal", "final"]
    dataset_manifest: str
    label_files: Tuple[str, ...]
    # Required for held-out cases: the freeze lock every label file must match.
    label_lock: Optional[str] = None
    knowledge_snapshot_id: str
    prompt_version: str
    agent_repetitions: int
    budgets: HostedComparisonBudgets
    candidate: HostedCandidate
    token_prices: Optional[TokenPrices] = None

    @field_validator("experiment_id")
    @classmethod
    def require_safe_identifier(cls, value: str) -> str:
        if not value or not all(ch.isalnum() or ch in "-_." for ch in value):
            raise ValueError("experiment_id must use only letters, digits, '-', '_', or '.'")
        return value

    @field_validator("agent_repetitions")
    @classmethod
    def require_repetitions(cls, value: int) -> int:
        if value < 1:
            raise ValueError("agent_repetitions must be at least 1")
        return value

    def repeats(self, system: str) -> Tuple[int, ...]:
        """The baseline is deterministic and runs once; the agent runs N times."""

        return (1,) if system == BASELINE_V1 else tuple(range(1, self.agent_repetitions + 1))


def load_plan(path: Path) -> ExperimentPlan:
    return ExperimentPlan.model_validate_json(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _plan_hash(plan: ExperimentPlan) -> str:
    canonical = json.dumps(plan.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------
# Preflight: every input is checked before any run starts.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class PreparedExperiment:
    plan: ExperimentPlan
    dataset: Dataset
    labels: Dict[str, Label]
    knowledge_root: Path
    manifest_path: Path
    label_paths: Tuple[Path, ...]
    label_lock_path: Optional[Path] = None


def load_experiment_labels(dataset: Dataset, manifest_path: Path, label_paths: Sequence[Path]) -> Dict[str, Label]:
    """Load one or more label files that together cover every case exactly once.

    A file with a ``batch`` field covers only that batch of the manifest.
    """

    raw_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    batch_of = {c["case_id"]: c.get("batch") for c in raw_manifest["cases"]}
    labels: Dict[str, Label] = {}
    for path in label_paths:
        raw = json.loads(path.read_text(encoding="utf-8"))
        batch = raw.get("batch")
        cases = tuple(c for c in dataset.cases if batch is None or batch_of.get(c.case_id) == batch)
        subset = Dataset(dataset.name, dataset.split, dataset.as_of, dataset.rubric_version, cases)
        for case_id, label in load_labels(path, subset).items():
            if case_id in labels:
                raise ExperimentError(f"{case_id} is labeled in more than one file")
            labels[case_id] = label
    missing = sorted({c.case_id for c in dataset.cases} - set(labels))
    if missing:
        raise ExperimentError(f"cases without a label: {missing}")
    return labels


def prepare_experiment(plan: ExperimentPlan, *, repo_root: Path = REPO_ROOT, knowledge_root: Path = KNOWLEDGE_ROOT) -> PreparedExperiment:
    manifest_path = repo_root / plan.dataset_manifest
    label_paths = tuple(repo_root / p for p in plan.label_files)
    missing_files = [str(p) for p in (manifest_path, *label_paths) if not p.is_file()]
    if missing_files:
        raise ExperimentError(f"missing input files: {missing_files}")
    dataset = load_dataset(manifest_path)
    raw_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    store = KnowledgeStore(knowledge_root)
    if store.snapshot_id != plan.knowledge_snapshot_id:
        raise ExperimentError(
            f"knowledge snapshot is {store.snapshot_id!r}, but the plan says {plan.knowledge_snapshot_id!r}"
        )
    # Held-out cases were written against one exact snapshot. A development
    # manifest may name an earlier snapshot that the current one extends; the
    # experiment record keeps both IDs.
    dataset_snapshot = raw_manifest.get("knowledge_snapshot_id")
    if dataset.split == "held_out" and dataset_snapshot != plan.knowledge_snapshot_id:
        raise ExperimentError(
            f"dataset manifest expects snapshot {dataset_snapshot!r}, but the plan says {plan.knowledge_snapshot_id!r}"
        )
    lock_problems = check_lock(knowledge_root)
    if lock_problems:
        raise ExperimentError("knowledge snapshot does not match its lock: " + "; ".join(lock_problems))

    if dataset.split == "held_out":
        if plan.phase != "final":
            raise ExperimentError("held-out cases are used only by the final experiment, never for rehearsal")
        for path in label_paths:
            status = json.loads(path.read_text(encoding="utf-8")).get("status")
            if status not in FROZEN_LABEL_STATUSES:
                raise ExperimentError(
                    f"{path.name} has status {status!r}; held-out labels must be frozen before any final run"
                )
        if not plan.label_lock:
            raise ExperimentError("held-out labels need a label_lock in the plan")
        label_problems = check_label_lock(
            repo_root / plan.label_lock, label_paths,
            repo_root=repo_root, knowledge_snapshot_id=plan.knowledge_snapshot_id,
        )
        if label_problems:
            raise ExperimentError("held-out labels do not match their freeze lock: " + "; ".join(label_problems))

    labels = load_experiment_labels(dataset, manifest_path, label_paths)
    lock_path = None if plan.label_lock is None else repo_root / plan.label_lock
    return PreparedExperiment(plan, dataset, labels, knowledge_root, manifest_path, label_paths, lock_path)


def experiment_record(prepared: PreparedExperiment, code_commit: str) -> dict:
    """Everything needed to trace a reported number back to its inputs."""

    knowledge = prepared.knowledge_root
    lock = knowledge / "SNAPSHOT_LOCK.json"
    return {
        "experiment_id": prepared.plan.experiment_id,
        "phase": prepared.plan.phase,
        "plan": prepared.plan.model_dump(mode="json"),
        "plan_sha256": _plan_hash(prepared.plan),
        "code_commit": code_commit,
        "dataset": {
            "manifest": prepared.plan.dataset_manifest,
            "sha256": _sha256(prepared.manifest_path),
            "split": prepared.dataset.split,
            "declared_knowledge_snapshot_id": json.loads(
                prepared.manifest_path.read_text(encoding="utf-8")
            ).get("knowledge_snapshot_id"),
            "as_of": prepared.dataset.as_of,
            "cases": len(prepared.dataset.cases),
        },
        "labels": [
            {"path": rel, "sha256": _sha256(path)}
            for rel, path in zip(prepared.plan.label_files, prepared.label_paths)
        ],
        "label_lock": None if prepared.label_lock_path is None else {
            "path": prepared.plan.label_lock,
            "sha256": _sha256(prepared.label_lock_path),
        },
        "knowledge": {
            "snapshot_id": prepared.plan.knowledge_snapshot_id,
            "manifest_sha256": _sha256(knowledge / "manifest.json"),
            "lock_sha256": _sha256(lock) if lock.exists() else None,
        },
    }


RECORDED_INPUTS = ("plan_sha256", "code_commit", "dataset", "labels", "label_lock", "knowledge")


def _changed_inputs(recorded: Mapping[str, object], current: Mapping[str, object]) -> List[str]:
    return [key for key in RECORDED_INPUTS if recorded.get(key) != current.get(key)]


def _ensure_experiment_record(experiment_root: Path, record: dict) -> None:
    """Write the record once; refuse to resume an experiment whose inputs changed."""

    path = experiment_root / "experiment.json"
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        changed = _changed_inputs(existing, record)
        if changed:
            raise ExperimentError(
                f"experiment {record['experiment_id']!r} already exists with a different {changed[0]}; "
                "use a new experiment_id"
            )
        return
    experiment_root.mkdir(parents=True, exist_ok=True)
    _write_json(path, {**record, "created_at": _now()})


# --------------------------------------------------------------------------
# Running
# --------------------------------------------------------------------------


ClientFactory = Callable[..., ModelClient]


def run_id_for(experiment_id: str, system: str, case_id: str, repeat_index: int) -> str:
    """Unique across experiments, systems, cases, and repeats."""

    return f"{experiment_id}-{system}-{case_id}-r{repeat_index}"


def run_directory(experiment_root: Path, system: str, case_id: str, repeat_index: int) -> Path:
    return experiment_root / "runs" / system / case_id / f"repeat-{repeat_index}"


def _measure_trace(events: Sequence[TraceEvent], prices: Optional[TokenPrices]) -> dict:
    responses = [e for e in events if e.event_type is TraceEventType.MODEL_RESPONDED]
    reported = [e for e in responses if e.input_tokens is not None and e.output_tokens is not None]
    input_tokens = sum(e.input_tokens for e in reported)
    output_tokens = sum(e.output_tokens for e in reported)
    complete_usage = len(reported) == len(responses)
    cost = None
    if prices is not None and responses and complete_usage:
        cost = round(
            input_tokens / 1e6 * prices.input_per_million_tokens
            + output_tokens / 1e6 * prices.output_per_million_tokens,
            6,
        )
    return {
        "model_calls": sum(e.event_type is TraceEventType.MODEL_REQUESTED for e in events),
        "model_responses": len(responses),
        "tool_calls": sum(e.event_type is TraceEventType.TOOL_REQUESTED for e in events),
        "model_elapsed_ms": sum(e.elapsed_ms or 0 for e in responses),
        "input_tokens": input_tokens if responses else 0,
        "output_tokens": output_tokens if responses else 0,
        "token_usage_complete": complete_usage,
        "cost": cost,
        "currency": prices.currency if prices else None,
    }


def _predict(
    system: str,
    case: Case,
    as_of: date,
    plan: ExperimentPlan,
    knowledge_store: KnowledgeStore,
    client_factory: ClientFactory,
    run_id: str,
    repeat_index: int,
    code_commit: str,
) -> Prediction:
    if system == BASELINE_V1:
        return predict_baseline_v1(case, as_of)

    prepared = prepare_evaluation_case(case, as_of)
    if not prepared.requires_agent:
        package = build_non_agent_output_package(
            prepared_case=prepared,
            run_id=run_id,
            package_version=PACKAGE_VERSION,
            knowledge_snapshot_id=knowledge_store.snapshot_id,
        )
        return prediction_from_output_package(case, HOSTED_AGENT, package, ())

    profile: HostedModelProfile = plan.candidate.hosted_profile()
    execution = execute_hosted_case(
        prepared_case=prepared,
        model_client=client_factory(profile=profile),
        profile=profile,
        budgets=plan.budgets,
        knowledge_store=knowledge_store,
        run_id=run_id,
        as_of=as_of,
        code_commit=code_commit,
        plan_version=plan.experiment_id,
        repeat_index=repeat_index,
    )
    return prediction_from_output_package(
        case, HOSTED_AGENT, execution.output_package, execution.canonical_trace_events
    )


def _prediction_record(prediction: Prediction, route_outcome: Optional[str]) -> dict:
    return {
        "case_id": prediction.case_id,
        "system": prediction.system,
        "disposition": prediction.disposition,
        "root_cause": prediction.root_cause,
        "deadline": prediction.deadline,
        "approval_state": prediction.approval_state,
        "warnings": list(prediction.warnings),
        "details": prediction.details,
        "route_outcome": route_outcome,
    }


def _write_run(final_dir: Path, prediction: Prediction, measurements: dict, run_id: str, repeat_index: int) -> None:
    """Write into a temporary sibling, then rename it into place in one step."""

    final_dir.parent.mkdir(parents=True, exist_ok=True)
    partial = final_dir.with_name(final_dir.name + ".partial")
    if partial.exists():
        partial.rename(final_dir.with_name(f"{final_dir.name}.abandoned-{time.time_ns()}"))
    partial.mkdir()

    route = prediction.details.get("route_outcome") if isinstance(prediction.details, Mapping) else None
    _write_json(partial / "prediction.json", _prediction_record(prediction, route))
    if prediction.output_package is not None:
        _write_json(
            partial / "output-package.json",
            prediction.output_package.model_dump(mode="json", exclude_none=True),
        )
    if prediction.trace_events:
        JsonLinesTraceWriter(partial / "trace").write(prediction.trace_events)
    _write_json(partial / "measurements.json", measurements)
    _write_json(partial / "complete.json", {
        "run_id": run_id,
        "case_id": prediction.case_id,
        "system": prediction.system,
        "repeat_index": repeat_index,
        "completed_at": _now(),
    })
    partial.rename(final_dir)


def _code_location(filename: str) -> str:
    """A repository-relative path for project code, or only the file name otherwise."""

    path = Path(filename)
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.name


def safe_failure_diagnostics(error: BaseException) -> dict:
    """Typed diagnostics with no unrestricted exception text.

    Exception messages can echo provider responses, request content, or
    credentials, so they are default-denied: only their SHA-256 and length are
    kept, which still lets identical failures be grouped. The stack keeps code
    locations (file, line, function) and never local values or source text.
    """

    message = str(error)
    chain = []
    current: Optional[BaseException] = error
    while current is not None and len(chain) < 5:
        chain.append(f"{type(current).__module__}.{type(current).__qualname__}")
        current = current.__cause__ or current.__context__
    frames = traceback.extract_tb(error.__traceback__)
    return {
        "error_type": type(error).__name__,
        "error_class": f"{type(error).__module__}.{type(error).__qualname__}",
        "exception_chain": chain,
        "message_sha256": hashlib.sha256(message.encode("utf-8", "replace")).hexdigest(),
        "message_length": len(message),
        "stack": [f"{_code_location(f.filename)}:{f.lineno} in {f.name}" for f in frames][-15:],
    }


def _record_failure(experiment_root: Path, system: str, case_id: str, repeat_index: int, run_id: str, error: BaseException) -> Path:
    directory = experiment_root / "failures" / system / case_id / f"repeat-{repeat_index}"
    directory.mkdir(parents=True, exist_ok=True)
    attempt = len(list(directory.glob("attempt-*.json"))) + 1
    path = directory / f"attempt-{attempt}.json"
    _write_json(path, {
        "run_id": run_id,
        "case_id": case_id,
        "system": system,
        "repeat_index": repeat_index,
        "attempt": attempt,
        "failed_at": _now(),
        **safe_failure_diagnostics(error),
    })
    return path


def run_experiment(
    prepared: PreparedExperiment,
    *,
    output_root: Path,
    code_commit: str,
    confirm_live: bool,
    systems: Sequence[str] = SYSTEMS,
    case_ids: Optional[Sequence[str]] = None,
    client_factory: ClientFactory = build_hosted_model_client,
) -> dict:
    """Run every planned (system, case, repeat) that is not already complete."""

    if HOSTED_AGENT in systems and not confirm_live:
        raise ExperimentError("running the hosted agent requires explicit live confirmation")
    unknown = set(systems) - set(SYSTEMS)
    if unknown:
        raise ExperimentError(f"unknown systems: {sorted(unknown)}")
    plan = prepared.plan
    cases = prepared.dataset.cases
    if case_ids is not None:
        selected = set(case_ids)
        missing = selected - {c.case_id for c in cases}
        if missing:
            raise ExperimentError(f"cases not in the dataset: {sorted(missing)}")
        cases = tuple(c for c in cases if c.case_id in selected)

    experiment_root = output_root / plan.experiment_id
    _ensure_experiment_record(experiment_root, experiment_record(prepared, code_commit))
    as_of = date.fromisoformat(prepared.dataset.as_of)
    store = KnowledgeStore(prepared.knowledge_root)

    completed, skipped, failed = 0, 0, []
    for system in systems:
        for case in cases:
            for repeat_index in plan.repeats(system):
                final_dir = run_directory(experiment_root, system, case.case_id, repeat_index)
                if (final_dir / "complete.json").is_file():
                    skipped += 1
                    continue
                if final_dir.exists():
                    raise ExperimentError(f"run directory without completion marker needs review: {final_dir}")
                run_id = run_id_for(plan.experiment_id, system, case.case_id, repeat_index)
                started = time.perf_counter()
                try:
                    prediction = _predict(
                        system, case, as_of, plan, store, client_factory, run_id, repeat_index, code_commit
                    )
                    measurements = {
                        "wall_clock_ms": round((time.perf_counter() - started) * 1000),
                        **_measure_trace(prediction.trace_events, plan.token_prices),
                    }
                    _write_run(final_dir, prediction, measurements, run_id, repeat_index)
                    completed += 1
                except Exception as error:  # noqa: BLE001 - every failure is recorded, then the run continues
                    failed.append(str(_record_failure(experiment_root, system, case.case_id, repeat_index, run_id, error)))

    return {
        "experiment_id": plan.experiment_id,
        "completed_runs": completed,
        "skipped_completed_runs": skipped,
        "failed_runs": len(failed),
        "failure_records": failed,
        "output_root": str(experiment_root),
    }


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------


def _load_prediction(directory: Path) -> Prediction:
    raw = json.loads((directory / "prediction.json").read_text(encoding="utf-8"))
    return Prediction(
        case_id=raw["case_id"],
        system=raw["system"],
        disposition=raw["disposition"],
        root_cause=raw["root_cause"],
        deadline=raw["deadline"],
        approval_state=raw["approval_state"],
        warnings=tuple(raw["warnings"]),
        details=raw["details"],
    )


def _rate(rate) -> dict:
    return {"hits": rate.hits, "total": rate.total, "value": rate.value, "wilson_95": rate.wilson()}


AGENT_ROUTE = "AGENT_REQUIRED"


def _read_run_trace(directory: Path, case_id: str) -> List[Mapping[str, object]]:
    """The canonical trace of one agent-routed run, in order.

    Missing evidence is never read as zero errors: a run that entered the agent
    must have a readable trace for this exact run and case, or scoring stops.
    """

    run_id = json.loads((directory / "complete.json").read_text(encoding="utf-8"))["run_id"]
    paths = sorted((directory / "trace").glob("*.jsonl")) if (directory / "trace").is_dir() else []
    if not paths:
        raise ExperimentError(f"agent run {run_id} has no canonical trace; trace metrics can't be reported")
    events: List[Mapping[str, object]] = []
    for path in paths:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as error:
                raise ExperimentError(f"agent run {run_id} has a malformed trace ({path.name} line {number})") from error
            if not isinstance(event, dict) or event.get("run_id") != run_id or event.get("case_id") != case_id:
                raise ExperimentError(f"agent run {run_id} has a trace event for another run or case ({path.name} line {number})")
            events.append(event)
    if not events:
        raise ExperimentError(f"agent run {run_id} has an empty canonical trace")
    sequence = sorted(event.get("sequence_number", 0) for event in events)
    if sequence != list(range(1, len(events) + 1)):
        raise ExperimentError(f"agent run {run_id} has a trace with missing or repeated sequence numbers")
    return sorted(events, key=lambda event: event["sequence_number"])


def claim_services(dataset: Dataset) -> Dict[str, ClaimService]:
    """The payer, procedure, and service date each case's policy requests must use."""

    services = {}
    for case in dataset.cases:
        claim = load_claim(case.claim_path)
        line = next(s for s in claim.service_lines if s["line"] == case.service_line_id)
        services[case.case_id] = ClaimService(
            payer=claim.payer, procedure_code=line["cpt"], service_date=claim.date_of_service.isoformat()
        )
    return services


def trace_safety_report(
    traces: Mapping[str, Sequence[Mapping[str, object]]], services: Mapping[str, ClaimService]
) -> dict:
    """Citation precision, policy-request accuracy, and trace-measurable safety errors."""

    behaviour = measure_traces(traces, services)
    return {
        "trace_safety_counts": trace_safety_counts(behaviour),
        "citation_precision": _rate(Rate(behaviour.citations_retrieved, behaviour.citations)),
        "policy_requests_matching_claim": _rate(
            Rate(behaviour.policy_requests_matching_claim, behaviour.policy_requests)
        ),
        "unretrieved_citations": [list(item) for item in behaviour.unretrieved_citations],
        "agent_runs": behaviour.agent_runs,
        "decisions_proposed": behaviour.decisions_proposed,
        "decisions_accepted": behaviour.decisions_accepted,
        "budget_overruns": behaviour.budget_overruns,
        "unterminated_runs": behaviour.unterminated_runs,
    }


def _percentile(values: List[float], fraction: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(fraction * (len(ordered) - 1))))]


def score_experiment(prepared: PreparedExperiment, *, output_root: Path) -> dict:
    """Score every system and repeat. Refuses while any planned run is incomplete."""

    plan = prepared.plan
    experiment_root = output_root / plan.experiment_id
    record_path = experiment_root / "experiment.json"
    if not record_path.exists():
        raise ExperimentError(f"no experiment at {experiment_root}")
    record = json.loads(record_path.read_text(encoding="utf-8"))
    # Re-hash every input now: a file changed at the same path must not be
    # scored as if it were the one the runs used.
    current = experiment_record(prepared, record["code_commit"])
    changed = [key for key in _changed_inputs(record, current) if key != "code_commit"]
    if changed:
        raise ExperimentError(
            f"inputs changed since the experiment ran ({', '.join(changed)}); "
            "score only against the recorded inputs, or run a new experiment_id"
        )

    incomplete = [
        f"{system}/{case.case_id}/repeat-{n}"
        for system in SYSTEMS
        for case in prepared.dataset.cases
        for n in plan.repeats(system)
        if not (run_directory(experiment_root, system, case.case_id, n) / "complete.json").is_file()
    ]
    if incomplete:
        raise ExperimentError(f"{len(incomplete)} planned runs are not complete, e.g. {incomplete[:5]}")

    as_of = date.fromisoformat(prepared.dataset.as_of)
    facts = case_facts(prepared.dataset)
    services = claim_services(prepared.dataset)
    agent_routed = {
        case.case_id for case in prepared.dataset.cases if prepare_evaluation_case(case, as_of).requires_agent
    }
    results: dict = {"experiment_id": plan.experiment_id, "plan_sha256": record["plan_sha256"],
                     "code_commit": record["code_commit"], "cases": len(prepared.dataset.cases), "systems": {}}

    for system in SYSTEMS:
        repeats = {}
        answers: Dict[str, set] = {}
        latencies: List[float] = []
        totals = {"model_calls": 0, "tool_calls": 0, "input_tokens": 0, "output_tokens": 0}
        costs: List[Optional[float]] = []
        for n in plan.repeats(system):
            predictions = {}
            traces: Dict[str, List[Mapping[str, object]]] = {}
            for case in prepared.dataset.cases:
                directory = run_directory(experiment_root, system, case.case_id, n)
                predictions[case.case_id] = _load_prediction(directory)
                if system == HOSTED_AGENT:
                    # Which runs need a trace comes from routing the case again,
                    # not from the run's own files, so a damaged artifact can't
                    # opt out of the check.
                    recorded = json.loads((directory / "prediction.json").read_text(encoding="utf-8")).get("route_outcome")
                    if (recorded == AGENT_ROUTE) != (case.case_id in agent_routed):
                        raise ExperimentError(f"{case.case_id} repeat-{n} recorded route {recorded!r}, which routing doesn't reproduce")
                    if case.case_id in agent_routed:
                        traces[case.case_id] = _read_run_trace(directory, case.case_id)
                answers.setdefault(case.case_id, set()).add(
                    (predictions[case.case_id].disposition, predictions[case.case_id].root_cause)
                )
                measured = json.loads((directory / "measurements.json").read_text(encoding="utf-8"))
                latencies.append(measured["wall_clock_ms"])
                for key in totals:
                    totals[key] += measured.get(key, 0)
                if measured.get("model_responses"):
                    costs.append(measured.get("cost"))
            score = score_system(system, predictions, prepared.labels, facts, as_of, has_traces=system == HOSTED_AGENT)
            safety_counts = {code: score.safety_counts.get(code, 0) for code in ALL_SAFETY_ERRORS}
            unmeasured = list(score.safety_unmeasured)
            trace_report = None
            if system == HOSTED_AGENT:
                # The answer-level scorer can't see citations, policy requests,
                # or silent stops; the canonical traces can.
                trace_report = trace_safety_report(traces, services)
                for code, count in trace_report["trace_safety_counts"].items():
                    safety_counts[code] += count
                unmeasured = [code for code in unmeasured if code not in trace_report["trace_safety_counts"]]
            repeats[f"repeat-{n}"] = {
                "joint_accuracy": _rate(score.joint),
                "disposition_accuracy": _rate(score.disposition),
                "root_cause_accuracy": _rate(score.root_cause),
                "coverage": _rate(score.coverage),
                "selective_accuracy": _rate(score.selective),
                "unnecessary_manual_review": _rate(score.unnecessary_manual_review),
                "deadline_accuracy": _rate(score.deadline),
                "manual_review_rate": _rate(score.manual_review_rate),
                "critical_safety_errors": safety_counts,
                "safety_unmeasured": unmeasured,
                "trace_measures": trace_report,
                "per_case": score.per_case,
            }
        stable = sum(len(v) == 1 for v in answers.values())
        results["systems"][system] = {
            "repeats": repeats,
            "stability": {
                "cases_with_identical_answers_across_repeats": stable,
                "cases": len(answers),
                "value": stable / len(answers) if answers else None,
            },
            "operations": {
                "runs": len(latencies),
                "wall_clock_ms_median": statistics.median(latencies) if latencies else None,
                "wall_clock_ms_p95": _percentile(latencies, 0.95),
                **totals,
                "cost_total": (round(sum(costs), 6) if costs and all(c is not None for c in costs) else None),
                "cost_currency": plan.token_prices.currency if plan.token_prices else None,
            },
        }

    failures = sorted(str(p.relative_to(experiment_root)) for p in (experiment_root / "failures").rglob("attempt-*.json")) \
        if (experiment_root / "failures").exists() else []
    results["failed_attempts_recorded"] = failures
    _write_json(experiment_root / "results.json", results)
    return results


# --------------------------------------------------------------------------
# Command line
# --------------------------------------------------------------------------


def main(arguments: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run or score the repeated-run evaluation experiment (#53).")
    parser.add_argument("command", choices=("check", "run", "score"))
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--code-commit", help="Exact commit recorded in the experiment (required for run).")
    parser.add_argument("--system", action="append", choices=SYSTEMS, help="Limit to one system (repeatable).")
    parser.add_argument("--case-id", action="append", help="Limit to one case (repeatable), for a smoke test.")
    parser.add_argument("--confirm-live", action="store_true", help="Allow hosted model calls.")
    args = parser.parse_args(arguments)

    try:
        prepared = prepare_experiment(load_plan(args.plan))
        if args.command == "check":
            summary = {"experiment_id": prepared.plan.experiment_id, "cases": len(prepared.dataset.cases),
                       "labels": len(prepared.labels), "planned_runs": sum(
                           len(prepared.dataset.cases) * len(prepared.plan.repeats(s)) for s in SYSTEMS)}
        elif args.command == "run":
            if not args.code_commit:
                raise ExperimentError("--code-commit is required to run")
            summary = run_experiment(
                prepared,
                output_root=args.output_root,
                code_commit=args.code_commit,
                confirm_live=args.confirm_live,
                systems=tuple(args.system) if args.system else SYSTEMS,
                case_ids=args.case_id,
            )
        else:
            results = score_experiment(prepared, output_root=args.output_root)
            summary = {
                system: {
                    "stability": data["stability"],
                    "operations": data["operations"],
                    "joint_accuracy_by_repeat": {k: v["joint_accuracy"]["value"] for k, v in data["repeats"].items()},
                    "critical_safety_errors_by_repeat": {
                        k: sum(v["critical_safety_errors"].values()) for k, v in data["repeats"].items()
                    },
                }
                for system, data in results["systems"].items()
            }
    except ExperimentError as error:
        print(json.dumps({"error": "experiment_error", "message": str(error)}, sort_keys=True))
        return 2

    print(json.dumps(summary, indent=2, sort_keys=True, default=str))
    return 1 if isinstance(summary, dict) and summary.get("failed_runs") else 0


if __name__ == "__main__":
    raise SystemExit(main())
