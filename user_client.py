"""Telethon клиент: мониторинг чатов, отправка сообщений, ведение диалогов."""
import json
import logging
import asyncio
from datetime import datetime
from telethon import TelegramClient, events, connection
from telethon.tl.custom import Message
from config import (TG_API_ID, TG_API_HASH, TG_PHONE, TG_SESSION_NAME,
                    TG_MTPROTO_HOST, TG_MTPROTO_PORT, TG_MTPROTO_SECRET,
                    COLD_LEAD_ENABLED, FOLLOWUP_MAX)
import database as db
from anti_ban import AntiBan
from lead_detector import detect_lead, detect_cold_lead

logger = logging.getLogger(__name__)


def _build_telegram_client() -> TelegramClient:
    """Создаёт TelegramClient, при необходимости через MTProto-прокси."""
    kwargs = {
        "connection_retries": 5,
        "retry_delay": 2,
    }
    if TG_MTPROTO_HOST and TG_MTPROTO_PORT and TG_MTPROTO_SECRET:
        logger.info(
            "Telethon: MTProto proxy %s:%s",
            TG_MTPROTO_HOST, TG_MTPROTO_PORT,
        )
        kwargs["connection"] = connection.ConnectionTcpMTProxyRandomizedIntermediate
        kwargs["proxy"] = (TG_MTPROTO_HOST, TG_MTPROTO_PORT, TG_MTPROTO_SECRET)
    else:
        logger.info("Telethon: прямое подключение (без MTProto proxy)")

    return TelegramClient(TG_SESSION_NAME, TG_API_ID, TG_API_HASH, **kwargs)


class UserClient:
    """Управление рабочим аккаунтом через Telethon."""

    def __init__(self, notification_callback=None):
        self.client = _build_telegram_client()
        self.anti_ban = AntiBan()
        self.notification_callback = notification_callback
        self._monitored_chat_ids: set[int] = set()
        self._running = False
        self._me_id: int | None = None
        self._ai_semaphore = asyncio.Semaphore(5)

    async def start(self):
        """Запуск клиента и авторизация."""
        await self.client.start(phone=TG_PHONE)
        me = await self.client.get_me()
        logger.info(f"Рабочий аккаунт: {me.first_name} (@{me.username}, ID: {me.id})")

        await self.anti_ban.init_warmup()
        await self._load_monitored_chats()
        self._me_id = me.id
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

            # Игнорируем свои собственные сообщения (кэшированный ID)
            if event.sender_id == self._me_id:
                return

            # Запускаем обработку без блокировки следующих сообщений
            asyncio.create_task(self._process_chat_message_safe(event))

        @self.client.on(events.NewMessage(incoming=True))
        async def on_private_message(event):
            """Обработка входящих личных сообщений (ответы клиентов в диалогах)."""
            if event.is_private:
                if event.sender_id == self._me_id:
                    return
                asyncio.create_task(self._process_private_message(event))

    async def _process_chat_message_safe(self, event):
        """Обёртка над _process_chat_message с обработкой ошибок и семафором."""
        try:
            await self._process_chat_message(event)
        except Exception as e:
            logger.error(f"Ошибка обработки сообщения из чата {event.chat_id}: {e}", exc_info=True)

    async def _process_chat_message(self, event):
        """Обработка сообщения из отслеживаемого чата."""
        message = event.message
        text = message.text or ""
        sender = await event.get_sender()

        # Логируем сообщение
        sender_name = getattr(sender, "first_name", "") or getattr(sender, "title", "Unknown")
        await db.log_message(event.chat_id, sender.id, sender_name, text,
                             message.id, "incoming")

        # Детекция лида (с семафором для AI-вызовов)
        sender_is_bot = getattr(sender, "bot", False)
        async with self._ai_semaphore:
            result = await detect_lead(text, message.date, sender_is_bot)

        if not result.is_lead:
            if result.reason.startswith("level"):
                logger.debug(f"L0/L1 отсеял: {text[:50]}...")
            verdict = result.reason
            # Пробуем найти холодного лида, если включено
            if COLD_LEAD_ENABLED:
                async with self._ai_semaphore:
                    cold_result = await detect_cold_lead(text, message.date, sender_is_bot)
                if cold_result.is_lead:
                    await db.update_message_verdict(
                        event.chat_id, message.id, f"cold_{cold_result.category}")
                    await self._process_cold_lead(event, cold_result, text)
                    return
                verdict = f"{verdict}|cold:{cold_result.reason}"
            await db.update_message_verdict(event.chat_id, message.id, verdict)
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
            market_price=result.market_price,
            market_deadline=result.market_deadline,
            lead_score=result.lead_score,
        )

        logger.info(f"ЛИД #{lead_id} [{result.category}] от {sender_name} в чате {chat_name}")
        await db.update_message_verdict(event.chat_id, message.id, f"lead_{result.category}")

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
                market_price=result.market_price,
                market_deadline=result.market_deadline,
                lead_score=result.lead_score,
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
            market_price=cold_result.market_price,
            market_deadline=cold_result.market_deadline,
            lead_score=cold_result.lead_score,
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
                market_price=cold_result.market_price,
                market_deadline=cold_result.market_deadline,
                lead_score=cold_result.lead_score,
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

        # A/B: если клиент ответил и у диалога есть ab_variant — фиксируем конверсию
        # (стадия уже QUALIFYING т.к. обновляется сразу после отправки первого сообщения)
        ab_variant = dialog.get("ab_variant", 0)
        if ab_variant is not None and ab_variant >= 0 and dialog["stage"] in ("INITIATING", "QUALIFYING"):
            # Проверяем что это первый ответ (в истории только 2 сообщения: context + first_message)
            messages = json.loads(dialog.get("ai_messages_json") or "[]")
            assistant_count = sum(1 for m in messages if m.get("role") == "assistant")
            if assistant_count <= 1:
                await db.ab_record_reply(ab_variant)
                logger.info(f"A/B: клиент ответил на вариант #{ab_variant} (диалог #{dialog['id']})")

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

    async def _anti_repeat_note(self) -> str:
        """Формирует блок с последними отправленными первыми сообщениями,
        чтобы AI не повторял одну и ту же структуру/фразы разным людям
        (иначе Telegram может расценить это как спам-рассылку и забанить аккаунт)."""
        recent = await db.get_recent_dialog_openers(limit=8)
        if not recent:
            return ""
        numbered = "\n".join(f"{i+1}. {m}" for i, m in enumerate(recent))
        return (
            "\n\n=== ПОСЛЕДНИЕ ПЕРВЫЕ СООБЩЕНИЯ, ОТПРАВЛЕННЫЕ ДРУГИМ ЛЮДЯМ ===\n"
            f"{numbered}\n\n"
            "ВАЖНО: не повторяй структуру, порядок слов и фразы из этих сообщений. "
            "Сформулируй своё сообщение совершенно иначе — другое начало, другой порядок "
            "мыслей, другие слова. Разные люди должны получать явно РАЗНЫЕ сообщения, "
            "иначе Telegram может посчитать это спам-рассылкой и заблокировать аккаунт."
        )

    async def _send_message(self, sender_id: int, text: str, fast: bool = False):
        """Отправка сообщения с анти-бан проверками.
        
        fast=True — пропуск долгой паузы перед действием (для пользовательских команд).
        """
        if not await self.anti_ban.can_send():
            logger.warning("Отправка заблокирована анти-бан модулем")
            return False

        if not fast:
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
        """Начало диалога с лидом: AI генерирует варианты первого сообщения (A/B), выбирает лучший и отправляет."""
        import ai_engine
        import random

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

        # Заметка об анти-повторе — до создания диалога, чтобы не учитывать текущую пустую запись
        anti_repeat_note = await self._anti_repeat_note()

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

        # A/B: генерируем 3 варианта, выбираем по весам
        variants = await ai_engine.generate_first_message_variants(
            context=context,
            anti_repeat_note=anti_repeat_note,
            num_variants=3,
        )

        if not variants:
            # Fallback на старый метод
            messages = [{"role": "user", "content": context + anti_repeat_note}]
            result = await ai_engine.generate_dialogue_response(
                messages_history=messages,
                stage="INITIATING",
            )
            if result is None:
                logger.error("Ошибка генерации первого сообщения (fallback)")
                return False
            first_message, new_stage = result
            chosen_variant = 0
        else:
            weights = await db.ab_get_weights(len(variants))
            chosen_variant = random.choices(range(len(variants)), weights=weights, k=1)[0]
            first_message = variants[chosen_variant]
            new_stage = "QUALIFYING"

        messages = [
            {"role": "user", "content": context + anti_repeat_note},
            {"role": "assistant", "content": first_message},
        ]

        await db.update_dialog_messages(dialog_id, json.dumps(messages, ensure_ascii=False))
        await db.update_dialog_stage(dialog_id, new_stage)
        await db.set_dialog_ab_variant(dialog_id, chosen_variant)
        await db.ab_record_sent(chosen_variant)

        # Отправляем
        success = await self._send_message(lead["sender_id"], first_message, fast=True)
        if success:
            await db.mark_user_seen(lead["sender_id"])
            await db.update_lead_status(lead_id, "DIALOG_STARTED")
            logger.info(f"Диалог #{dialog_id} начат с лидом #{lead_id} (A/B вариант #{chosen_variant})")
            return True
        return False

    async def start_dialog_with_cold_lead(self, lead_id: int) -> bool:
        """Начало диалога с холодным лидом: AI генерирует варианты первого сообщения (A/B), выбирает и отправляет."""
        import ai_engine
        import random

        lead = await db.get_cold_lead(lead_id)
        if not lead:
            logger.error(f"Холодный лид #{lead_id} не найден")
            return False

        if await db.has_seen_user(lead["sender_id"]):
            logger.warning(f"Уже писали пользователю {lead['sender_id']}")
            return False

        if not await self.anti_ban.can_send():
            logger.warning("Невозможно начать холодный диалог: анти-бан лимиты")
            return False

        anti_repeat_note = await self._anti_repeat_note()

        dialog_id = await db.create_dialog(
            lead_id=lead_id,
            sender_id=lead["sender_id"],
            sender_name=lead["sender_name"],
            sender_username=lead.get("sender_username"),
        )

        context = (
            f"Вы увидели сообщение в чате \"{lead['chat_name']}\":\n"
            f"\"{lead['message_text']}\"\n\n"
            f"Это ХОЛОДНЫЙ лид: человек не искал разработчика напрямую, "
            f"но у него есть потребность.\n"
        )
        if lead.get("business_type"):
            context += f"Тип бизнеса: {lead['business_type']}\n"
        if lead.get("pain"):
            context += f"Его боль/потребность: {lead['pain']}\n"
        if lead.get("hook"):
            context += f"Рекомендуемый подход: {lead['hook']}\n"
        context += (
            "\nНапишите первое сообщение этому человеку. Будьте деликатны: "
            "он не просил помощи. Кратко представьтесь, мягко зацепитесь за его "
            "сообщение и предложите обсудить, чем можете быть полезны. "
            "Не более 2-3 предложений, без навязчивости."
        )

        # A/B: генерируем 3 варианта, выбираем по весам
        variants = await ai_engine.generate_first_message_variants(
            context=context,
            anti_repeat_note=anti_repeat_note,
            num_variants=3,
        )

        if not variants:
            messages = [{"role": "user", "content": context + anti_repeat_note}]
            result = await ai_engine.generate_dialogue_response(
                messages_history=messages,
                stage="INITIATING",
            )
            if result is None:
                logger.error("Ошибка генерации первого сообщения холодному лиду (fallback)")
                return False
            first_message, new_stage = result
            chosen_variant = 0
        else:
            weights = await db.ab_get_weights(len(variants))
            chosen_variant = random.choices(range(len(variants)), weights=weights, k=1)[0]
            first_message = variants[chosen_variant]
            new_stage = "QUALIFYING"

        messages = [
            {"role": "user", "content": context + anti_repeat_note},
            {"role": "assistant", "content": first_message},
        ]

        await db.update_dialog_messages(dialog_id, json.dumps(messages, ensure_ascii=False))
        await db.update_dialog_stage(dialog_id, new_stage)
        await db.set_dialog_ab_variant(dialog_id, chosen_variant)
        await db.ab_record_sent(chosen_variant)

        success = await self._send_message(lead["sender_id"], first_message, fast=True)
        if success:
            await db.mark_user_seen(lead["sender_id"])
            await db.update_cold_lead_status(lead_id, "DIALOG_STARTED")
            logger.info(f"Холодный диалог #{dialog_id} начат с лидом #{lead_id} (A/B вариант #{chosen_variant})")
            return True
        return False

    async def send_followup(self, dialog: dict) -> bool:
        """Отправка follow-up сообщения клиенту, который не ответил.
        Генерирует короткое ненавязчивое напоминание через AI.
        """
        import ai_engine

        if not await self.anti_ban.can_send():
            logger.warning(f"Follow-up диалог #{dialog['id']}: анти-бан лимиты")
            return False

        messages = json.loads(dialog.get("ai_messages_json") or "[]")
        followup_count = dialog.get("followup_count", 0)

        context = (
            f"Клиент не ответил на ваше последнее сообщение. "
            f"Это {followup_count + 1}-е напоминание (не более {FOLLOWUP_MAX}). "
            f"Напишите короткое, ненавязчивое напоминание (1-2 предложения). "
            f"Будьте дружелюбны, без давления. Не повторяйте предыдущие сообщения. "
            f"Мягко напомните о себе и предложите обсудить, если ещё актуально."
        )
        messages.append({"role": "user", "content": context})

        result = await ai_engine.generate_dialogue_response(
            messages_history=messages,
            stage=dialog.get("stage", "INITIATING"),
        )

        if result is None:
            logger.error(f"Ошибка генерации follow-up для диалога #{dialog['id']}")
            return False

        followup_msg, new_stage = result
        messages.append({"role": "assistant", "content": followup_msg})

        await db.update_dialog_messages(dialog["id"], json.dumps(messages, ensure_ascii=False))
        await db.increment_followup(dialog["id"])

        success = await self._send_message(dialog["sender_id"], followup_msg, fast=True)
        if success:
            logger.info(f"Follow-up #{followup_count + 1} отправлен для диалога #{dialog['id']}")
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
