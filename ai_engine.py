"""AI движок: интеграция с OmniRoute, Groq, OpenRouter, OpenAI и DeepSeek.

Все провайдеры используют OpenAI-совместимый API — отличается только base_url.
OmniRoute — агрегатор 50+ бесплатных провайдеров (Claude, GPT, Gemini) с auto-fallback.
Groq и OpenRouter также имеют бесплатные модели.
"""
import json
import logging
import re
import httpx
from openai import AsyncOpenAI
from config import (OMNIROUTE_API_KEY, OMNIROUTE_BASE_URL,
                    GROQ_API_KEY, GROQ_API_KEY_2, GROQ_API_KEY_3, OPENROUTER_API_KEY, OPENAI_API_KEY, DEEPSEEK_API_KEY, ANTHROPIC_API_KEY, GEMINI_API_KEY, NVIDIA_API_KEY,
                    BYTEPLUS_API_KEY, BYTEPLUS_ENDPOINT_ID,
                    AI_HTTP_PROXY,
                    DIALOG_AI_PROVIDER, DIALOG_MODEL,
                    CLASSIFY_AI_PROVIDER, CLASSIFY_MODEL,
                    BROADCAST_AI_PROVIDER, BROADCAST_MODEL,
                    CLASSIFY_SYSTEM_PROMPT, DIALOG_SYSTEM_PROMPT,
                    COLD_CLASSIFY_PROMPT,
                    BROADCAST_PROMPT, NO_RULES_MARKER, DIRECT_PROMPT,
                    DEVELOPER_INFO, DEVELOPER_GENDER)

logger = logging.getLogger(__name__)

_GENDER_RU = {"male": "мужской", "female": "женский"}


async def _get_gender_ru() -> str:
    """Пол разработчика: из настроек БД (меняется через GUI), иначе из .env."""
    try:
        import database as db
        value = await db.get_setting("developer_gender")
    except Exception:
        value = None
    return _GENDER_RU.get(value or DEVELOPER_GENDER, "мужской")

# Base URLs для OpenAI-совместимых провайдеров
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
ANTHROPIC_BASE_URL = "https://api.anthropic.com/v1"
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"
NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
BYTEPLUS_BASE_URL = "https://ark.ap-southeast.bytepluses.com/api/v3"

# Модели, которые поддерживают response_format json_object
_JSON_MODELS = {
    "gpt-4o", "gpt-4o-mini", "gpt-4-turbo",
    "deepseek-chat", "deepseek-reasoner",
}

_clients: dict[str, AsyncOpenAI] = {}

# Все ключи Groq для ротации при 429
_GROQ_KEYS = [k for k in (GROQ_API_KEY, GROQ_API_KEY_2, GROQ_API_KEY_3) if k]
_groq_key_idx = 0


def _get_groq_client() -> AsyncOpenAI:
    """Создаёт Groq-клиент с текущим ключом (ротация при 429)."""
    global _groq_key_idx
    key = _GROQ_KEYS[_groq_key_idx % len(_GROQ_KEYS)]
    return AsyncOpenAI(
        api_key=key,
        base_url=GROQ_BASE_URL,
        http_client=_proxied_http_client(),
        max_retries=0
    )


def _rotate_groq_key():
    """Переключается на следующий Groq ключ."""
    global _groq_key_idx
    _groq_key_idx += 1
    if len(_GROQ_KEYS) > 1:
        logger.warning(f"Ротация Groq ключа: теперь ключ #{_groq_key_idx % len(_GROQ_KEYS) + 1}")
    # Сбрасываем кэш клиента чтобы пересоздался с новым ключом
    _clients.pop("groq", None)


async def _try_groq_all_keys(model: str, messages: list, kwargs: dict) -> str | None:
    """Пробует указанную модель на всех доступных ключах Groq по очереди
    (начиная с текущего). Ротирует ключ при неудаче."""
    for _ in range(len(_GROQ_KEYS)):
        try:
            client = _get_client("groq")
            response = await client.chat.completions.create(
                model=model, messages=messages, **kwargs
            )
            if response.choices and response.choices[0].message.content:
                return response.choices[0].message.content
        except Exception as e:
            logger.warning(f"Groq ключ #{_groq_key_idx % len(_GROQ_KEYS) + 1}, модель {model}: {e}")
        _rotate_groq_key()
    return None


def _extract_json(text: str | None) -> dict:
    """Извлекает JSON из текста ответа (для моделей без response_format).

    Устойчив к: None, reasoning-блокам, обрезанному JSON (max_tokens),
    JSON внутри code-блоков и лишнему тексту вокруг.
    """
    _default = {"category": "NOT_LEAD", "task": "", "budget": "", "deadline": ""}

    if not text:
        logger.warning("Пустой ответ AI (content=None)")
        return dict(_default)

    # Убираем reasoning-блоки <think>...</think>
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()

    # Если текст уже валидный JSON
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Ищем JSON в блоке кода ```json ... ```
    match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    # Ищем от первой { до последней } (JSON с вложенностью)
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass
    # Ищем первый плоский { ... } блок
    match = re.search(r'\{[^{}]*\}', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    # Последний рубеж: JSON обрезан лимитом токенов — вытаскиваем поля регэкспами,
    # чтобы не потерять лид из-за оборванного ответа
    field_re = re.compile(r'"(category|task|budget|deadline|business_type|pain|hook'
                          r'|skip|reason|variant_a|variant_b|variant_c|selected'
                          r'|chat_niche|entry_point|message)"'
                          r'\s*:\s*"?([^",}]*)', re.DOTALL)
    fields = {m.group(1): m.group(2).strip() for m in field_re.finditer(text)}
    if fields.get("category") or fields.get("skip") or fields.get("variant_a"):
        logger.warning(f"JSON обрезан, восстановил поля регэкспом: {list(fields.keys())}")
        result = {}
        result.update(fields)
        # Преобразуем skip из строки в bool если нужно
        if "skip" in result:
            result["skip"] = result["skip"].lower() in ("true", "1", "yes")
        return result

    logger.warning(f"Не удалось извлечь JSON из ответа: {text[:200]}")
    return dict(_default)


def _proxied_http_client() -> httpx.AsyncClient | None:
    """HTTP-клиент с прокси для внешних провайдеров (Groq блокирует РФ-IP 403 Forbidden)."""
    if AI_HTTP_PROXY:
        return httpx.AsyncClient(proxy=AI_HTTP_PROXY)
    return None


def _get_client(provider: str) -> AsyncOpenAI:
    """Возвращает клиент для указанного провайдера."""
    if provider not in _clients:
        if provider == "omniroute":
            _clients[provider] = AsyncOpenAI(
                api_key=OMNIROUTE_API_KEY,
                base_url=OMNIROUTE_BASE_URL,
                max_retries=0
            )
        elif provider == "groq":
            if not _GROQ_KEYS:
                raise ValueError("GROQ_API_KEY не настроен. Получите бесплатно на console.groq.com")
            _clients[provider] = _get_groq_client()
        elif provider == "openrouter":
            if not OPENROUTER_API_KEY:
                raise ValueError("OPENROUTER_API_KEY не настроен. Получите на openrouter.ai")
            _clients[provider] = AsyncOpenAI(
                api_key=OPENROUTER_API_KEY,
                base_url=OPENROUTER_BASE_URL,
                http_client=_proxied_http_client(),
                max_retries=0
            )
        elif provider == "openai":
            if not OPENAI_API_KEY:
                raise ValueError("OPENAI_API_KEY не настроен")
            _clients[provider] = AsyncOpenAI(api_key=OPENAI_API_KEY, http_client=_proxied_http_client())
        elif provider == "deepseek":
            if not DEEPSEEK_API_KEY:
                raise ValueError("DEEPSEEK_API_KEY не настроен")
            _clients[provider] = AsyncOpenAI(
                api_key=DEEPSEEK_API_KEY,
                base_url=DEEPSEEK_BASE_URL,
                http_client=_proxied_http_client()
            )
        elif provider == "anthropic":
            if not ANTHROPIC_API_KEY:
                raise ValueError("ANTHROPIC_API_KEY не настроен. Получите на console.anthropic.com")
            _clients[provider] = AsyncOpenAI(
                api_key=ANTHROPIC_API_KEY,
                base_url=ANTHROPIC_BASE_URL,
                http_client=_proxied_http_client()
            )
        elif provider == "gemini":
            if not GEMINI_API_KEY:
                raise ValueError("GEMINI_API_KEY не настроен. Получите бесплатно на ai.google.dev")
            _clients[provider] = AsyncOpenAI(
                api_key=GEMINI_API_KEY,
                base_url=GEMINI_BASE_URL,
            )
        elif provider == "nvidia":
            if not NVIDIA_API_KEY:
                raise ValueError("NVIDIA_API_KEY не настроен. Получите бесплатно на build.nvidia.com")
            _clients[provider] = AsyncOpenAI(
                api_key=NVIDIA_API_KEY,
                base_url=NVIDIA_BASE_URL,
                max_retries=0
            )
        elif provider == "byteplus":
            if not BYTEPLUS_API_KEY:
                raise ValueError("BYTEPLUS_API_KEY не настроен. Регистрация: byteplus.com -> ModelArk")
            _clients[provider] = AsyncOpenAI(
                api_key=BYTEPLUS_API_KEY,
                base_url=BYTEPLUS_BASE_URL,
                max_retries=0
            )
        else:
            raise ValueError(f"Неизвестный AI провайдер: {provider}")
    return _clients[provider]


_RETRYABLE_CODES = ["429", "502", "503", "rate limit", "too many requests",
                     "all_accounts_inactive", "service temporarily unavailable"]

# response_format json_object не поддерживается Groq для некоторых моделей —
# убираем его при кросс-провайдерном fallback, чтобы не сломать запрос.
_GROQ_FALLBACK_MODEL = "llama-3.3-70b-versatile"


async def _chat_with_fallback(
    provider: str,
    model: str,
    messages: list[dict],
    **kwargs
) -> str | None:
    """Отправляет запрос к AI с fallback: сначала auto на том же провайдере,
    затем (если настроен) кросс-провайдерный fallback на Groq."""
    client = _get_client(provider)
    try:
        response = await client.chat.completions.create(
            model=model, messages=messages, **kwargs
        )
        if not response.choices:
            logger.warning(f"AI вернул пустой choices для модели {model}")
            return None
        return response.choices[0].message.content
    except Exception as e:
        error_text = str(e).lower()
        if not any(code in error_text for code in _RETRYABLE_CODES):
            raise
        logger.warning(f"Модель {model} недоступна ({e}), пробуем fallback")
        if provider == "groq":
            # Пробуем ту же модель на всех оставшихся ключах Groq
            result = await _try_groq_all_keys(model, messages, kwargs)
            if result:
                return result
            # Пробуем резервную модель на всех ключах Groq
            result = await _try_groq_all_keys(_GROQ_FALLBACK_MODEL, messages, kwargs)
            if result:
                return result
            logger.error("Все ключи Groq и резервная модель не сработали")
            # Groq исчерпан — пробуем OpenRouter как бесплатный fallback
            if OPENROUTER_API_KEY:
                logger.warning("Пробуем fallback на OpenRouter (бесплатная модель)")
                try:
                    or_client = _get_client("openrouter")
                    or_kwargs = {k: v for k, v in kwargs.items() if k != "response_format"}
                    response = await or_client.chat.completions.create(
                        model="nvidia/nemotron-3-super-120b-a12b:free", messages=messages, **or_kwargs
                    )
                    if response.choices and response.choices[0].message.content:
                        return response.choices[0].message.content
                    else:
                        logger.warning(f"OpenRouter вернул пустой ответ: {response}")
                except Exception as e_or:
                    logger.error(f"Fallback на OpenRouter тоже не сработал: {e_or}")
            # OpenRouter тоже недоступен — пробуем NVIDIA NIM
            if NVIDIA_API_KEY:
                logger.warning("Пробуем fallback на NVIDIA NIM")
                try:
                    nv_client = _get_client("nvidia")
                    nv_kwargs = {k: v for k, v in kwargs.items() if k != "response_format"}
                    response = await nv_client.chat.completions.create(
                        model="nvidia/nemotron-3-super-120b-a12b", messages=messages, **nv_kwargs
                    )
                    if response.choices and response.choices[0].message.content:
                        return response.choices[0].message.content
                    else:
                        logger.warning(f"NVIDIA NIM вернул пустой ответ: {response}")
                except Exception as e_nv:
                    logger.error(f"Fallback на NVIDIA NIM тоже не сработал: {e_nv}")
            # NVIDIA тоже недоступна — пробуем BytePlus ModelArk
            if BYTEPLUS_API_KEY and BYTEPLUS_ENDPOINT_ID:
                logger.warning("Пробуем fallback на BytePlus ModelArk")
                try:
                    bp_client = _get_client("byteplus")
                    bp_kwargs = {k: v for k, v in kwargs.items() if k != "response_format"}
                    response = await bp_client.chat.completions.create(
                        model=BYTEPLUS_ENDPOINT_ID, messages=messages, **bp_kwargs
                    )
                    if response.choices and response.choices[0].message.content:
                        return response.choices[0].message.content
                    else:
                        logger.warning(f"BytePlus вернул пустой ответ: {response}")
                except Exception as e_bp:
                    logger.error(f"Fallback на BytePlus тоже не сработал: {e_bp}")
            raise
        try:
            response = await client.chat.completions.create(
                model="auto", messages=messages, **kwargs
            )
            if not response.choices:
                logger.warning(f"AI вернул пустой choices для модели auto")
                return None
            return response.choices[0].message.content
        except Exception as e2:
            logger.error(f"Fallback auto тоже не сработал: {e2}")
            # Пробуем OpenRouter (бесплатные модели) как fallback
            if provider != "openrouter" and OPENROUTER_API_KEY:
                logger.warning("Пробуем fallback на OpenRouter (бесплатная модель)")
                try:
                    or_client = _get_client("openrouter")
                    or_kwargs = {k: v for k, v in kwargs.items() if k != "response_format"}
                    response = await or_client.chat.completions.create(
                        model="nvidia/nemotron-3-super-120b-a12b:free", messages=messages, **or_kwargs
                    )
                    if response.choices:
                        return response.choices[0].message.content
                except Exception as e_or:
                    logger.error(f"Fallback на OpenRouter тоже не сработал: {e_or}")
            # Последний рубеж: Groq
            if provider != "groq" and _GROQ_KEYS:
                logger.warning("Пробуем кросс-провайдерный fallback на Groq")
                try:
                    groq_client = _get_client("groq")
                    groq_kwargs = {k: v for k, v in kwargs.items() if k != "response_format"}
                    response = await groq_client.chat.completions.create(
                        model=_GROQ_FALLBACK_MODEL, messages=messages, **groq_kwargs
                    )
                    if response.choices:
                        return response.choices[0].message.content
                except Exception as e3:
                    logger.error(f"Fallback на Groq тоже не сработал: {e3}")
            raise


async def classify_message(message_text: str) -> dict | None:
    """Классификация сообщения: лид/не лид. Возвращает dict с category, task, budget, deadline."""
    try:
        client = _get_client(CLASSIFY_AI_PROVIDER)
        kwargs = {
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

        gender_ru = await _get_gender_ru()
        system_content = f"{DIALOG_SYSTEM_PROMPT.format(gender=gender_ru)}\n\n{DEVELOPER_INFO}\n\n"
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


# === A/B Testing: генерация вариантов первого сообщения ===

_AB_VARIANT_STYLES = [
    "Прямой подход: сразу к делу, покажите экспертизу и предложите обсудить проект.",
    "Дружелюбный подход: начните с эмпатии к проблеме клиента, мягко предложите помощь.",
    "Вопросный подход: задайте уточняющий вопрос по его запросу, чтобы завязать диалог.",
]


async def generate_first_message_variants(
    context: str,
    anti_repeat_note: str = "",
    num_variants: int = 3,
) -> list[str]:
    """Генерирует num_variants вариантов первого сообщения разными подходами.

    Возвращает список текстов (может быть меньше num_variants при ошибках).
    """
    gender_ru = await _get_gender_ru()
    system_content = f"{DIALOG_SYSTEM_PROMPT.format(gender=gender_ru)}\n\n{DEVELOPER_INFO}\n\n"
    system_content += "Текущая стадия диалога: INITIATING\n"

    variants = []
    for i in range(num_variants):
        style = _AB_VARIANT_STYLES[i % len(_AB_VARIANT_STYLES)]
        user_msg = f"{context}\n\nСтиль сообщения: {style}\n{anti_repeat_note}"
        messages = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_msg},
        ]
        try:
            content = await _chat_with_fallback(
                DIALOG_AI_PROVIDER, DIALOG_MODEL, messages,
                temperature=0.8 + i * 0.1, max_tokens=300,
            )
            if content and content.strip():
                variants.append(content.strip())
        except Exception as e:
            logger.error(f"Ошибка генерации варианта #{i}: {e}")

    if not variants:
        logger.error("Не удалось сгенерировать ни одного варианта первого сообщения")
    return variants


# Диапазоны Unicode для "чужих" алфавитов, которые не должны появляться в русском тексте
# (CJK, хирагана/катакана, хангыль, арабский, деванагари и т.п.)
_FOREIGN_SCRIPT_RE = re.compile(
    r'[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af\u0600-\u06ff\u0900-\u097f'
    r'\u0e00-\u0e7f\u1100-\u11ff\uff00-\uffef]'
)


def _has_foreign_script(text: str) -> bool:
    """Проверяет, есть ли в тексте символы чужих алфавитов (баг слабых моделей)."""
    return bool(_FOREIGN_SCRIPT_RE.search(text or ""))


async def generate_broadcast(chat_rules: str, recent_messages: list[str],
                             now_str: str, is_direct_promo: bool = False) -> dict | None:
    """Генерация рекламной рассылки с учётом правил чата.

    Если is_direct_promo=True — используется прямой промпт (для чатов без правил).
    Возвращает {"skip": bool, "reason": str, "message": str} или None при ошибке.
    """
    try:
        recent_text = "\n---\n".join(recent_messages) if recent_messages else "(ещё не было отправок)"

        if is_direct_promo:
            prompt = DIRECT_PROMPT.format(
                gender=await _get_gender_ru(),
                developer_info=DEVELOPER_INFO,
                recent_messages=recent_text,
                current_datetime=now_str,
            )
        else:
            rules_text = chat_rules.strip()
            if rules_text == NO_RULES_MARKER:
                rules_text = "Правил в чате нет (владелец подтвердил). Пиши в нейтральном деловом тоне."

            prompt = BROADCAST_PROMPT.format(
                gender=await _get_gender_ru(),
                developer_info=DEVELOPER_INFO,
                chat_rules=rules_text,
                recent_messages=recent_text,
                current_datetime=now_str,
            )

        messages = [{"role": "user", "content": prompt}]

        logger.info(f"generate_broadcast: is_direct_promo={is_direct_promo}, prompt_len={len(prompt)}")
        content = await _chat_with_fallback(
            BROADCAST_AI_PROVIDER, BROADCAST_MODEL,
            messages,
            temperature=0.9, max_tokens=1200,
        )
        logger.info(f"generate_broadcast: AI ответил, content_len={len(content) if content else 0}")

        # Retry с напоминанием о языке, если первая попытка вернула посторонние символы
        if content and _has_foreign_script(content):
            logger.warning("generate_broadcast: первая попытка содержала посторонние символы, retry")
            messages.append({"role": "assistant", "content": content})
            messages.append({"role": "user", "content": "В твоём ответе обнаружены иероглифы или символы чужих алфавитов. Перепиши ТОЛЬКО на русском кириллицей. Убери все нерусские символы. Верни только JSON."})
            content = await _chat_with_fallback(
                BROADCAST_AI_PROVIDER, BROADCAST_MODEL,
                messages,
                temperature=0.7, max_tokens=1200,
            )
        if not content:
            logger.error("generate_broadcast: content пустой после AI")
            return None

        logger.info(f"generate_broadcast: raw content[:300]={content[:300]}")
        result = _extract_json(content)
        logger.info(f"generate_broadcast: extracted keys={list(result.keys())}")
        skip = result.get("skip")
        if not isinstance(skip, bool):
            logger.error(f"Неверный формат ответа рассылки (skip не bool): skip={skip!r}, content[:200]={content[:200]}")
            return None
        
        # Получаем выбранный вариант
        selected = result.get("selected", "a").lower()
        variant_key = f"variant_{selected}"
        message = result.get(variant_key, "").strip()
        
        # Если выбранного варианта нет, пробуем другие
        if not message:
            for key in ["variant_a", "variant_b", "variant_c"]:
                if result.get(key, "").strip():
                    message = result.get(key, "").strip()
                    break
        
        if not skip and not message:
            logger.error(f"Рассылка без текста при skip=false: selected={selected!r}, variant_key={variant_key!r}, result_keys={list(result.keys())}, content[:300]={content[:300]}")
            return None

        # Защита от бага слабых моделей: посторонние алфавиты (иероглифы и т.п.)
        if not skip and _has_foreign_script(message):
            logger.error(f"Рассылка отклонена: обнаружены посторонние символы в тексте: {message[:200]}")
            return None

        result["reason"] = result.get("reason") or ""
        result["message"] = message
        niche = result.get("chat_niche", "")
        logger.info(f"Рассылка сгенерирована: skip={skip}, selected={selected}, niche={niche}, "
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
