"""Тест явного указания модели tllm/claude_sonnet_4."""
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

    print("\n=== Тест явного указания tllm/claude_sonnet_4 ===")
    test_claude = """curl -s -X POST http://127.0.0.1:20128/v1/chat/completions \\
  -H 'Content-Type: application/json' \\
  -H 'Authorization: Bearer omni' \\
  -d '{
    "model": "tllm/claude_sonnet_4",
    "messages": [{"role": "user", "content": "Say hello in Russian and tell me you are Claude Sonnet 4"}],
    "max_tokens": 100
  }'"""
    run(ssh, test_claude)

    ssh.close()
    print("\n=== Готово ===")

if __name__ == "__main__":
    main()
