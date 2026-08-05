"""Добавляет HTTP прокси для Bot API в .env на сервере и перезапускает сервис."""
import sys
from pathlib import Path
import paramiko

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HOST = "82.202.170.14"
USER = "root"
PASSWORD = "vbif88vbif"

BOT_PROXY = "http://user425172:apocw5@93.127.154.81:4565"

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

    # Добавляем TG_BOT_PROXY если его нет
    if "TG_BOT_PROXY" not in env_content:
        print("\n=== 2. Добавляем TG_BOT_PROXY в .env ===")
        new_env = env_content.strip() + f"\nTG_BOT_PROXY={BOT_PROXY}\n"
        sftp = ssh.open_sftp()
        with sftp.file("/opt/lead-hunter/.env", "w") as f:
            f.write(new_env)
        sftp.close()
        print("TG_BOT_PROXY добавлен в .env")
    else:
        print("\n=== Обновляем TG_BOT_PROXY в .env ===")
        # Заменяем существующую строку
        lines = env_content.strip().split('\n')
        new_lines = []
        for line in lines:
            if line.startswith("TG_BOT_PROXY="):
                new_lines.append(f"TG_BOT_PROXY={BOT_PROXY}")
            else:
                new_lines.append(line)
        new_env = '\n'.join(new_lines) + '\n'
        sftp = ssh.open_sftp()
        with sftp.file("/opt/lead-hunter/.env", "w") as f:
            f.write(new_env)
        sftp.close()
        print("TG_BOT_PROXY обновлен в .env")

    print("\n=== 3. Перезапускаем lead-hunter сервис ===")
    run(ssh, "systemctl restart lead-hunter.service")

    print("\n=== 4. Ждём 15 секунд и проверяем статус ===")
    import time
    time.sleep(15)
    run(ssh, "systemctl status lead-hunter.service --no-pager")

    print("\n=== 5. Проверяем последние логи (ищем Bot API proxy) ===")
    run(ssh, "journalctl -u lead-hunter -n 30 --no-pager | grep -i -E 'proxy|bot' || echo 'Нет логов'")

    ssh.close()
    print("\n=== Готово ===")
    print(f"\n📱 Теперь отправьте /start боту @MyLeadKillerBot в Telegram")

if __name__ == "__main__":
    main()
