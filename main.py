"""Точка входа: запуск всех компонентов системы."""
import asyncio
import logging
import sys
import os

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("bot.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("main")

# Создаём директории
os.makedirs("data", exist_ok=True)
os.makedirs("logs", exist_ok=True)

import config
import database as db
from user_client import UserClient
from notification_bot import NotificationBot
from scheduler import MessageScheduler


async def main():
    """Главная асинхронная функция: инициализация и запуск."""
    # Проверка конфигурации
    errors = []
    if not config.TG_API_ID or not config.TG_API_HASH:
        errors.append("TG_API_ID и TG_API_HASH не настроены")
    if not config.NOTIF_BOT_TOKEN:
        errors.append("NOTIF_BOT_TOKEN не настроен")
    if not config.OWNER_TG_ID:
        errors.append("OWNER_TG_ID не настроен")
    # OmniRoute работает без ключа (ключ по умолчанию "omni"), так что проверяем только если провайдер не omniroute
    has_ai_key = (config.OMNIROUTE_API_KEY or config.GROQ_API_KEY or
                  config.OPENROUTER_API_KEY or config.OPENAI_API_KEY or
                  config.DEEPSEEK_API_KEY)
    if not has_ai_key:
        errors.append("Нужен хотя бы один AI провайдер. Бесплатно: OmniRoute (npm install -g omniroute) или Groq (console.groq.com)")

    if errors:
        logger.error("Ошибки конфигурации:")
        for e in errors:
            logger.error(f"  - {e}")
        logger.error("Скопируйте .env.example в .env и заполните настройки")
        sys.exit(1)

    # Инициализация БД
    logger.info("Инициализация базы данных...")
    await db.init_db()

    # Создание компонентов
    notif_bot = NotificationBot()

    async def notification_router(**kwargs):
        """Маршрутизатор уведомлений: обычные лиды -> notify_lead,
        холодные лиды -> notify_cold_lead."""
        category = kwargs.get("category", "")
        if category in ("HOT_COLD", "WARM_COLD"):
            await notif_bot.notify_cold_lead(**kwargs)
        else:
            await notif_bot.notify_lead(**kwargs)

    user_client = UserClient(
        notification_callback=notification_router
    )
    notif_bot.user_client = user_client

    async def broadcast_notify(text: str):
        await notif_bot.bot.send_message(config.OWNER_TG_ID, text)

    scheduler = MessageScheduler(user_client=user_client,
                                 broadcast_notify_callback=broadcast_notify)

    # Запуск
    logger.info("Запуск рабочего аккаунта (Telethon)...")
    await user_client.start()

    logger.info("Запуск планировщика...")
    await scheduler.start()

    logger.info("Запуск бота уведомлений...")
    bot_task = asyncio.create_task(notif_bot.start())

    logger.info("✅ Система запущена! Ожидание лидов...")
    logger.info("Управление через бота уведомлений. Команда: /help")

    try:
        # Держим основной цикл
        await bot_task
    except asyncio.CancelledError:
        pass
    finally:
        logger.info("Остановка системы...")
        await scheduler.stop()
        await user_client.stop()
        await notif_bot.stop()
        logger.info("Система остановлена")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Прервано пользователем")
    except Exception as e:
        logger.error(f"Фатальная ошибка: {e}", exc_info=True)
        sys.exit(1)
