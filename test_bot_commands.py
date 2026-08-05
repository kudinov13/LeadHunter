"""Проверка бота уведомлений на сервере: работает ли он и принимает команды."""
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

    print("\n=== 1. Проверка статуса lead-hunter сервиса ===")
    run(ssh, "systemctl status lead-hunter.service --no-pager")

    print("\n=== 2. Проверка последних логов (поиск ошибок бота) ===")
    run(ssh, "journalctl -u lead-hunter -n 50 --no-pager | grep -i bot || echo 'Нет логов с bot'")

    print("\n=== 3. Проверка .env файла (токен бота) ===")
    run(ssh, "cat /opt/lead-hunter/.env | grep NOTIF_BOT_TOKEN")

    print("\n=== 4. Проверка подключения бота к Telegram API ===")
    run(ssh, "curl -s https://api.telegram.org/bot8912881099:AAFnND9BQLOeAO_LVjzhw0sdtLG2xrao3lo/getMe")

    print("\n=== 5. Проверка webhook (если используется) ===")
    run(ssh, "curl -s https://api.telegram.org/bot8912881099:AAFnND9BQLOeAO_LVjzhw0sdtLG2xrao3lo/getWebhookInfo")

    ssh.close()
    print("\n=== Проверка завершена ===")
    print("\n📱 Теперь отправьте команду /start боту @LeadHunterBot в Telegram")
    print("📱 Или ваш токен бота: 8912881099:AAFnND9BQLOeAO_LVjzhw0sdtLG2xrao3lo")

if __name__ == "__main__":
    main()
