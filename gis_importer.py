"""Модуль импорта данных из 2GIS CSV-парсера."""
import csv
import logging
from pathlib import Path
from config import GIS_IMPORT_PATH, GIS_CSV_ENCODING, GIS_CSV_DELIMITER

logger = logging.getLogger(__name__)


def import_gis_csv(file_path: str | Path = None) -> list[dict]:
    """Импортирует компании из CSV-файла 2GIS.

    Поддерживаемые колонки:
    - name: название компании
    - phone: телефон
    - address: адрес
    - category: категория бизнеса
    - city: город
    - has_website: 1/0/true/false/пусто
    """
    file_path = Path(file_path or GIS_IMPORT_PATH)
    if not file_path.exists():
        logger.warning(f"Файл 2GIS не найден: {file_path}")
        return []

    companies = []
    with open(file_path, "r", encoding=GIS_CSV_ENCODING, errors="replace") as f:
        reader = csv.DictReader(f, delimiter=GIS_CSV_DELIMITER)
        for row in reader:
            company = {
                "name": row.get("name", "").strip(),
                "phone": row.get("phone", "").strip(),
                "address": row.get("address", "").strip(),
                "category": row.get("category", "").strip(),
                "city": row.get("city", "").strip(),
                "has_website": _parse_bool(row.get("has_website", "")),
            }
            if company["name"] and company["phone"]:
                companies.append(company)

    logger.info(f"Импортировано компаний из 2GIS: {len(companies)}")
    return companies


def _parse_bool(value: str) -> int:
    value = str(value).strip().lower()
    if value in ("1", "true", "yes", "да", "есть"):
        return 1
    return 0
