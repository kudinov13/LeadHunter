"""Планировщик рассылки сообщений по чатам по расписанию (cron)."""
import json
import random
import re
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from config import SCHEDULER_TIMEZONE
import database as db
import ai_engine

logger = logging.getLogger(__name__)

TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")

RU_WEEKDAYS = ["понедельник", "вторник", "среда", "четверг",
               "пятница", "суббота", "воскресенье"]
RU_MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня",
             "июля", "августа", "сентября", "октября", "ноября", "декабря"]


def parse_broadcast_times(times_str: str) -> list[tuple[int, int]]:
    """Парсит строку '10:00, 19:30' в список (час, минута). Неверные — пропускает."""
    result = []
    for part in (times_str or "").split(","):
        m = TIME_RE.match(part.strip())
        if m:
            result.append((int(m.group(1)), int(m.group(2))))
    return result


def now_ru_str() -> str:
    """Текущие дата и время по-русски в TZ планировщика (для промпта AI)."""
    now = datetime.now(ZoneInfo(SCHEDULER_TIMEZONE))
    return (f"{RU_WEEKDAYS[now.weekday()]}, {now.day} {RU_MONTHS[now.month - 1]} "
            f"{now.year} года, {now.strftime('%H:%M')}")


class MessageScheduler:
    """Управление запланированными рассылками в чаты."""

    def __init__(self, user_client=None, broadcast_notify_callback=None):
        self.scheduler = AsyncIOScheduler(timezone=SCHEDULER_TIMEZONE)
        self.user_client = user_client
        self.broadcast_notify_callback = broadcast_notify_callback

    async def start(self):
        await self._load_jobs()
        self.scheduler.start()
        logger.info(f"Планировщик запущен (TZ: {SCHEDULER_TIMEZONE})")

    async def stop(self):
        self.scheduler.shutdown(wait=False)
        logger.info("Планировщик остановлен")

    async def _load_jobs(self):
        """Загружает все запланированные рассылки из БД."""
        chats = await db.get_scheduled_chats()
        for chat in chats:
            self._add_job(chat)
        logger.info(f"Загружено запланированных рассылок: {len(chats)}")

        bcast_chats = await db.get_broadcast_chats()
        jobs_count = 0
        for chat in bcast_chats:
            jobs_count += self._add_broadcast_jobs(chat)
        logger.info(f"Загружено AI-рассылок: {len(bcast_chats)} чатов, {jobs_count} задач")

    def _add_job(self, chat: dict):
        """Добавляет cron-задачу для чата."""
        chat_id = chat["chat_id"]
        cron = chat.get("schedule_cron")
        if not cron:
            return

        try:
            trigger = CronTrigger.from_crontab(cron, timezone=SCHEDULER_TIMEZONE)
        except Exception as e:
            logger.error(f"Неверный cron для чата {chat_id}: {cron} — {e}")
            return

        job_id = f"chat_{chat_id}"

        # Удаляем старую задачу если есть
        if self.scheduler.get_job(job_id):
            self.scheduler.remove_job(job_id)

        self.scheduler.add_job(
            self._send_scheduled,
            trigger=trigger,
            args=[chat_id],
            id=job_id,
            name=f"Рассылка: {chat.get('chat_name', chat_id)}",
            misfire_grace_time=300,
        )
        logger.info(f"Добавлена задача {job_id}: cron={cron}")

    async def _send_scheduled(self, chat_id: int):
        """Отправка запланированного сообщения в чат."""
        if not self.user_client:
            logger.error("User client не инициализирован для scheduler")
            return

        # Получаем настройки чата
        async with db.aiosqlite.connect(db.DB_PATH) as conn:
            conn.row_factory = db.aiosqlite.Row
            async with conn.execute(
                "SELECT * FROM chats WHERE chat_id = ?", (chat_id,)
            ) as cursor:
                row = await cursor.fetchone()
                if not row:
                    return
                chat = dict(row)

        # Выбираем текст: случайный вариант или основной
        text = chat.get("message_text", "")
        variants_json = chat.get("message_variants")
        if variants_json:
            try:
                variants = json.loads(variants_json)
                if variants:
                    text = random.choice(variants)
            except json.JSONDecodeError:
                pass

        if not text:
            logger.warning(f"Нет текста для рассылки в чат {chat_id}")
            return

        logger.info(f"Отправка запланированного сообщения в чат {chat.get('chat_name', chat_id)}")
        await self.user_client.send_scheduled_message(chat_id, text)

    def _add_broadcast_jobs(self, chat: dict) -> int:
        """Добавляет cron-задачи AI-рассылки для чата. Возвращает число задач."""
        chat_id = chat["chat_id"]
        times = parse_broadcast_times(chat.get("broadcast_times") or "")
        if not times:
            logger.warning(f"Чат {chat_id}: неверный формат времени рассылки "
                           f"'{chat.get('broadcast_times')}'")
            return 0

        count = 0
        for hour, minute in times:
            job_id = f"bcast_{chat_id}_{hour:02d}{minute:02d}"
            if self.scheduler.get_job(job_id):
                self.scheduler.remove_job(job_id)
            self.scheduler.add_job(
                self._send_ai_broadcast,
                trigger=CronTrigger(hour=hour, minute=minute,
                                    timezone=SCHEDULER_TIMEZONE, jitter=1500),
                args=[chat_id],
                id=job_id,
                name=f"AI-рассылка: {chat.get('chat_name', chat_id)} {hour:02d}:{minute:02d}",
                misfire_grace_time=900,
                coalesce=True,
            )
            count += 1
        return count

    async def _notify_owner(self, text: str):
        if self.broadcast_notify_callback:
            try:
                await self.broadcast_notify_callback(text)
            except Exception as e:
                logger.error(f"Ошибка уведомления владельца: {e}")

    async def _send_ai_broadcast(self, chat_id: int):
        """Генерация и отправка AI-рассылки в чат."""
        if not self.user_client:
            logger.error("User client не инициализирован для AI-рассылки")
            return

        # Перечитываем чат: правила/флаги могли измениться после создания джоба
        chat = await db.get_chat(chat_id)
        if not chat or not chat.get("is_broadcast"):
            logger.info(f"AI-рассылка в чат {chat_id} отменена: чат выключен")
            return
        chat_name = chat.get("chat_name", str(chat_id))

        rules = (chat.get("chat_rules") or "").strip()
        if not rules:
            logger.warning(f"Чат {chat_name}: правила не настроены, рассылка не отправлена")
            await self._notify_owner(
                f"⚠️ Рассылка в чат «{chat_name}» НЕ отправлена:\n"
                f"правила чата не настроены. Откройте программу управления чатами "
                f"и впишите правила (или отметьте «Правил нет»)."
            )
            return

        recent = await db.get_recent_broadcasts(chat_id)
        result = await ai_engine.generate_broadcast(rules, recent, now_ru_str())

        if result is None:
            await self._notify_owner(
                f"❌ Рассылка в чат «{chat_name}» не отправлена: ошибка AI-генерации."
            )
            return

        if result["skip"]:
            logger.info(f"AI пропустил рассылку в {chat_name}: {result['reason']}")
            await self._notify_owner(
                f"⏭️ Пропуск рассылки в «{chat_name}»:\n{result['reason']}"
            )
            return

        success = await self.user_client.send_scheduled_message(chat_id, result["message"])
        if success:
            await self._notify_owner(
                f"✅ Рассылка отправлена в «{chat_name}»:\n\n{result['message']}"
            )
        else:
            await self._notify_owner(
                f"❌ Рассылка в «{chat_name}» не отправлена: анти-бан лимиты или ошибка отправки."
            )

    async def preview_broadcast(self, chat_id: int) -> dict | None:
        """Генерация текста рассылки БЕЗ отправки (кнопка «Тест» в GUI)."""
        chat = await db.get_chat(chat_id)
        if not chat:
            return None
        rules = (chat.get("chat_rules") or "").strip()
        if not rules:
            return {"skip": True, "reason": "Правила чата не настроены", "message": ""}
        recent = await db.get_recent_broadcasts(chat_id)
        return await ai_engine.generate_broadcast(rules, recent, now_ru_str())

    async def reload_jobs(self):
        """Перезагрузка всех задач (после изменения расписаний)."""
        self.scheduler.remove_all_jobs()
        await self._load_jobs()
