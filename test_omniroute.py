"""Тест подключения к OmniRoute и Claude Sonnet."""
import asyncio
import json
import os
from openai import AsyncOpenAI
from dotenv import load_dotenv

load_dotenv()

BASE_URL = os.getenv("OMNIROUTE_BASE_URL", "http://localhost:20128/v1")
API_KEY = os.getenv("OMNIROUTE_API_KEY", "omni")

# Попробуем несколько вариантов Claude
TEST_MODELS = [
    "no-think/kr/claude-sonnet-4.5",
    "no-think/kiro/claude-sonnet-5",
    "kr/claude-sonnet-4.5",
    "kiro/claude-sonnet-4.5",
    "auto",
]


async def test_model(client: AsyncOpenAI, model: str):
    """Тестирует одну модель на классификации и диалоге."""
    print(f"\n{'='*60}")
    print(f"Тестируем модель: {model}")
    print("=" * 60)

    # Тест 1: классификация
    try:
        classify_prompt = (
            "Ты — аналитик лидов. Определи, является ли автор потенциальным клиентом. "
            "Ответ строго в JSON: {\"category\": \"HOT|WARM|NOT_LEAD\", "
            "\"task\": \"...\", \"budget\": \"...\", \"deadline\": \"...\"}\n\n"
            "Сообщение: \"Привет! Ищу разработчика для мобильного приложения под iOS, "
            "примерно бюджет 200к, срок месяц. Кто возьмется?\""
        )
        response = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": classify_prompt}],
            max_tokens=300,
            temperature=0.1,
        )
        classify_result = response.choices[0].message.content
        print(f"Классификация:\n{classify_result}\n")
    except Exception as e:
        print(f"Ошибка классификации: {e}")
        return False

    # Тест 2: диалог
    try:
        dialog_prompt = (
            "Ты — опытный менеджер по продажам IT-услуг. Кратко, по-человечески "
            "ответь потенциальному клиенту, который ищет разработчика мобильного приложения. "
            "Задай 1-2 уточняющих вопроса."
        )
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": dialog_prompt},
                {"role": "user", "content": "Нужно приложение для доставки еды, бюджет 200к"},
            ],
            max_tokens=300,
            temperature=0.7,
        )
        dialog_result = response.choices[0].message.content
        print(f"Диалог:\n{dialog_result}\n")
    except Exception as e:
        print(f"Ошибка диалога: {e}")
        return False

    return True


async def main():
    client = AsyncOpenAI(api_key=API_KEY, base_url=BASE_URL)

    # Проверяем список моделей
    try:
        models = await client.models.list()
        claude_models = [m.id for m in models.data if "claude" in m.id.lower()]
        print(f"Доступно Claude-моделей: {len(claude_models)}")
        for m in claude_models[:10]:
            print(f"  - {m}")
    except Exception as e:
        print(f"Не удалось получить список моделей: {e}")

    # Тестируем каждую модель
    working_models = []
    for model in TEST_MODELS:
        success = await test_model(client, model)
        if success:
            working_models.append(model)

    print(f"\n{'='*60}")
    print("Работающие модели:")
    for m in working_models:
        print(f"  - {m}")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
