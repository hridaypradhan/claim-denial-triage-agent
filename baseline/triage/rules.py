from datetime import date, timedelta

from . import reference

DISTINCT_SERVICE_MODIFIERS = {"59", "XE", "XS", "XP", "XU", "25"}
CLINICAL_DOCS = {
    "clinical_note",
    "operative_report",
    "therapy_plan_of_care",
    "medical_records",
    "progress_notes",
}

DISPOSITION_PRIORITY = [
    "APPEAL_WITH_DOCUMENTATION",
    "CORRECT_AND_RESUBMIT",
    "ROUTE_TO_CORRECT_PAYER",
    "MANUAL_REVIEW",
    "BILL_PATIENT",
    "WRITE_OFF",
    "NO_ACTION_CONTRACTUAL",
]
RECOVERABLE = {"APPEAL_WITH_DOCUMENTATION", "CORRECT_AND_RESUBMIT", "ROUTE_TO_CORRECT_PAYER"}


def _authorization(claim, denial, line, rules):
    auth = claim.encounter.get("prior_auth_number")
    if auth:
        return (
            "CORRECT_AND_RESUBMIT",
            [
                "Authorization {} is on file but was not carried on the submitted claim.".format(auth),
                "Resubmit as a corrected claim (frequency code 7) with the auth number in the 2300 REF*G1 segment.",
            ],
            [],
            "Auth exists in the encounter record, so this is a transmission gap rather than a true auth failure.",
        )

    days_since_service = (claim.remit_date - claim.date_of_service).days
    if rules["retro_auth_window_days"] and days_since_service <= rules["retro_auth_window_days"]:
        return (
            "APPEAL_WITH_DOCUMENTATION",
            [
                "Request retroactive authorization; {} allows {} days from the date of service.".format(
                    rules["display_name"], rules["retro_auth_window_days"]
                ),
            ],
            ["clinical_note", "urgency_justification"],
            "No auth on file, but the payer's retroactive authorization window is still open.",
        )

    return (
        "MANUAL_REVIEW",
        [
            "No authorization on file and the retroactive window has closed.",
            "Confirm whether the service was emergent or auth-exempt before choosing between appeal and write-off.",
        ],
        ["clinical_note", "auth_requirement_grid"],
        "The outcome depends on facts the claim record does not carry, so a human should decide.",
    )


def _coding(claim, denial, line, rules):
    missing = []
    for code in denial.rarc:
        entry = reference.rarc(code)
        if entry and entry["missing_element"] != "none":
            missing.append((code, entry["missing_element"], entry["description"]))

    if denial.carc == "16" and not missing:
        return (
            "MANUAL_REVIEW",
            [
                "CO-16 was returned without a remark code, so the missing element is not identified.",
                "Pull the full 835 remittance or call the payer to isolate the rejected field.",
            ],
            ["835_remittance"],
            "The denial is real but unactionable as received; the code pair does not name a field.",
        )

    actions = ["Correct {} ({}: {})".format(element, code, desc) for code, element, desc in missing]

    if denial.carc == "4":
        modifiers = line.get("modifiers", [])
        actions.append(
            "Review modifier usage on CPT {} (submitted: {}) against payer policy.".format(
                line.get("cpt", "n/a"), ", ".join(modifiers) if modifiers else "none"
            )
        )
    if denial.carc == "11":
        actions.append(
            "Re-review the encounter note for a diagnosis that supports CPT {}; submitted: {}.".format(
                line.get("cpt", "n/a"), ", ".join(line.get("icd10", [])) or "none"
            )
        )

    actions.append("Resubmit as a corrected claim (frequency code 7), not as a new claim.")
    return (
        "CORRECT_AND_RESUBMIT",
        actions,
        [element for _, element, _ in missing],
        "The payer named a specific defect on the claim, so this is fixable without an appeal.",
    )


def _medical_necessity(claim, denial, line, rules):
    attached = claim.encounter.get("documentation_attached", [])
    on_file = [d for d in attached if str(d).lower() in CLINICAL_DOCS]
    evidence = [] if on_file else ["clinical_note", "medical_records"]

    actions = [
        "Locate the {} medical policy for CPT {} and map the chart findings to its coverage criteria.".format(
            rules["display_name"], line.get("cpt", "n/a")
        )
    ]
    if denial.carc == "151":
        actions.append(
            "Frequency denial: document why {} units were medically required in this period.".format(
                line.get("units", "the billed")
            )
        )
    if on_file:
        actions.append("Attach {} to the appeal.".format(", ".join(on_file)))
    else:
        actions.append("Retrieve supporting documentation from the chart before filing.")
    actions.append("File a {} via {}.".format(rules["first_level_appeal"], rules["submission_channel"]))

    return (
        "APPEAL_WITH_DOCUMENTATION",
        actions,
        evidence,
        "Medical necessity denials are overturned on clinical evidence, not on claim corrections.",
    )


def _bundling(claim, denial, line, rules):
    modifiers = set(line.get("modifiers", []))
    distinct = modifiers & DISTINCT_SERVICE_MODIFIERS
    if distinct:
        return (
            "APPEAL_WITH_DOCUMENTATION",
            [
                "Modifier {} was already appended and the payer bundled the line anyway.".format(
                    ", ".join(sorted(distinct))
                ),
                "Appeal with documentation showing the services were separately identifiable.",
            ],
            ["operative_report", "clinical_note"],
            "The distinct-service claim was already made on the claim, so the dispute is factual.",
        )
    return (
        "CORRECT_AND_RESUBMIT",
        [
            "Check the NCCI procedure-to-procedure edit for CPT {} against the other lines on this claim.".format(
                line.get("cpt", "n/a")
            ),
            "If the services were distinct and documented, append the appropriate 59 or X-series modifier and resubmit.",
        ],
        ["ncci_edit_lookup"],
        "No distinct-service modifier was submitted, so the edit fired as designed.",
    )


def _duplicate(claim, denial, line, rules):
    return (
        "MANUAL_REVIEW",
        [
            "Compare against previously adjudicated claims for {} and CPT {}.".format(
                claim.date_of_service, line.get("cpt", "n/a")
            ),
            "If the service genuinely repeated, resubmit with modifier 76 or 77 and supporting documentation.",
        ],
        ["prior_claim_history"],
        "Duplicate logic needs claim history that the record does not carry.",
    )


def _timely_filing(claim, denial, line, rules):
    if claim.encounter.get("proof_of_timely_filing"):
        return (
            "APPEAL_WITH_DOCUMENTATION",
            [
                "Appeal with the clearinghouse acceptance report showing the original submission date.",
                "File via {}.".format(rules["submission_channel"]),
            ],
            ["clearinghouse_acceptance_report"],
            "Timely filing denials are reversible only with proof of the original submission.",
        )
    return (
        "WRITE_OFF",
        [
            "No proof of timely filing is available.",
            "Post as a provider write-off; a CO-group adjustment cannot be transferred to the patient.",
        ],
        [],
        "Nothing is recoverable without submission evidence, and the balance is not patient responsibility.",
    )


def _eligibility(claim, denial, line, rules):
    alternate = claim.encounter.get("alternate_coverage")
    if alternate:
        return (
            "ROUTE_TO_CORRECT_PAYER",
            [
                "Coverage terminated before {}; rebill {} as primary.".format(claim.date_of_service, alternate),
            ],
            ["updated_insurance_card"],
            "Another active plan is recorded on the encounter.",
        )
    return (
        "BILL_PATIENT",
        [
            "Re-verify coverage for the date of service.",
            "If no active plan is found, transfer the balance to the patient with a coverage-termination notice.",
        ],
        ["eligibility_response"],
        "Coverage had lapsed, so the balance becomes patient responsibility.",
    )


def _cob(claim, denial, line, rules):
    if claim.encounter.get("primary_eob_on_file"):
        return (
            "CORRECT_AND_RESUBMIT",
            ["Resubmit with the primary payer's EOB attached in the coordination-of-benefits loop."],
            [],
            "The primary remittance exists and only needs to accompany the claim.",
        )
    return (
        "ROUTE_TO_CORRECT_PAYER",
        [
            "Confirm the coordination-of-benefits order with the patient or the payer.",
            "Bill the primary payer first, then submit this claim as secondary with the primary EOB.",
        ],
        ["primary_eob", "cob_confirmation"],
        "This payer is not primary for the date of service.",
    )


def _non_covered(claim, denial, line, rules):
    if claim.encounter.get("abn_signed") or denial.group_code == "PR":
        return (
            "BILL_PATIENT",
            ["Transfer the balance to the patient; a signed advance notice or patient-responsibility adjustment applies."],
            [],
            "The service is excluded from the plan and the patient was placed on notice.",
        )
    return (
        "WRITE_OFF",
        [
            "The service is excluded from the plan and no signed waiver is on file.",
            "Write off, and add this CPT to the pre-service waiver checklist.",
        ],
        [],
        "Without an advance notice on file the balance cannot be moved to the patient.",
    )


def _contractual(claim, denial, line, rules):
    return (
        "NO_ACTION_CONTRACTUAL",
        [
            "Post the contractual adjustment.",
            "Spot-check the allowed amount against the contracted fee schedule; escalate only if underpaid.",
        ],
        [],
        "Not a denial. This is the negotiated write-down between the billed and allowed amounts.",
    )


HANDLERS = {
    "AUTHORIZATION": _authorization,
    "CODING": _coding,
    "MEDICAL_NECESSITY": _medical_necessity,
    "BUNDLING_NCCI": _bundling,
    "DUPLICATE": _duplicate,
    "TIMELY_FILING": _timely_filing,
    "REGISTRATION_ELIGIBILITY": _eligibility,
    "COORDINATION_OF_BENEFITS": _cob,
    "NON_COVERED_BENEFIT": _non_covered,
    "CONTRACTUAL": _contractual,
}


def _confidence(ref, denial, disposition):
    if ref is None or disposition == "MANUAL_REVIEW":
        return "low"
    if ref.get("needs_rarc") and not denial.rarc:
        return "low"
    if ref.get("specificity") == "high":
        return "high"
    return "medium"


def _assess(claim, denial, rules, as_of):
    ref = reference.carc(denial.carc)
    line = claim.line(denial.service_line)

    if ref is None:
        category = "UNKNOWN"
        description = "CARC {} is not in the local code set.".format(denial.carc)
        disposition = "MANUAL_REVIEW"
        actions = ["Look up the code in the current X12 CARC list and route to an A/R specialist."]
        evidence = ["carc_lookup"]
        rationale = "Unmapped denial code; the baseline will not guess."
    else:
        category = ref["category"]
        description = ref["description"]
        disposition, actions, evidence, rationale = HANDLERS[category](claim, denial, line, rules)

    if denial.group_code == "PR" and disposition in ("WRITE_OFF", "NO_ACTION_CONTRACTUAL"):
        disposition = "BILL_PATIENT"
        actions = ["Patient-responsibility adjustment; move the balance to the patient statement."]
        rationale = "The PR group code assigns this balance to the patient."

    deadline = None
    days_remaining = None
    if disposition == "APPEAL_WITH_DOCUMENTATION":
        deadline = claim.remit_date + timedelta(days=rules["appeal_window_days"])
    elif disposition in ("CORRECT_AND_RESUBMIT", "ROUTE_TO_CORRECT_PAYER"):
        deadline = claim.date_of_service + timedelta(days=rules["corrected_claim_window_days"])

    if deadline:
        days_remaining = (deadline - as_of).days
        if days_remaining < 0:
            actions = [
                "Filing deadline passed on {}.".format(deadline.isoformat()),
                "Escalate to the A/R lead for a payer exception request or write-off approval.",
            ] + actions
            disposition = "MANUAL_REVIEW"

    return {
        "service_line": denial.service_line,
        "cpt": line.get("cpt"),
        "group_code": denial.group_code,
        "group_meaning": reference.group_name(denial.group_code),
        "carc": denial.carc,
        "rarc": denial.rarc,
        "amount": round(denial.amount, 2),
        "root_cause": category,
        "denial_description": description,
        "disposition": disposition,
        "appealable": disposition == "APPEAL_WITH_DOCUMENTATION",
        "deadline": deadline.isoformat() if deadline else None,
        "days_remaining": days_remaining,
        "required_actions": actions,
        "evidence_needed": evidence,
        "rationale": rationale,
        "confidence": _confidence(ref, denial, disposition),
    }


def triage_claim(claim, as_of=None):
    as_of = as_of or date.today()
    rules = reference.payer_rules(claim.payer)
    findings = [_assess(claim, denial, rules, as_of) for denial in claim.denials]

    ordered = sorted(findings, key=lambda f: DISPOSITION_PRIORITY.index(f["disposition"]))
    recoverable = round(sum(f["amount"] for f in findings if f["disposition"] in RECOVERABLE), 2)

    warnings = []
    if not rules["is_known_payer"]:
        warnings.append(
            "Payer {} is not in the contract matrix; default filing windows were used.".format(claim.payer)
        )
    if any(f["confidence"] == "low" for f in findings):
        warnings.append("One or more findings are low confidence and need a human reviewer.")

    return {
        "claim_id": claim.claim_id,
        "patient_account": claim.patient_account,
        "payer": claim.payer,
        "payer_display": rules["display_name"],
        "date_of_service": claim.date_of_service.isoformat(),
        "remit_date": claim.remit_date.isoformat(),
        "evaluated_on": as_of.isoformat(),
        "billed_amount": round(claim.billed_amount, 2),
        "paid_amount": round(claim.paid_amount, 2),
        "denied_amount": claim.denied_amount,
        "documentation_on_file": list(claim.encounter.get("documentation_attached", [])),
        "recoverable_amount": recoverable,
        "primary_disposition": ordered[0]["disposition"],
        "primary_root_cause": ordered[0]["root_cause"],
        "findings": findings,
        "warnings": warnings,
    }
