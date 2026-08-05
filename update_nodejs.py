"""Обновление Node.js до версии 22.22 на сервере."""
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

    print("\n=== 1. Установка Node.js 22.x через NodeSource ===")
    run(ssh, "curl -fsSL https://deb.nodesource.com/setup_22.x | bash -")

    print("\n=== 2. Установка Node.js 22 ===")
    run(ssh, "apt-get install -y nodejs")

    print("\n=== 3. Проверка версии Node.js ===")
    run(ssh, "node --version")

    print("\n=== 4. Остановка OmniRoute ===")
    run(ssh, "docker stop omniroute && docker rm omniroute")

    print("\n=== 5. Запуск OmniRoute с HTTP прокси и новым Node ===")
    HTTP_PROXY = "http://user425172:apocw5@93.127.154.81:4565"
    run_cmd = f"""docker run -d --name omniroute --restart unless-stopped \\
  -p 127.0.0.1:20128:20128 \\
  -v /opt/omniroute-data:/root/.omniroute \\
  -e PORT=20128 \\
  -e HOST=127.0.0.1 \\
  -e NODE_ENV=production \\
  -e HTTP_PROXY={HTTP_PROXY} \\
  -e HTTPS_PROXY={HTTP_PROXY} \\
  -e INITIAL_PASSWORD='LeadHunterOmni2026' \\
  diegosouzapw/omniroute:latest"""
    run(ssh, run_cmd)

    print("\n=== 6. Ждём 10 секунд для запуска ===")
    import time
    time.sleep(10)

    print("\n=== 7. Проверяем статус OmniRoute ===")
    run(ssh, "docker ps --filter name=omniroute")

    print("\n=== 8. Тест Claude Sonnet через OmniRoute ===")
    test_claude = """curl -s -X POST http://127.0.0.1:20128/v1/chat/completions \\
  -H 'Content-Type: application/json' \\
  -H 'Authorization: Bearer omni' \\
  -d '{
    "model": "auto/claude-sonnet",
    "messages": [{"role": "user", "content": "Say hello in Russian and tell me you are Claude Sonnet 4.5"}],
    "max_tokens": 100
  }'"""
    run(ssh, test_claude)

    print("\n=== 9. Перезапуск lead-hunter сервиса ===")
    run(ssh, "systemctl restart lead-hunter.service")

    print("\n=== 10. Ждём 15 секунд и проверяем статус ===")
    time.sleep(15)
    run(ssh, "systemctl status lead-hunter.service --no-pager")

    ssh.close()
    print("\n=== Готово ===")

if __name__ == "__main__":
    main()
