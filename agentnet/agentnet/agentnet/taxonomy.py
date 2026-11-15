"""Shared vocabulary ("what is this post about?").

Posts and agents are both mapped onto the same topic space.  Keyword extraction
is the cheap, explainable first layer; swap `extract()` for an embedding / LLM
classifier in production and keep everything downstream unchanged.
"""
import re

TOPICS = {
    "payments": ["upi", "payment*", "pay", "paying", "wallet*", "qr", "cashback", "transaction*",
                 "checkout", "net banking", "neft", "imps", "bnpl"],
    "cards": ["card*", "credit card", "debit card", "no-cost emi", "reward points"],
    "banking": ["bank*", "deposit*", "savings", "fixed deposit", "fd", "account*", "interest rate*"],
    "lending": ["loan*", "emi", "mortgage", "financing", "credit score", "interest rate*"],
    "offers": ["offer*", "discount*", "off", "cashback", "deal*", "sale", "coupon*", "voucher*", "reward*"],
    "festive": ["diwali", "dussehra", "navratri", "holi", "eid", "christmas", "festiv*", "onam",
                "pongal", "big billion", "new year"],
    "food_delivery": ["swiggy", "zomato", "food", "restaurant*", "meal*", "deliver*", "instamart", "dining"],
    "ecommerce": ["flipkart", "amazon", "shopping", "cart", "e-commerce", "ecommerce", "marketplace",
                  "seller*", "product*", "sale"],
    "logistics": ["shipping", "courier", "warehouse*", "supply chain", "fulfil*", "last-mile"],
    "auto": ["bike*", "motorcycle*", "scooter*", "vehicle*", "car", "cars", "dealership*", "test ride",
             "showroom", "bajaj"],
    "ev": ["ev", "evs", "electric", "charging", "battery"],
    "travel": ["flight*", "hotel*", "travel*", "booking*", "holiday*", "trip*", "airline*"],
    "markets": ["stock*", "shares", "sensex", "nifty", "mutual fund*", "ipo", "trading", "market*"],
    "regulation": ["rbi", "sebi", "npci", "regulat*", "compliance", "circular", "repo rate"],
    "insurance": ["insur*", "premium*", "claim*"],
    "electronics": ["phone*", "laptop*", "smartphone*", "electronics", "gadget*"],
}

# ADJ[a][b] = "if a post is about a, how relevant is it to someone who cares about b".
# This is the co-marketing / ecosystem graph: an offer is relevant to payments and cards even
# though the post never says "UPI".  Could be learned from engagement data over time.
ADJ = {
    "offers": {"payments": 0.50, "cards": 0.45, "ecommerce": 0.30},
    "food_delivery": {"payments": 0.45, "offers": 0.30, "logistics": 0.25},
    "festive": {"offers": 0.50, "ecommerce": 0.35, "payments": 0.30, "travel": 0.30, "auto": 0.30},
    "ecommerce": {"payments": 0.50, "cards": 0.40, "logistics": 0.50, "electronics": 0.30},
    "auto": {"ev": 0.50, "lending": 0.40},
    "ev": {"auto": 0.60},
    "lending": {"banking": 0.60, "cards": 0.30},
    "cards": {"banking": 0.60, "payments": 0.50},
    "payments": {"banking": 0.40, "cards": 0.40},
    "markets": {"banking": 0.30, "regulation": 0.40},
    "travel": {"payments": 0.30, "cards": 0.40, "insurance": 0.30},
    "regulation": {"banking": 0.60, "payments": 0.60, "markets": 0.50, "lending": 0.50},
}


def _compile(kw):
    if kw.endswith("*"):
        return re.compile(r"\b" + re.escape(kw[:-1]) + r"\w*")
    return re.compile(r"\b" + re.escape(kw) + r"\b")


_PATTERNS = {t: [_compile(k) for k in kws] for t, kws in TOPICS.items()}


def extract(text, explicit=()):
    """text -> {topic: weight in (0,1]}"""
    text = (text or "").lower()
    vec = {}
    for topic, pats in _PATTERNS.items():
        hits = sum(1 for p in pats if p.search(text))
        if hits:
            vec[topic] = min(1.0, 0.45 + 0.2 * hits)
    for t in explicit or ():
        if t in TOPICS:
            vec[t] = 1.0
    return vec


def expand(vec):
    """One-hop expansion through the ecosystem graph."""
    out = dict(vec)
    for t, w in vec.items():
        for t2, a in ADJ.get(t, {}).items():
            out[t2] = max(out.get(t2, 0.0), w * a)
    return out
