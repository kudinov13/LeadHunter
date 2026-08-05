"""Проверка CLI команд OmniRoute для добавления провайдера."""
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

    print("\n=== 1. Проверка доступных команд OmniRoute CLI ===")
    run(ssh, "omniroute --help")

    print("\n=== 2. Проверка команд для провайдеров ===")
    run(ssh, "omniroute providers --help 2>/dev/null || omniroute provider --help 2>/dev/null || echo 'No provider command found'")

    print("\n=== 3. Проверка команд для подключения ===")
    run(ssh, "omniroute connect --help 2>/dev/null || omniroute connection --help 2>/dev/null || echo 'No connect command found'")

    ssh.close()
    print("\n=== Готово ===")

if __name__ == "__main__":
    main()
