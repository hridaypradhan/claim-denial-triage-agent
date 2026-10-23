import json
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

REQUIRED_FIELDS = ["claim_id", "payer", "date_of_service", "remit_date", "denials"]


class ClaimFormatError(ValueError):
    pass


def _parse_date(value, label):
    if value in (None, ""):
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        raise ClaimFormatError(f"{label} must be an ISO date (YYYY-MM-DD), got {value!r}")


@dataclass
class Denial:
    group_code: str
    carc: str
    rarc: list = field(default_factory=list)
    amount: float = 0.0
    service_line: int = 1


@dataclass
class Claim:
    claim_id: str
    payer: str
    date_of_service: date
    remit_date: date
    billed_amount: float
    paid_amount: float
    denials: list
    service_lines: list
    encounter: dict
    patient_account: str = ""

    @property
    def denied_amount(self):
        return round(sum(d.amount for d in self.denials), 2)

    def line(self, number):
        for entry in self.service_lines:
            if entry.get("line") == number:
                return entry
        return {}


def load_claim(path):
    path = Path(path)
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except json.JSONDecodeError as exc:
        raise ClaimFormatError(f"{path.name} is not valid JSON: {exc}")

    missing = [key for key in REQUIRED_FIELDS if key not in raw]
    if missing:
        raise ClaimFormatError(f"{path.name} is missing required field(s): {', '.join(missing)}")
    if not raw["denials"]:
        raise ClaimFormatError(f"{path.name} has an empty denials list; nothing to triage")

    denials = []
    for entry in raw["denials"]:
        if "carc" not in entry:
            raise ClaimFormatError(f"{path.name} has a denial entry without a carc code")
        denials.append(
            Denial(
                group_code=str(entry.get("group_code", "CO")).upper(),
                carc=str(entry["carc"]),
                rarc=[str(code).upper() for code in entry.get("rarc", [])],
                amount=float(entry.get("amount", 0.0)),
                service_line=int(entry.get("service_line", 1)),
            )
        )

    return Claim(
        claim_id=raw["claim_id"],
        payer=str(raw["payer"]).upper(),
        date_of_service=_parse_date(raw["date_of_service"], "date_of_service"),
        remit_date=_parse_date(raw["remit_date"], "remit_date"),
        billed_amount=float(raw.get("billed_amount", 0.0)),
        paid_amount=float(raw.get("paid_amount", 0.0)),
        denials=denials,
        service_lines=raw.get("service_lines", []),
        encounter=raw.get("encounter", {}),
        patient_account=raw.get("patient_account", ""),
    )
