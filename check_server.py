"""Проверка состояния сервера: что работает, а что нет."""
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
    print(out[-8000:])
    if err:
        print(f"STDERR: {err[-2000:]}")
    print(f"[exit {code}]")
    return code, out, err

def main():
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(HOST, username=USER, password=PASSWORD, timeout=30, banner_timeout=90)
    print("=== Подключено к серверу ===")

    print("\n=== 1. Проверка директории проекта ===")
    run(ssh, "ls -la /opt/lead-hunter/ 2>/dev/null || echo 'Директория не существует'")

    print("\n=== 2. Проверка .env файла ===")
    run(ssh, "cat /opt/lead-hunter/.env 2>/dev/null || echo '.env не найден'")

    print("\n=== 3. Проверка session файла ===")
    run(ssh, "ls -lh /opt/lead-hunter/*.session* 2>/dev/null || echo 'Session файлы не найдены'")

    print("\n=== 4. Проверка OmniRoute (Docker) ===")
    run(ssh, "docker ps --filter name=omniroute")
    run(ssh, "curl -s http://127.0.0.1:20128/v1/models -H 'Authorization: Bearer omni' 2>/dev/null | head -c 2000 || echo 'OmniRoute не отвечает'")

    print("\n=== 5. Проверка systemd сервисов ===")
    run(ssh, "systemctl status omniroute.service --no-pager")
    run(ssh, "systemctl status lead-hunter.service --no-pager")

    print("\n=== 6. Проверка логов lead-hunter ===")
    run(ssh, "journalctl -u lead-hunter -n 30 --no-pager 2>/dev/null || echo 'Логи недоступны'")

    print("\n=== 7. Проверка портов ===")
    run(ssh, "ss -tlnp | grep -E '20128|3001|80|443' || echo 'Порты не найдены'")

    print("\n=== 8. Проверка веб-сервера (nginx/koosmo) ===")
    run(ssh, "systemctl status nginx --no-pager 2>/dev/null || systemctl status koosmo --no-pager 2>/dev/null || echo 'Веб-сервер не найден'")

    print("\n=== 9. Проверка Python окружения ===")
    run(ssh, "/opt/lead-hunter/.venv/bin/python --version 2>/dev/null || echo 'venv не найден'")

    ssh.close()
    print("\n=== Проверка завершена ===")

if __name__ == "__main__":
    main()
