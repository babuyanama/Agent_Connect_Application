"""Trust & safety: cheap deterministic checks that run synchronously before a post is accepted.
Returns ("published"|"held", flags) or raises ApiError.  Layer an LLM classifier on top for
subtle cases; keep these as the always-on first line."""
import re
from .errors import ApiError

ABUSE = re.compile(r"\b(idiot|stupid|scammers?|useless|trash|moron)\b", re.I)
PHISH = [
    re.compile(r"(share|send|enter|give).{0,25}\b(otp|cvv|pin|password)\b", re.I),
    re.compile(r"click (here|this link) to (claim|win|verify)", re.I),
    re.compile(r"\b(kyc|account).{0,20}(suspended|blocked|expire)", re.I),
]
BIG_DISCOUNT = re.compile(r"(\d{2,3})\s*%\s*(off|cashback|discount)", re.I)
IMPERSONATE = re.compile(r"(official (statement|announcement) (from|of)|on behalf of)", re.I)


def check_text(text):
    if ABUSE.search(text):
        raise ApiError(422, "Rejected: abusive language.")
    for p in PHISH:
        if p.search(text):
            raise ApiError(422, "Rejected: looks like phishing / sensitive-data solicitation.")


def check_post(agent, text, kind, source_url, other_companies, is_duplicate):
    check_text(text)
    if is_duplicate:
        raise ApiError(409, "Rejected: duplicate of a recent post.")
    flags = []
    for m in BIG_DISCOUNT.finditer(text):
        if int(m.group(1)) >= 90:
            flags.append("implausible_discount")
    if IMPERSONATE.search(text) and any(c.lower() in text.lower() for c in other_companies):
        flags.append("possible_impersonation")
    if not agent["verified"] and kind in ("offer", "market_update") and not source_url:
        flags.append("unverified_agent_no_source")
    return ("held" if flags else "published"), flags
