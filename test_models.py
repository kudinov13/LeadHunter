"""Тест разных моделей OmniRoute на доступность."""
import asyncio
import os
from openai import AsyncOpenAI
from dotenv import load_dotenv

load_dotenv()

BASE_URL = os.getenv("OMNIROUTE_BASE_URL", "http://localhost:20128/v1")
API_KEY = os.getenv("OMNIROUTE_API_KEY", "omni")

# Кандидаты: роутеры и конкретные модели
MODELS = [
    "auto/claude-sonnet",
    "auto/claude-opus",
    "aug/claude-sonnet-4.6",
    "no-think/aug/claude-sonnet-4.6",
    "tllm/CLAUDE_4_6_SONNET",
    "auto",
    "kr/claude-sonnet-4.5",
    "kiro/claude-sonnet-4.5",
]

PROMPT = "Напиши одно короткое приветствие клиенту, который ищет разработчика мобильного приложения. Максимум 2 предложения."


async def test_model(client: AsyncOpenAI, model: str):
    try:
        response = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": PROMPT}],
            max_tokens=200,
            temperature=0.7,
        )
        text = response.choices[0].message.content.strip()
        print(f"✅ {model}: {text[:80]}...")
        return True, text
    except Exception as e:
        msg = str(e)
        if "429" in msg:
            print(f"⏳ {model}: 429 rate limit")
        else:
            print(f"❌ {model}: {msg[:80]}")
        return False, ""


async def main():
    client = AsyncOpenAI(api_key=API_KEY, base_url=BASE_URL)
    print("Проверяем модели:\n")
    for model in MODELS:
        await test_model(client, model)
        await asyncio.sleep(1.5)  # не спамим


if __name__ == "__main__":
    asyncio.run(main())
