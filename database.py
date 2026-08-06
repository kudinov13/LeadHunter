"""SQLite база данных: схема и асинхронные операции."""
import aiosqlite
from datetime import datetime
from pathlib import Path
from config import DB_PATH


SCHEMA = """
CREATE TABLE IF NOT EXISTS chats (
    chat_id INTEGER PRIMARY KEY,
    chat_name TEXT NOT NULL,
    is_monitored INTEGER DEFAULT 1,
    last_read_message_id INTEGER DEFAULT 0,
    added_at TEXT DEFAULT (datetime('now')),
    schedule_cron TEXT,
    message_text TEXT,
    message_variants TEXT
);

CREATE TABLE IF NOT EXISTS messages_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    sender_id INTEGER NOT NULL,
    sender_name TEXT,
    message_text TEXT,
    message_id INTEGER NOT NULL,
    direction TEXT NOT NULL,
    filter_verdict TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    chat_name TEXT,
    sender_id INTEGER NOT NULL,
    sender_name TEXT,
    sender_username TEXT,
    message_text TEXT,
    category TEXT NOT NULL,
    task TEXT,
    budget TEXT,
    deadline TEXT,
    status TEXT DEFAULT 'NEW',
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (chat_id) REFERENCES chats(chat_id)
);

CREATE TABLE IF NOT EXISTS dialogs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id INTEGER NOT NULL,
    sender_id INTEGER NOT NULL,
    sender_name TEXT,
    sender_username TEXT,
    stage TEXT DEFAULT 'INITIATING',
    ai_messages_json TEXT DEFAULT '[]',
    price TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (lead_id) REFERENCES leads(id)
);

CREATE TABLE IF NOT EXISTS users_seen (
    sender_id INTEGER PRIMARY KEY,
    first_contact_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS anti_ban_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    hour INTEGER NOT NULL,
    actions_count INTEGER DEFAULT 0,
    UNIQUE(date, hour)
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS cold_leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    chat_name TEXT,
    sender_id INTEGER NOT NULL,
    sender_name TEXT,
    sender_username TEXT,
    message_text TEXT,
    category TEXT NOT NULL,
    business_type TEXT,
    pain TEXT,
    hook TEXT,
    status TEXT DEFAULT 'NEW',
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS gis_companies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    phone TEXT,
    address TEXT,
    category TEXT,
    city TEXT,
    has_website INTEGER DEFAULT 0,
    telegram_id INTEGER,
    telegram_username TEXT,
    status TEXT DEFAULT 'NEW',
    generated_message TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS cold_outreach_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,
    target_id INTEGER NOT NULL,
    target_name TEXT,
    message_text TEXT,
    status TEXT,
    sent_at TEXT DEFAULT (datetime('now'))
);
"""


async def init_db():
    """Инициализация БД: создание директории, таблиц и миграции."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.executescript(SCHEMA)
        await _migrate_chats(db)
        await _migrate_messages_log(db)
        await db.commit()


async def _migrate_chats(db):
    """Добавляет новые колонки в chats, если их ещё нет."""
    async with db.execute("PRAGMA table_info(chats)") as cursor:
        columns = {row[1] for row in await cursor.fetchall()}
    if "is_broadcast" not in columns:
        await db.execute("ALTER TABLE chats ADD COLUMN is_broadcast INTEGER DEFAULT 0")
    if "chat_rules" not in columns:
        await db.execute("ALTER TABLE chats ADD COLUMN chat_rules TEXT")
    if "broadcast_times" not in columns:
        await db.execute("ALTER TABLE chats ADD COLUMN broadcast_times TEXT")


async def _migrate_messages_log(db):
    """Добавляет новые колонки в messages_log, если их ещё нет."""
    async with db.execute("PRAGMA table_info(messages_log)") as cursor:
        columns = {row[1] for row in await cursor.fetchall()}
    if "filter_verdict" not in columns:
        await db.execute("ALTER TABLE messages_log ADD COLUMN filter_verdict TEXT")


# === Chats ===

async def add_chat(chat_id: int, chat_name: str, schedule_cron: str = None,
                   message_text: str = None, message_variants: list[str] = None):
    import json
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT OR IGNORE INTO chats (chat_id, chat_name, schedule_cron, message_text, message_variants)
               VALUES (?, ?, ?, ?, ?)""",
            (chat_id, chat_name, schedule_cron, message_text,
             json.dumps(message_variants, ensure_ascii=False) if message_variants else None)
        )
        await db.commit()


async def update_chat_schedule(chat_id: int, schedule_cron: str,
                               message_text: str, message_variants: list[str] = None):
    import json
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """UPDATE chats SET schedule_cron = ?, message_text = ?, message_variants = ?
               WHERE chat_id = ?""",
            (schedule_cron, message_text,
             json.dumps(message_variants, ensure_ascii=False) if message_variants else None,
             chat_id)
        )
        await db.commit()


async def get_monitored_chats() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM chats WHERE is_monitored = 1") as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


async def get_all_chats() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM chats ORDER BY chat_name") as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


async def upsert_dialog_chat(chat_id: int, chat_name: str):
    """Синхронизация чата из списка диалогов Telegram.

    Новые чаты вставляются с is_monitored=0 (default в схеме = 1 —
    иначе синк всех диалогов включил бы мониторинг везде).
    У существующих обновляется только имя.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO chats (chat_id, chat_name, is_monitored)
               VALUES (?, ?, 0)
               ON CONFLICT(chat_id) DO UPDATE SET chat_name = excluded.chat_name""",
            (chat_id, chat_name)
        )
        await db.commit()


async def update_chat_flags(chat_id: int, is_monitored: int, is_broadcast: int,
                            chat_rules: str | None, broadcast_times: str | None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """UPDATE chats SET is_monitored = ?, is_broadcast = ?,
               chat_rules = ?, broadcast_times = ? WHERE chat_id = ?""",
            (is_monitored, is_broadcast, chat_rules, broadcast_times, chat_id)
        )
        await db.commit()


async def update_chat(chat_id: int, chat_name: str = None, is_monitored: bool = None,
                      is_broadcast: bool = None, chat_rules: str = None,
                      broadcast_times: str = None, message_text: str = None,
                      message_variants: list[str] = None, schedule_cron: str = None):
    """Полное обновление чата (для API синхронизации с GUI)."""
    import json
    async with aiosqlite.connect(DB_PATH) as db:
        updates = []
        params = []
        
        if chat_name is not None:
            updates.append("chat_name = ?")
            params.append(chat_name)
        if is_monitored is not None:
            updates.append("is_monitored = ?")
            params.append(1 if is_monitored else 0)
        if is_broadcast is not None:
            updates.append("is_broadcast = ?")
            params.append(1 if is_broadcast else 0)
        if chat_rules is not None:
            updates.append("chat_rules = ?")
            params.append(chat_rules)
        if broadcast_times is not None:
            updates.append("broadcast_times = ?")
            params.append(broadcast_times)
        if message_text is not None:
            updates.append("message_text = ?")
            params.append(message_text)
        if message_variants is not None:
            updates.append("message_variants = ?")
            params.append(json.dumps(message_variants, ensure_ascii=False))
        if schedule_cron is not None:
            updates.append("schedule_cron = ?")
            params.append(schedule_cron)
        
        if updates:
            params.append(chat_id)
            query = f"UPDATE chats SET {', '.join(updates)} WHERE chat_id = ?"
            await db.execute(query, params)
            await db.commit()


async def get_broadcast_chats() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM chats WHERE is_broadcast = 1
               AND broadcast_times IS NOT NULL AND broadcast_times != ''"""
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


async def get_chat(chat_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM chats WHERE chat_id = ?", (chat_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def get_recent_broadcasts(chat_id: int, limit: int = 5) -> list[str]:
    """Последние отправленные рассылки в чат (для неповторяемости текстов)."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """SELECT message_text FROM messages_log
               WHERE chat_id = ? AND direction = 'outgoing_scheduled'
               ORDER BY id DESC LIMIT ?""",
            (chat_id, limit)
        ) as cursor:
            rows = await cursor.fetchall()
            return [r[0] for r in rows if r[0]]


async def get_scheduled_chats() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM chats WHERE schedule_cron IS NOT NULL AND is_monitored = 1"
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


async def update_last_read_message(chat_id: int, message_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE chats SET last_read_message_id = ? WHERE chat_id = ?",
            (message_id, chat_id)
        )
        await db.commit()


async def get_last_read_message(chat_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT last_read_message_id FROM chats WHERE chat_id = ?", (chat_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0


async def add_cold_lead(chat_id: int, chat_name: str, sender_id: int,
                        sender_name: str, sender_username: str, message_text: str,
                        category: str, business_type: str = None, pain: str = None,
                        hook: str = None) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """INSERT INTO cold_leads (chat_id, chat_name, sender_id, sender_name,
               sender_username, message_text, category, business_type, pain, hook)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (chat_id, chat_name, sender_id, sender_name, sender_username,
             message_text, category, business_type, pain, hook)
        )
        await db.commit()
        return cursor.lastrowid


async def get_cold_lead(lead_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM cold_leads WHERE id = ?", (lead_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def update_cold_lead_status(lead_id: int, status: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE cold_leads SET status = ? WHERE id = ?", (status, lead_id)
        )
        await db.commit()


async def import_gis_companies(companies: list[dict]):
    """Импорт компаний из 2GIS. Ожидает список словарей с name, phone, address, category, city."""
    async with aiosqlite.connect(DB_PATH) as db:
        for c in companies:
            await db.execute(
                """INSERT OR IGNORE INTO gis_companies
                   (name, phone, address, category, city, has_website)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (c.get("name"), c.get("phone"), c.get("address"),
                 c.get("category"), c.get("city"), int(c.get("has_website", 0)))
            )
        await db.commit()


async def get_pending_gis_companies(limit: int = 10) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM gis_companies WHERE status = 'NEW' LIMIT ?", (limit,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


async def update_gis_company_status(company_id: int, status: str,
                                    telegram_id: int = None, telegram_username: str = None,
                                    generated_message: str = None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """UPDATE gis_companies SET status = ?, telegram_id = ?,
               telegram_username = ?, generated_message = ? WHERE id = ?""",
            (status, telegram_id, telegram_username, generated_message, company_id)
        )
        await db.commit()


async def log_cold_outreach(type: str, target_id: int, target_name: str,
                            message_text: str, status: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO cold_outreach_log (type, target_id, target_name, message_text, status)
               VALUES (?, ?, ?, ?, ?)""",
            (type, target_id, target_name, message_text, status)
        )
        await db.commit()


async def get_cold_outreach_count_today() -> int:
    today = datetime.now().strftime("%Y-%m-%d")
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM cold_outreach_log WHERE DATE(sent_at) = ?",
            (today,)
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0


# === Leads ===

async def add_lead(chat_id: int, chat_name: str, sender_id: int,
                   sender_name: str, sender_username: str, message_text: str,
                   category: str, task: str = None, budget: str = None,
                   deadline: str = None) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """INSERT INTO leads (chat_id, chat_name, sender_id, sender_name, sender_username,
               message_text, category, task, budget, deadline)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (chat_id, chat_name, sender_id, sender_name, sender_username,
             message_text, category, task, budget, deadline)
        )
        await db.commit()
        return cursor.lastrowid


async def update_lead_status(lead_id: int, status: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE leads SET status = ? WHERE id = ?", (status, lead_id)
        )
        await db.commit()


async def get_lead(lead_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


# === Dialogs ===

async def create_dialog(lead_id: int, sender_id: int, sender_name: str,
                        sender_username: str) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """INSERT INTO dialogs (lead_id, sender_id, sender_name, sender_username)
               VALUES (?, ?, ?, ?)""",
            (lead_id, sender_id, sender_name, sender_username)
        )
        await db.commit()
        return cursor.lastrowid


async def get_dialog_by_lead(lead_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM dialogs WHERE lead_id = ?", (lead_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def get_dialog_by_sender(sender_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM dialogs WHERE sender_id = ? ORDER BY id DESC LIMIT 1",
            (sender_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def update_dialog_stage(dialog_id: int, stage: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE dialogs SET stage = ?, updated_at = datetime('now') WHERE id = ?",
            (stage, dialog_id)
        )
        await db.commit()


async def update_dialog_messages(dialog_id: int, messages_json: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE dialogs SET ai_messages_json = ?, updated_at = datetime('now') WHERE id = ?",
            (messages_json, dialog_id)
        )
        await db.commit()


async def update_dialog_price(dialog_id: int, price: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE dialogs SET price = ?, updated_at = datetime('now') WHERE id = ?",
            (price, dialog_id)
        )
        await db.commit()


async def get_dialog_by_id(dialog_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM dialogs WHERE id = ?", (dialog_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


# === Users seen ===

async def has_seen_user(sender_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT 1 FROM users_seen WHERE sender_id = ?", (sender_id,)
        ) as cursor:
            return await cursor.fetchone() is not None


async def mark_user_seen(sender_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users_seen (sender_id) VALUES (?)", (sender_id,)
        )
        await db.commit()


# === Messages log ===

async def log_message(chat_id: int, sender_id: int, sender_name: str,
                      message_text: str, message_id: int, direction: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO messages_log (chat_id, sender_id, sender_name, message_text, message_id, direction)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (chat_id, sender_id, sender_name, message_text, message_id, direction)
        )
        await db.commit()


async def update_message_verdict(chat_id: int, message_id: int, verdict: str):
    """Записывает вердикт фильтрации (level0_filtered/ai_error/lead_HOT/cold_...) для диагностики."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """UPDATE messages_log SET filter_verdict = ?
               WHERE chat_id = ? AND message_id = ? AND direction = 'incoming'""",
            (verdict, chat_id, message_id)
        )
        await db.commit()


async def get_last_messages_per_monitored_chat() -> list[dict]:
    """Последнее входящее сообщение по каждому отслеживаемому чату — для диагностики
    (проверить, что бот реально читает чат и как классифицировал последнее сообщение)."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT c.chat_id, c.chat_name, ml.message_text, ml.sender_name,
                   ml.filter_verdict, ml.created_at
            FROM chats c
            LEFT JOIN messages_log ml ON ml.id = (
                SELECT id FROM messages_log
                WHERE chat_id = c.chat_id AND direction = 'incoming'
                ORDER BY id DESC LIMIT 1
            )
            WHERE c.is_monitored = 1
            ORDER BY c.chat_name
            """
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


# === Anti-ban stats ===

async def record_action():
    now = datetime.now()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO anti_ban_stats (date, hour, actions_count)
               VALUES (?, ?, 1)
               ON CONFLICT(date, hour)
               DO UPDATE SET actions_count = actions_count + 1""",
            (now.strftime("%Y-%m-%d"), now.hour)
        )
        await db.commit()


async def get_hourly_actions_count() -> int:
    now = datetime.now()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT actions_count FROM anti_ban_stats WHERE date = ? AND hour = ?",
            (now.strftime("%Y-%m-%d"), now.hour)
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0


async def get_daily_actions_count() -> int:
    now = datetime.now()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COALESCE(SUM(actions_count), 0) FROM anti_ban_stats WHERE date = ?",
            (now.strftime("%Y-%m-%d"),)
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0


# === Settings ===

async def get_setting(key: str) -> str | None:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None


async def set_setting(key: str, value: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value)
        )
        await db.commit()
