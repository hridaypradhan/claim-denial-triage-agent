"""Rule-based claim denial triage baseline. Standard library only."""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from triage import load_claim, render_report, triage_claim
from triage.claim import ClaimFormatError

SKIP = {"labels.json"}


def collect_inputs(target):
    path = Path(target)
    if path.is_dir():
        return sorted(p for p in path.glob("*.json") if p.name not in SKIP)
    if path.is_file():
        return [path]
    raise FileNotFoundError("No such file or directory: {}".format(target))


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Triage denied medical claims from a JSON remittance record.")
    parser.add_argument("--input", required=True, help="Claim JSON file, or a directory of claim JSON files")
    parser.add_argument("--outdir", default="outputs", help="Directory for report and JSON output (default: outputs)")
    parser.add_argument("--as-of", dest="as_of", help="Evaluate deadlines against this date (YYYY-MM-DD)")
    parser.add_argument("--json", action="store_true", help="Print the structured result instead of the report")
    parser.add_argument("--quiet", action="store_true", help="Write files without printing to stdout")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)

    as_of = None
    if args.as_of:
        try:
            as_of = datetime.strptime(args.as_of, "%Y-%m-%d").date()
        except ValueError:
            print("--as-of must be an ISO date, e.g. 2026-09-06", file=sys.stderr)
            return 2

    try:
        inputs = collect_inputs(args.input)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if not inputs:
        print("No claim files found in {}".format(args.input), file=sys.stderr)
        return 2

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    failures = 0
    for path in inputs:
        try:
            claim = load_claim(path)
        except ClaimFormatError as exc:
            print("SKIPPED {}: {}".format(path.name, exc), file=sys.stderr)
            failures += 1
            continue

        result = triage_claim(claim, as_of=as_of)
        report = render_report(result, as_of=as_of)

        (outdir / "{}.json".format(result["claim_id"])).write_text(
            json.dumps(result, indent=2), encoding="utf-8"
        )
        (outdir / "{}.txt".format(result["claim_id"])).write_text(report, encoding="utf-8")

        if args.quiet:
            continue
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(report)
        if len(inputs) > 1:
            print()

    if not args.quiet:
        print("Processed {} claim(s). Output written to {}/".format(len(inputs) - failures, outdir))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
