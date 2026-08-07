"""Детектор лидов: двухуровневая фильтрация сообщений из чатов."""
import re
import logging
from datetime import datetime, timedelta
from config import (LEAD_KEYWORDS, LEAD_EXCLUDE_KEYWORDS, MAX_MESSAGE_AGE_DAYS,
                    COLD_LEAD_KEYWORDS, COLD_LEAD_EXCLUDE_KEYWORDS,
                    COLD_CLASSIFY_PROMPT)
import ai_engine

logger = logging.getLogger(__name__)

# Компилируем паттерны один раз
_keyword_patterns = [re.compile(kw, re.IGNORECASE) for kw in LEAD_KEYWORDS]
_exclude_patterns = [re.compile(kw, re.IGNORECASE) for kw in LEAD_EXCLUDE_KEYWORDS]
_cold_keyword_patterns = [re.compile(kw, re.IGNORECASE) for kw in COLD_LEAD_KEYWORDS]
_cold_exclude_patterns = [re.compile(kw, re.IGNORECASE) for kw in COLD_LEAD_EXCLUDE_KEYWORDS]

# Минимальная длина сообщения для анализа
MIN_MESSAGE_LENGTH = 15

# Паттерны для отсева ботов/рекламы
_bot_patterns = re.compile(
    r"(?i)(#реклама|#ad|промокод|купить со скидкой|переходи по ссылке|"
    r"казино|ставки|букмекер|криптобиржа|заработок|инвестиции|"
    r"пирамида|mlm|сетевой|"
    r"otc|воркер|дам ворк|ищу людей на|ищу людей для|нужны воркеры|"
    r"сетка проектов|пассивный доход|накрутка|подписчик|"
    r"аккаунты? (steam|discord)|продам аккаунт|куплю аккаунт|"
    r"фрифаер|free fire|верификаци.{0,3}аккаунт)"
)

# Паттерны признаков реального запроса
_request_patterns = re.compile(
    r"(?i)(нужен|ищу|требуется|заказать|сделать|разработать|"
    r"кто\s+может|кто\s+сделает|помогите|оценить|сколько\s+стоит|"
    r"бюджет|сроки|тз|техническое\s+задание|под\s+ключ)"
)


class LeadResult:
    """Результат анализа сообщения."""
    def __init__(self, is_lead: bool, category: str = "NOT_LEAD",
                 task: str = None, budget: str = None, deadline: str = None,
                 market_price: str = None, market_deadline: str = None,
                 business_type: str = None, pain: str = None, hook: str = None,
                 lead_score: int = 0, reason: str = ""):
        self.is_lead = is_lead
        self.category = category
        self.task = task
        self.budget = budget
        self.deadline = deadline
        self.market_price = market_price
        self.market_deadline = market_deadline
        self.business_type = business_type
        self.pain = pain
        self.hook = hook
        self.lead_score = lead_score
        self.reason = reason


def _matches_cold_keywords(text: str) -> bool:
    """Проверка холодных ключевых слов с учётом исключений."""
    # Сначала исключения
    for pattern in _cold_exclude_patterns:
        if pattern.search(text):
            return False
    # Потом сигналы
    for pattern in _cold_keyword_patterns:
        if pattern.search(text):
            return True
    return False


def _level0_filter(text: str, message_date: datetime, sender_is_bot: bool) -> bool:
    """Уровень 0: быстрый отсев мусора. True = пропустить сообщение."""
    # Возраст сообщения
    age = datetime.now(tz=message_date.tzinfo) - message_date
    if age > timedelta(days=MAX_MESSAGE_AGE_DAYS):
        return True

    # Слишком короткое
    if len(text) < MIN_MESSAGE_LENGTH:
        return True

    # От бота
    if sender_is_bot:
        return True

    # Очевидная реклама/спам
    if _bot_patterns.search(text):
        return True

    return False


def _level1_filter(text: str) -> bool:
    """Уровень 1: ключевые слова. True = прошло фильтр (потенциальный лид)."""
    # Сначала проверяем стоп-слова: если есть исключения — сразу отбрасываем
    for pattern in _exclude_patterns:
        if pattern.search(text):
            logger.debug(f"Сообщение отсеяно по стоп-слову: {pattern.pattern[:40]}")
            return False

    # Проверка по ключевым словам
    for pattern in _keyword_patterns:
        if pattern.search(text):
            return True

    # Проверка по паттернам запроса + IT-термины
    if _request_patterns.search(text):
        it_terms = re.compile(
            r"(?i)(приложение|сайт|бот|telegram|мобильн|веб|frontend|backend|"
            r"фронтенд|бэкенд|python|javascript|react|vue|flutter|"
            r"android|ios|десктоп|программ|разработ|скрипт|парсер|"
            r"автоматизац|api|база\s+данных|crm|интеграция)"
        )
        if it_terms.search(text):
            return True

    return False


async def detect_cold_lead(text: str, message_date: datetime,
                           sender_is_bot: bool = False) -> LeadResult:
    """Детектор холодных лидов: ищет латентный спрос в сообщениях бизнес-чатов.
    
    Сначала L0 отсев, потом ключевые слова холодных сигналов,
    затем AI-классификация на HOT_COLD / WARM_COLD / NOT_LEAD.
    """
    # Уровень 0
    if _level0_filter(text, message_date, sender_is_bot):
        return LeadResult(False, reason="level0_filtered")

    # Уровень 1: холодные ключевые слова
    if not _matches_cold_keywords(text):
        return LeadResult(False, reason="no_cold_signals")

    # Уровень 2: AI-классификация холодного лидера
    logger.info(f"Холодный сигнал прошёл L1, отправляю на AI: {text[:80]}...")
    try:
        content = await ai_engine._chat_with_fallback(
            ai_engine.CLASSIFY_AI_PROVIDER, ai_engine.CLASSIFY_MODEL,
            [
                {"role": "system", "content": COLD_CLASSIFY_PROMPT},
                {"role": "user", "content": text},
            ],
            temperature=0.2, max_tokens=300,
        )
        result = ai_engine._extract_json(content)
    except Exception as e:
        logger.error(f"Ошибка холодной классификации: {e}")
        return LeadResult(False, reason="ai_error")

    category = result.get("category", "NOT_LEAD").upper()
    if category not in ("HOT_COLD", "WARM_COLD"):
        return LeadResult(False, category="NOT_LEAD", reason="ai_not_cold_lead")

    score = int(result.get("lead_score", 0) or 0)
    return LeadResult(
        is_lead=True,
        category=category,
        business_type=result.get("business_type"),
        pain=result.get("pain"),
        hook=result.get("hook"),
        market_price=result.get("market_price"),
        market_deadline=result.get("market_deadline"),
        lead_score=score,
        reason="cold_ai_classified",
    )


async def detect_lead(text: str, message_date: datetime,
                      sender_is_bot: bool = False) -> LeadResult:
    """Полный пайплайн фильтрации сообщения.
    
    Уровень 0: быстрый отсев (возраст, длина, боты, спам)
    Уровень 1: ключевые слова и паттерны
    Уровень 2: AI-классификация (DeepSeek/GPT)
    """
    # Уровень 0
    if _level0_filter(text, message_date, sender_is_bot):
        return LeadResult(False, reason="level0_filtered")

    # Уровень 1
    if not _level1_filter(text):
        return LeadResult(False, reason="level1_no_keywords")

    # Уровень 2: AI-классификация
    logger.info(f"Сообщение прошло L0+L1, отправляю на AI-классификацию: {text[:80]}...")
    result = await ai_engine.classify_message(text)

    if result is None:
        return LeadResult(False, reason="ai_error")

    category = result.get("category", "NOT_LEAD").upper()
    if category not in ("HOT", "WARM"):
        return LeadResult(False, category="NOT_LEAD", reason="ai_not_lead")

    score = int(result.get("lead_score", 0) or 0)
    return LeadResult(
        is_lead=True,
        category=category,
        task=result.get("task"),
        budget=result.get("budget"),
        deadline=result.get("deadline"),
        market_price=result.get("market_price"),
        market_deadline=result.get("market_deadline"),
        lead_score=score,
        reason="ai_classified"
    )
