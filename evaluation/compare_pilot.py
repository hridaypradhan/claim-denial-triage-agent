"""Run the Milestone 2 pilot: three systems on both datasets, one comparison.

From the repository root:

    python -m evaluation.compare_pilot

For each dataset and system this runs ``evaluation.run_pilot`` against the
adjudicated labels, then writes one comparison table per dataset, the
agent-behavior metrics from S0's traces, and the labeler agreement computed
from the two original label files. Results go to ``--outdir``
(default ``evaluation/results/m2-pilot``).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from baseline.triage.claim import load_claim

from evaluation import run_pilot
from evaluation.agreement import labeler_agreement
from evaluation.metrics import ALL_SAFETY_ERRORS, Rate
from evaluation.pilot_data import REPO_ROOT, load_dataset
from evaluation.trace_metrics import ClaimService, measure_traces, trace_safety_counts

DATASETS = (
    ("A", "set_a", "Rule and safety triage"),
    ("B", "set_b", "Evidence-dependent policy review"),
)
SYSTEMS = (
    ("S0", "scripted-agent", "Scripted integrated agent"),
    ("S1", "baseline-v1", "Frozen rule-based baseline"),
    ("S2", "escalate-all", "Escalate-all baseline"),
)
RATE_ROWS = (
    ("joint", "Joint accuracy (root cause and disposition)"),
    ("disposition", "Disposition accuracy"),
    ("root_cause", "Root-cause accuracy"),
    ("coverage", "Resolution coverage (gold is not manual review)"),
    ("selective", "Selective accuracy (system did not escalate)"),
    ("unnecessary_manual_review", "Unnecessary manual review"),
    ("manual_review_rate", "Manual-review rate"),
    ("deadline", "Deadline accuracy (same disposition as gold)"),
)
DEFAULT_OUTDIR = REPO_ROOT / "evaluation" / "results" / "m2-pilot"


def _paths(prefix: str) -> Dict[str, Path]:
    labels = REPO_ROOT / "datasets" / "labels"
    return {
        "manifest": REPO_ROOT / "datasets" / "development" / prefix / "manifest.json",
        "adjudicated": labels / f"{prefix}_development.adjudicated.json",
        "labeler_a": labels / f"{prefix}_development.labeler_a.json",
        "labeler_b": labels / f"{prefix}_development.labeler_b.json",
    }


def _rate_text(entry: dict) -> str:
    return Rate(entry["hits"], entry["total"]).text()


def _safety_count(scores: dict, name: str, from_traces: Dict[str, int]) -> Optional[int]:
    """A system's count for one error, or ``None`` when it isn't measured."""

    if name in from_traces:
        return from_traces[name]
    if name in scores["safety_unmeasured"]:
        return None
    return scores["safety_counts"].get(name, 0)


def _safety_text(scores: dict, name: str, from_traces: Dict[str, int]) -> str:
    count = _safety_count(scores, name, from_traces)
    if count is None:
        return "n/a"
    return "%d (traces)" % count if name in from_traces else str(count)


def _services(manifest: Path) -> Dict[str, ClaimService]:
    services = {}
    for case in load_dataset(manifest).cases:
        claim = load_claim(case.claim_path)
        line = next(s for s in claim.service_lines if s["line"] == case.service_line_id)
        services[case.case_id] = ClaimService(
            payer=claim.payer,
            procedure_code=line["cpt"],
            service_date=claim.date_of_service.isoformat(),
        )
    return services


def _read_traces(directory: Path) -> Dict[str, List[dict]]:
    traces: Dict[str, List[dict]] = {}
    if not directory.exists():
        return traces
    for path in sorted(directory.glob("*.jsonl")):
        events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if events:
            traces[events[0]["case_id"]] = events
    return traces


def _comparison_table(scores_by_system: Dict[str, dict], trace_counts: Dict[str, Dict[str, int]]) -> List[str]:
    header = "| Metric | " + " | ".join("%s `%s`" % (code, name) for code, name, _ in SYSTEMS) + " |"
    lines = [header, "|---|" + "---|" * len(SYSTEMS)]
    for key, label in RATE_ROWS:
        cells = [_rate_text(scores_by_system[name]["rates"][key]) for _, name, _ in SYSTEMS]
        lines.append("| %s | %s |" % (label, " | ".join(cells)))
    totals = []
    for _, name, _ in SYSTEMS:
        counts = [_safety_count(scores_by_system[name], n, trace_counts.get(name, {})) for n in ALL_SAFETY_ERRORS]
        totals.append(str(sum(c for c in counts if c is not None)))
    lines.append("| **Critical safety errors, measured total** | %s |" % " | ".join(totals))
    for error in ALL_SAFETY_ERRORS:
        cells = [_safety_text(scores_by_system[name], error, trace_counts.get(name, {})) for _, name, _ in SYSTEMS]
        lines.append("| `%s` | %s |" % (error, " | ".join(cells)))
    return lines


def _differences(scores_by_system: Dict[str, dict]) -> List[str]:
    lines = ["| Case | Gold | " + " | ".join(code for code, _, _ in SYSTEMS) + " |", "|---|---|" + "---|" * len(SYSTEMS)]
    per_system = {name: {row["case_id"]: row for row in scores_by_system[name]["per_case"]} for _, name, _ in SYSTEMS}
    first = per_system[SYSTEMS[0][1]]
    for case_id in sorted(first):
        rows = [per_system[name][case_id] for _, name, _ in SYSTEMS]
        if all(r["joint_correct"] and not r["safety_errors"] for r in rows):
            continue
        cells = []
        for row in rows:
            mark = "ok" if row["joint_correct"] else "miss"
            errors = ", ".join(e.replace("CSE_", "") for e in row["safety_errors"])
            cells.append("%s: %s%s" % (mark, row["predicted_disposition"], " (%s)" % errors if errors else ""))
        lines.append("| %s | %s | %s |" % (case_id, rows[0]["gold_disposition"], " | ".join(cells)))
    return lines


def run(arguments: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run the three-system, two-dataset pilot.")
    parser.add_argument("--outdir", type=Path, default=DEFAULT_OUTDIR)
    args = parser.parse_args(arguments)
    outdir: Path = args.outdir
    outdir.mkdir(parents=True, exist_ok=True)

    report = [
        "# Milestone 2 pilot: three systems on two datasets",
        "",
        "Synthetic development pilot. Scored against the adjudicated labels (rubric v0.1). "
        "S0 runs the real router, runtime, tools, validator, and output packaging, but its model "
        "responses are case-specific scripts written after the labels, so S0 shows the integrated "
        "pipeline handles each case correctly; it does not measure how a live model would reason.",
        "",
        "Rates show hits/total, the percentage, and a 95% Wilson interval. Most safety errors are judged "
        "from the final answer. `(traces)` marks a count read from S0's canonical traces, which cover only "
        "its agent-routed cases. `n/a` marks an error that can't be measured for that system: S1 and S2 "
        "write no traces, and `CSE_INSTRUCTION_FOLLOWED` can't be read from a trace. B10's resistance to "
        "the injected instruction is scripted, so it is not evidence either way.",
        "",
        "Regenerate with `python -m evaluation.compare_pilot`.",
    ]
    summary: Dict[str, dict] = {}

    for code, prefix, title in DATASETS:
        paths = _paths(prefix)
        scores_by_system: Dict[str, dict] = {}
        for _, system, _ in SYSTEMS:
            experiment = f"{prefix}-{system}"
            run_pilot.run([
                "--manifest", str(paths["manifest"]),
                "--labels", str(paths["adjudicated"]),
                "--experiment", experiment,
                "--system", system,
                "--outdir", str(outdir),
            ])
            scores_by_system[system] = json.loads((outdir / experiment / "scores.json").read_text(encoding="utf-8"))

        traces = _read_traces(outdir / f"{prefix}-scripted-agent" / "traces")
        behaviour = measure_traces(traces, _services(paths["manifest"]))
        agreement = labeler_agreement(paths["labeler_a"], paths["labeler_b"])
        cases = scores_by_system[SYSTEMS[0][1]]["cases"]

        report += [
            "",
            "## Dataset %s: %s (%d cases)" % (code, title, cases),
            "",
            *_comparison_table(scores_by_system, {"scripted-agent": trace_safety_counts(behaviour)}),
            "",
            "### Cases where any system differs from the label",
            "",
            *_differences(scores_by_system),
            "",
            "### S0 agent behavior (from canonical traces)",
            "",
            "| Measure | S0 |",
            "|---|---|",
            *["| %s | %s |" % row for row in behaviour.rows()],
            "",
            "### Labeler agreement (original label files, before adjudication)",
            "",
            "| Field | Agreement |",
            "|---|---|",
            *["| %s | %s |" % (a.field.replace("_", " "), a.text()) for a in agreement],
        ]
        summary[code] = {
            "cases": cases,
            "systems": {
                system: {
                    "rates": scores_by_system[system]["rates"],
                    "safety_counts": scores_by_system[system]["safety_counts"],
                    "safety_unmeasured": scores_by_system[system]["safety_unmeasured"],
                }
                for _, system, _ in SYSTEMS
            },
            "agent_behaviour": dict(behaviour.rows()),
            "trace_safety_counts": trace_safety_counts(behaviour),
            "unretrieved_citations": behaviour.unretrieved_citations,
            "labeler_agreement": {
                a.field: {"agreed": a.agreed, "total": a.total, "kappa": a.kappa} for a in agreement
            },
        }

    (outdir / "comparison.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    (outdir / "comparison.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    run_pilot._print_safely("\nWrote %s\n" % (outdir / "comparison.md"))
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
