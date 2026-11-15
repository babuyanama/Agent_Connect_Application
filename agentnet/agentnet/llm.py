"""LLM access.  Without ANTHROPIC_API_KEY the platform uses a rule-based 'mock brain'
(see brain.py) so the whole demo runs offline."""
import json, os, urllib.request

API_KEY = os.environ.get("ANTHROPIC_API_KEY")
MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5")


def enabled():
    return bool(API_KEY)


def _call(system, user, max_tokens=300):
    body = json.dumps({"model": MODEL, "max_tokens": max_tokens, "system": system,
                       "messages": [{"role": "user", "content": user}]}).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=body,
        headers={"content-type": "application/json", "x-api-key": API_KEY,
                 "anthropic-version": "2023-06-01"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read())
    return "".join(b.get("text", "") for b in data["content"] if b.get("type") == "text")


def _json(text):
    text = text.strip()
    s, e = text.find("{"), text.rfind("}")
    return json.loads(text[s:e + 1])


def decide(agent, post, author_name):
    """Ask the LLM whether this agent should engage and, if so, to draft the comment."""
    system = (f"You are the social agent for {agent['company']}. About you: {agent['description']} "
              "You see posts from other companies' agents. Engage ONLY if you can add concrete value "
              "(a complementary offer, a clarification, a partnership angle). Never invent offers "
              "you are not authorised to make; if unsure, do not engage. "
              'Reply with JSON only: {"engage": true|false, "comment": "<max 280 chars>"}')
    user = f"Post by {author_name}: {post['text']}"
    try:
        out = _json(_call(system, user))
        return bool(out.get("engage")), str(out.get("comment", ""))[:400]
    except Exception as e:  # network / parse failure -> fall back to mock brain
        print("[llm] decide failed:", e)
        return None


def judge(agent, post):
    """Optional re-rank for borderline matches. Returns 0..1 or None."""
    system = ("Rate how relevant a post is to a company's agent on a 0..1 scale. "
              'Reply with JSON only: {"relevance": <float>}')
    user = f"Company: {agent['company']}. {agent['description']}\nPost: {post['text']}"
    try:
        return float(_json(_call(system, user, 60))["relevance"])
    except Exception:
        return None
