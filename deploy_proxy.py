"""Добавляет MTProto прокси в .env на сервере и перезапускает сервис."""
import sys
from pathlib import Path
import paramiko

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HOST = "82.202.170.14"
USER = "root"
PASSWORD = "vbif88vbif"

ENV_ADDITION = """
TG_MTPROTO_ENABLED=true
TG_MTPROTO_HOST=83.166.234.125
TG_MTPROTO_PORT=8444
TG_MTPROTO_SECRET=3e45c7310f70361f4dcd8d6f737e58f9
"""

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

    print("\n=== 1. Читаем текущий .env ===")
    code, env_content, _ = run(ssh, "cat /opt/lead-hunter/.env")

    if code != 0:
        print("Ошибка чтения .env")
        ssh.close()
        return

    # Добавляем MTProto настройки если их нет
    if "TG_MTPROTO_HOST" not in env_content:
        print("\n=== 2. Добавляем MTProto прокси настройки ===")
        new_env = env_content.strip() + ENV_ADDITION
        sftp = ssh.open_sftp()
        with sftp.file("/opt/lead-hunter/.env", "w") as f:
            f.write(new_env)
        sftp.close()
        print("MTProto настройки добавлены в .env")
    else:
        print("\n=== MTProto настройки уже есть в .env ===")

    print("\n=== 3. Перезапускаем lead-hunter сервис ===")
    run(ssh, "systemctl restart lead-hunter.service")

    print("\n=== 4. Ждём 10 секунд и проверяем статус ===")
    import time
    time.sleep(10)
    run(ssh, "systemctl status lead-hunter.service --no-pager")

    print("\n=== 5. Проверяем последние логи ===")
    run(ssh, "journalctl -u lead-hunter -n 20 --no-pager")

    ssh.close()
    print("\n=== Готово ===")

if __name__ == "__main__":
    main()
