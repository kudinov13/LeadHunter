"""Telethon клиент: мониторинг чатов, отправка сообщений, ведение диалогов."""
import json
import logging
import asyncio
from datetime import datetime
from telethon import TelegramClient, events
from telethon.tl.custom import Message
from config import (TG_API_ID, TG_API_HASH, TG_PHONE, TG_SESSION_NAME,
                    COLD_LEAD_ENABLED)
import database as db
from anti_ban import AntiBan
from lead_detector import detect_lead, detect_cold_lead

logger = logging.getLogger(__name__)


class UserClient:
    """Управление рабочим аккаунтом через Telethon."""

    def __init__(self, notification_callback=None):
        self.client = TelegramClient(
            TG_SESSION_NAME, TG_API_ID, TG_API_HASH,
            connection_retries=5,
            retry_delay=2,
        )
        self.anti_ban = AntiBan()
        self.notification_callback = notification_callback
        self._monitored_chat_ids: set[int] = set()
        self._running = False

    async def start(self):
        """Запуск клиента и авторизация."""
        await self.client.start(phone=TG_PHONE)
        me = await self.client.get_me()
        logger.info(f"Рабочий аккаунт: {me.first_name} (@{me.username}, ID: {me.id})")

        await self.anti_ban.init_warmup()
        await self._load_monitored_chats()
        self._register_handlers()
        self._running = True

    async def stop(self):
        self._running = False
        await self.client.disconnect()

    async def _load_monitored_chats(self):
        """Загружает список отслеживаемых чатов из БД."""
        chats = await db.get_monitored_chats()
        self._monitored_chat_ids = {c["chat_id"] for c in chats}
        logger.info(f"Отслеживается чатов: {len(self._monitored_chat_ids)}")

    async def reload_chats(self):
        """Перезагрузка списка чатов (вызывается при добавлении нового чата)."""
        await self._load_monitored_chats()

    def _register_handlers(self):
        """Регистрация обработчиков событий Telethon."""

        @self.client.on(events.NewMessage())
        async def on_new_message(event):
            """Обработка нового сообщения в чате."""
            chat_id = event.chat_id
            if chat_id not in self._monitored_chat_ids:
                return

            # Игнорируем свои собственные сообщения
            me = await self.client.get_me()
            if event.sender_id == me.id:
                return

            await self._process_chat_message(event)

        @self.client.on(events.NewMessage(incoming=True))
        async def on_private_message(event):
            """Обработка входящих личных сообщений (ответы клиентов в диалогах)."""
            if event.is_private:
                me = await self.client.get_me()
                if event.sender_id == me.id:
                    return
                await self._process_private_message(event)

    async def _process_chat_message(self, event):
        """Обработка сообщения из отслеживаемого чата."""
        message = event.message
        text = message.text or ""
        sender = await event.get_sender()

        # Логируем сообщение
        sender_name = getattr(sender, "first_name", "") or getattr(sender, "title", "Unknown")
        await db.log_message(event.chat_id, sender.id, sender_name, text,
                             message.id, "incoming")

        # Детекция лида
        sender_is_bot = getattr(sender, "bot", False)
        result = await detect_lead(text, message.date, sender_is_bot)

        if not result.is_lead:
            if result.reason.startswith("level"):
                logger.debug(f"L0/L1 отсеял: {text[:50]}...")
            # Пробуем найти холодного лида, если включено
            if COLD_LEAD_ENABLED:
                cold_result = await detect_cold_lead(text, message.date, sender_is_bot)
                if cold_result.is_lead:
                    await self._process_cold_lead(event, cold_result, text)
            return

        # Лид найден
        chat_entity = await event.get_chat()
        chat_name = getattr(chat_entity, "title", str(event.chat_id))
        sender_username = getattr(sender, "username", None)

        lead_id = await db.add_lead(
            chat_id=event.chat_id,
            chat_name=chat_name,
            sender_id=sender.id,
            sender_name=sender_name,
            sender_username=sender_username,
            message_text=text,
            category=result.category,
            task=result.task,
            budget=result.budget,
            deadline=result.deadline,
        )

        logger.info(f"ЛИД #{lead_id} [{result.category}] от {sender_name} в чате {chat_name}")

        # Уведомление владельцу через бота
        if self.notification_callback:
            await self.notification_callback(
                lead_id=lead_id,
                chat_name=chat_name,
                sender_name=sender_name,
                sender_username=sender_username,
                message_text=text,
                category=result.category,
                task=result.task,
                budget=result.budget,
                deadline=result.deadline,
                sender_id=sender.id,
            )

    async def _process_cold_lead(self, event, cold_result: 'LeadResult', text: str):
        """Сохраняет холодного лид и уведомляет владельца."""
        sender = await event.get_sender()
        chat_entity = await event.get_chat()
        chat_name = getattr(chat_entity, "title", str(event.chat_id))
        sender_name = getattr(sender, "first_name", "") or getattr(sender, "title", "Unknown")
        sender_username = getattr(sender, "username", None)

        lead_id = await db.add_cold_lead(
            chat_id=event.chat_id,
            chat_name=chat_name,
            sender_id=sender.id,
            sender_name=sender_name,
            sender_username=sender_username,
            message_text=text,
            category=cold_result.category,
            business_type=cold_result.business_type,
            pain=cold_result.pain,
            hook=cold_result.hook,
        )
        logger.info(f"ХОЛОДНЫЙ ЛИД #{lead_id} [{cold_result.category}] от {sender_name}")

        if self.notification_callback:
            await self.notification_callback(
                lead_id=lead_id,
                chat_name=chat_name,
                sender_name=sender_name,
                sender_username=sender_username,
                message_text=text,
                category=cold_result.category,
                business_type=cold_result.business_type,
                pain=cold_result.pain,
                hook=cold_result.hook,
                sender_id=sender.id,
            )

    async def _process_private_message(self, event):
        """Обработка входящего ЛС — ответ клиента в активном диалоге."""
        sender_id = event.sender_id
        text = event.message.text or ""

        # Проверяем, есть ли активный диалог с этим пользователем
        dialog = await db.get_dialog_by_sender(sender_id)
        if not dialog:
            logger.debug(f"ЛС от {sender_id} без активного диалога: {text[:50]}...")
            return

        # Проверяем стадию диалога
        if dialog["stage"] in ("TASK_RECEIVED", "ENDED", "CLOSING"):
            # Если на стадии TASK_RECEIVED и клиент пишет — возможно дополняет задачу
            if dialog["stage"] == "TASK_RECEIVED":
                await self._handle_task_received_reply(dialog, event, text)
            return

        # На стадиях QUALIFYING / NEGOTIATING — AI продолжает диалог
        await self._continue_dialog(dialog, text)

    async def _handle_task_received_reply(self, dialog: dict, event, text: str):
        """Обработка ответа клиента на стадии TASK_RECEIVED (уточнение задачи)."""
        # Логируем
        await db.log_message(0, dialog["sender_id"], dialog["sender_name"],
                             text, event.message.id, "incoming")

        # Обновляем историю сообщений
        messages = json.loads(dialog.get("ai_messages_json") or "[]")
        messages.append({"role": "user", "content": text})

        # Уведомляем владельца о дополнении задачи
        if self.notification_callback:
            await self.notification_callback(
                lead_id=dialog["lead_id"],
                chat_name="ЛС",
                sender_name=dialog["sender_name"],
                sender_username=dialog.get("sender_username"),
                message_text=text,
                category="TASK_UPDATE",
                sender_id=dialog["sender_id"],
                dialog_id=dialog["id"],
            )

    async def _continue_dialog(self, dialog: dict, client_message: str):
        """Продолжение диалога AI-ответом."""
        import ai_engine

        # Загружаем историю
        messages = json.loads(dialog.get("ai_messages_json") or "[]")
        messages.append({"role": "user", "content": client_message})

        # Сохраняем обновлённую историю
        await db.update_dialog_messages(dialog["id"], json.dumps(messages, ensure_ascii=False))

        # Генерируем ответ AI
        result = await ai_engine.generate_dialogue_response(
            messages_history=messages,
            stage=dialog["stage"],
            price=dialog.get("price"),
        )

        if result is None:
            logger.error(f"Ошибка генерации ответа для диалога #{dialog['id']}")
            return

        ai_response, new_stage = result

        # Добавляем ответ AI в историю
        messages.append({"role": "assistant", "content": ai_response})
        await db.update_dialog_messages(dialog["id"], json.dumps(messages, ensure_ascii=False))

        # Обновляем стадию
        await db.update_dialog_stage(dialog["id"], new_stage)

        # Отправляем ответ клиенту
        await self._send_message(dialog["sender_id"], ai_response)

        # Если стадия сменилась на TASK_RECEIVED — уведомляем владельца
        if new_stage == "TASK_RECEIVED" and dialog["stage"] != "TASK_RECEIVED":
            if self.notification_callback:
                # Собираем описание задачи из последних сообщений
                task_text = "\n".join(
                    m["content"] for m in messages
                    if m["role"] == "user"
                )
                await self.notification_callback(
                    lead_id=dialog["lead_id"],
                    chat_name="ЛС",
                    sender_name=dialog["sender_name"],
                    sender_username=dialog.get("sender_username"),
                    message_text=task_text,
                    category="TASK_READY",
                    sender_id=dialog["sender_id"],
                    dialog_id=dialog["id"],
                )

    async def _send_message(self, sender_id: int, text: str):
        """Отправка сообщения с анти-бан проверками."""
        if not await self.anti_ban.can_send():
            logger.warning("Отправка заблокирована анти-бан модулем")
            return False

        await self.anti_ban.wait_before_action()
        await self.anti_ban.wait_typing(text)

        try:
            await self.client.send_message(sender_id, text)
            await self.anti_ban.record_action()
            await db.log_message(0, sender_id, "", text, 0, "outgoing")
            logger.info(f"Отправлено сообщение для {sender_id}: {text[:60]}...")
            return True
        except Exception as e:
            logger.error(f"Ошибка отправки сообщения: {e}")
            return False

    async def start_dialog_with_lead(self, lead_id: int) -> bool:
        """Начало диалога с лидом: AI генерирует первое сообщение и отправляет его."""
        import ai_engine

        lead = await db.get_lead(lead_id)
        if not lead:
            logger.error(f"Лид #{lead_id} не найден")
            return False

        # Проверяем, не писали ли уже этому пользователю
        if await db.has_seen_user(lead["sender_id"]):
            logger.warning(f"Уже писали пользователю {lead['sender_id']}")
            return False

        # Проверяем анти-бан
        if not await self.anti_ban.can_send():
            logger.warning("Невозможно начать диалог: анти-бан лимиты")
            return False

        # Создаём запись диалога
        dialog_id = await db.create_dialog(
            lead_id=lead_id,
            sender_id=lead["sender_id"],
            sender_name=lead["sender_name"],
            sender_username=lead.get("sender_username"),
        )

        # Формируем контекст для первого сообщения
        context = (
            f"Вы увидели сообщение в чате \"{lead['chat_name']}\":\n"
            f"\"{lead['message_text']}\"\n\n"
            f"Напишите первое сообщение этому человеку. "
            f"Кратко представьтесь, упомяните что увидели его запрос, "
            f"предложите обсудить проект. Не более 2-3 предложений."
        )

        messages = [{"role": "user", "content": context}]
        result = await ai_engine.generate_dialogue_response(
            messages_history=messages,
            stage="INITIATING",
        )

        if result is None:
            logger.error("Ошибка генерации первого сообщения")
            return False

        first_message, new_stage = result
        messages.append({"role": "assistant", "content": first_message})

        await db.update_dialog_messages(dialog_id, json.dumps(messages, ensure_ascii=False))
        await db.update_dialog_stage(dialog_id, new_stage)

        # Отправляем
        success = await self._send_message(lead["sender_id"], first_message)
        if success:
            await db.mark_user_seen(lead["sender_id"])
            await db.update_lead_status(lead_id, "DIALOG_STARTED")
            logger.info(f"Диалог #{dialog_id} начат с лидом #{lead_id}")
            return True
        return False

    async def continue_dialog_with_price(self, dialog_id: int, price: str):
        """Продолжение диалога с указанной ценой (команда от владельца)."""
        import ai_engine

        dialog = await db.get_dialog_by_id(dialog_id)
        if not dialog:
            logger.error(f"Диалог #{dialog_id} не найден")
            return

        await db.update_dialog_price(dialog_id, price)
        await db.update_dialog_stage(dialog_id, "NEGOTIATING")

        messages = json.loads(dialog.get("ai_messages_json") or "[]")

        # Добавляем инструкцию менеджера
        manager_msg = (
            f"[ВНУТРЕННЯЯ КОМАНДА] Цена для клиента: {price}. "
            f"Теперь презентуй эту цену клиенту и продолжай переговоры."
        )
        messages.append({"role": "user", "content": manager_msg})

        result = await ai_engine.generate_dialogue_response(
            messages_history=messages,
            stage="NEGOTIATING",
            price=price,
        )

        if result is None:
            logger.error("Ошибка генерации ответа с ценой")
            return

        ai_response, new_stage = result
        messages.append({"role": "assistant", "content": ai_response})
        await db.update_dialog_messages(dialog_id, json.dumps(messages, ensure_ascii=False))
        await db.update_dialog_stage(dialog_id, new_stage)

        await self._send_message(dialog["sender_id"], ai_response)

    async def send_scheduled_message(self, chat_id: int, text: str):
        """Отправка запланированного сообщения в чат (для scheduler)."""
        if not await self.anti_ban.can_send():
            logger.warning("Запланированная отправка заблокирована анти-бан модулем")
            return False

        await self.anti_ban.wait_before_action()

        # Повторная проверка: пока ждали, другая задача могла исчерпать лимит
        if not await self.anti_ban.can_send():
            logger.warning("Лимит исчерпан во время ожидания, отправка отменена")
            return False

        await self.anti_ban.wait_typing(text)

        try:
            await self.client.send_message(chat_id, text)
            await self.anti_ban.record_action()
            await db.log_message(chat_id, 0, "scheduled", text, 0, "outgoing_scheduled")
            logger.info(f"Запланированное сообщение отправлено в чат {chat_id}")
            return True
        except Exception as e:
            logger.error(f"Ошибка запланированной отправки: {e}")
            return False

    async def join_chat(self, chat_link: str) -> int | None:
        """Вступление в чат по ссылке или username."""
        try:
            from telethon import utils as tl_utils
            await self.anti_ban.wait_before_action()
            entity = await self.client.get_entity(chat_link)
            await self.client.join_chat(entity)
            await self.anti_ban.record_action()
            # get_peer_id даёт маркированный ID (-100...), совпадающий с event.chat_id
            chat_id = tl_utils.get_peer_id(entity)
            chat_name = getattr(entity, "title", str(chat_id))
            logger.info(f"Вступил в чат: {chat_name} (ID: {chat_id})")
            return chat_id
        except Exception as e:
            logger.error(f"Ошибка вступления в чат {chat_link}: {e}")
            return None

    async def fetch_dialogs(self) -> list[dict]:
        """Список всех групп/каналов аккаунта (для интерфейса управления чатами).

        Dialog.id — уже маркированный ID, совместим с event.chat_id.
        """
        dialogs = []
        async for d in self.client.iter_dialogs():
            if d.is_group or d.is_channel:
                dialogs.append({"chat_id": d.id, "chat_name": d.title or str(d.id)})
        logger.info(f"Получено диалогов (группы/каналы): {len(dialogs)}")
        return dialogs

    @property
    def is_running(self) -> bool:
        return self._running
