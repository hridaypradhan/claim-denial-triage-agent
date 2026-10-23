import textwrap

from .letter import build_appeal_letter

WIDTH = 78


def _rule(char="="):
    return char * WIDTH


def _field(label, value):
    prefix = "{:<14}: ".format(label)
    return textwrap.wrap(prefix + str(value), width=WIDTH, subsequent_indent=" " * len(prefix))


def _numbered(index, text):
    prefix = "  {}. ".format(index)
    return textwrap.wrap(prefix + text, width=WIDTH, subsequent_indent=" " * len(prefix))


def _deadline_text(finding):
    if not finding["deadline"]:
        return "no filing deadline applies"
    days = finding["days_remaining"]
    if days is None:
        return finding["deadline"]
    if days < 0:
        return "{} (EXPIRED {} days ago)".format(finding["deadline"], abs(days))
    return "{} ({} days remaining)".format(finding["deadline"], days)


def _denial_code(finding):
    code = "{}-{}".format(finding["group_code"], finding["carc"])
    if finding["rarc"]:
        code += "  RARC " + ", ".join(finding["rarc"])
    return code


def _render_finding(index, finding):
    lines = [
        "",
        _rule("-"),
        "FINDING {} - line {} - CPT {} - ${:,.2f}".format(
            index, finding["service_line"], finding["cpt"] or "n/a", finding["amount"]
        ),
        _rule("-"),
    ]
    lines += _field("Denial code", _denial_code(finding))
    lines += _field("Group meaning", finding["group_meaning"])
    lines += _field("Payer says", finding["denial_description"])
    lines += _field("Root cause", finding["root_cause"])
    lines += _field(
        "Disposition", "{}  (confidence: {})".format(finding["disposition"], finding["confidence"])
    )
    lines += _field("Deadline", _deadline_text(finding))
    lines += _field("Why", finding["rationale"])
    lines += ["", "Required actions:"]

    for number, action in enumerate(finding["required_actions"], start=1):
        lines += _numbered(number, action)

    if finding["evidence_needed"]:
        lines += textwrap.wrap(
            "Evidence to gather: " + ", ".join(finding["evidence_needed"]),
            width=WIDTH,
            subsequent_indent="  ",
        )
    return lines


def render_report(result, as_of=None):
    lines = [
        _rule(),
        "CLAIM DENIAL TRIAGE REPORT",
        _rule(),
        "Claim ID          : {}".format(result["claim_id"]),
        "Patient Account   : {}".format(result["patient_account"] or "n/a"),
        "Payer             : {} ({})".format(result["payer_display"], result["payer"]),
        "Date of Service   : {}".format(result["date_of_service"]),
        "Remittance Date   : {}".format(result["remit_date"]),
        "Evaluated On      : {}".format(result["evaluated_on"]),
        "",
        "Billed ${:,.2f}   Paid ${:,.2f}   Denied ${:,.2f}   Recoverable ${:,.2f}".format(
            result["billed_amount"],
            result["paid_amount"],
            result["denied_amount"],
            result["recoverable_amount"],
        ),
        "",
        "PRIMARY DISPOSITION : {}".format(result["primary_disposition"]),
        "PRIMARY ROOT CAUSE  : {}".format(result["primary_root_cause"]),
    ]

    for index, finding in enumerate(result["findings"], start=1):
        lines += _render_finding(index, finding)

    if result["warnings"]:
        lines += ["", _rule("-"), "WARNINGS", _rule("-")]
        for warning in result["warnings"]:
            lines += textwrap.wrap("  ! " + warning, width=WIDTH, subsequent_indent="    ")

    letter = build_appeal_letter(result, as_of=as_of)
    if letter:
        lines += ["", _rule(), "DRAFT APPEAL LETTER", _rule(), "", letter]

    lines += ["", _rule(), "End of report. Human review required before any payer submission.", _rule()]
    return "\n".join(lines)
