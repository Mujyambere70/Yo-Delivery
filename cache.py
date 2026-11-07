"""
cache.py - version 6 (mixin class used by Store): a cache that several server processes share.

Some answers are slow to compute and change seldom: the neighbours of a product in the learned model (a cosine
over every product vector), the cross-validation of the chat model, the numbers of the monitoring page. They are
kept for a short time (ttl seconds):

    layer 1  a dictionary in the memory of the process (fastest, but every Gunicorn worker has its own)
    layer 2  the table cache_entries in the shop database (shared by all processes and machines)
Values are JSON. A "generation" per name space (settings key cache_gen:<space>) makes a whole space old at once:
cache_bump("model") after the model is trained again, and every process starts reading the new answers.

Version 7: when REDIS_URL is set (and the redis package is installed), layer 2 is a managed Redis (for example
Upstash or Fly's Redis) instead of the database table: faster, and it forgets old answers by itself (SETEX). If Redis
does not answer, the cache falls back to the table, so the shop never stops because of the cache.
"""

import json
import threading
import time
from datetime import datetime, timedelta

CACHE_SCHEMA = """
CREATE TABLE IF NOT EXISTS cache_entries (
    key TEXT PRIMARY KEY, value TEXT NOT NULL, expires_at TEXT NOT NULL
);
"""

_LOCAL = {}
_LOCK = threading.Lock()
LOCAL_MAX = 2000
STATS = {"hits": 0, "shared_hits": 0, "misses": 0}


class CacheMixin:
    _redis = None

    def cache_backend(self):
        """'redis' or 'database' (layer 2)."""
        return "redis" if self._redis_client() is not None else "database"

    def _redis_client(self):
        if self._redis is False:
            return None
        if self._redis is None:
            url = self.env.get("REDIS_URL", "")
            if not url:
                self._redis = False
                return None
            try:
                import redis                               # optional: pip install redis
                self._redis = redis.Redis.from_url(url, socket_timeout=0.5, socket_connect_timeout=0.5)
            except Exception:
                self._redis = False
                return None
        return self._redis

    def _shared_get(self, key):
        r = self._redis_client()
        if r is not None:
            try:
                v = r.get("yo:" + key)
                return None if v is None else json.loads(v)
            except Exception:                              # Redis is down: use the table
                pass
        with self.db() as conn:
            row = conn.execute("SELECT value, expires_at FROM cache_entries WHERE key = ?", (key,)).fetchone()
        if row and row["expires_at"] > datetime.now().strftime("%Y-%m-%d %H:%M:%S"):
            return json.loads(row["value"])
        return None

    def _shared_set(self, key, value, ttl):
        text = json.dumps(value, ensure_ascii=False, default=str)
        r = self._redis_client()
        if r is not None:
            try:
                r.setex("yo:" + key, int(ttl), text)
                return
            except Exception:
                pass
        exp = (datetime.now() + timedelta(seconds=ttl)).strftime("%Y-%m-%d %H:%M:%S")
        with self.db() as conn:
            conn.execute("INSERT INTO cache_entries (key, value, expires_at) VALUES (?, ?, ?) ON CONFLICT (key) "
                         "DO UPDATE SET value = excluded.value, expires_at = excluded.expires_at", (key, text, exp))

    def _cache_gen(self, space):
        with self.db() as conn:
            r = conn.execute("SELECT value FROM settings WHERE key = ?", (f"cache_gen:{space}",)).fetchone()
        return r[0] if r else "0"

    def cache_bump(self, space, conn=None):
        """Every answer of this name space is old now (in every process)."""
        if conn is not None:
            return self._bump(conn, space)
        with self.db() as c:
            return self._bump(c, space)

    @staticmethod
    def _bump(conn, space):
        conn.execute("INSERT INTO settings (key, value) VALUES (?, '1') ON CONFLICT (key) DO UPDATE SET "
                     "value = CAST(CAST(settings.value AS INTEGER) + 1 AS TEXT)", (f"cache_gen:{space}",))

    def cached(self, space, key, ttl, compute):
        """The answer of compute() for (space, key), kept ttl seconds."""
        full = f"{space}:{self._cache_gen(space)}:{key}"
        now_t = time.time()
        with _LOCK:
            hit = _LOCAL.get(full)
            if hit and hit[0] > now_t:
                STATS["hits"] += 1
                return hit[1]
        shared = self._shared_get(full)
        if shared is not None:
            value = shared
            STATS["shared_hits"] += 1
        else:
            value = compute()
            STATS["misses"] += 1
            self._shared_set(full, value, ttl)
        with _LOCK:
            if len(_LOCAL) >= LOCAL_MAX:
                _LOCAL.clear()
            _LOCAL[full] = (now_t + min(ttl, 60), value)            # the memory layer keeps it at most 1 minute
        return value

    def cache_clean(self):
        """Hourly: old entries are deleted."""
        with self.db() as conn:
            return conn.execute("DELETE FROM cache_entries WHERE expires_at < ?",
                                (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),)).rowcount

    def cache_numbers(self):
        with self.db() as conn:
            n = conn.execute("SELECT COUNT(*) FROM cache_entries").fetchone()[0]
        total = STATS["hits"] + STATS["shared_hits"] + STATS["misses"]
        return {"entries": n, **STATS, "backend": self.cache_backend(),
                "hit_rate": round(100 * (STATS["hits"] + STATS["shared_hits"]) / total) if total else None}
