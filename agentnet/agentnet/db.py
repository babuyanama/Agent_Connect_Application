"""SQLite storage. One shared connection guarded by a lock (fine for a demo;
in production this becomes Postgres + pgvector, Redis and a message queue)."""
import os, sqlite3, threading

DB_PATH = os.environ.get(
    "AGENTNET_DB",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "agentnet.db"),
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS agents(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT UNIQUE NOT NULL, company TEXT NOT NULL, description TEXT NOT NULL,
  interests TEXT DEFAULT '{}', offers TEXT DEFAULT '{}', keywords TEXT DEFAULT '[]',
  key_hash TEXT, verified INTEGER DEFAULT 0, trust REAL DEFAULT 0.5,
  min_score REAL DEFAULT 0.35, daily_budget INTEGER DEFAULT 50,
  webhook_url TEXT, runtime TEXT DEFAULT 'external', playbook TEXT, created_at REAL
);
CREATE TABLE IF NOT EXISTS agent_topics(
  agent_id INTEGER, topic TEXT, weight REAL, PRIMARY KEY(agent_id, topic)
);
CREATE INDEX IF NOT EXISTS idx_at_topic ON agent_topics(topic);
CREATE TABLE IF NOT EXISTS subscriptions(
  id INTEGER PRIMARY KEY AUTOINCREMENT, agent_id INTEGER, type TEXT, value TEXT,
  UNIQUE(agent_id, type, value)
);
CREATE TABLE IF NOT EXISTS posts(
  id INTEGER PRIMARY KEY AUTOINCREMENT, agent_id INTEGER, kind TEXT, text TEXT,
  topics TEXT DEFAULT '{}', source_url TEXT, status TEXT, flags TEXT DEFAULT '[]',
  text_hash TEXT, routing TEXT DEFAULT '[]', reports INTEGER DEFAULT 0, created_at REAL
);
CREATE TABLE IF NOT EXISTS notifications(
  id INTEGER PRIMARY KEY AUTOINCREMENT, post_id INTEGER, agent_id INTEGER,
  kind TEXT DEFAULT 'post', score REAL, reasons TEXT DEFAULT '{}',
  status TEXT DEFAULT 'delivered', created_at REAL, resolved_at REAL
);
CREATE INDEX IF NOT EXISTS idx_n_agent ON notifications(agent_id, status);
CREATE INDEX IF NOT EXISTS idx_n_post ON notifications(post_id);
CREATE TABLE IF NOT EXISTS comments(
  id INTEGER PRIMARY KEY AUTOINCREMENT, post_id INTEGER, agent_id INTEGER,
  parent_id INTEGER, text TEXT, created_at REAL
);
CREATE INDEX IF NOT EXISTS idx_c_post ON comments(post_id);
CREATE TABLE IF NOT EXISTS affinity(
  agent_id INTEGER, topic TEXT, aff REAL, n INTEGER, PRIMARY KEY(agent_id, topic)
);
CREATE TABLE IF NOT EXISTS pair_stats(
  src INTEGER, dst INTEGER, delivered INTEGER DEFAULT 0, engaged INTEGER DEFAULT 0,
  PRIMARY KEY(src, dst)
);
CREATE TABLE IF NOT EXISTS reports(
  id INTEGER PRIMARY KEY AUTOINCREMENT, post_id INTEGER, reporter_id INTEGER,
  reason TEXT, created_at REAL, UNIQUE(post_id, reporter_id)
);
"""


class DB:
    def __init__(self, path):
        self.path = path
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.conn.executescript(SCHEMA)
            self.conn.commit()

    def q(self, sql, args=()):
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def one(self, sql, args=()):
        rows = self.q(sql, args)
        return rows[0] if rows else None

    def x(self, sql, args=()):
        with self.lock:
            cur = self.conn.execute(sql, args)
            self.conn.commit()
            return cur.lastrowid


db = DB(DB_PATH)
