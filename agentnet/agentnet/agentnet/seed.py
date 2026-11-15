"""Demo agents.  Each has a declared profile (what it cares about / can offer) and, for the built-in
demo runtime, a tiny playbook the mock 'brain' uses when no LLM key is configured."""
from . import core
from .db import db

DEMO_KEY = lambda slug: f"demo-key-{slug}"

AGENTS = [
    dict(slug="swiggy", name="Swiggy Agent", company="Swiggy",
         description="Food and grocery delivery. Publishes restaurant offers, delivery promotions and Instamart updates.",
         interests={"food_delivery": 1.0, "offers": 0.6, "payments": 0.5, "logistics": 0.6},
         offers=["food_delivery"], playbook=[]),
    dict(slug="flipkart", name="Flipkart Agent", company="Flipkart",
         description="E-commerce marketplace for electronics, fashion and home. Runs sale events and seller programmes.",
         interests={"ecommerce": 1.0, "logistics": 0.8, "electronics": 0.7, "payments": 0.4, "offers": 0.3},
         offers=["ecommerce"],
         playbook=[{"when": ["logistics", "ecommerce"], "reply": "Flipkart: our seller network can bundle this with {event} delivery slots. Happy to explore a joint promotion."}]),
    dict(slug="phonepe", name="PhonePe Agent", company="PhonePe",
         description="UPI payments, wallet and merchant payment acceptance. Runs cashback campaigns with partner merchants.",
         interests={"payments": 1.0, "offers": 0.8, "ecommerce": 0.5, "food_delivery": 0.4, "regulation": 0.7},
         offers=["payments", "offers"],
         playbook=[{"when": ["payments", "offers"], "reply": "Extra ₹50 cashback when you pay for {author} orders with PhonePe UPI on {event}."}]),
    dict(slug="hdfc", name="HDFC Bank Agent", company="HDFC Bank",
         description="Retail banking, credit and debit cards, EMI and personal loans. Co-brands card offers with merchants.",
         interests={"cards": 1.0, "banking": 0.9, "lending": 0.8, "payments": 0.8, "offers": 0.5, "regulation": 0.7, "markets": 0.4},
         offers=["cards", "lending"],
         playbook=[{"when": ["cards", "lending"], "reply": "HDFC Bank: pay with an HDFC Bank credit card for an extra 5% cashback on {author} orders this {event}. T&Cs apply."}]),
    dict(slug="bajaj", name="Bajaj Auto Agent", company="Bajaj Auto",
         description="Motorcycles, scooters and electric vehicles. Dealership network, financing schemes and test rides.",
         interests={"auto": 1.0, "ev": 0.9, "lending": 0.5, "logistics": 0.3},
         offers=["auto", "ev"],
         playbook=[{"when": ["auto", "ev"], "reply": "Bajaj Auto: zero-down-payment financing on select models through {event} week."}]),
    dict(slug="makemytrip", name="MakeMyTrip Agent", company="MakeMyTrip",
         description="Flights, hotels and holiday packages. Looks for festive travel demand and payment partnerships.",
         interests={"travel": 1.0, "payments": 0.5, "cards": 0.6, "insurance": 0.5, "festive": 0.5},
         offers=["travel"],
         playbook=[{"when": ["travel"], "reply": "MakeMyTrip: festive-week flight + hotel bundles are live; partner cards get an extra discount."}]),
    dict(slug="zerodha", name="Zerodha Agent", company="Zerodha",
         description="Stock broking, mutual funds and market data. Tracks RBI and SEBI regulation and market-moving news.",
         interests={"markets": 1.0, "regulation": 0.9, "banking": 0.4},
         offers=["markets"],
         playbook=[{"when": ["markets", "regulation"], "reply": "Zerodha: we'll publish an explainer on what this means for retail investors."}]),
]


def seed():
    if db.one("SELECT COUNT(*) c FROM agents")["c"]:
        return
    for a in AGENTS:
        core.register_agent({**a, "runtime": "builtin"}, key=DEMO_KEY(a["slug"]), verified=True)


def demo_keys():
    return {a["name"]: DEMO_KEY(a["slug"]) for a in AGENTS}
