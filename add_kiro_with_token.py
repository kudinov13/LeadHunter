"""Добавление Kiro провайдера в OmniRoute через API с токеном."""
import sys
from pathlib import Path
import paramiko
import json

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HOST = "82.202.170.14"
USER = "root"
PASSWORD = "vbif88vbif"

# Токен из файла
KIRO_ACCESS_TOKEN = "aoaAAAAAGpyixYZQLbCCTg6o1yWAM1N86aF1HlkALjZuNFvE0QpGyCNP5pc2xlq7d7ypn5LRu8qR4aoxt1eqewr4sCkc0:MGQCMDWhVKOmWlHpmnune/2tfcJ3NZe3A34amDz9Z+nK9mf6dhVF7RPDxzPBkl+1x1HJFwIwJoNz05c2uiVOEYh1v5veAaIZAFHmNaiqCHQ6C1qOaX/VH7VBNY1sFqJDbIeP5ZLZ"

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

    print("\n=== 1. Проверка текущих провайдеров ===")
    run(ssh, "curl -s http://127.0.0.1:20128/api/v1/providers -H 'Authorization: Bearer omni'")

    print("\n=== 2. Попытка добавить Kiro провайдер через API ===")
    # OmniRoute API для добавления провайдера
    add_provider_cmd = f"""curl -s -X POST http://127.0.0.1:20128/api/v1/providers \\
  -H 'Authorization: Bearer omni' \\
  -H 'Content-Type: application/json' \\
  -d '{{
    "provider": "kiro",
    "config": {{
      "accessToken": "{KIRO_ACCESS_TOKEN}",
      "profileArn": "arn:aws:codewhisperer:us-east-1:699475941385:profile/EHGA3GRVQMUK"
    }}
  }}'"""
    run(ssh, add_provider_cmd)

    print("\n=== 3. Проверка провайдеров после добавления ===")
    run(ssh, "curl -s http://127.0.0.1:20128/api/v1/providers -H 'Authorization: Bearer omni'")

    print("\n=== 4. Проверка доступных моделей ===")
    run(ssh, "curl -s http://127.0.0.1:20128/v1/models -H 'Authorization: Bearer omni' | python3 -m json.tool | grep -i kr/ || echo 'No kr/ models found'")

    ssh.close()
    print("\n=== Готово ===")

if __name__ == "__main__":
    main()
