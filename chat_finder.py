"""Авто-поиск новых чатов через Telegram Search API.

Ищет публичные чаты по ключевым словам, сканирует через AI на предмет
бизнес-тематике (отсеивает казино, порно, спам), предлагает владельцу
для одобрения. При одобрении — вступает, мьютит, архивирует, сортирует по папкам.
"""
import logging
import asyncio
from telethon.tl.functions.messages import SearchGlobalRequest
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.functions.messages import ReadMentionsRequest
from telethon.tl.functions.account import UpdateNotifySettingsRequest
from telethon.tl.types import (
    InputPeerNotifySettings,
    DialogFilter,
    InputPeerEmpty,
    InputFolderPeer,
)
from telethon.tl.functions.folders import EditPeerFoldersRequest
from telethon.tl.functions.messages import GetDialogFiltersRequest, UpdateDialogFilterRequest
from telethon import utils

import ai_engine
import database as db
from config import (
    CHAT_SEARCH_KEYWORDS,
    CHAT_SEARCH_MAX_RESULTS,
    CHAT_SEARCH_MIN_MEMBERS,
    CHAT_SEARCH_MAX_MEMBERS,
    CHAT_SCAN_SAMPLE_MESSAGES,
    CHAT_FOLDER_FREELANCE,
    CHAT_FOLDER_BUSINESS,
    CHAT_JOIN_DAILY_LIMIT,
    CHAT_SEARCH_EXCLUSION_KEYWORDS,
    CHAT_SEARCH_DAILY_LIMIT,
    CHAT_SEARCH_BATCH_SIZE,
)

logger = logging.getLogger(__name__)

# Live-статус поиска для проверки извне
_search_status = {
    "total_found": 0,
    "scanned": 0,
    "approved": 0,
    "rejected": 0,
    "current_chat": "",
    "current_chat_title": "",
    "is_running": False,
    "started_at": "",
}


def get_search_status() -> dict:
    """Возвращает текущий статус поиска чатов."""
    return dict(_search_status)

# Ключевые слова для отсеивания мусорных чатов
_JUNK_KEYWORDS = [
    "казино", "ставки", "букмекер", "порно", "18+", "xxx", "секс",
    "наркотики", "магазин аккаунтов", "продажа аккаунтов",
    "crypto pump", "pump signal", "airdrop free",
    "лишь бы не работать", "заработок без вложений",
    "млм", "сетевой маркетинг", "пирамида",
]

# Ключевые слова для бизнес-чатов
_BUSINESS_KEYWORDS = [
    "заказ", "разработка", "фриланс", "проект", "бюджет",
    "сайт", "приложение", "telegram bot", "парсер",
    "программист", "разработчик", "ит", "tech",
    "услуга", "оплата", "срок", "дедлайн",
    "веб", "mobile", "backend", "frontend",
]


async def search_chats(client, keywords: list[str] = None, max_results: int = None) -> list[dict]:
    """Ищет публичные чаты по ключевым словам через Telegram Search API.

    Возвращает список: {id, title, username, participants_count, is_channel, is_group}
    """
    if keywords is None:
        keywords = CHAT_SEARCH_KEYWORDS
    if max_results is None:
        max_results = CHAT_SEARCH_MAX_RESULTS

    found = {}
    for kw in keywords:
        try:
            result = await client(SearchGlobalRequest(q=kw, limit=max_results))
            for chat in result.chats:
                chat_id = chat.id
                if chat_id in found:
                    continue
                participants = getattr(chat, "participants_count", 0) or 0
                username = getattr(chat, "username", None)
                # Только публичные чаты с username
                if not username:
                    continue
                # Фильтр по размеру
                if participants < CHAT_SEARCH_MIN_MEMBERS:
                    continue
                if participants > CHAT_SEARCH_MAX_MEMBERS:
                    continue

                is_channel = getattr(chat, "megagroup", False) is False and getattr(chat, "broadcast", False)
                is_group = getattr(chat, "megagroup", False)

                found[chat_id] = {
                    "id": chat_id,
                    "title": chat.title,
                    "username": username,
                    "participants_count": participants,
                    "is_channel": is_channel,
                    "is_group": is_group,
                    "access_hash": chat.access_hash,
                }
            logger.info(f"Поиск по '{kw}': найдено {len(result.chats)} чатов")
            await asyncio.sleep(1)
        except Exception as e:
            logger.error(f"Ошибка поиска по '{kw}': {e}")

    return list(found.values())


async def scan_chat_quality(client, chat: dict) -> dict:
    """Сканирует чат: берёт последние сообщения и классифицирует через AI.

    Возвращает: {is_business, category, reason, sample_texts}
    category: 'freelance', 'business', 'junk', 'unknown'
    """
    chat_username = chat["username"]
    sample_texts = []

    try:
        entity = await client.get_entity(chat_username)
        async for msg in client.iter_messages(entity, limit=CHAT_SCAN_SAMPLE_MESSAGES):
            text = msg.text or ""
            if text.strip():
                sample_texts.append(text[:200])
    except Exception as e:
        logger.error(f"Ошибка чтения сообщений из @{chat_username}: {e}")
        return {"is_business": False, "category": "unknown", "reason": f"read_error: {e}", "sample_texts": []}

    if not sample_texts:
        return {"is_business": False, "category": "unknown", "reason": "no_messages", "sample_texts": []}

    combined = "\n---\n".join(sample_texts[:10])

    # Быстрый pre-filter по ключевым словам мусора
    combined_lower = combined.lower()
    junk_hits = sum(1 for kw in _JUNK_KEYWORDS if kw in combined_lower)
    if junk_hits >= 3:
        return {"is_business": False, "category": "junk", "reason": f"junk_keywords: {junk_hits}", "sample_texts": sample_texts}

    # AI классификация
    prompt = (
        f"Проанализируйте сообщения из Telegram-чата \"{chat['title']}\" "
        f"(участников: {chat['participants_count']}).\n\n"
        f"Последние сообщения:\n{combined}\n\n"
        f"Определите тип чата. Ответьте JSON:\n"
        f'{{"category": "freelance|business|junk|unknown", '
        f'"reason": "краткое объяснение", '
        f'"has_orders": true/false}}\n\n'
        f"freelance — чат где ищут разработчиков/исполнителей, публикуют заказы.\n"
        f"business — чат с деловыми обсуждениями, нетворкингом.\n"
        f"junk — спам, реклама, казино, порно, мошенничество.\n"
        f"unknown — непонятная тематика."
    )

    try:
        result = await ai_engine._chat_with_fallback(
            ai_engine.CLASSIFY_AI_PROVIDER, ai_engine.CLASSIFY_MODEL,
            [{"role": "user", "content": prompt}],
            temperature=0.3, max_tokens=200,
        )
        if result:
            parsed = ai_engine._extract_json(result)
            category = parsed.get("category", "unknown")
            reason = parsed.get("reason", "")
            has_orders = parsed.get("has_orders", False)
            is_business = category in ("freelance", "business")
            return {
                "is_business": is_business,
                "category": category,
                "reason": reason,
                "has_orders": has_orders,
                "sample_texts": sample_texts,
            }
    except Exception as e:
        logger.error(f"AI классификация чата @{chat_username}: {e}")

    # Fallback: считаем бизнес-ключевые слова
    business_hits = sum(1 for kw in _BUSINESS_KEYWORDS if kw in combined_lower)
    if business_hits >= 3:
        return {"is_business": True, "category": "business", "reason": f"business_keywords: {business_hits}", "sample_texts": sample_texts}

    return {"is_business": False, "category": "unknown", "reason": "no_signal", "sample_texts": sample_texts}


async def search_and_scan(client, keywords: list[str] = None,
                          progress_callback=None) -> list[dict]:
    """Полный цикл: поиск + сканирование. Возвращает только бизнес-чаты.

    progress_callback: async функция, вызывается с dict статуса после каждого чата.
    """
    from datetime import datetime
    _search_status.update({
        "total_found": 0, "scanned": 0, "approved": 0, "rejected": 0,
        "current_chat": "", "current_chat_title": "",
        "is_running": True, "started_at": datetime.now().strftime("%H:%M:%S"),
    })

    chats = await search_chats(client, keywords)
    _search_status["total_found"] = len(chats)
    logger.info(f"Найдено {len(chats)} чатов, начинаем сканирование...")

    if progress_callback:
        await progress_callback(dict(_search_status))

    good_chats = []
    for chat in chats:
        _search_status["current_chat"] = chat["username"]
        _search_status["current_chat_title"] = chat["title"]

        if progress_callback:
            await progress_callback(dict(_search_status))

        scan = await scan_chat_quality(client, chat)
        chat["scan"] = scan
        _search_status["scanned"] += 1

        if scan["is_business"]:
            good_chats.append(chat)
            _search_status["approved"] += 1
            logger.info(f"✅ @{chat['username']} — {scan['category']} ({scan['reason']})")
        else:
            _search_status["rejected"] += 1
            logger.info(f"❌ @{chat['username']} — {scan['category']} ({scan['reason']})")

        if progress_callback:
            await progress_callback(dict(_search_status))

        await asyncio.sleep(2)  # анти-бан задержка

    _search_status["is_running"] = False
    logger.info(f"Сканирование завершено: {len(good_chats)} бизнес-чатов из {len(chats)}")
    return good_chats


async def search_chats_no_ai(client, keywords: list[str] = None,
                             batch_size: int = None,
                             exclude_already_found: bool = True) -> list[dict]:
    """Ищет чаты по ключевым словам БЕЗ AI-проверки.

    Фильтрация:
    - по размеру (min/max members)
    - по exclusion keywords (в названии и username)
    - исключает уже найденные ранее чаты (из БД)

    Возвращает список чатов: {id, title, username, participants_count, is_channel, is_group, search_keyword}
    """
    if keywords is None:
        keywords = CHAT_SEARCH_KEYWORDS
    if batch_size is None:
        batch_size = CHAT_SEARCH_BATCH_SIZE

    already_found = await db.get_all_found_chat_ids() if exclude_already_found else set()

    found = {}
    for kw in keywords:
        if len(found) >= batch_size:
            break
        try:
            result = await client(SearchGlobalRequest(q=kw, limit=CHAT_SEARCH_MAX_RESULTS))
            for chat in result.chats:
                chat_id = chat.id
                if chat_id in found or chat_id in already_found:
                    continue

                participants = getattr(chat, "participants_count", 0) or 0
                username = getattr(chat, "username", None)
                if not username:
                    continue

                if participants < CHAT_SEARCH_MIN_MEMBERS:
                    continue
                if participants > CHAT_SEARCH_MAX_MEMBERS:
                    continue

                title = chat.title or ""
                title_lower = title.lower()
                username_lower = username.lower()

                # Фильтр по exclusion keywords
                is_junk = any(
                    ex_kw in title_lower or ex_kw in username_lower
                    for ex_kw in CHAT_SEARCH_EXCLUSION_KEYWORDS
                )
                if is_junk:
                    logger.info(f"Пропуск @{username} — exclusion keyword в названии")
                    continue

                is_channel = getattr(chat, "megagroup", False) is False and getattr(chat, "broadcast", False)
                is_group = getattr(chat, "megagroup", False)

                found[chat_id] = {
                    "id": chat_id,
                    "title": title,
                    "username": username,
                    "participants_count": participants,
                    "is_channel": is_channel,
                    "is_group": is_group,
                    "search_keyword": kw,
                }

                if len(found) >= batch_size:
                    break

            logger.info(f"Поиск по '{kw}': найдено {len(result.chats)} чатов, отобрано {len(found)}")
            await asyncio.sleep(1)
        except Exception as e:
            logger.error(f"Ошибка поиска по '{kw}': {e}")

    return list(found.values())


async def join_and_organize_chat(client, chat: dict, folder: str = None) -> bool:
    """Вступает в чат, мьютит, архивирует, добавляет в папку.
    Проверяет дневной лимит вступлений.

    folder: 'freelance' или 'business' — определяет в какую папку Telegram добавить.
    """
    username = chat["username"]

    # Проверяем дневной лимит
    joined_today = await db.get_chat_joins_today()
    if joined_today >= CHAT_JOIN_DAILY_LIMIT:
        logger.warning(f"Дневной лимит вступлений ({CHAT_JOIN_DAILY_LIMIT}) исчерпан. Пропускаю @{username}")
        return False

    try:
        # 1. Вступаем в чат
        entity = await client.get_entity(username)
        await client(JoinChannelRequest(entity))
        await db.record_chat_join(username)
        logger.info(f"Вступил в @{username} (сегодня: {joined_today + 1}/{CHAT_JOIN_DAILY_LIMIT})")

        await asyncio.sleep(2)

        # 2. Мьютим уведомления
        await client(UpdateNotifySettingsRequest(
            peer=entity,
            settings=InputPeerNotifySettings(
                mute_until=2 ** 31 - 1,  # навсегда
                show_previews=False,
                silent=True,
            )
        ))
        logger.info(f"Мьют включён для @{username}")

        # 3. Архивируем
        peer = await client.get_input_entity(entity)
        await client(EditPeerFoldersRequest(
            folder_peers=[InputFolderPeer(peer=peer, folder_id=1)]
        ))
        logger.info(f"Чат @{username} отправлен в архив")

        # 4. Добавляем в папку
        if folder:
            await add_chat_to_folder(client, peer, folder)

        return True
    except Exception as e:
        logger.error(f"Ошибка вступления/организации @{username}: {e}")
        return False


async def get_dialog_folders(client) -> list:
    """Получает список папок диалогов Telegram."""
    try:
        result = await client(GetDialogFiltersRequest())
        return result.filters
    except Exception as e:
        logger.error(f"Ошибка получения папок: {e}")
        return []


async def add_chat_to_folder(client, peer, folder_name: str):
    """Добавляет чат в папку Telegram. Создаёт папку если её нет.

    folder_name: 'freelance' или 'business' (маппится на CHAT_FOLDER_FREELANCE/BUSINESS)
    """
    folder_title = CHAT_FOLDER_FREELANCE if folder_name == "freelance" else CHAT_FOLDER_BUSINESS

    try:
        filters = await get_dialog_folders(client)

        # Ищем существующую папку
        target_filter = None
        for f in filters:
            if isinstance(f, DialogFilter) and getattr(f, "title", "") == folder_title:
                target_filter = f
                break

        if target_filter:
            # Добавляем peer в include_peers
            include_peers = list(target_filter.include_peers or [])
            peer_id = utils.get_peer_id(peer)
            if peer_id not in include_peers:
                include_peers.append(peer)
            await client(UpdateDialogFilterRequest(
                id=target_filter.id,
                filter=DialogFilter(
                    id=target_filter.id,
                    title=folder_title,
                    include_peers=include_peers,
                    exclude_peers=target_filter.exclude_peers or [],
                    pinned_peers=target_filter.pinned_peers or [],
                )
            ))
            logger.info(f"Чат добавлен в папку '{folder_title}'")
        else:
            logger.warning(f"Папка '{folder_title}' не найдена. Создайте её вручную в Telegram.")
    except Exception as e:
        logger.error(f"Ошибка добавления в папку: {e}")
