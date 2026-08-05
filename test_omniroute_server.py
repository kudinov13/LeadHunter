"""Проверка OmniRoute на сервере: работает ли Claude Sonnet через Kiro."""
import sys
from pathlib import Path
import paramiko

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HOST = "82.202.170.14"
USER = "root"
PASSWORD = "vbif88vbif"

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
    print("=== Подключено к серверу ===")

    print("\n=== 1. Проверка OmniRoute контейнера ===")
    run(ssh, "docker ps --filter name=omniroute")

    print("\n=== 2. Проверка доступности OmniRoute API ===")
    run(ssh, "curl -s http://127.0.0.1:20128/v1/models -H 'Authorization: Bearer omni'")

    print("\n=== 3. Проверка провайдеров в OmniRoute ===")
    run(ssh, "docker exec omniroute omniroute providers 2>/dev/null || echo 'Команда не поддерживается'")

    print("\n=== 4. Тестовый запрос к OmniRoute (Claude Sonnet) ===")
    test_request = """curl -s -X POST http://127.0.0.1:20128/v1/chat/completions \\
  -H 'Content-Type: application/json' \\
  -H 'Authorization: Bearer omni' \\
  -d '{
    "model": "auto/claude-sonnet",
    "messages": [{"role": "user", "content": "Say hello in Russian"}],
    "max_tokens": 50
  }'"""
    run(ssh, test_request)

    print("\n=== 5. Проверка логов OmniRoute ===")
    run(ssh, "docker logs omniroute --tail 20")

    ssh.close()
    print("\n=== Проверка завершена ===")

if __name__ == "__main__":
    main()
