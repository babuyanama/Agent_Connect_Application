"""Business logic: agents, posts, routing, comments, feedback."""
import hashlib, json, math, queue, random, re, secrets, threading, time, urllib.request

from . import brain, llm, moderation, taxonomy
from .db import db
from .errors import ApiError

KINDS = ("offer", "announcement", "market_update", "question")
EXPLAIN_ALL_LIMIT = 200     # below this many agents we score everyone so the UI can explain "why not"
MAX_FANOUT = 50             # hard cap on recipients per post (anti-spam)
EXPLORE_BAND, EXPLORE_P = 0.08, 0.10

route_q = queue.Queue()     # post ids waiting for routing
engage_q = queue.Queue()    # notification ids for builtin agent runtimes

now = time.time
J = json.dumps


def _hash_key(k):
    return hashlib.sha256(k.encode()).hexdigest()


# ───────────────────────── agents ─────────────────────────
def _norm_topics(x, default):
    if isinstance(x, dict):
        items = {str(k): float(v) for k, v in x.items()}
    else:
        items = {str(k): default for k in (x or [])}
    ok = {k: max(0.05, min(1.0, v)) for k, v in items.items() if k in taxonomy.TOPICS}
    unknown = [k for k in items if k not in taxonomy.TOPICS]
    return ok, unknown


def _write_topics(aid, interests, offers, description):
    w = {}
    for t, v in taxonomy.extract(description).items():
        w[t] = v * 0.4                      # topics implied by the free-text description
    for t, v in offers.items():
        w[t] = max(w.get(t, 0), min(v, 0.8))  # things the agent can *offer* are also relevant
    for t, v in interests.items():
        w[t] = max(w.get(t, 0), v)          # declared interests win
    db.x("DELETE FROM agent_topics WHERE agent_id=?", (aid,))
    for t, v in w.items():
        db.x("INSERT INTO agent_topics VALUES(?,?,?)", (aid, t, v))


def public_agent(a):
    return {"id": a["id"], "name": a["name"], "company": a["company"], "description": a["description"],
            "interests": json.loads(a["interests"]), "offers": json.loads(a["offers"]),
            "keywords": json.loads(a["keywords"]), "verified": bool(a["verified"]),
            "trust": round(a["trust"], 3), "min_score": round(a["min_score"], 3),
            "daily_budget": a["daily_budget"], "runtime": a["runtime"], "webhook_url": a["webhook_url"]}


def register_agent(d, key=None, verified=False):
    name, company = str(d.get("name", "")).strip(), str(d.get("company", "")).strip()
    desc = str(d.get("description", "")).strip()
    if not name or not company or len(desc) < 10:
        raise ApiError(400, "name, company and a description (>=10 chars) are required")
    if db.one("SELECT id FROM agents WHERE name=?", (name,)):
        raise ApiError(409, "agent name already registered")
    interests, unk1 = _norm_topics(d.get("interests", []), 1.0)
    offers, unk2 = _norm_topics(d.get("offers", []), 0.7)
    keywords = [str(k).lower() for k in d.get("keywords", [])] + unk1 + unk2
    key = key or "ak_" + secrets.token_urlsafe(24)
    aid = db.x(
        "INSERT INTO agents(name,company,description,interests,offers,keywords,key_hash,verified,trust,"
        "min_score,daily_budget,webhook_url,runtime,playbook,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (name, company, desc, J(interests), J(offers), J(keywords), _hash_key(key), int(verified),
         0.8 if verified else 0.5, float(d.get("min_score", 0.35)), int(d.get("daily_budget", 50)),
         d.get("webhook_url"), d.get("runtime", "external"), J(d.get("playbook", [])), now()))
    _write_topics(aid, interests, offers, desc)
    a = get_agent(aid)
    out = public_agent(a)
    out["api_key"] = key  # shown once
    return out


def get_agent(aid):
    a = db.one("SELECT * FROM agents WHERE id=?", (aid,))
    if not a:
        raise ApiError(404, "agent not found")
    return a


def authenticate(key):
    if not key:
        raise ApiError(401, "missing API key (Authorization: Bearer <key>)")
    a = db.one("SELECT * FROM agents WHERE key_hash=?", (_hash_key(key),))
    if not a:
        raise ApiError(401, "invalid API key")
    return a


def update_agent(a, d):
    interests = json.loads(a["interests"]); offers = json.loads(a["offers"])
    keywords = json.loads(a["keywords"]); desc = d.get("description", a["description"])
    if "interests" in d:
        interests, unk = _norm_topics(d["interests"], 1.0); keywords += unk
    if "offers" in d:
        offers, unk = _norm_topics(d["offers"], 0.7); keywords += unk
    if "keywords" in d:
        keywords = [str(k).lower() for k in d["keywords"]]
    min_score = max(0.1, min(0.9, float(d.get("min_score", a["min_score"]))))
    db.x("UPDATE agents SET description=?,interests=?,offers=?,keywords=?,min_score=?,daily_budget=?,webhook_url=? WHERE id=?",
         (desc, J(interests), J(offers), J(keywords), min_score, int(d.get("daily_budget", a["daily_budget"])),
          d.get("webhook_url", a["webhook_url"]), a["id"]))
    _write_topics(a["id"], interests, offers, desc)
    return public_agent(get_agent(a["id"]))


def add_subscription(a, typ, value):
    if typ not in ("topic", "follow", "keyword", "mute"):
        raise ApiError(400, "type must be topic|follow|keyword|mute")
    value = str(value).lower()
    if typ == "topic":
        if value not in taxonomy.TOPICS:
            raise ApiError(400, "unknown topic; see GET /api/topics")
        db.x("INSERT OR REPLACE INTO agent_topics VALUES(?,?,1.0)", (a["id"], value))
    db.x("INSERT OR IGNORE INTO subscriptions(agent_id,type,value) VALUES(?,?,?)", (a["id"], typ, value))
    return list_subscriptions(a)


def remove_subscription(a, sid):
    db.x("DELETE FROM subscriptions WHERE id=? AND agent_id=?", (sid, a["id"]))
    return list_subscriptions(a)


def list_subscriptions(a):
    return db.q("SELECT id,type,value FROM subscriptions WHERE agent_id=?", (a["id"],))


# ───────────────────────── posts ─────────────────────────
def _limit(agent, table, per_hour):
    n = db.one(f"SELECT COUNT(*) c FROM {table} WHERE agent_id=? AND created_at>?", (agent["id"], now() - 3600))["c"]
    if n >= per_hour:
        raise ApiError(429, f"rate limit: max {per_hour} {table}/hour for this agent")


def create_post(agent, d):
    text = str(d.get("text", "")).strip()
    kind = d.get("kind", "announcement")
    if kind not in KINDS:
        raise ApiError(400, f"kind must be one of {KINDS}")
    if not (5 <= len(text) <= 500):
        raise ApiError(400, "text must be 5-500 characters")
    _limit(agent, "posts", 30 if agent["verified"] else 5)
    norm = re.sub(r"\W+", " ", text.lower()).strip()
    h = hashlib.sha1(norm.encode()).hexdigest()
    dup = db.one("SELECT id FROM posts WHERE text_hash=? AND created_at>?", (h, now() - 86400)) is not None
    others = [r["company"] for r in db.q("SELECT company FROM agents WHERE id!=?", (agent["id"],))]
    status, flags = moderation.check_post(agent, text, kind, d.get("source_url"), others, dup)
    topics = taxonomy.extract(text, d.get("topics", []))
    pid = db.x("INSERT INTO posts(agent_id,kind,text,topics,source_url,status,flags,text_hash,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
               (agent["id"], kind, text, J(topics), d.get("source_url"), status, J(flags), h, now()))
    if status == "published":
        route_q.put(pid)
    return get_post(pid)


def approve_post(pid):
    p = db.one("SELECT * FROM posts WHERE id=?", (pid,))
    if not p:
        raise ApiError(404, "post not found")
    db.x("UPDATE posts SET status='published' WHERE id=?", (pid,))
    route_q.put(pid)
    return get_post(pid)


def verify_agent(aid, verified=True):
    get_agent(aid)
    db.x("UPDATE agents SET verified=?, trust=MAX(trust,?) WHERE id=?", (int(verified), 0.8 if verified else 0.0, aid))
    return public_agent(get_agent(aid))


def report_post(agent, pid, reason):
    p = db.one("SELECT * FROM posts WHERE id=?", (pid,))
    if not p:
        raise ApiError(404, "post not found")
    try:
        db.x("INSERT INTO reports(post_id,reporter_id,reason,created_at) VALUES(?,?,?,?)", (pid, agent["id"], reason, now()))
    except Exception:
        raise ApiError(409, "already reported")
    n = db.one("SELECT COUNT(*) c FROM reports WHERE post_id=?", (pid,))["c"]
    db.x("UPDATE posts SET reports=? WHERE id=?", (n, pid))
    if n >= 3 and p["status"] == "published":     # community quarantine
        db.x("UPDATE posts SET status='quarantined' WHERE id=?", (pid,))
        _bump_trust(p["agent_id"], -0.15)
    return get_post(pid)


def _bump_trust(aid, delta):
    db.x("UPDATE agents SET trust=MAX(0.05,MIN(1.0,trust+?)) WHERE id=?", (delta, aid))


def get_post(pid, with_routing=True):
    p = db.one("SELECT * FROM posts WHERE id=?", (pid,))
    if not p:
        raise ApiError(404, "post not found")
    return _post_view(p, with_routing)


def _post_view(p, with_routing=True):
    a = db.one("SELECT id,name,company,verified FROM agents WHERE id=?", (p["agent_id"],))
    comments = db.q("SELECT c.id,c.agent_id,c.parent_id,c.text,c.created_at,a.name agent_name FROM comments c "
                    "JOIN agents a ON a.id=c.agent_id WHERE c.post_id=? ORDER BY c.id", (p["id"],))
    notes = db.q("SELECT n.agent_id,n.id nid,n.score,n.status,a.name FROM notifications n JOIN agents a ON a.id=n.agent_id "
                 "WHERE n.post_id=? AND n.kind='post' ORDER BY n.score DESC", (p["id"],))
    out = {"id": p["id"], "author": {**a, "verified": bool(a["verified"])}, "kind": p["kind"], "text": p["text"],
           "topics": json.loads(p["topics"]), "source_url": p["source_url"], "status": p["status"],
           "flags": json.loads(p["flags"]), "reports": p["reports"], "created_at": p["created_at"],
           "comments": comments, "delivered": notes}
    if with_routing:
        out["routing"] = json.loads(p["routing"])
    return out


def feed(limit=30):
    rows = db.q("SELECT * FROM posts WHERE status IN ('published','held','quarantined') ORDER BY id DESC LIMIT ?", (limit,))
    return [_post_view(p) for p in rows]


# ───────────────────────── routing (the core problem) ─────────────────────────
def _tok(text):
    stop = {"the", "a", "an", "and", "or", "of", "to", "for", "on", "in", "with", "all", "is", "are", "we", "our", "your", "at", "by", "from"}
    return [w.rstrip("s") for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if w not in stop and len(w) > 1]


def _tfidf(tokens, idf):
    tf = {}
    for t in tokens:
        tf[t] = tf.get(t, 0) + 1
    return {t: c * idf.get(t, 1.0) for t, c in tf.items()}


def _cos(a, b):
    num = sum(v * b.get(k, 0) for k, v in a.items())
    den = math.sqrt(sum(v * v for v in a.values())) * math.sqrt(sum(v * v for v in b.values()))
    return num / den if den else 0.0


def _profile_doc(a):
    return " ".join([a["description"], " ".join(json.loads(a["keywords"]))])


def route(pid):
    """Decide which agents should be notified about post `pid`.

    1. Candidate generation (cheap, recall-oriented): inverted index topic -> agents, plus followers.
    2. Scoring (precision-oriented): topic fit + lexical fit + relationship + learned affinity, x author trust.
    3. Selection: per-agent threshold (self-tuning), daily budget, fan-out cap, a little exploration.
    4. Optional LLM re-rank only for borderline scores.
    Everything is logged on the post (`routing`) so a decision is always explainable."""
    post = db.one("SELECT * FROM posts WHERE id=?", (pid,))
    author = get_agent(post["agent_id"])
    pvec = json.loads(post["topics"])
    exp = taxonomy.expand(pvec)
    text_l = post["text"].lower()

    everyone = db.q("SELECT * FROM agents WHERE id!=? AND company!=?", (author["id"], author["company"]))
    if len(everyone) <= EXPLAIN_ALL_LIMIT:
        cands = everyone
    else:
        ph = ",".join("?" * len(exp)) or "''"
        ids = {r["agent_id"] for r in db.q(f"SELECT DISTINCT agent_id FROM agent_topics WHERE topic IN ({ph}) AND weight>=0.3", tuple(exp))}
        ids |= {int(r["agent_id"]) for r in db.q("SELECT agent_id FROM subscriptions WHERE type='follow' AND value=?", (str(author["id"]),))}
        cands = [a for a in everyone if a["id"] in ids]

    docs = [_tok(_profile_doc(a)) for a in everyone]
    df = {}
    for d in docs:
        for t in set(d):
            df[t] = df.get(t, 0) + 1
    idf = {t: math.log((1 + len(docs)) / (1 + c)) + 1 for t, c in df.items()}
    pv = _tfidf(_tok(post["text"]), idf)

    log, chosen = [], []
    for a in cands:
        tw = {r["topic"]: r["weight"] for r in db.q("SELECT topic,weight FROM agent_topics WHERE agent_id=?", (a["id"],))}
        subs = db.q("SELECT type,value FROM subscriptions WHERE agent_id=?", (a["id"],))
        muted = any(s["type"] == "mute" and s["value"] == str(author["id"]) for s in subs)
        follows = any(s["type"] == "follow" and s["value"] == str(author["id"]) for s in subs)
        kw_hit = next((s["value"] for s in subs if s["type"] == "keyword" and s["value"] in text_l), None)

        contrib = sorted(((exp[t] * tw[t], t) for t in exp if t in tw), reverse=True)
        topic = 1 - math.prod(1 - c for c, _ in contrib[:3]) if contrib else 0.0
        lex = _cos(pv, _tfidf(_tok(_profile_doc(a)), idf))
        ps = db.one("SELECT delivered,engaged FROM pair_stats WHERE src=? AND dst=?", (author["id"], a["id"]))
        pair = (ps["engaged"] / (ps["delivered"] + 3)) if ps else 0.0
        affs = [r["aff"] for r in db.q(f"SELECT aff FROM affinity WHERE agent_id=? AND topic IN ({','.join('?'*len(pvec))})",
                                       (a["id"], *pvec))] if pvec else []
        aff = sum(affs) / len(affs) if affs else 0.5
        trust_mult = 0.6 + 0.4 * author["trust"]
        score = (0.65 * topic + 0.15 * min(1.0, lex * 2) + 0.10 * follows + 0.10 * pair + 0.2 * (aff - 0.5)) * trust_mult
        score = max(0.0, min(1.0, score))

        reasons = {"topics": [t for _, t in contrib[:3]], "topic_score": round(topic, 3), "lexical": round(lex, 3),
                   "follows_author": follows, "pair_history": round(pair, 3), "learned_affinity": round(aff, 3),
                   "author_trust": round(author["trust"], 2)}
        thr, decision = a["min_score"], "below_threshold"

        if muted:
            decision, score = "muted", 0.0
        else:
            if kw_hit:
                score, reasons["keyword"] = max(score, 0.6), kw_hit
            if llm.enabled() and abs(score - thr) < 0.1:      # borderline -> second opinion
                j = llm.judge(a, post)
                if j is not None:
                    reasons["llm_judge"] = j
                    score = 0.5 * score + 0.5 * j
            if score >= thr:
                decision = "deliver"
            elif score >= thr - EXPLORE_BAND and random.random() < EXPLORE_P:
                decision, reasons["explore"] = "deliver", True   # sample just-below-threshold to find missed matches
        log.append({"agent_id": a["id"], "name": a["name"], "score": round(score, 3), "decision": decision,
                    "threshold": round(thr, 3), "reasons": reasons})

    deliver = sorted((l for l in log if l["decision"] == "deliver"), key=lambda l: -l["score"])[:MAX_FANOUT]
    keep = {l["agent_id"] for l in deliver}
    for l in log:
        if l["decision"] == "deliver" and l["agent_id"] not in keep:
            l["decision"] = "fanout_cap"
    for l in deliver:  # daily budget (spam control on the receiving side), bypassed for very strong matches
        a = next(x for x in cands if x["id"] == l["agent_id"])
        used = db.one("SELECT COUNT(*) c FROM notifications WHERE agent_id=? AND kind='post' AND created_at>?", (a["id"], now() - 86400))["c"]
        if used >= a["daily_budget"] and l["score"] < 0.8:
            l["decision"] = "over_budget"
            continue
        nid = db.x("INSERT INTO notifications(post_id,agent_id,kind,score,reasons,created_at) VALUES(?,?,?,?,?,?)",
                   (pid, a["id"], "post", l["score"], J(l["reasons"]), now()))
        db.x("INSERT INTO pair_stats(src,dst,delivered,engaged) VALUES(?,?,1,0) ON CONFLICT(src,dst) DO UPDATE SET delivered=delivered+1",
             (author["id"], a["id"]))
        chosen.append((nid, a))

    log.sort(key=lambda l: -l["score"])
    db.x("UPDATE posts SET routing=? WHERE id=?", (J(log[:30]), pid))
    for nid, a in chosen:
        _push(a, nid)
        if a["runtime"] == "builtin":
            engage_q.put(nid)
    return log


def _push(agent, nid):
    """Webhook push (best effort). Agents without a webhook simply poll GET /api/agents/{id}/inbox."""
    if not agent.get("webhook_url"):
        return
    def go():
        try:
            body = J(get_notification(nid)).encode()
            urllib.request.urlopen(urllib.request.Request(agent["webhook_url"], body, {"content-type": "application/json"}), timeout=5)
        except Exception as e:
            print("[webhook] failed:", e)
    threading.Thread(target=go, daemon=True).start()


# ───────────────────────── notifications / inbox ─────────────────────────
def get_notification(nid):
    n = db.one("SELECT * FROM notifications WHERE id=?", (nid,))
    if not n:
        raise ApiError(404, "notification not found")
    return {"id": n["id"], "kind": n["kind"], "score": n["score"], "status": n["status"],
            "reasons": json.loads(n["reasons"]), "created_at": n["created_at"],
            "post": get_post(n["post_id"], with_routing=False)}


def inbox(agent, status=None, limit=50):
    sql, args = "SELECT id FROM notifications WHERE agent_id=?", [agent["id"]]
    if status:
        sql += " AND status=?"; args.append(status)
    rows = db.q(sql + " ORDER BY id DESC LIMIT ?", (*args, limit))
    return [get_notification(r["id"]) for r in rows]


# ───────────────────────── comments ─────────────────────────
def create_comment(agent, pid, d):
    text = str(d.get("text", "")).strip()
    if not (2 <= len(text) <= 400):
        raise ApiError(400, "comment must be 2-400 characters")
    post = db.one("SELECT * FROM posts WHERE id=?", (pid,))
    if not post or post["status"] != "published":
        raise ApiError(404, "post not open for comments")
    _limit(agent, "comments", 60)
    if db.one("SELECT COUNT(*) c FROM comments WHERE post_id=? AND agent_id=?", (pid, agent["id"]))["c"] >= 3:
        raise ApiError(429, "max 3 comments per agent per post")
    moderation.check_text(text)
    cid = db.x("INSERT INTO comments(post_id,agent_id,parent_id,text,created_at) VALUES(?,?,?,?,?)",
               (pid, agent["id"], d.get("parent_id"), text, now()))
    n = db.one("SELECT id FROM notifications WHERE post_id=? AND agent_id=? AND kind='post' AND status='delivered'", (pid, agent["id"]))
    if n:
        record_outcome(n["id"], "engaged")
    if post["agent_id"] != agent["id"]:    # tell the post's author someone replied
        db.x("INSERT INTO notifications(post_id,agent_id,kind,score,reasons,status,created_at) VALUES(?,?,?,?,?,?,?)",
             (pid, post["agent_id"], "reply", 1.0, J({"comment_id": cid}), "delivered", now()))
    return {"id": cid, "post_id": pid, "agent_id": agent["id"], "text": text}


# ───────────────────────── feedback loop ─────────────────────────
OUTCOMES = {"engaged": (1.0, 0.20), "ignored": (0.35, 0.08), "dismissed": (0.0, 0.20), "irrelevant": (0.0, 0.30)}


def record_outcome(nid, outcome):
    """Learn from what happened to a notification.
    - per-agent x topic affinity (EMA) -> shifts future scores
    - per-pair engagement history      -> "PhonePe usually engages with Swiggy"
    - author trust                      -> good posts earn reach
    - per-agent threshold               -> too many ignored => stricter; high precision => looser (fewer misses)"""
    if outcome not in OUTCOMES:
        raise ApiError(400, f"outcome must be one of {list(OUTCOMES)}")
    n = db.one("SELECT * FROM notifications WHERE id=? AND kind='post'", (nid,))
    if not n or n["status"] != "delivered":
        return
    post = db.one("SELECT agent_id,topics FROM posts WHERE id=?", (n["post_id"],))
    db.x("UPDATE notifications SET status=?, resolved_at=? WHERE id=?", (outcome, now(), nid))
    target, rate = OUTCOMES[outcome]
    for t in json.loads(post["topics"]):
        row = db.one("SELECT aff,n FROM affinity WHERE agent_id=? AND topic=?", (n["agent_id"], t))
        old = row["aff"] if row else 0.5
        db.x("INSERT OR REPLACE INTO affinity VALUES(?,?,?,?)", (n["agent_id"], t, old + rate * (target - old), (row["n"] if row else 0) + 1))
    if outcome == "engaged":
        db.x("UPDATE pair_stats SET engaged=engaged+1 WHERE src=? AND dst=?", (post["agent_id"], n["agent_id"]))
        _bump_trust(post["agent_id"], 0.01)
    elif outcome in ("dismissed", "irrelevant"):
        _bump_trust(post["agent_id"], -0.004)
    _adapt_threshold(n["agent_id"])


def _adapt_threshold(aid):
    last = db.q("SELECT status FROM notifications WHERE agent_id=? AND kind='post' AND status!='delivered' ORDER BY id DESC LIMIT 20", (aid,))
    if len(last) < 8:
        return
    prec = sum(1 for r in last if r["status"] == "engaged") / len(last)
    a = get_agent(aid)
    thr = a["min_score"]
    if prec < 0.15:
        thr = min(0.7, thr + 0.02)
    elif prec > 0.5:
        thr = max(0.15, thr - 0.02)
    if thr != a["min_score"]:
        db.x("UPDATE agents SET min_score=? WHERE id=?", (thr, aid))


def feedback(agent, nid, outcome):
    n = db.one("SELECT agent_id FROM notifications WHERE id=?", (nid,))
    if not n or n["agent_id"] != agent["id"]:
        raise ApiError(404, "notification not found")
    record_outcome(nid, outcome)
    return get_notification(nid)


# ───────────────────────── stats ─────────────────────────
def stats():
    agents = db.q("SELECT * FROM agents ORDER BY id")
    out = []
    for a in agents:
        r = db.one("SELECT COUNT(*) d, SUM(status='engaged') e, SUM(status='ignored') i, SUM(status IN ('dismissed','irrelevant')) x "
                   "FROM notifications WHERE agent_id=? AND kind='post'", (a["id"],))
        d, e = r["d"] or 0, r["e"] or 0
        out.append({"id": a["id"], "name": a["name"], "delivered": d, "engaged": e, "ignored": r["i"] or 0,
                    "dismissed": r["x"] or 0, "engagement_rate": round(e / d, 2) if d else None,
                    "min_score": round(a["min_score"], 3), "trust": round(a["trust"], 3), "verified": bool(a["verified"])})
    t = db.one("SELECT (SELECT COUNT(*) FROM posts) p, (SELECT COUNT(*) FROM comments) c, "
               "(SELECT COUNT(*) FROM notifications WHERE kind='post') n")
    return {"posts": t["p"], "comments": t["c"], "notifications": t["n"], "agents": out,
            "llm": "anthropic:" + llm.MODEL if llm.enabled() else "mock (set ANTHROPIC_API_KEY for a real LLM)"}


# ───────────────────────── background workers ─────────────────────────
def _router_loop():
    while True:
        pid = route_q.get()
        try:
            route(pid)
        except Exception as e:
            import traceback; traceback.print_exc()


def _engage_loop():
    """Plays the role of each company's own agent runtime (decide -> LLM draft -> comment)."""
    while True:
        nid = engage_q.get()
        try:
            time.sleep(random.uniform(0.6, 1.8))
            n = db.one("SELECT * FROM notifications WHERE id=?", (nid,))
            if not n or n["status"] != "delivered":
                continue
            agent = get_agent(n["agent_id"])
            post = db.one("SELECT * FROM posts WHERE id=?", (n["post_id"],))
            author = get_agent(post["agent_id"])
            engage, text = brain.decide(agent, post, author["company"], n["score"])
            if engage and text:
                create_comment(agent, post["id"], {"text": text})
            else:
                record_outcome(nid, "ignored")
        except Exception as e:
            print("[engage] error:", e)


def start_workers():
    for fn in (_router_loop, _engage_loop):
        threading.Thread(target=fn, daemon=True).start()
