"""Скрипт для сборки .exe файла с PyInstaller."""
import PyInstaller.__main__
import os
import sys

# Параметры сборки
PyInstaller.__main__.run([
    '--onefile',
    '--name=LeadHunter',
    '--icon=NONE',
    '--add-data=.env.example;.',
    '--add-data=data;data',
    '--hidden-import=telethon',
    '--hidden-import=aiogram',
    '--hidden-import=openai',
    '--hidden-import=apscheduler',
    '--hidden-import=aiohttp',
    '--hidden-import=aiohttp_socks',
    '--hidden-import=python_socks',
    '--hidden-import=pydantic',
    '--hidden-import=pydantic_settings',
    '--collect-all=telethon',
    '--collect-all=aiogram',
    'main_gui.py'
])

print("\n✅ Сборка завершена!")
print(f"📁 .exe файл находится в: dist/LeadHunter.exe")
print("📋 Не забудьте создать .env файл рядом с .exe перед запуском!")
