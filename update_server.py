"""Обновляет код на сервере через git pull и перезапускает сервис."""
import sys
from pathlib import Path
import paramiko

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HOST = "82.202.170.14"
USER = "root"
PASSWORD = "vbif88vbif"

def run(ssh, cmd, timeout=120):
    print(f"\n$ {cmd[:150]}")
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=timeout, get_pty=True)
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    code = stdout.channel.recv_exit_status()
    print(out[-6000:])
    if err:
        print(f"STDERR: {err[-1500:]}")
    print(f"[exit {code}]")
    return code, out, err

def main():
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(HOST, username=USER, password=PASSWORD, timeout=30, banner_timeout=90)
    print("=== Подключено к серверу ===")

    print("\n=== 1. Проверяем версию user_client.py на сервере ===")
    run(ssh, "head -n 35 /opt/lead-hunter/user_client.py")

    print("\n=== 2. Git pull для обновления кода ===")
    run(ssh, "cd /opt/lead-hunter && git fetch --all && git reset --hard origin/main")

    print("\n=== 3. Переустанавливаем зависимости ===")
    run(ssh, "cd /opt/lead-hunter && .venv/bin/pip install -q -r requirements.txt")

    print("\n=== 4. Проверяем обновлённый user_client.py ===")
    run(ssh, "head -n 35 /opt/lead-hunter/user_client.py")

    print("\n=== 5. Перезапускаем lead-hunter сервис ===")
    run(ssh, "systemctl restart lead-hunter.service")

    print("\n=== 6. Ждём 15 секунд и проверяем статус ===")
    import time
    time.sleep(15)
    run(ssh, "systemctl status lead-hunter.service --no-pager")

    print("\n=== 7. Проверяем последние логи ===")
    run(ssh, "journalctl -u lead-hunter -n 30 --no-pager")

    ssh.close()
    print("\n=== Готово ===")

if __name__ == "__main__":
    main()
