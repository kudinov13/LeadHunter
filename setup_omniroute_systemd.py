"""Настройка systemd сервиса для OmniRoute (npm версия)."""
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

    print("\n=== 1. Остановка и удаление Docker OmniRoute ===")
    run(ssh, "docker stop omniroute && docker rm omniroute")
    run(ssh, "systemctl disable omniroute.service 2>/dev/null || true")

    print("\n=== 2. Создание systemd сервиса для OmniRoute ===")
    service_content = """[Unit]
Description=OmniRoute AI Gateway
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/root
Environment=PORT=20128
Environment=HOST=127.0.0.1
Environment=NODE_ENV=production
Environment=HTTP_PROXY=http://user425172:apocw5@93.127.154.81:4565
Environment=HTTPS_PROXY=http://user425172:apocw5@93.127.154.81:4565
Environment=INITIAL_PASSWORD=LeadHunterOmni2026
ExecStart=/usr/bin/omniroute
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target"""
    
    run(ssh, "cat > /etc/systemd/system/omniroute.service << 'EOF'\n" + service_content + "\nEOF")

    print("\n=== 3. Перезагрузка systemd и запуск OmniRoute ===")
    run(ssh, "systemctl daemon-reload")
    run(ssh, "systemctl enable omniroute.service")
    run(ssh, "systemctl start omniroute.service")

    print("\n=== 4. Ждём 10 секунд и проверяем статус ===")
    import time
    time.sleep(10)
    run(ssh, "systemctl status omniroute.service --no-pager")

    print("\n=== 5. Проверка доступности OmniRoute ===")
    run(ssh, "curl -s http://127.0.0.1:20128/")

    print("\n=== 6. Проверка доступных моделей ===")
    run(ssh, "curl -s http://127.0.0.1:20128/v1/models -H 'Authorization: Bearer omni' | python3 -m json.tool | grep -i kr/ || echo 'No kr/ models found'")

    ssh.close()
    print("\n=== Готово ===")
    print("\nТеперь вы можете:")
    print("1. SSH туннель уже активен (вы подключены)")
    print("2. Откройте в браузере: http://localhost:20128")
    print("3. Пароль: LeadHunterOmni2026")
    print("4. Настройте Kiro провайдер в Settings → Providers")
    print("5. Добавьте прокси в Settings → Proxy для Kiro")

if __name__ == "__main__":
    main()
