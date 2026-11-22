# Frozen deterministic baseline

This directory preserves the dependency-free rules system used as
`baseline-v1` in both project evaluations. It validates synthetic claim JSON,
maps a working subset of denial codes, applies deterministic routing and
deadline rules, and writes human-readable and structured results.

The baseline is intentionally unchanged so comparisons remain meaningful. It
does not retrieve policies or clinical notes, call a language model, or replace
human review.

Run its regression suite from the repository root:

```bash
python -m unittest discover -s baseline/tests -p "test*.py" -q
```

Examples and expected outcomes are documented in
[`examples/readme.md`](examples/readme.md). For the current agentic system, see
the [project README](../README.md).
