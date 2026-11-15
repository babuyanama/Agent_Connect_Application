import os, sys, tempfile, time, unittest
d = tempfile.mkdtemp()
os.environ["AGENTNET_DB"] = os.path.join(d, "t.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agentnet import core, seed, moderation  # noqa: E402
from agentnet.db import db  # noqa: E402
from agentnet.errors import ApiError  # noqa: E402

seed.seed()


def agent(slug): return core.authenticate(seed.DEMO_KEY(slug))


class T(unittest.TestCase):
    def delivered(self, post):
        return {r["name"] for r in db.q("SELECT a.name FROM notifications n JOIN agents a ON a.id=n.agent_id WHERE n.post_id=? AND n.kind='post'", (post["id"],))}

    def test_a_swiggy_diwali(self):
        p = core.create_post(agent("swiggy"), {"text": "5% off all deliveries on Diwali.", "kind": "offer"})
        core.route_q.get_nowait(); core.route(p["id"])
        got = self.delivered(p)
        print("Swiggy Diwali ->", got)
        self.assertIn("PhonePe Agent", got); self.assertIn("HDFC Bank Agent", got)
        self.assertNotIn("Bajaj Auto Agent", got); self.assertNotIn("Zerodha Agent", got)

    def test_a2_rbi_post(self):
        p = core.create_post(agent("zerodha"), {"text": "RBI issues new circular on UPI transaction limits and bank compliance.", "kind": "market_update"})
        core.route_q.get_nowait(); core.route(p["id"])
        got = self.delivered(p); print("RBI ->", got)
        self.assertIn("PhonePe Agent", got); self.assertNotIn("Bajaj Auto Agent", got)

    def test_z_feedback_moves_threshold_and_affinity(self):
        a = agent("hdfc"); before = core.get_agent(a["id"])["min_score"]
        for i in range(10):
            p = core.create_post(agent("swiggy"), {"text": f"Swiggy dining offer number {i} on food delivery discount", "kind": "offer"})
            core.route_q.get_nowait(); core.route(p["id"])
            n = db.one("SELECT id FROM notifications WHERE post_id=? AND agent_id=?", (p["id"], a["id"]))
            if n: core.record_outcome(n["id"], "irrelevant")
        self.assertGreater(core.get_agent(a["id"])["min_score"], before)

    def test_b_moderation(self):
        with self.assertRaises(ApiError): core.create_post(agent("swiggy"), {"text": "Please share your OTP to claim", "kind": "offer"})
        p = core.create_post(agent("swiggy"), {"text": "99% off everything today only!!", "kind": "offer"})
        self.assertEqual(p["status"], "held")
        u = core.register_agent({"name": "Shady", "company": "Shady Co", "description": "An unverified agent for testing"})
        p = core.create_post(core.authenticate(u["api_key"]), {"text": "Free recharge for everyone today", "kind": "offer"})
        self.assertEqual(p["status"], "held")

    def test_c_comment_marks_engaged(self):
        p = core.create_post(agent("swiggy"), {"text": "Weekend biryani festival deals on food delivery", "kind": "offer"})
        core.route_q.get_nowait(); core.route(p["id"])
        pp = agent("phonepe")
        core.create_comment(pp, p["id"], {"text": "Cashback with UPI!"})
        n = db.one("SELECT status FROM notifications WHERE post_id=? AND agent_id=? AND kind='post'", (p["id"], pp["id"]))
        self.assertEqual(n["status"], "engaged")


if __name__ == "__main__":
    unittest.main(verbosity=2)
