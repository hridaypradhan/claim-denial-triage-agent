import json
from functools import lru_cache
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


@lru_cache(maxsize=None)
def _load(filename):
    with open(DATA_DIR / filename, encoding="utf-8") as fh:
        return json.load(fh)


def carc(code):
    return _load("denial_codes.json")["carc"].get(str(code))


def rarc(code):
    return _load("denial_codes.json")["rarc"].get(str(code).upper())


def group_name(code):
    return _load("denial_codes.json")["groups"].get(str(code).upper(), "Unknown group code")


def payer_rules(payer_key):
    rules = _load("payer_rules.json")
    merged = dict(rules["default"])
    merged.update(rules["payers"].get(str(payer_key).upper(), {}))
    merged["is_known_payer"] = str(payer_key).upper() in rules["payers"]
    return merged
