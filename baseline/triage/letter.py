import textwrap
from datetime import date

from . import reference

ARGUMENTS = {
    "MEDICAL_NECESSITY": (
        "The service was ordered and performed to address a documented clinical presentation. "
        "The enclosed record establishes the indication, the findings, and the treatment decision, "
        "and we ask that it be reviewed against the plan's coverage criteria for this procedure."
    ),
    "AUTHORIZATION": (
        "Authorization was obtained for this service prior to delivery. "
        "We are enclosing the authorization record and ask that the claim be reprocessed against it."
    ),
    "TIMELY_FILING": (
        "The claim was submitted within the filing window. "
        "The enclosed clearinghouse acceptance report shows the original transmission date and payer receipt."
    ),
    "BUNDLING_NCCI": (
        "The service was distinct from the other procedures billed on this encounter and was reported with the "
        "appropriate distinct-service modifier. The enclosed documentation describes the separate site, session, "
        "and clinical purpose."
    ),
    "CODING": (
        "The coding on the submitted claim reflects the service documented in the record. "
        "The enclosed documentation supports the code combination as billed."
    ),
}

WIDTH = 78


def _wrap(text):
    return textwrap.wrap(text, width=WIDTH)


DEFAULT_ARGUMENT = (
    "We believe this denial was issued in error and ask that the claim be reopened and reprocessed. "
    "Supporting documentation is enclosed."
)


def _appealable(result):
    return [f for f in result["findings"] if f["appealable"]]


def build_appeal_letter(result, as_of=None):
    findings = _appealable(result)
    if not findings:
        return None

    as_of = as_of or date.today()
    rules = reference.payer_rules(result["payer"])
    level = rules["first_level_appeal"]

    lines = [
        "[PRACTICE LETTERHEAD]",
        "",
        as_of.strftime("%B %d, %Y"),
        "",
        rules["display_name"],
        "Attn: {} Unit".format(level),
        "",
        "RE: Request for {}".format(level),
        "",
        "    Claim ID:          {}".format(result["claim_id"]),
        "    Patient Account:   {}".format(result["patient_account"] or "n/a"),
        "    Date of Service:   {}".format(result["date_of_service"]),
        "    Remittance Date:   {}".format(result["remit_date"]),
        "    Billed Amount:     ${:,.2f}".format(result["billed_amount"]),
        "    Amount in Dispute: ${:,.2f}".format(sum(f["amount"] for f in findings)),
        "",
        "To the review team,",
        "",
    ]

    for finding in findings:
        lines.extend(_wrap(
            "The remittance dated {} returned {}-{} on service line {} (CPT {}): \"{}\"".format(
                result["remit_date"],
                finding["group_code"],
                finding["carc"],
                finding["service_line"],
                finding["cpt"] or "n/a",
                finding["denial_description"],
            )
        ))
        lines.append("")
        lines.extend(_wrap(ARGUMENTS.get(finding["root_cause"], DEFAULT_ARGUMENT)))
        lines.append("")

    enclosures = sorted(
        {item for f in findings for item in f["evidence_needed"]}
        | set(result.get("documentation_on_file", []))
    ) or ["clinical_documentation"]
    lines.append("Enclosures:")
    lines.extend("  - {}".format(item.replace("_", " ")) for item in enclosures)
    lines.extend(
        [
            "",
        ]
    )
    lines.extend(
        _wrap(
            "Please reprocess the claim and issue a corrected remittance. "
            "The billing office is available for any additional information required."
        )
    )
    lines.extend(
        [
            "",
            "Sincerely,",
            "",
            "[NAME], [TITLE]",
            "[PRACTICE NAME] - Revenue Cycle",
            "",
            "-- DRAFT: requires biller review and signature before submission --",
        ]
    )
    return "\n".join(lines)
