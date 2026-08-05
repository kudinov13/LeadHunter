"""Проверка логов бота на сервере."""
import sys
from pathlib import Path
import paramiko

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HOST = "82.202.170.14"
USER = "root"
PASSWORD = "vbif88vbif"

def run(ssh, cmd, timeout=30):
    print(f"\n$ {cmd[:100]}")
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=timeout, get_pty=True)
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    code = stdout.channel.recv_exit_status()
    print(out[-3000:])
    if err:
        print(f"STDERR: {err[-500:]}")
    print(f"[exit {code}]")
    return code, out, err

def main():
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(HOST, username=USER, password=PASSWORD, timeout=30, banner_timeout=90)
    print("=== Подключено к серверу ===")

    print("\n=== 1. Статус lead-hunter сервиса ===")
    run(ssh, "systemctl status lead-hunter.service --no-pager")

    print("\n=== 2. Последние логи (поиск bot) ===")
    run(ssh, "journalctl -u lead-hunter -n 100 --no-pager | grep -i -E 'bot|notif|telegram' || echo 'Нет логов'")

    print("\n=== 3. Проверка процесса Python ===")
    run(ssh, "ps aux | grep python | grep lead-hunter || echo 'Процесс не найден'")

    ssh.close()
    print("\n=== Готово ===")
    print("\n📱 Отправьте /start боту @MyLeadKillerBot в Telegram для проверки")

if __name__ == "__main__":
    main()
