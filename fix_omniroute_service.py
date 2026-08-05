"""Исправление systemd сервиса для OmniRoute - проверка логов и исправление."""
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

    print("\n=== 1. Проверка логов OmniRoute ===")
    run(ssh, "journalctl -u omniroute -n 30 --no-pager")

    print("\n=== 2. Попытка запустить OmniRoute вручную для проверки ===")
    print("Запуск в фоновом режиме с nohup...")
    run(ssh, "nohup omniroute > /tmp/omniroute.log 2>&1 &")

    print("\n=== 3. Ждём 5 секунд и проверяем процесс ===")
    import time
    time.sleep(5)
    run(ssh, "ps aux | grep omniroute | grep -v grep || echo 'Процесс не найден'")

    print("\n=== 4. Проверка логов ===")
    run(ssh, "tail -20 /tmp/omniroute.log")

    print("\n=== 5. Проверка доступности ===")
    run(ssh, "curl -s http://127.0.0.1:20128/")

    print("\n=== 6. Если работает, обновляем systemd сервис ===")
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
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target"""
    
    run(ssh, "cat > /etc/systemd/system/omniroute.service << 'EOF'\n" + service_content + "\nEOF")
    run(ssh, "systemctl daemon-reload")
    run(ssh, "systemctl restart omniroute.service")
    
    print("\n=== 7. Ждём 10 секунд и проверяем статус ===")
    time.sleep(10)
    run(ssh, "systemctl status omniroute.service --no-pager")

    ssh.close()
    print("\n=== Готово ===")

if __name__ == "__main__":
    main()
