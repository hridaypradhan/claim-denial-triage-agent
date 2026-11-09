"""Run every pilot system on both datasets and write one comparison report.

Usage (from the repository root):

    python -m evaluation.compare_systems --experiment m2-pilot

Each system x dataset run is a normal ``run_pilot`` experiment under
``<outdir>/<experiment>/<dataset>-<system>/``. The combined tables go to
``<outdir>/<experiment>/comparison.md`` and ``comparison.json``.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from evaluation.agreement import compare_label_files
from evaluation.metrics import Rate
from evaluation.pilot_data import REPO_ROOT
from evaluation.run_pilot import _print_safely, run

LABELS = REPO_ROOT / "datasets" / "labels"
DATASETS = {
    "A": {
        "manifest": REPO_ROOT / "datasets" / "development" / "set_a" / "manifest.json",
        "labels": LABELS / "set_a_development.adjudicated.json",
        "labelers": (LABELS / "set_a_development.labeler_a.json", LABELS / "set_a_development.labeler_b.json"),
    },
    "B": {
        "manifest": REPO_ROOT / "datasets" / "development" / "set_b" / "manifest.json",
        "labels": LABELS / "set_b_development.adjudicated.json",
        "labelers": (LABELS / "set_b_development.labeler_a.json", LABELS / "set_b_development.labeler_b.json"),
    },
}
# Display name, harness system id. S0 is the current system; S1 and S2 are baselines.
SYSTEMS = (
    ("S0 agent (scripted)", "scripted-agent"),
    ("S1 baseline-v1", "baseline-v1"),
    ("S2 escalate-all", "escalate-all"),
)
RATE_ROWS = (
    ("joint", "Joint accuracy"),
    ("disposition", "Disposition accuracy"),
    ("root_cause", "Root-cause accuracy"),
    ("coverage", "Resolution coverage"),
    ("selective", "Selective accuracy"),
    ("unnecessary_manual_review", "Unnecessary manual review"),
    ("manual_review_rate", "Manual-review rate"),
    ("deadline", "Deadline accuracy"),
)


def _rate_text(rate: Dict[str, int]) -> str:
    return Rate(rate["hits"], rate["total"]).text()


def run_all(outdir: Path, experiment: str) -> Dict[str, Dict[str, dict]]:
    """Run each system on each dataset quietly and return their scores."""

    scores: Dict[str, Dict[str, dict]] = {}
    for dataset, paths in DATASETS.items():
        for _, system in SYSTEMS:
            name = "%s-%s" % (dataset, system)
            with contextlib.redirect_stdout(io.StringIO()):
                code = run([
                    "--manifest", str(paths["manifest"]),
                    "--labels", str(paths["labels"]),
                    "--experiment", name,
                    "--system", system,
                    "--outdir", str(outdir / experiment),
                ])
            if code != 0:
                raise RuntimeError("run failed: %s" % name)
            run_dir = outdir / experiment / name
            scores.setdefault(dataset, {})[system] = {
                "scores": json.loads((run_dir / "scores.json").read_text(encoding="utf-8")),
                "manifest": json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8")),
            }
    return scores


def render(scores: Dict[str, Dict[str, dict]], agreement: Dict[str, dict]) -> str:
    lines: List[str] = ["# Milestone 2 pilot: system comparison", ""]
    first = next(iter(next(iter(scores.values())).values()))["manifest"]
    lines += [
        "- Labels: adjudicated (two independent labelers per case, rubric v%s)" % first["rubric_version"],
        "- As of: %s; code commit `%s`" % (first["as_of"], (first.get("code_commit") or "unknown")[:12]),
        "- S0 runs the real router, tools, validator, and trace adapter, but its model is a "
        "**per-case scripted response, not a hosted LLM**. Its accuracy shows the pipeline "
        "works end to end, not that a model reasoned on its own.",
        "",
    ]
    for dataset, by_system in scores.items():
        cases = next(iter(by_system.values()))["scores"]["cases"]
        lines += ["## Dataset %s (%d cases)" % (dataset, cases), ""]
        lines += ["| Metric | " + " | ".join(n for n, _ in SYSTEMS) + " |",
                  "|---|" + "---|" * len(SYSTEMS)]
        for key, label in RATE_ROWS:
            lines.append("| %s | %s |" % (label, " | ".join(
                _rate_text(by_system[s]["scores"]["rates"][key]) for _, s in SYSTEMS)))
        safety_names = sorted({n for s in by_system.values() for n in s["scores"]["safety_counts"]}
                              | {n for s in by_system.values() for n in s["scores"]["safety_unmeasured"]})
        lines += ["", "**Critical safety errors** (n/a = needs traces this system doesn't produce)", "",
                  "| Error | " + " | ".join(n for n, _ in SYSTEMS) + " |",
                  "|---|" + "---|" * len(SYSTEMS)]
        for name in safety_names:
            cells = []
            for _, s in SYSTEMS:
                sc = by_system[s]["scores"]
                cells.append("n/a" if name in sc["safety_unmeasured"] else str(sc["safety_counts"].get(name, 0)))
            lines.append("| `%s` | %s |" % (name, " | ".join(cells)))
        totals = []
        for _, s in SYSTEMS:
            sc = by_system[s]["scores"]
            totals.append(str(sum(v for k, v in sc["safety_counts"].items() if k not in sc["safety_unmeasured"])))
        lines.append("| **Total measured** | %s |" % " | ".join(totals))
        lines += ["", "**Per case** (`ok` means root cause and disposition both match the gold label; otherwise the predicted disposition is shown)", "",
                  "| Case | Gold | " + " | ".join(n.split(" ")[0] for n, _ in SYSTEMS) + " |",
                  "|---|---|" + "---|" * len(SYSTEMS)]
        per_case = {s: {r["case_id"]: r for r in by_system[s]["scores"]["per_case"]} for _, s in SYSTEMS}
        for case_id, row in per_case[SYSTEMS[0][1]].items():
            cells = []
            for _, s in SYSTEMS:
                r = per_case[s][case_id]
                mark = "ok" if r["joint_correct"] else r["predicted_disposition"]
                if r["safety_errors"]:
                    mark += " (" + ", ".join(e.replace("CSE_", "") for e in r["safety_errors"]) + ")"
                cells.append(mark)
            lines.append("| %s | %s | %s |" % (case_id, row["gold_disposition"], " | ".join(cells)))
        lines.append("")

    lines += ["## Inter-labeler agreement (before adjudication)", "",
              "| Dataset | Field | Items | Agreement | Cohen's kappa |", "|---|---|---|---|---|"]
    for dataset, result in agreement.items():
        for f in result["fields"]:
            kappa = "n/a" if f.kappa is None else "%.2f" % f.kappa
            lines.append("| %s | %s | %d | %d/%d (%.0f%%) | %s |" % (
                dataset, f.field, f.items, f.agreed, f.items, 100 * f.percent, kappa))
    lines.append("")
    return "\n".join(lines)


def main(arguments: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run all pilot systems on both datasets.")
    parser.add_argument("--experiment", required=True)
    parser.add_argument("--outdir", type=Path, default=REPO_ROOT / ".local-output" / "eval")
    args = parser.parse_args(arguments)

    scores = run_all(args.outdir, args.experiment)
    agreement = {d: compare_label_files(*p["labelers"]) for d, p in DATASETS.items()}
    report = render(scores, agreement)
    out = args.outdir / args.experiment
    (out / "comparison.md").write_text(report, encoding="utf-8")
    (out / "comparison.json").write_text(json.dumps({
        "scores": {d: {s: v["scores"] for s, v in by.items()} for d, by in scores.items()},
        "agreement": {d: {"cases": r["cases"], "disagreements": r["disagreements"],
                          "fields": [f.__dict__ for f in r["fields"]]} for d, r in agreement.items()},
    }, indent=2) + "\n", encoding="utf-8")
    _print_safely(report + "\nWrote %s\n" % out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
