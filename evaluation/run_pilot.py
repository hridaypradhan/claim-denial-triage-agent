"""Run one pilot experiment and write predictions, scores, and a results table.

Example, from the repository root:

    python -m evaluation.run_pilot \
        --manifest datasets/development/set_a/manifest.json \
        --labels datasets/labels/set_a_development.labeler_a.json \
        --experiment set-a-dev-baseline

Outputs go to ``.local-output/eval/<experiment>/``, which Git ignores.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from baseline.triage import reference
from baseline.triage.claim import load_claim

from claim_agent.agent.trace_writer import JsonLinesTraceWriter

from evaluation.metrics import ALL_SAFETY_ERRORS, CaseFacts, SystemScore, score_system
from evaluation.pilot_data import REPO_ROOT, Dataset, load_dataset, load_labels
from evaluation.systems import (
    SYSTEMS,
    Prediction,
)

HAS_TRACES = {"baseline-v1": False}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_commit() -> Optional[str]:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _write_prediction_artifacts(
    output_directory: Path,
    predictions: Dict[str, Prediction],
) -> tuple[int, int]:
    """Persist inspectable packages and traces produced by one experiment."""

    output_package_directory = output_directory / "output_packages"
    trace_writer = JsonLinesTraceWriter(output_directory / "traces")
    output_package_count = 0
    trace_count = 0

    for case_id, prediction in predictions.items():
        if prediction.output_package is not None:
            output_package_directory.mkdir(
                parents=True,
                exist_ok=True,
            )
            package_path = output_package_directory / f"{case_id}.json"
            package_path.write_text(
                json.dumps(
                    prediction.output_package.model_dump(
                        mode="json",
                        exclude_none=True,
                    ),
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            output_package_count += 1

        if prediction.trace_events:
            trace_writer.write(prediction.trace_events)
            trace_count += 1

    return output_package_count, trace_count


def case_facts(dataset: Dataset) -> Dict[str, CaseFacts]:
    """Read the claim facts the safety checks need, using the frozen payer rules."""

    facts = {}
    for case in dataset.cases:
        claim = load_claim(case.claim_path)
        rules = reference.payer_rules(claim.payer)
        denial = next(
            d for d in claim.denials if d.service_line == case.service_line_id
        )
        facts[case.case_id] = CaseFacts(
            group_code=denial.group_code,
            payer_appeal_days=rules["appeal_window_days"],
            payer_corrected_days=rules["corrected_claim_window_days"],
            remit_date=claim.remit_date,
            date_of_service=claim.date_of_service,
            abn_signed=bool(claim.encounter.get("abn_signed")),
        )
    return facts


def render_markdown(dataset: Dataset, score: SystemScore, label_status: str) -> str:
    rows = [
        ("Joint accuracy (root cause and disposition)", score.joint.text()),
        ("Disposition accuracy", score.disposition.text()),
        ("Root-cause accuracy", score.root_cause.text()),
        ("Resolution coverage (gold not manual review)", score.coverage.text()),
        ("Selective accuracy (system did not escalate)", score.selective.text()),
        ("Unnecessary manual review", score.unnecessary_manual_review.text()),
        ("Manual-review rate", score.manual_review_rate.text()),
        ("Deadline accuracy (same disposition as gold)", score.deadline.text()),
    ]
    lines = [
        "# Pilot results: Dataset %s (%s), system `%s`"
        % (dataset.name, dataset.split, score.system),
        "",
        "- Cases: %d service lines, as of %s, rubric v%s"
        % (score.cases, dataset.as_of, dataset.rubric_version),
        "- Labels: %s" % label_status,
        "",
        "| Metric | Result |",
        "|---|---|",
    ]
    lines += ["| %s | %s |" % row for row in rows]
    lines += ["", "## Critical safety errors", "", "| Error | Count |", "|---|---|"]
    for name in ALL_SAFETY_ERRORS:
        if name in score.safety_unmeasured:
            lines.append("| `%s` | n/a (not measurable for this system) |" % name)
        else:
            lines.append("| `%s` | %d |" % (name, score.safety_counts.get(name, 0)))
    lines += [
        "",
        "## Disposition confusion (gold -> predicted)",
        "",
        "| Gold | Predicted | Cases |",
        "|---|---|---|",
    ]
    for (gold, pred), count in sorted(score.confusion.items()):
        lines.append("| %s | %s | %d |" % (gold, pred, count))
    lines += [
        "",
        "## Cases that differ from the labels",
        "",
        "| Case | Gold | Predicted | Safety errors |",
        "|---|---|---|---|",
    ]
    for row in score.per_case:
        if not row["joint_correct"] or row["safety_errors"]:
            lines.append(
                "| %s | %s / %s | %s / %s | %s |"
                % (
                    row["case_id"],
                    row["gold_root_cause"],
                    row["gold_disposition"],
                    row["predicted_root_cause"],
                    row["predicted_disposition"],
                    ", ".join(row["safety_errors"]) or "-",
                )
            )
    return "\n".join(lines) + "\n"


def run(arguments: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run one pilot evaluation experiment.")
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--labels", required=True, type=Path)
    parser.add_argument("--experiment", required=True)
    parser.add_argument("--system", default="baseline-v1", choices=sorted(SYSTEMS))
    parser.add_argument(
        "--outdir", type=Path, default=REPO_ROOT / ".local-output" / "eval"
    )
    args = parser.parse_args(arguments)

    dataset = load_dataset(args.manifest)
    labels = load_labels(args.labels, dataset)
    label_status = json.loads(args.labels.read_text(encoding="utf-8")).get(
        "status", "unknown"
    )
    as_of = date.fromisoformat(dataset.as_of)

    predict = SYSTEMS[args.system]
    predictions: Dict[str, Prediction] = {
        case.case_id: predict(case, as_of) for case in dataset.cases
    }
    score = score_system(
        args.system,
        predictions,
        labels,
        case_facts(dataset),
        as_of,
        has_traces=HAS_TRACES.get(args.system, True),
    )

    out = args.outdir / args.experiment
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "predictions.jsonl", "w", encoding="utf-8", newline="\n") as handle:
        for case in dataset.cases:
            p = predictions[case.case_id]
            handle.write(
                json.dumps(
                    {
                        "case_id": p.case_id,
                        "system": p.system,
                        "disposition": p.disposition,
                        "root_cause": p.root_cause,
                        "deadline": p.deadline,
                        "approval_state": p.approval_state,
                        "warnings": list(p.warnings),
                        "details": p.details,
                    }
                )
                + "\n"
            )
    output_package_count, trace_count = _write_prediction_artifacts(
        output_directory=out,
        predictions=predictions,
    )
    run_manifest = {
        "experiment": args.experiment,
        "system": args.system,
        "dataset": dataset.name,
        "split": dataset.split,
        "as_of": dataset.as_of,
        "rubric_version": dataset.rubric_version,
        "label_status": label_status,
        "manifest_sha256": _sha256(args.manifest),
        "labels_sha256": _sha256(args.labels),
        "code_commit": _git_commit(),
        "output_package_count": output_package_count,
        "canonical_trace_count": trace_count,
    }
    (out / "run_manifest.json").write_text(
        json.dumps(run_manifest, indent=2) + "\n", encoding="utf-8"
    )
    (out / "scores.json").write_text(
        json.dumps(
            {
                "system": score.system,
                "cases": score.cases,
                "rates": {
                    name: {"hits": r.hits, "total": r.total}
                    for name, r in [
                        ("joint", score.joint),
                        ("disposition", score.disposition),
                        ("root_cause", score.root_cause),
                        ("coverage", score.coverage),
                        ("selective", score.selective),
                        ("unnecessary_manual_review", score.unnecessary_manual_review),
                        ("manual_review_rate", score.manual_review_rate),
                        ("deadline", score.deadline),
                    ]
                },
                "safety_counts": score.safety_counts,
                "safety_unmeasured": list(score.safety_unmeasured),
                "per_case": score.per_case,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    report = render_markdown(dataset, score, label_status)
    (out / "results.md").write_text(report, encoding="utf-8")
    _print_safely(report + "\nWrote %s\n" % out)
    return 0


def _print_safely(text: str) -> None:
    """Print text even on a console that can't encode every character (e.g. Windows cp1252)."""

    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    sys.stdout.write(text.encode(encoding, errors="replace").decode(encoding))


if __name__ == "__main__":
    raise SystemExit(run())
