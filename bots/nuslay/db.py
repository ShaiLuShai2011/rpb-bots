import os

import aiosqlite

DB_PATH = os.getenv("DB_PATH", "/data/nuslay.db" if os.path.isdir("/data") else "nuslay.db")


class Database:
    def __init__(self, path: str = DB_PATH):
        self.path = path
        self.db = None

    async def init(self) -> None:
        self.db = await aiosqlite.connect(self.path)
        self.db.row_factory = aiosqlite.Row
        await self.db.execute("PRAGMA foreign_keys = ON")
        await self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS users(
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                full_name TEXT,
                banned INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS nominations(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                emoji TEXT DEFAULT '🏆',
                description TEXT DEFAULT '',
                active INTEGER DEFAULT 1,
                position INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS nominees(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nomination_id INTEGER NOT NULL REFERENCES nominations(id) ON DELETE CASCADE,
                title TEXT NOT NULL,
                added_by INTEGER,
                is_custom INTEGER DEFAULT 0,
                approved INTEGER DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now')),
                UNIQUE(nomination_id, title)
            );
            CREATE TABLE IF NOT EXISTS votes(
                user_id INTEGER NOT NULL,
                nomination_id INTEGER NOT NULL REFERENCES nominations(id) ON DELETE CASCADE,
                nominee_id INTEGER NOT NULL REFERENCES nominees(id) ON DELETE CASCADE,
                updated_at TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (user_id, nomination_id)
            );
            CREATE TABLE IF NOT EXISTS settings(
                key TEXT PRIMARY KEY,
                value TEXT
            );
            """
        )
        await self.db.commit()

    async def close(self) -> None:
        if self.db:
            await self.db.close()

    async def upsert_user(self, user_id: int, username, full_name) -> None:
        await self.db.execute(
            "INSERT INTO users(user_id, username, full_name) VALUES(?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET username=excluded.username, full_name=excluded.full_name",
            (user_id, username, full_name),
        )
        await self.db.commit()

    async def get_user(self, user_id: int):
        cur = await self.db.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
        return await cur.fetchone()

    async def set_banned(self, user_id: int, banned: bool) -> None:
        await self.db.execute(
            "UPDATE users SET banned=? WHERE user_id=?", (int(banned), user_id)
        )
        await self.db.commit()

    async def all_user_ids(self):
        cur = await self.db.execute("SELECT user_id FROM users WHERE banned=0")
        return [r[0] for r in await cur.fetchall()]

    async def users_count(self) -> int:
        cur = await self.db.execute("SELECT COUNT(*) FROM users")
        return (await cur.fetchone())[0]

    async def get_stage(self) -> str:
        cur = await self.db.execute("SELECT value FROM settings WHERE key='stage'")
        row = await cur.fetchone()
        return row[0] if row else "suggestions"

    async def get_setting(self, key: str, default=None):
        cur = await self.db.execute("SELECT value FROM settings WHERE key=?", (key,))
        row = await cur.fetchone()
        return row[0] if row else default

    async def set_setting(self, key: str, value) -> None:
        await self.db.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )
        await self.db.commit()

    async def set_stage(self, stage: str) -> None:
        await self.db.execute(
            "INSERT INTO settings(key, value) VALUES('stage', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (stage,),
        )
        await self.db.commit()

    async def create_nomination(self, title: str, emoji: str, description: str = "") -> int:
        cur = await self.db.execute(
            "INSERT INTO nominations(title, emoji, description) VALUES(?,?,?)",
            (title, emoji or "🏆", description or ""),
        )
        await self.db.commit()
        return cur.lastrowid

    async def update_nomination(self, nomination_id: int, **fields) -> None:
        allowed = {"title", "emoji", "description", "active"}
        sets, vals = [], []
        for key, value in fields.items():
            if key in allowed:
                sets.append(f"{key}=?")
                vals.append(value)
        if not sets:
            return
        vals.append(nomination_id)
        await self.db.execute(
            f"UPDATE nominations SET {', '.join(sets)} WHERE id=?", vals
        )
        await self.db.commit()

    async def delete_nomination(self, nomination_id: int) -> None:
        await self.db.execute("DELETE FROM nominations WHERE id=?", (nomination_id,))
        await self.db.commit()

    async def get_nomination(self, nomination_id: int):
        cur = await self.db.execute("SELECT * FROM nominations WHERE id=?", (nomination_id,))
        return await cur.fetchone()

    async def get_nominations(self, active_only: bool = True):
        query = "SELECT * FROM nominations"
        if active_only:
            query += " WHERE active=1"
        query += " ORDER BY position, id"
        cur = await self.db.execute(query)
        return await cur.fetchall()

    async def nominations_count(self) -> int:
        cur = await self.db.execute("SELECT COUNT(*) FROM nominations")
        return (await cur.fetchone())[0]

    async def add_nominee(self, nomination_id: int, title: str, added_by=None,
                          is_custom: bool = False, approved: bool = True):
        try:
            cur = await self.db.execute(
                "INSERT INTO nominees(nomination_id, title, added_by, is_custom, approved) "
                "VALUES(?,?,?,?,?)",
                (nomination_id, title, added_by, int(is_custom), int(approved)),
            )
            await self.db.commit()
            return cur.lastrowid
        except aiosqlite.IntegrityError:
            return None

    async def delete_nominee(self, nominee_id: int) -> None:
        await self.db.execute("DELETE FROM nominees WHERE id=?", (nominee_id,))
        await self.db.commit()

    async def get_nominee(self, nominee_id: int):
        cur = await self.db.execute("SELECT * FROM nominees WHERE id=?", (nominee_id,))
        return await cur.fetchone()

    async def set_nominee_approved(self, nominee_id: int, approved: bool = True) -> None:
        await self.db.execute(
            "UPDATE nominees SET approved=? WHERE id=?", (int(approved), nominee_id)
        )
        await self.db.commit()

    async def get_nominees(self, nomination_id: int, approved_only: bool = True):
        query = "SELECT * FROM nominees WHERE nomination_id=?"
        if approved_only:
            query += " AND approved=1"
        query += " ORDER BY approved, id"
        cur = await self.db.execute(query, (nomination_id,))
        return await cur.fetchall()

    async def vote(self, user_id: int, nomination_id: int, nominee_id: int) -> None:
        await self.db.execute(
            "INSERT INTO votes(user_id, nomination_id, nominee_id) VALUES(?,?,?) "
            "ON CONFLICT(user_id, nomination_id) DO UPDATE SET "
            "nominee_id=excluded.nominee_id, updated_at=datetime('now')",
            (user_id, nomination_id, nominee_id),
        )
        await self.db.commit()

    async def get_vote(self, user_id: int, nomination_id: int):
        cur = await self.db.execute(
            "SELECT nominee_id FROM votes WHERE user_id=? AND nomination_id=?",
            (user_id, nomination_id),
        )
        row = await cur.fetchone()
        return row[0] if row else None

    async def voted_nominations(self, user_id: int):
        cur = await self.db.execute(
            "SELECT nomination_id FROM votes WHERE user_id=?", (user_id,)
        )
        return {r[0] for r in await cur.fetchall()}

    async def votes_count(self, nomination_id=None) -> int:
        if nomination_id is None:
            cur = await self.db.execute("SELECT COUNT(*) FROM votes")
        else:
            cur = await self.db.execute(
                "SELECT COUNT(*) FROM votes WHERE nomination_id=?", (nomination_id,)
            )
        return (await cur.fetchone())[0]

    async def nomination_results(self, nomination_id: int):
        cur = await self.db.execute(
            "SELECT c.id, c.title, COUNT(v.user_id) AS cnt "
            "FROM nominees c LEFT JOIN votes v ON v.nominee_id=c.id "
            "WHERE c.nomination_id=? AND c.approved=1 "
            "GROUP BY c.id ORDER BY cnt DESC, c.id",
            (nomination_id,),
        )
        return await cur.fetchall()

    async def my_nominee(self, nomination_id: int, user_id: int):
        cur = await self.db.execute(
            "SELECT * FROM nominees WHERE nomination_id=? AND added_by=? AND is_custom=1 "
            "ORDER BY id DESC LIMIT 1",
            (nomination_id, user_id),
        )
        return await cur.fetchone()

    async def my_suggestions(self, user_id: int):
        cur = await self.db.execute(
            "SELECT DISTINCT nomination_id FROM nominees WHERE added_by=? AND is_custom=1",
            (user_id,),
        )
        return {r[0] for r in await cur.fetchall()}

    async def export_votes(self):
        cur = await self.db.execute(
            "SELECT v.user_id, u.username, n.title, c.title, v.updated_at "
            "FROM votes v "
            "JOIN users u ON u.user_id=v.user_id "
            "JOIN nominations n ON n.id=v.nomination_id "
            "JOIN nominees c ON c.id=v.nominee_id "
            "ORDER BY n.id, v.updated_at"
        )
        return await cur.fetchall()


db = Database()
