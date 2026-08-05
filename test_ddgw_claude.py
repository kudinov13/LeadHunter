"""Тест ddgw провайдера для Claude (альтернатива The Old LLM)."""
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

    models_to_test = [
        ("ddgw/claude-sonnet-5", "Claude Sonnet 5 (ddgw)"),
        ("ddgw/claude-sonnet-5-500k", "Claude Sonnet 5 500K (ddgw)"),
        ("ddgw/claude-fable-5", "Claude Fable 5 (ddgw)"),
    ]

    for model_id, model_name in models_to_test:
        print(f"\n=== Тест {model_name} ===")
        test_cmd = f"""curl -s -X POST http://127.0.0.1:20128/v1/chat/completions \\
  -H 'Content-Type: application/json' \\
  -H 'Authorization: Bearer omni' \\
  -d '{{"model": "{model_id}", "messages": [{{"role": "user", "content": "Say hello in Russian and tell me you are Claude"}}], "max_tokens": 50}}'"""
        code, out, _ = run(ssh, test_cmd)
        if code == 0 and "error" not in out.lower():
            print(f"✅ {model_name} РАБОТАЕТ!")
        else:
            print(f"❌ {model_name} не работает")

    ssh.close()
    print("\n=== Проверка завершена ===")

if __name__ == "__main__":
    main()
