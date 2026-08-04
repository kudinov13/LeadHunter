import asyncio
import os
from openai import AsyncOpenAI
from dotenv import load_dotenv

load_dotenv()

c = AsyncOpenAI(
    api_key=os.getenv("OMNIROUTE_API_KEY", "omni"),
    base_url=os.getenv("OMNIROUTE_BASE_URL", "http://localhost:20128/v1")
)

models_to_test = [
    "auto/claude-haiku",
    "auto/claude-sonnet",
    "auto",
]

async def test(m):
    try:
        r = await c.chat.completions.create(
            model=m,
            messages=[{"role": "user", "content": "Say hi briefly"}],
            max_tokens=50,
            temperature=0.7,
        )
        print(f"✅ {m}: {r.choices[0].message.content.strip()[:80]}")
    except Exception as e:
        print(f"❌ {m}: {str(e)[:100]}")

async def main():
    for m in models_to_test:
        await test(m)

asyncio.run(main())
