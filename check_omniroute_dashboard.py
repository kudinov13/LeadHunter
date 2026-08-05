"""Проверка доступности OmniRoute дашборда на сервере."""
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

    print("\n=== 1. Проверка доступности дашборда с сервера ===")
    run(ssh, "curl -s http://127.0.0.1:20128/")

    print("\n=== 2. Проверка текущей привязки порта ===")
    run(ssh, "docker ps --filter name=omniroute --format '{{.Ports}}'")

    print("\n=== 3. Проверка доступных моделей (поиск kr/) ===")
    run(ssh, "curl -s http://127.0.0.1:20128/v1/models -H 'Authorization: Bearer omni' | python3 -m json.tool | grep -i kr/ || echo 'No kr/ models found'")

    print("\n=== 4. Инструкция по доступу к дашборду ===")
    print("Для доступа к дашборду OmniRoute с вашего ПК:")
    print("1. Установите SSH туннель:")
    print("   ssh -L 20128:127.0.0.1:20128 root@82.202.170.14")
    print("2. Откройте в браузере: http://localhost:20128")
    print("3. Пароль: LeadHunterOmni2026")
    print("4. Настройте Kiro провайдер и прокси в Settings")

    ssh.close()
    print("\n=== Готово ===")

if __name__ == "__main__":
    main()
