"""Recompute the #66 / poster values that are not a single field in results.json.

Usage (repo root, bundle unzipped so final-heldout-v1/ sits next to this script or pass its path):
    python derived_values.py <path-to-final-heldout-v1> <path-to-repo>
Reads only results.json, runs/*/post-decision.json and the locked held-out labels.
"""
import json, sys
from collections import Counter
from pathlib import Path

B = Path(sys.argv[1]); REPO = Path(sys.argv[2])
r = json.loads((B / "results.json").read_text(encoding="utf-8"))
base = r["systems"]["baseline-v1"]["repeats"]["repeat-1"]
agent = r["systems"]["hosted-agent"]["repeats"]

print("== Baseline critical safety errors ==")
print({k: v for k, v in base["critical_safety_errors"].items() if v},
      "total", sum(base["critical_safety_errors"].values()),
      "cases", sum(bool(c["safety_errors"]) for c in base["per_case"]))

print("== Agent per repeat ==")
for name, rep in agent.items():
    cse = {k: v for k, v in rep["critical_safety_errors"].items() if v}
    print(name, "joint", rep["joint_accuracy"]["hits"], "CSE", cse, "total", sum(cse.values()),
          "MR", rep["manual_review_rate"]["hits"], "selective",
          f'{rep["selective_accuracy"]["hits"]}/{rep["selective_accuracy"]["total"]}')

per = {}
for rep in agent.values():
    for c in rep["per_case"]:
        per.setdefault(c["case_id"], []).append(c)
print("== Combining the 3 repeats ==")
print("majority-of-3 correct", sum(sum(x["joint_correct"] for x in v) >= 2 for v in per.values()),
      "all-3", sum(all(x["joint_correct"] for x in v) for v in per.values()),
      "any", sum(any(x["joint_correct"] for x in v) for v in per.values()))
print("majority manual review", sum(sum(x["predicted_disposition"] == "MANUAL_REVIEW" for x in v) >= 2 for v in per.values()))

tm = [rep["trace_measures"]["citation_precision"] for rep in agent.values()]
print("citation precision pooled", sum(t["hits"] for t in tm), "/", sum(t["total"] for t in tm))

print("== Gold labels ==")
print(Counter(c["gold_disposition"] for c in base["per_case"]))

print("== Post-decision: verifier escalations and accepted drafts vs gold ==")
gold = {c["case_id"]: c["gold_disposition"] for c in base["per_case"]}
esc, appeals = Counter(), Counter()
for p in sorted((B / "runs" / "hosted-agent").glob("*/repeat-?/post-decision.json")):
    pd = json.loads(p.read_text(encoding="utf-8"))
    cid = p.parts[-3]
    if pd.get("escalation_reason"):
        esc[(pd["escalation_reason"], pd.get("decision_disposition"), gold[cid])] += 1
    elif pd.get("final_disposition") == "APPEAL_WITH_DOCUMENTATION":
        appeals[gold[cid]] += 1
print("escalations (reason, decision, gold):", dict(esc))
print("accepted appeal drafts by gold label:", dict(appeals))

print("== Route of CLM-2026-710220-L1 ==")
for i in (1, 2, 3):
    d = json.loads((B / f"runs/hosted-agent/CLM-2026-710220-L1/repeat-{i}/output-package.json").read_text(encoding="utf-8"))
    print(i, d.get("route_outcome"), d.get("disposition") or d.get("final_disposition"))

# Each run's measurements.json keeps decision-stage usage at the top level and
# verifier/drafter usage under "post_decision"; "pipeline" is their sum.
# results.json -> operations mirrors this: top-level = decision stage,
# operations.post_decision = verifier and drafter.
KEYS = ("model_calls", "input_tokens", "output_tokens")
ops = r["systems"]["hosted-agent"]["operations"]
done = {k: ops[k] + ops["post_decision"][k] for k in KEYS}
print("== Provider usage, completed and scored runs (240) ==")
print("decision stage", {k: ops[k] for k in KEYS})
print("post-decision", {k: ops["post_decision"][k] for k in KEYS})
print("total", done)

print("== Provider usage, 13 failed attempts (abandoned folders, not scored) ==")
dec = dict.fromkeys(KEYS, 0); post = dict.fromkeys(KEYS, 0); n = 0; complete = True
for p in sorted((B / "runs").glob("*/*/*.abandoned-*/measurements.json")):
    m = json.loads(p.read_text(encoding="utf-8"))
    n += 1
    for k in KEYS:
        dec[k] += m[k]
        post[k] += m["post_decision"][k]
        assert m["pipeline"][k] == m[k] + m["post_decision"][k]
    complete &= m["pipeline"]["token_usage_complete"]
failed = {k: dec[k] + post[k] for k in KEYS}
print("attempts", n, "usage reported for every call", complete)
print("decision stage", dec)
print("post-decision", post)
print("total", failed)

print("== Provider usage, all attempts ==")
print({k: done[k] + failed[k] for k in KEYS})
