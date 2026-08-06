"""Бот уведомлений (aiogram): присылает лиды на основной аккаунт владельца."""
import logging
import asyncio
from datetime import datetime
from aiogram import Bot, Dispatcher, F
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.types import (Message, CallbackQuery, InlineKeyboardMarkup,
                           InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton)
from aiogram.filters import Command
from aiogram.enums import ParseMode
from config import NOTIF_BOT_TOKEN, OWNER_TG_ID, COLD_OUTREACH_DAILY_LIMIT, TG_BOT_PROXY
import database as db
import gis_importer
import cold_outreach

logger = logging.getLogger(__name__)


class NotificationBot:
    """Бот для уведомлений владельца о найденных лидах и управления диалогами."""

    def __init__(self, user_client=None):
        # Создаем сессию с поддержкой прокси если указан
        if TG_BOT_PROXY:
            session = AiohttpSession(proxy=TG_BOT_PROXY)
            self.bot = Bot(token=NOTIF_BOT_TOKEN, session=session)
            logger.info(f"Bot API через прокси: {TG_BOT_PROXY}")
        else:
            self.bot = Bot(token=NOTIF_BOT_TOKEN)
            logger.info("Bot API: прямое подключение (без прокси)")
        self.dp = Dispatcher()
        self.user_client = user_client
        self._register_handlers()

    def _main_keyboard(self) -> ReplyKeyboardMarkup:
        """Постоянная клавиатура с кнопкой быстрой диагностики."""
        return ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="🔎 Последнее сообщение"), KeyboardButton(text="📊 Статус")],
            ],
            resize_keyboard=True,
        )

    def _register_handlers(self):
        @self.dp.message(F.text == "🔎 Последнее сообщение")
        async def btn_lastmsg(message: Message):
            if message.from_user.id != OWNER_TG_ID:
                return
            await self._handle_lastmsg(message)

        @self.dp.message(F.text == "📊 Статус")
        async def btn_status(message: Message):
            if message.from_user.id != OWNER_TG_ID:
                return
            await self._handle_status(message)

        @self.dp.message(Command("start"))
        async def cmd_start(message: Message):
            if message.from_user.id != OWNER_TG_ID:
                return
            await message.answer(
                "🤖 Бот-ассистент запущен!\n\n"
                "Я буду присылать вам найденных лидов из чатов.\n\n"
                "Команды:\n"
                "/status — статус системы\n"
                "/chats — список отслеживаемых чатов\n"
                "/lastmsg — последнее прочитанное сообщение по каждому чату (проверка что бот читает)\n"
                "/addchat — добавить чат для мониторинга\n"
                "/import_gis — импорт компаний из 2GIS CSV\n"
                "/next_gis — следующая 2GIS-компания на проверку\n"
                "/approve_gis <id> — одобрить и отправить холодное сообщение\n"
                "/cold_stats — статистика холодного обхода\n"
                "/warmup_done — завершить прогрев аккаунта\n"
                "/active — активные диалоги (с кнопками закрытия)\n"
                "/ab_stats — статистика A/B тестирования первых сообщений\n"
                "/search_chats — авто-поиск бизнес-чатов по ключевым словам\n"
                "/help — помощь",
                reply_markup=self._main_keyboard(),
            )

        @self.dp.message(Command("help"))
        async def cmd_help(message: Message):
            if message.from_user.id != OWNER_TG_ID:
                return
            await message.answer(
                "📋 Команды:\n\n"
                "/status — статус системы и анти-бан статистика\n"
                "/chats — список отслеживаемых чатов\n"
                "/addchat <chat_link> — добавить чат для мониторинга\n"
                "/addschedule <chat_id> <cron> <текст> — настроить рассылку\n"
                "/import_gis — импорт компаний из 2GIS CSV (data/gis_companies.csv)\n"
                "/next_gis — следующая компания на проверку\n"
                "/approve_gis <id> — одобрить и отправить холодное сообщение\n"
                "/cold_stats — статистика холодного обхода\n"
                "/warmup_done — завершить прогрев аккаунта\n"
                "/lastmsg — последнее прочитанное сообщение по каждому чату\n"
                "/stats — статистика лидов\n"
                "/active — активные диалоги (с кнопками закрытия)\n"
                "/ab_stats — статистика A/B тестирования первых сообщений\n"
                "/search_chats — авто-поиск бизнес-чатов по ключевым словам\n\n"
                "Когда приходит лид — нажмите «Написать клиенту» чтобы AI начал диалог.\n"
                "Когда клиент опишет задачу — укажите цену сообщением.\n"
                "Формат: /price <dialog_id> <цена>"
            )

        @self.dp.message(Command("status"))
        async def cmd_status(message: Message):
            if message.from_user.id != OWNER_TG_ID:
                return
            await self._handle_status(message)

        @self.dp.message(Command("chats"))
        async def cmd_chats(message: Message):
            if message.from_user.id != OWNER_TG_ID:
                return
            chats = await db.get_monitored_chats()
            if not chats:
                await message.answer("Нет отслеживаемых чатов. Добавьте через /addchat")
                return
            text = "📋 Отслеживаемые чаты:\n\n"
            for c in chats:
                sched = f" (рассылка: {c['schedule_cron']})" if c.get("schedule_cron") else ""
                text += f"• {c['chat_name']} — ID: {c['chat_id']}{sched}\n"
            await message.answer(text)

        @self.dp.message(Command("addchat"))
        async def cmd_addchat(message: Message):
            if message.from_user.id != OWNER_TG_ID:
                return
            args = message.text.split(maxsplit=1)
            if len(args) < 2:
                await message.answer(
                    "Использование: /addchat <chat_link или username>\n"
                    "Пример: /addchat @itfreelance_ru\n"
                    "Пример: /addchat https://t.me/itfreelance_ru"
                )
                return
            chat_link = args[1]
            await message.answer(f"Попытка вступить в чат: {chat_link}...")
            if self.user_client:
                chat_id = await self.user_client.join_chat(chat_link)
                if chat_id:
                    entity = await self.user_client.client.get_entity(chat_id)
                    chat_name = getattr(entity, "title", str(chat_id))
                    await db.add_chat(chat_id, chat_name)
                    await self.user_client.reload_chats()
                    await message.answer(f"✅ Вступил в чат: {chat_name} (ID: {chat_id})")
                else:
                    await message.answer("❌ Не удалось вступить в чат")
            else:
                await message.answer("❌ Клиент не инициализирован")

        @self.dp.message(Command("warmup_done"))
        async def cmd_warmup_done(message: Message):
            if message.from_user.id != OWNER_TG_ID:
                return
            if self.user_client:
                await self.user_client.anti_ban.mark_warmup_done()
                await message.answer("✅ Прогрев аккаунта завершён. Отправка сообщений разблокирована.")
            else:
                await message.answer("❌ Клиент не инициализирован")

        @self.dp.message(Command("lastmsg"))
        async def cmd_lastmsg(message: Message):
            """Диагностика: последнее прочитанное сообщение в каждом чате + вердикт фильтра.
            Позволяет проверить, что бот реально читает чаты и почему сообщение не стало лидом."""
            if message.from_user.id != OWNER_TG_ID:
                return
            await self._handle_lastmsg(message)

        @self.dp.message(Command("stats"))
        async def cmd_stats(message: Message):
            if message.from_user.id != OWNER_TG_ID:
                return
            async with db.aiosqlite.connect(db.DB_PATH) as conn:
                async with conn.execute(
                    "SELECT category, COUNT(*) FROM leads GROUP BY category"
                ) as cursor:
                    cat_counts = await cursor.fetchall()
                async with conn.execute(
                    "SELECT status, COUNT(*) FROM leads GROUP BY status"
                ) as cursor:
                    status_counts = await cursor.fetchall()
                async with conn.execute("SELECT COUNT(*) FROM dialogs") as cursor:
                    dialogs_count = (await cursor.fetchone())[0]

            text = "📈 Статистика:\n\n"
            text += "Лиды по категориям:\n"
            for cat, cnt in cat_counts:
                text += f"  {cat}: {cnt}\n"
            text += "\nЛиды по статусам:\n"
            for stat, cnt in status_counts:
                text += f"  {stat}: {cnt}\n"
            text += f"\nВсего диалогов: {dialogs_count}"
            await message.answer(text)

        @self.dp.message(Command("active"))
        async def cmd_active(message: Message):
            """Список активных диалогов с кнопками закрытия."""
            if message.from_user.id != OWNER_TG_ID:
                return
            await self._handle_active(message)

        @self.dp.message(Command("ab_stats"))
        async def cmd_ab_stats(message: Message):
            """Статистика A/B тестирования первых сообщений."""
            if message.from_user.id != OWNER_TG_ID:
                return
            await self._handle_ab_stats(message)

        @self.dp.message(Command("search_chats"))
        async def cmd_search_chats(message: Message):
            """Авто-поиск новых чатов по ключевым словам."""
            if message.from_user.id != OWNER_TG_ID:
                return
            await self._handle_search_chats(message)

        @self.dp.callback_query(F.data.startswith("close_"))
        async def cb_close_dialog(callback: CallbackQuery):
            if callback.from_user.id != OWNER_TG_ID:
                return
            dialog_id = int(callback.data.split("_")[1])
            await db.close_dialog(dialog_id)
            await callback.answer("Диалог закрыт")
            await callback.message.edit_text(
                callback.message.text + "\n\n✅ Закрыто"
            )

        @self.dp.callback_query(F.data.startswith("approve_chat_"))
        async def cb_approve_chat(callback: CallbackQuery):
            if callback.from_user.id != OWNER_TG_ID:
                return
            parts = callback.data.split("_")
            # approve_chat_{folder}_{username}
            folder = parts[2]
            username = parts[3]
            await callback.answer(f"Вступаю в @{username}...")
            if self.user_client and hasattr(self.user_client, 'client'):
                import chat_finder
                from config import CHAT_JOIN_DAILY_LIMIT
                joined_today = await db.get_chat_joins_today()
                if joined_today >= CHAT_JOIN_DAILY_LIMIT:
                    await callback.message.edit_text(
                        callback.message.text + f"\n\n❌ Дневной лимит вступлений исчерпан ({joined_today}/{CHAT_JOIN_DAILY_LIMIT})"
                    )
                    return
                chat = {"username": username, "title": f"@{username}", "participants_count": 0}
                success = await chat_finder.join_and_organize_chat(
                    self.user_client.client, chat, folder=folder
                )
                if success:
                    await callback.message.edit_text(
                        callback.message.text + f"\n\n✅ Вступил, мьют, архив, папка: {folder}"
                    )
                else:
                    await callback.message.edit_text(
                        callback.message.text + "\n\n❌ Ошибка вступления"
                    )
            else:
                await callback.message.answer("❌ Клиент не инициализирован")

        @self.dp.callback_query(F.data.startswith("reject_chat_"))
        async def cb_reject_chat(callback: CallbackQuery):
            if callback.from_user.id != OWNER_TG_ID:
                return
            await callback.answer("Отклонено")
            await callback.message.edit_text(
                callback.message.text + "\n\n❌ Отклонено"
            )

        @self.dp.message(Command("price"))
        async def cmd_price(message: Message):
            if message.from_user.id != OWNER_TG_ID:
                return
            args = message.text.split(maxsplit=2)
            if len(args) < 3:
                await message.answer("Использование: /price <dialog_id> <цена>\nПример: /price 5 350000")
                return
            try:
                dialog_id = int(args[1])
                price = args[2]
            except ValueError:
                await message.answer("Неверный формат. Пример: /price 5 350000")
                return

            if self.user_client:
                await self.user_client.continue_dialog_with_price(dialog_id, price)
                await message.answer(f"✅ Цена {price} отправлена в диалог #{dialog_id}. AI продолжает переговоры.")
            else:
                await message.answer("❌ Клиент не инициализирован")

        @self.dp.callback_query(F.data.startswith("write_"))
        async def cb_write(callback: CallbackQuery):
            if callback.from_user.id != OWNER_TG_ID:
                return
            lead_id = int(callback.data.split("_")[1])
            await callback.answer("Начинаю диалог...")
            if self.user_client:
                success = await self.user_client.start_dialog_with_lead(lead_id)
                if success:
                    await callback.message.edit_text(
                        callback.message.text + "\n\n✅ AI начал диалог с клиентом"
                    )
                else:
                    await callback.message.edit_text(
                        callback.message.text + "\n\n❌ Не удалось начать диалог (лимиты или ошибка)"
                    )
            else:
                await callback.message.answer("❌ Клиент не инициализирован")

        @self.dp.callback_query(F.data.startswith("ignore_"))
        async def cb_ignore(callback: CallbackQuery):
            if callback.from_user.id != OWNER_TG_ID:
                return
            lead_id = int(callback.data.split("_")[1])
            await db.update_lead_status(lead_id, "IGNORED")
            await callback.answer("Лид проигнорирован")
            await callback.message.edit_text(
                callback.message.text + "\n\n❌ Проигнорировано"
            )

        @self.dp.callback_query(F.data.startswith("task_"))
        async def cb_task(callback: CallbackQuery):
            if callback.from_user.id != OWNER_TG_ID:
                return
            dialog_id = int(callback.data.split("_")[1])
            await callback.answer("Укажите цену командой /price")
            await callback.message.edit_text(
                callback.message.text
                + f"\n\n💰 Укажите цену командой:\n/price {dialog_id} <сумма>"
            )

        @self.dp.callback_query(F.data.startswith("cold_write_"))
        async def cb_cold_write(callback: CallbackQuery):
            if callback.from_user.id != OWNER_TG_ID:
                return
            lead_id = int(callback.data.split("_")[2])
            await callback.answer("Начинаю холодный диалог...")
            if self.user_client:
                success = await self.user_client.start_dialog_with_cold_lead(lead_id)
                if success:
                    await callback.message.edit_text(
                        callback.message.text + "\n\n✅ AI начал диалог с холодным лидом"
                    )
                else:
                    await callback.message.edit_text(
                        callback.message.text + "\n\n❌ Не удалось начать диалог (лимиты, уже писали или ошибка)"
                    )
            else:
                await callback.message.answer("❌ Клиент не инициализирован")

        @self.dp.callback_query(F.data.startswith("cold_ignore_"))
        async def cb_cold_ignore(callback: CallbackQuery):
            if callback.from_user.id != OWNER_TG_ID:
                return
            lead_id = int(callback.data.split("_")[2])
            await db.update_cold_lead_status(lead_id, "IGNORED")
            await callback.answer("Холодный лид проигнорирован")
            await callback.message.edit_text(
                callback.message.text + "\n\n❌ Проигнорировано"
            )

        @self.dp.callback_query(F.data.startswith("takeover_"))
        async def cb_takeover(callback: CallbackQuery):
            if callback.from_user.id != OWNER_TG_ID:
                return
            dialog_id = int(callback.data.split("_")[1])
            await db.update_dialog_stage(dialog_id, "ENDED")
            await callback.answer("Диалог передан вам")
            await callback.message.edit_text(
                callback.message.text + "\n\n👤 Диалог передан вам. Общайтесь напрямую."
            )

        @self.dp.message(Command("cold_stats"))
        async def cmd_cold_stats(message: Message):
            if message.from_user.id != OWNER_TG_ID:
                return
            async with db.aiosqlite.connect(db.DB_PATH) as conn:
                async with conn.execute(
                    "SELECT status, COUNT(*) FROM gis_companies GROUP BY status"
                ) as cursor:
                    gis_counts = await cursor.fetchall()
                async with conn.execute(
                    "SELECT COUNT(*) FROM cold_outreach_log WHERE DATE(sent_at) = ?",
                    (datetime.now().strftime("%Y-%m-%d"),)
                ) as cursor:
                    sent_today = (await cursor.fetchone())[0]
                async with conn.execute(
                    "SELECT COUNT(*) FROM cold_outreach_log"
                ) as cursor:
                    total_sent = (await cursor.fetchone())[0]

            text = "🧊 Холодный обход:\n\n"
            text += "2GIS компании:\n"
            for status, cnt in gis_counts:
                text += f"  {status}: {cnt}\n"
            text += f"\nОтправлено сегодня: {sent_today}/{COLD_OUTREACH_DAILY_LIMIT}\n"
            text += f"Всего отправлено: {total_sent}\n"
            await message.answer(text)

        @self.dp.message(Command("import_gis"))
        async def cmd_import_gis(message: Message):
            if message.from_user.id != OWNER_TG_ID:
                return
            try:
                companies = gis_importer.import_gis_csv()
                if not companies:
                    await message.answer("❌ Файл data/gis_companies.csv не найден или пуст")
                    return
                await db.import_gis_companies(companies)
                await message.answer(f"✅ Импортировано компаний из 2GIS: {len(companies)}")
            except Exception as e:
                logger.error(f"Ошибка импорта 2GIS: {e}")
                await message.answer(f"❌ Ошибка импорта: {e}")

        @self.dp.message(Command("next_gis"))
        async def cmd_next_gis(message: Message):
            if message.from_user.id != OWNER_TG_ID:
                return
            if not self.user_client:
                await message.answer("❌ Клиент не инициализирован")
                return
            try:
                company = await cold_outreach.prepare_next_gis_company(
                    self.user_client.client
                )
                if not company:
                    await message.answer("Нет новых 2GIS-компаний для обработки")
                    return

                text = (
                    f"🧊 2GIS компания #{company['id']}\n\n"
                    f"🏢 {company['name']}\n"
                    f"📞 {company['phone']}\n"
                    f"🆔 Telegram ID: {company['telegram_id'] or 'не найден'}\n"
                    f"👤 Username: @{company['telegram_username'] or '-'}\n\n"
                    f"💬 Сообщение для отправки:\n{company['generated_message']}\n\n"
                    f"Одобрите командой:\n/approve_gis {company['id']}"
                )
                await message.answer(text)
            except Exception as e:
                logger.error(f"Ошибка подготовки 2GIS: {e}")
                await message.answer(f"❌ Ошибка: {e}")

        @self.dp.message(Command("approve_gis"))
        async def cmd_approve_gis(message: Message):
            if message.from_user.id != OWNER_TG_ID:
                return
            args = message.text.split(maxsplit=1)
            if len(args) < 2:
                await message.answer("Использование: /approve_gis <id>\nПример: /approve_gis 5")
                return
            try:
                company_id = int(args[1])
            except ValueError:
                await message.answer("Неверный ID. Пример: /approve_gis 5")
                return
            if not self.user_client:
                await message.answer("❌ Клиент не инициализирован")
                return
            try:
                success = await cold_outreach.approve_and_send_cold_message(
                    self.user_client.client, company_id, self.user_client.anti_ban
                )
                if success:
                    await message.answer(f"✅ Сообщение компании #{company_id} отправлено")
                else:
                    await message.answer(f"⏳ Не удалось отправить #{company_id}: лимиты или ошибка")
            except Exception as e:
                logger.error(f"Ошибка approve_gis: {e}")
                await message.answer(f"❌ Ошибка: {e}")

    async def _handle_status(self, message: Message):
        hourly = await db.get_hourly_actions_count()
        daily = await db.get_daily_actions_count()
        cold_daily = await db.get_cold_outreach_count_today()
        chats = await db.get_monitored_chats()
        warmup = self.user_client.anti_ban.is_warmup if self.user_client else "?"
        running = self.user_client.is_running if self.user_client else False

        status_text = (
            f"📊 Статус системы\n\n"
            f"• Клиент: {'✅ Запущен' if running else '❌ Остановлен'}\n"
            f"• Прогрев: {'⏳ Активен' if warmup else '✅ Завершён'}\n"
            f"• Чатов отслеживается: {len(chats)}\n"
            f"• Действий за час: {hourly}\n"
            f"• Действий за день: {daily}\n"
            f"• Холодных сообщений сегодня: {cold_daily}/{COLD_OUTREACH_DAILY_LIMIT}\n"
        )
        await message.answer(status_text)

    async def _handle_active(self, message: Message):
        """Список активных диалогов с кнопками закрытия."""
        dialogs = await db.get_active_dialogs()
        if not dialogs:
            await message.answer("📭 Нет активных диалогов")
            return

        text = f"📋 Активные диалогы ({len(dialogs)}):\n\n"
        for d in dialogs:
            score = d.get("lead_score", 0) or 0
            stage = d.get("stage", "?")
            task = (d.get("task") or "")[:60]
            price = d.get("price")
            followups = d.get("followup_count", 0)
            sender = d.get("sender_name", "?")
            username = d.get("sender_username")
            updated = d.get("updated_at", "?")

            text += (
                f"#{d['id']} ⭐{score}/100 | {stage}"
                + (f" | follow-up:{followups}" if followups else "")
                + "\n"
                f"  👤 {sender}"
                + (f" (@{username})" if username else "")
                + "\n"
            )
            if task:
                text += f"  🎯 {task}\n"
            if price:
                text += f"  💰 {price}\n"
            text += f"  🕐 {updated}\n\n"

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=f"✅ Закрыть #{d['id']}",
                callback_data=f"close_{d['id']}"
            )] for d in dialogs[:10]
        ])
        if len(dialogs) > 10:
            text += f"\n(показано первые 10 из {len(dialogs)})"
        await message.answer(text, reply_markup=keyboard)

    async def _handle_ab_stats(self, message: Message):
        """Статистика A/B тестирования первых сообщений."""
        stats = await db.ab_get_stats()
        if not stats:
            await message.answer("📭 A/B статистика пуста. Пока не отправлено ни одного варианта.")
            return

        variant_names = ["Прямой", "Дружелюбный", "Вопросный"]
        text = "📊 A/B статистика первых сообщений:\n\n"
        total_sent = 0
        total_replied = 0
        for s in stats:
            v = s["variant"]
            name = variant_names[v] if v < len(variant_names) else f"Вариант {v}"
            sent = s["sent_count"]
            replied = s["replied_count"]
            rate = (replied / sent * 100) if sent > 0 else 0
            total_sent += sent
            total_replied += replied
            text += (
                f"#{v} {name}\n"
                f"  Отправлено: {sent}\n"
                f"  Ответов: {replied}\n"
                f"  Конверсия: {rate:.1f}%\n\n"
            )
        overall = (total_replied / total_sent * 100) if total_sent > 0 else 0
        text += f"📈 Всего: {total_sent} отправлено, {total_replied} ответов ({overall:.1f}%)"
        await message.answer(text)

    async def _handle_search_chats(self, message: Message):
        """Авто-поиск чатов: ищет, сканирует, предлагает для одобрения."""
        if not self.user_client or not hasattr(self.user_client, 'client'):
            await message.answer("❌ Клиент не инициализирован")
            return

        await message.answer("🔍 Ищу чаты по ключевым словам...")

        import chat_finder
        import asyncio as _asyncio
        from config import CHAT_JOIN_DAILY_LIMIT

        joined_today = await db.get_chat_joins_today()
        remaining = CHAT_JOIN_DAILY_LIMIT - joined_today
        await message.answer(
            f"📊 Лимит вступлений сегодня: {joined_today}/{CHAT_JOIN_DAILY_LIMIT} "
            f"(осталось {remaining})"
        )

        good_chats = await chat_finder.search_and_scan(self.user_client.client)

        if not good_chats:
            await message.answer("📭 Бизнес-чатов не найдено. Попробуйте позже или измените ключевые слова.")
            return

        await message.answer(f"✅ Найдено {len(good_chats)} бизнес-чатов. Отправляю на проверку...")

        for chat in good_chats[:10]:
            scan = chat.get("scan", {})
            category = scan.get("category", "unknown")
            reason = scan.get("reason", "")
            has_orders = scan.get("has_orders", False)
            folder = "freelance" if has_orders else "business"

            cat_emoji = {"freelance": "🔥", "business": "💼"}.get(category, "❓")

            text = (
                f"{cat_emoji} Чат: {chat['title']}\n"
                f"📍 @{chat['username']}\n"
                f"👥 Участников: {chat['participants_count']}\n"
                f"📂 Категория: {category}\n"
                f"📝 {reason}\n"
            )

            # Показываем пример сообщения
            samples = scan.get("sample_texts", [])
            if samples:
                text += f"\n💬 Пример:\n  {samples[0][:150]}...\n"

            keyboard = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(
                    text=f"✅ Вступить ({folder})",
                    callback_data=f"approve_chat_{folder}_{chat['username']}"
                ),
                InlineKeyboardButton(
                    text="❌ Отклонить",
                    callback_data=f"reject_chat_0_{chat['username']}"
                ),
            ]])
            await message.answer(text, reply_markup=keyboard)
            await _asyncio.sleep(0.5)

    async def _handle_lastmsg(self, message: Message):
        """Диагностика: последнее прочитанное сообщение в каждом чате + вердикт фильтра.
        Позволяет проверить, что бот реально читает чаты и почему сообщение не стало лидом."""
        rows = await db.get_last_messages_per_monitored_chat()
        if not rows:
            await message.answer("Нет отслеживаемых чатов. Добавьте через /addchat")
            return

        verdict_labels = {
            "level0_filtered": "🗑 отсеяно (возраст/длина/бот/спам)",
            "level1_no_keywords": "🗑 нет ключевых слов",
            "no_cold_signals": "🗑 нет холодных сигналов",
            "ai_error": "⚠️ ошибка AI-классификации",
            "ai_not_lead": "➖ AI: не лид",
            "ai_not_cold_lead": "➖ AI: не холодный лид",
        }

        text = "🔎 Последнее прочитанное сообщение по чатам:\n\n"
        for r in rows:
            chat_name = r["chat_name"] or str(r["chat_id"])
            if not r.get("created_at"):
                text += f"📍 {chat_name}\n   ещё не читал ни одного сообщения\n\n"
                continue

            verdict = r.get("filter_verdict") or "⏳ обрабатывается / устарело"
            if verdict.startswith("lead_"):
                label = f"🔥 ЛИД ({verdict[5:]})"
            elif verdict.startswith("cold_"):
                label = f"❄️ холодный лид ({verdict[5:]})"
            else:
                base = verdict.split("|")[0]
                label = verdict_labels.get(base, verdict)

            when = r["created_at"]
            msg_preview = (r.get("message_text") or "")[:200]
            text += (
                f"📍 {chat_name}\n"
                f"   🕒 {when}\n"
                f"   👤 {r.get('sender_name') or '?'}\n"
                f"   💬 {msg_preview}\n"
                f"   ➜ {label}\n\n"
            )

        for chunk_start in range(0, len(text), 3500):
            await message.answer(text[chunk_start:chunk_start + 3500])

    async def notify_cold_lead(self, lead_id: int, chat_name: str, sender_name: str,
                               sender_username: str | None, message_text: str,
                               category: str, business_type: str = None,
                               pain: str = None, hook: str = None,
                               market_price: str = None, market_deadline: str = None,
                               lead_score: int = 0, sender_id: int = None):
        """Отправка уведомления о холодном лиде владельцу."""
        category_emoji = {"HOT_COLD": "❄️🔥", "WARM_COLD": "🧊"}.get(category, "❓")
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Написать клиенту", callback_data=f"cold_write_{lead_id}"),
                InlineKeyboardButton(text="❌ Игнорировать", callback_data=f"cold_ignore_{lead_id}"),
            ],
        ])
        text = (
            f"{category_emoji} Холодный лид #{lead_id}\n\n"
            f"📍 Чат: {chat_name}\n"
            f"👤 Автор: {sender_name}"
            + (f" (@{sender_username})" if sender_username else "") + "\n"
            f"📂 Категория: {category}\n"
            f"⭐ Скор: {lead_score}/100\n"
        )
        if business_type:
            text += f"🏢 Тип бизнеса: {business_type}\n"
        if pain:
            text += f"💡 Потребность: {pain}\n"
        text += f"\n💬 Сообщение:\n{message_text[:500]}\n"
        if hook:
            text += f"\n🎯 Подход: {hook}\n"
        if market_price or market_deadline:
            text += "\n📊 Рыночная оценка (совет AI):\n"
            if market_price:
                text += f"   💵 Рекомендуемая цена: {market_price}\n"
            if market_deadline:
                text += f"   ⏱ Рекомендуемый срок: {market_deadline}\n"
        await self.bot.send_message(OWNER_TG_ID, text, reply_markup=keyboard)

    async def notify_lead(self, lead_id: int, chat_name: str, sender_name: str,
                          sender_username: str | None, message_text: str,
                          category: str, task: str = None, budget: str = None,
                          deadline: str = None, market_price: str = None,
                          market_deadline: str = None, lead_score: int = 0,
                          sender_id: int = None, dialog_id: int = None):
        """Отправка уведомления о лиде владельцу."""

        if category == "TASK_READY":
            # Клиент описал задачу — нужен ввод цены
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="💬 Указать цену", callback_data=f"task_{dialog_id}"),
                    InlineKeyboardButton(text="👤 Взять на себя", callback_data=f"takeover_{dialog_id}"),
                ],
            ])
            text = (
                f"📋 Клиент описал задачу!\n\n"
                f"👤 Клиент: {sender_name}"
                + (f" (@{sender_username})" if sender_username else "") + "\n"
                f"💬 Диалог #{dialog_id}\n\n"
                f"📝 Описание от клиента:\n{message_text[:500]}\n\n"
                f"Укажите цену командой:\n/price {dialog_id} <ваша_цена>\n"
                f"Или возьмите диалог на себя."
            )
            await self.bot.send_message(OWNER_TG_ID, text, reply_markup=keyboard)
            return

        if category == "TASK_UPDATE":
            # Дополнение задачи от клиента
            text = (
                f"💬 Клиент дополнил задачу:\n\n"
                f"👤 {sender_name}"
                + (f" (@{sender_username})" if sender_username else "") + "\n"
                f"📝 {message_text[:300]}\n\n"
                f"Диалог #{dialog_id}. Укажите цену: /price {dialog_id} <цена>"
            )
            await self.bot.send_message(OWNER_TG_ID, text)
            return

        # Обычный лид из чата
        category_emoji = {"HOT": "🔥", "WARM": "🌡️"}.get(category, "❓")
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Написать клиенту", callback_data=f"write_{lead_id}"),
                InlineKeyboardButton(text="❌ Игнорировать", callback_data=f"ignore_{lead_id}"),
            ],
        ])

        text = (
            f"{category_emoji} Найден лид #{lead_id}\n\n"
            f"📍 Чат: {chat_name}\n"
            f"👤 Автор: {sender_name}"
            + (f" (@{sender_username})" if sender_username else "") + "\n"
            f"📂 Категория: {category}\n"
            f"⭐ Скор: {lead_score}/100\n\n"
            f"💬 Сообщение:\n{message_text[:500]}\n"
        )
        if task:
            text += f"\n🎯 Задача: {task}\n"
        if budget:
            text += f"💰 Бюджет клиента: {budget}\n"
        if deadline:
            text += f"⏰ Сроки клиента: {deadline}\n"
        if market_price or market_deadline:
            text += "\n📊 Рыночная оценка (совет AI, клиент её не называл):\n"
            if market_price:
                text += f"   💵 Рекомендуемая цена: {market_price}\n"
            if market_deadline:
                text += f"   ⏱ Рекомендуемый срок: {market_deadline}\n"

        await self.bot.send_message(OWNER_TG_ID, text, reply_markup=keyboard)

    async def start(self):
        """Запуск бота (polling)."""
        await self.bot.delete_webhook(drop_pending_updates=True)
        logger.info("Бот уведомлений запущен")
        await self.dp.start_polling(self.bot)

    async def stop(self):
        await self.dp.stop_polling()
        await self.bot.session.close()
