"""Установка последней версии OmniRoute через npm на сервере."""
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

    print("\n=== 1. Проверка текущей версии OmniRoute ===")
    run(ssh, "omniroute --version 2>/dev/null || echo 'OmniRoute не установлен глобально'")

    print("\n=== 2. Установка последней версии OmniRoute (может занять время) ===")
    run(ssh, "npm install -g omniroute@latest", timeout=300)

    print("\n=== 3. Проверка версии после установки ===")
    run(ssh, "omniroute --version")

    print("\n=== 4. Остановка Docker контейнера OmniRoute ===")
    run(ssh, "docker stop omniroute && docker rm omniroute")

    print("\n=== 5. Запуск OmniRoute через npm (вместо Docker) ===")
    print("Примечание: OmniRoute будет запущен в фоне через systemd")

    print("\n=== 6. Создание systemd сервиса для OmniRoute ===")
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

    print("\n=== 7. Перезагрузка systemd и запуск OmniRoute ===")
    run(ssh, "systemctl daemon-reload")
    run(ssh, "systemctl enable omniroute.service")
    run(ssh, "systemctl start omniroute.service")

    print("\n=== 8. Ждём 10 секунд и проверяем статус ===")
    import time
    time.sleep(10)
    run(ssh, "systemctl status omniroute.service --no-pager")

    print("\n=== 9. Проверка доступности OmniRoute ===")
    run(ssh, "curl -s http://127.0.0.1:20128/")

    print("\n=== 10. Проверка доступных моделей ===")
    run(ssh, "curl -s http://127.0.0.1:20128/v1/models -H 'Authorization: Bearer omni' | python3 -m json.tool | grep -i kr/ || echo 'No kr/ models found'")

    ssh.close()
    print("\n=== Готово ===")
    print("\nТеперь вы можете:")
    print("1. Создать SSH туннель: ssh -L 20128:127.0.0.1:20128 root@82.202.170.14")
    print("2. Открыть дашборд: http://localhost:20128")
    print("3. Настроить Kiro провайдер в Settings → Providers")

if __name__ == "__main__":
    main()
