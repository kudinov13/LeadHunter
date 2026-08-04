"""Тест холодной классификации и 2GIS импорта."""
import asyncio
import os
import csv
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

# Создаем тестовый CSV 2GIS
os.makedirs("data", exist_ok=True)
with open("data/gis_companies.csv", "w", encoding="utf-8-sig", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=["name", "phone", "address", "category", "city", "has_website"], delimiter=";")
    writer.writeheader()
    writer.writerow({
        "name": "Кафе 'Уют'",
        "phone": "+79991234567",
        "address": "ул. Ленина, 10",
        "category": "Кафе",
        "city": "Москва",
        "has_website": "0",
    })
    writer.writerow({
        "name": "Салон красоты 'Люкс'",
        "phone": "+79997654321",
        "address": "пр. Мира, 25",
        "category": "Салон красоты",
        "city": "Санкт-Петербург",
        "has_website": "0",
    })

from gis_importer import import_gis_csv
from lead_detector import detect_cold_lead


async def test_cold_classification():
    test_cases = [
        ("ru_open", "Ребята, открыли кафе на Ленина, пока только Instagram. Клиенты не могут нас найти в интернете."),
        ("ru_manual", "Записываем все заказы вручную, уже не справляемся. Нужно как-то автоматизировать."),
        ("en_online", "Just opened a salon. We only use Facebook, no website. Customers can't find us online."),
        ("ru_notlead", "Ищу работу бариста в кафе, есть опыт."),
        ("en_notlead", "I am a developer looking for new projects. Check my portfolio."),
    ]

    for label, text in test_cases:
        print(f"\n[{label}] {text[:80]}...")
        result = await detect_cold_lead(text, datetime.now())
        print(f"  → lead={result.is_lead}, category={result.category}, business={result.business_type}, pain={result.pain}")


async def test_gis_import():
    print("\n--- 2GIS Import ---")
    companies = import_gis_csv()
    print(f"Imported: {len(companies)}")
    for c in companies:
        print(f"  {c['name']} | {c['category']} | {c['phone']} | site={c['has_website']}")


async def test_cold_message_generation():
    print("\n--- Cold Message Generation ---")
    from cold_outreach import generate_cold_message
    company = {
        "name": "Кафе 'Уют'",
        "category": "Кафе",
        "city": "Москва",
        "address": "ул. Ленина, 10",
        "has_website": 0,
    }
    msg = await generate_cold_message(company)
    print(f"Generated message:\n{msg}\n")


async def main():
    await test_cold_classification()
    await test_gis_import()
    await test_cold_message_generation()


if __name__ == "__main__":
    asyncio.run(main())
