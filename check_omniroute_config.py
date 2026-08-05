"""Проверка конфигурационных файлов OmniRoute для добавления Kiro вручную."""
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
    print("=== Подключено к сервере ===")

    print("\n=== 1. Проверка директории OmniRoute ===")
    run(ssh, "ls -la /root/.omniroute/")

    print("\n=== 2. Проверка конфигурационных файлов ===")
    run(ssh, "cat /root/.omniroute/.env")

    print("\n=== 3. Проверка провайдеров через CLI ===")
    run(ssh, "omniroute providers list")

    print("\n=== 4. Проверка доступных провайдеров в каталоге ===")
    run(ssh, "omniroute providers available | grep -i kiro || echo 'No Kiro in catalog'")

    print("\n=== 5. Попытка найти файлы конфигурации провайдеров ===")
    run(ssh, "find /root/.omniroute -name '*provider*' -o -name '*kiro*' 2>/dev/null || echo 'No provider files found'")

    ssh.close()
    print("\n=== Готово ===")

if __name__ == "__main__":
    main()
