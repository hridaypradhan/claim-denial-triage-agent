"""Read denial codes and payer profiles from the frozen baseline tables.

Baseline logic stays unchanged. These lookups only add the explicit behavior the
agent needs: an unknown code or payer is reported, never filled with a default.
"""

from __future__ import annotations

from typing import Dict, Optional, Sequence

from baseline.triage import reference

DENIAL_CODE_SOURCE_ID = "REF-DENIAL-CODES@v1"
PAYER_PROFILE_SOURCE_ID = "REF-PAYER-RULES@v1"

_PROFILE_VERSION = "baseline-v1"
_PROFILE_VERIFICATION = "ILLUSTRATIVE"
_UNKNOWN_GROUP_NAME = "Unknown group code"


def find_denial_code(
    carc_code: str,
    rarc_codes: Sequence[str],
    group_code: str,
) -> Optional[Dict[str, object]]:
    """Return what the tables say about one denial, or None for an unknown CARC.

    An unknown group or RARC does not hide a known CARC. It stays visible in
    unknown_codes so the agent cannot treat it as understood.
    """

    carc_entry = reference.carc(carc_code)

    if carc_entry is None:
        return None

    unknown_codes = []
    remark_entries = []

    for rarc_code in rarc_codes:
        rarc_entry = reference.rarc(rarc_code)

        if rarc_entry is None:
            unknown_codes.append(f"RARC:{rarc_code.upper()}")
            continue

        remark_entries.append(
            {
                "code": rarc_code.upper(),
                "description": rarc_entry["description"],
                "missing_element": rarc_entry["missing_element"],
            }
        )

    group_name = reference.group_name(group_code)

    if group_name == _UNKNOWN_GROUP_NAME:
        unknown_codes.append(f"GROUP:{group_code.upper()}")
        group_name = None

    return {
        "carc": {
            "code": carc_code,
            "description": carc_entry["description"],
            "category": carc_entry["category"],
            "specificity": carc_entry["specificity"],
        },
        "group": {"code": group_code.upper(), "name": group_name},
        "rarc": remark_entries,
        "identified_defects": [
            entry["missing_element"] for entry in remark_entries
        ],
        "unknown_codes": unknown_codes,
    }


def find_payer_profile(payer_id: str) -> Optional[Dict[str, object]]:
    """Return one payer's profile, or None when the payer is not listed.

    The baseline falls back to a default profile for unlisted payers. The tool
    does not, because a guessed filing window could cost a real deadline.
    """

    profile = reference.payer_rules(payer_id)

    if not profile["is_known_payer"]:
        return None

    return {
        "payer_id": payer_id.upper(),
        "display_name": profile["display_name"],
        "appeal_window_days": profile["appeal_window_days"],
        "corrected_claim_window_days": profile["corrected_claim_window_days"],
        "retro_auth_window_days": profile["retro_auth_window_days"],
        "first_level_appeal": profile["first_level_appeal"],
        "submission_channel": profile["submission_channel"],
        "profile_version": _PROFILE_VERSION,
        "verification_status": _PROFILE_VERIFICATION,
    }
