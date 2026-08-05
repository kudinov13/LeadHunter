"""Проверка доступных провайдеров в OmniRoute и поиск альтернативных путей к Claude."""
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

    print("\n=== 1. Проверка всех доступных моделей ===")
    run(ssh, "curl -s http://127.0.0.1:20128/v1/models -H 'Authorization: Bearer omni' | python3 -m json.tool | grep -i claude || echo 'No Claude models'")

    print("\n=== 2. Проверка логов OmniRoute для провайдеров ===")
    run(ssh, "docker logs omniroute 2>&1 | grep -i -E 'provider|connected|kiro|anthropic' | tail -20 || echo 'No provider logs'")

    print("\n=== 3. Попытка использовать auto/claude-opus ===")
    test_opus = """curl -s -X POST http://127.0.0.1:20128/v1/chat/completions \\
  -H 'Content-Type: application/json' \\
  -H 'Authorization: Bearer omni' \\
  -d '{
    "model": "auto/claude-opus",
    "messages": [{"role": "user", "content": "Say hello in Russian"}],
    "max_tokens": 50
  }'"""
    run(ssh, test_opus)

    print("\n=== 4. Попытка использовать tllm/GPT_4o ===")
    test_gpt4 = """curl -s -X POST http://127.0.0.1:20128/v1/chat/completions \\
  -H 'Content-Type: application/json' \\
  -H 'Authorization: Bearer omni' \\
  -d '{
    "model": "tllm/GPT_4o",
    "messages": [{"role": "user", "content": "Say hello in Russian"}],
    "max_tokens": 50
  }'"""
    run(ssh, test_gpt4)

    ssh.close()
    print("\n=== Готово ===")

if __name__ == "__main__":
    main()
