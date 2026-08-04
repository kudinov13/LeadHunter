"""AI движок: интеграция с OmniRoute, Groq, OpenRouter, OpenAI и DeepSeek.

Все провайдеры используют OpenAI-совместимый API — отличается только base_url.
OmniRoute — агрегатор 50+ бесплатных провайдеров (Claude, GPT, Gemini) с auto-fallback.
Groq и OpenRouter также имеют бесплатные модели.
"""
import json
import logging
from openai import AsyncOpenAI
from config import (OMNIROUTE_API_KEY, OMNIROUTE_BASE_URL,
                    GROQ_API_KEY, OPENROUTER_API_KEY, OPENAI_API_KEY, DEEPSEEK_API_KEY,
                    DIALOG_AI_PROVIDER, DIALOG_MODEL,
                    CLASSIFY_AI_PROVIDER, CLASSIFY_MODEL,
                    BROADCAST_AI_PROVIDER, BROADCAST_MODEL,
                    CLASSIFY_SYSTEM_PROMPT, DIALOG_SYSTEM_PROMPT,
                    BROADCAST_PROMPT, NO_RULES_MARKER,
                    DEVELOPER_INFO)

logger = logging.getLogger(__name__)

# Base URLs для OpenAI-совместимых провайдеров
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"

# Модели, которые поддерживают response_format json_object
_JSON_MODELS = {
    "gpt-4o", "gpt-4o-mini", "gpt-4-turbo",
    "deepseek-chat", "deepseek-reasoner",
}

_clients: dict[str, AsyncOpenAI] = {}


def _extract_json(text: str) -> dict:
    """Извлекает JSON из текста ответа (для моделей без response_format)."""
    # Если текст уже валидный JSON
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Ищем JSON в блоке кода ```json ... ```
    import re
    match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    # Ищем первый { ... } блок
    match = re.search(r'\{[^{}]*\}', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    # Если не удалось — возвращаем NOT_LEAD
    logger.warning(f"Не удалось извлечь JSON из ответа: {text[:200]}")
    return {"category": "NOT_LEAD", "task": "", "budget": "", "deadline": ""}


def _get_client(provider: str) -> AsyncOpenAI:
    """Возвращает клиент для указанного провайдера."""
    if provider not in _clients:
        if provider == "omniroute":
            _clients[provider] = AsyncOpenAI(
                api_key=OMNIROUTE_API_KEY,
                base_url=OMNIROUTE_BASE_URL
            )
        elif provider == "groq":
            if not GROQ_API_KEY:
                raise ValueError("GROQ_API_KEY не настроен. Получите бесплатно на console.groq.com")
            _clients[provider] = AsyncOpenAI(
                api_key=GROQ_API_KEY,
                base_url=GROQ_BASE_URL
            )
        elif provider == "openrouter":
            if not OPENROUTER_API_KEY:
                raise ValueError("OPENROUTER_API_KEY не настроен. Получите на openrouter.ai")
            _clients[provider] = AsyncOpenAI(
                api_key=OPENROUTER_API_KEY,
                base_url=OPENROUTER_BASE_URL
            )
        elif provider == "openai":
            if not OPENAI_API_KEY:
                raise ValueError("OPENAI_API_KEY не настроен")
            _clients[provider] = AsyncOpenAI(api_key=OPENAI_API_KEY)
        elif provider == "deepseek":
            if not DEEPSEEK_API_KEY:
                raise ValueError("DEEPSEEK_API_KEY не настроен")
            _clients[provider] = AsyncOpenAI(
                api_key=DEEPSEEK_API_KEY,
                base_url=DEEPSEEK_BASE_URL
            )
        else:
            raise ValueError(f"Неизвестный AI провайдер: {provider}")
    return _clients[provider]


async def _chat_with_fallback(
    provider: str,
    model: str,
    messages: list[dict],
    **kwargs
) -> str | None:
    """Отправляет запрос к AI с fallback на auto при ошибке."""
    client = _get_client(provider)
    try:
        response = await client.chat.completions.create(
            model=model, messages=messages, **kwargs
        )
        return response.choices[0].message.content
    except Exception as e:
        error_text = str(e).lower()
        if any(code in error_text for code in ["429", "502", "503", "rate limit", "too many requests"]):
            logger.warning(f"Модель {model} недоступна ({e}), пробуем auto fallback")
            try:
                response = await client.chat.completions.create(
                    model="auto", messages=messages, **kwargs
                )
                return response.choices[0].message.content
            except Exception as e2:
                logger.error(f"Fallback auto тоже не сработал: {e2}")
        raise


async def classify_message(message_text: str) -> dict | None:
    """Классификация сообщения: лид/не лид. Возвращает dict с category, task, budget, deadline."""
    try:
        client = _get_client(CLASSIFY_AI_PROVIDER)
        kwargs = {
            "model": CLASSIFY_MODEL,
            "messages": [
                {"role": "system", "content": CLASSIFY_SYSTEM_PROMPT},
                {"role": "user", "content": message_text},
            ],
            "temperature": 0.1,
            "max_tokens": 300,
        }
        # response_format json_object поддерживают не все модели
        if CLASSIFY_MODEL in _JSON_MODELS or CLASSIFY_AI_PROVIDER == "openai":
            kwargs["response_format"] = {"type": "json_object"}
        content = await _chat_with_fallback(
            CLASSIFY_AI_PROVIDER, CLASSIFY_MODEL, kwargs.pop("messages"), **kwargs
        )
        # Если модель не поддерживает json_object, пытаемся извлечь JSON из текста
        result = _extract_json(content)
        logger.info(f"Классификация: {result.get('category')} | task={result.get('task', '')[:60]}")
        return result
    except json.JSONDecodeError as e:
        logger.error(f"Ошибка парсинга JSON от AI: {e}\nСырой ответ: {content}")
        return None
    except Exception as e:
        logger.error(f"Ошибка классификации: {e}")
        return None


async def generate_dialogue_response(
    messages_history: list[dict],
    stage: str,
    price: str = None,
) -> tuple[str, str] | None:
    """Генерация ответа для диалога с клиентом.
    
    Возвращает (текст_ответа, новая_стадия) или None при ошибке.
    """
    try:
        client = _get_client(DIALOG_AI_PROVIDER)

        system_content = f"{DIALOG_SYSTEM_PROMPT}\n\n{DEVELOPER_INFO}\n\n"
        system_content += f"Текущая стадия диалога: {stage}\n"
        if price:
            system_content += f"\nМенеджер установил цену: {price}. "
            system_content += "Презентуй эту цену клиенту через ценность, не как сухую цифру. "
            system_content += "Обоснуй почему эта цена оправдана для его задачи."

        messages = [{"role": "system", "content": system_content}]
        messages.extend(messages_history)

        content = await _chat_with_fallback(
            DIALOG_AI_PROVIDER, DIALOG_MODEL, messages, temperature=0.7, max_tokens=500
        )
        if content:
            content = content.strip()
        else:
            return None

        # Определение новой стадии по содержанию ответа
        new_stage = stage
        if stage == "INITIATING":
            new_stage = "QUALIFYING"
        elif stage == "QUALIFYING":
            # Если AI написал про уточнение у команды — переходим к ожиданию задачи
            if any(phrase in content.lower() for phrase in [
                "у команды", "уточню", "вернусь к вам", "пара минут",
                "паре минут", "через пару", "свяжусь с командой"
            ]):
                new_stage = "TASK_RECEIVED"
        elif stage == "NEGOTIATING":
            # Если диалог идёт к финалу
            if any(phrase in content.lower() for phrase in [
                "договор", "предоплата", "начать работу", "следующий шаг",
                "контакт", "связаться", "телеграм", "почта", "обсудить детали"
            ]):
                new_stage = "CLOSING"

        logger.info(f"AI ответ (стадия {stage} -> {new_stage}): {content[:80]}...")
        return content, new_stage

    except Exception as e:
        logger.error(f"Ошибка генерации ответа диалога: {e}")
        return None


async def generate_broadcast(chat_rules: str, recent_messages: list[str],
                             now_str: str) -> dict | None:
    """Генерация рекламной рассылки с учётом правил чата.

    Возвращает {"skip": bool, "reason": str, "message": str} или None при ошибке.
    """
    try:
        rules_text = chat_rules.strip()
        if rules_text == NO_RULES_MARKER:
            rules_text = "Правил в чате нет (владелец подтвердил). Пиши в нейтральном деловом тоне."

        recent_text = "\n---\n".join(recent_messages) if recent_messages else "(ещё не было отправок)"

        prompt = BROADCAST_PROMPT.format(
            developer_info=DEVELOPER_INFO,
            chat_rules=rules_text,
            recent_messages=recent_text,
            current_datetime=now_str,
        )

        content = await _chat_with_fallback(
            BROADCAST_AI_PROVIDER, BROADCAST_MODEL,
            [{"role": "user", "content": prompt}],
            temperature=0.9, max_tokens=600,
        )
        if not content:
            return None

        result = _extract_json(content)
        skip = result.get("skip")
        if not isinstance(skip, bool):
            logger.error(f"Неверный формат ответа рассылки (skip не bool): {content[:200]}")
            return None
        if not skip and not (result.get("message") or "").strip():
            logger.error(f"Рассылка без текста при skip=false: {content[:200]}")
            return None

        result["reason"] = result.get("reason") or ""
        result["message"] = (result.get("message") or "").strip()
        logger.info(f"Рассылка сгенерирована: skip={skip}, "
                    f"{result['reason'][:60] if skip else result['message'][:60]}")
        return result
    except Exception as e:
        logger.error(f"Ошибка генерации рассылки: {e}")
        return None


async def generate_message_variants(base_text: str, count: int = 3) -> list[str]:
    """Генерация вариантов сообщения для рассылки (анти-бан рандомизация)."""
    try:
        client = _get_client(CLASSIFY_AI_PROVIDER)
        prompt = (
            f"Создай {count} варианта следующего сообщения для Telegram-чата. "
            f"Смысл одинаковый, но формулировки разные. "
            f"Не используй эмодзи. Пиши коротко и естественно.\n\n"
            f"Оригинал: {base_text}\n\n"
            f"Ответай в JSON: {{\"variants\": [\"вариант1\", \"вариант2\", ...]}}"
        )
        kwargs = {
            "model": CLASSIFY_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.8,
            "max_tokens": 500,
        }
        if CLASSIFY_MODEL in _JSON_MODELS or CLASSIFY_AI_PROVIDER == "openai":
            kwargs["response_format"] = {"type": "json_object"}
        content = await _chat_with_fallback(
            CLASSIFY_AI_PROVIDER, CLASSIFY_MODEL, kwargs.pop("messages"), **kwargs
        )
        result = _extract_json(content)
        variants = result.get("variants", [base_text])
        logger.info(f"Сгенерировано {len(variants)} вариантов сообщения")
        return variants
    except Exception as e:
        logger.error(f"Ошибка генерации вариантов: {e}")
        return [base_text]
