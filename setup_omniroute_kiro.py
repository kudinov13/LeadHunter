"""Настройка OmniRoute для Claude Sonnet через Kiro провайдер."""
import sys
from pathlib import Path
import paramiko
import json

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HOST = "82.202.170.14"
USER = "root"
PASSWORD = "vbif88vbif"
OMNI_PASSWORD = "LeadHunterOmni2026"  # Из bootstrap.sh

def run(ssh, cmd, timeout=60):
    print(f"\n$ {cmd[:150]}")
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=timeout, get_pty=True)
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    code = stdout.channel.recv_exit_status()
    print(out[-4000:])
    if err:
        print(f"STDERR: {err[-1000:]}")
    print(f"[exit {code}]")
    return code, out, err

def main():
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(HOST, username=USER, password=PASSWORD, timeout=30, banner_timeout=90)
    print("=== Подключено к сервере ===")

    print("\n=== 1. Проверка текущих провайдеров через API ===")
    run(ssh, f"curl -s http://127.0.0.1:20128/api/providers -H 'Authorization: Bearer {OMNI_PASSWORD}' 2>/dev/null || echo 'API недоступен'")

    print("\n=== 2. Попытка добавить Kiro через API ===")
    # OmniRoute может иметь API для добавления провайдеров
    provider_config = {
        "provider": "kiro",
        "enabled": True
    }
    run(ssh, f"""curl -s -X POST http://127.0.0.1:20128/api/providers \\
  -H 'Content-Type: application/json' \\
  -H 'Authorization: Bearer {OMNI_PASSWORD}' \\
  -d '{json.dumps(provider_config)}'""")

    print("\n=== 3. Проверка OmniRoute веб-интерфейса ===")
    run(ssh, "curl -s http://127.0.0.1:20128/ 2>/dev/null | head -c 2000 || echo 'Веб-интерфейс недоступен'")

    print("\n=== 4. Попытка использовать глобальный OmniRoute CLI (если установлен) ===")
    run(ssh, "which omniroute && omniroute setup --non-interactive --add-provider --provider kiro || echo 'CLI не найден'")

    print("\n=== 5. Проверка доступных моделей ===")
    run(ssh, "curl -s http://127.0.0.1:20128/v1/models -H 'Authorization: Bearer omni'")

    print("\n=== 6. Тест с явным указанием модели Kiro ===")
    test_kiro = """curl -s -X POST http://127.0.0.1:20128/v1/chat/completions \\
  -H 'Content-Type: application/json' \\
  -H 'Authorization: Bearer omni' \\
  -d '{
    "model": "kiro/claude-sonnet-4",
    "messages": [{"role": "user", "content": "Say hello in Russian"}],
    "max_tokens": 50
  }'"""
    run(ssh, test_kiro)

    ssh.close()
    print("\n=== Проверка завершена ===")

if __name__ == "__main__":
    main()
