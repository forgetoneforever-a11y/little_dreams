import sqlite3
from datetime import datetime
from config import DB_PATH


def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            username TEXT,
            full_name TEXT,
            status TEXT DEFAULT 'open',
            created_at TEXT,
            last_activity TEXT,
            last_message TEXT
        );

        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_id INTEGER NOT NULL,
            from_user_id INTEGER NOT NULL,
            is_admin INTEGER DEFAULT 0,
            text TEXT,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            full_name TEXT,
            first_seen TEXT,
            last_seen TEXT
        );
        """)


def _now():
    return datetime.utcnow().isoformat()


def upsert_user(user_id: int, username: str, full_name: str):
    with _conn() as conn:
        conn.execute("""
            INSERT INTO users (user_id, username, full_name, first_seen, last_seen)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username=excluded.username,
                full_name=excluded.full_name,
                last_seen=excluded.last_seen
        """, (user_id, username, full_name, _now(), _now()))


def all_user_ids():
    with _conn() as conn:
        return [r["user_id"] for r in conn.execute("SELECT user_id FROM users").fetchall()]


def get_open_ticket(user_id: int):
    with _conn() as conn:
        row = conn.execute("""
            SELECT * FROM tickets WHERE user_id=? AND status='open'
            ORDER BY id DESC LIMIT 1
        """, (user_id,)).fetchone()
        return dict(row) if row else None


def create_ticket(user_id: int, username: str, full_name: str, text: str) -> int:
    with _conn() as conn:
        cur = conn.execute("""
            INSERT INTO tickets (user_id, username, full_name, status, created_at, last_activity, last_message)
            VALUES (?, ?, ?, 'open', ?, ?, ?)
        """, (user_id, username, full_name, _now(), _now(), text))
        return cur.lastrowid


def update_ticket_activity(ticket_id: int, text: str):
    with _conn() as conn:
        conn.execute("""
            UPDATE tickets SET last_activity=?, last_message=? WHERE id=?
        """, (_now(), text, ticket_id))


def close_ticket(ticket_id: int):
    with _conn() as conn:
        conn.execute("UPDATE tickets SET status='closed' WHERE id=?", (ticket_id,))


def get_ticket(ticket_id: int):
    with _conn() as conn:
        row = conn.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
        return dict(row) if row else None


def get_open_tickets():
    with _conn() as conn:
        rows = conn.execute("""
            SELECT * FROM tickets WHERE status='open'
            ORDER BY last_activity DESC
        """).fetchall()
        return [dict(r) for r in rows]


def get_user_tickets(user_id: int, limit=5):
    with _conn() as conn:
        rows = conn.execute("""
            SELECT * FROM tickets WHERE user_id=?
            ORDER BY id DESC LIMIT ?
        """, (user_id, limit)).fetchall()
        return [dict(r) for r in rows]


def add_message(ticket_id: int, from_user_id: int, text: str, is_admin: bool = False):
    with _conn() as conn:
        conn.execute("""
            INSERT INTO messages (ticket_id, from_user_id, is_admin, text, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (ticket_id, from_user_id, 1 if is_admin else 0, text, _now()))


def get_stats():
    with _conn() as conn:
        total = conn.execute("SELECT COUNT(*) c FROM tickets").fetchone()["c"]
        today = datetime.utcnow().date().isoformat()
        today_count = conn.execute(
            "SELECT COUNT(*) c FROM tickets WHERE created_at LIKE ?",
            (today + "%",)
        ).fetchone()["c"]
        open_count = conn.execute(
            "SELECT COUNT(*) c FROM tickets WHERE status='open'"
        ).fetchone()["c"]

        top_users = conn.execute("""
            SELECT user_id, username, COUNT(*) c FROM tickets
            GROUP BY user_id ORDER BY c DESC LIMIT 5
        """).fetchall()

        rows = conn.execute("""
            SELECT t.id, t.created_at as t_start,
                   MIN(m.created_at) as first_admin_msg
            FROM tickets t
            JOIN messages m ON m.ticket_id = t.id AND m.is_admin = 1
            GROUP BY t.id
        """).fetchall()

    avg_minutes = None
    if rows:
        deltas = []
        for r in rows:
            try:
                start = datetime.fromisoformat(r["t_start"])
                end = datetime.fromisoformat(r["first_admin_msg"])
                deltas.append((end - start).total_seconds() / 60)
            except Exception:
                pass
        if deltas:
            avg_minutes = round(sum(deltas) / len(deltas), 1)

    return {
        "total": total,
        "today": today_count,
        "open": open_count,
        "avg_minutes": avg_minutes,
        "top_users": [dict(u) for u in top_users],
    }
