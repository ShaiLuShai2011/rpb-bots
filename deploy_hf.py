#!/usr/bin/env python3
"""Деплой ботов на Hugging Face Spaces (бесплатно, без карты).

Использование:
    export HF_TOKEN=hf_xxx
    export BOT_TOKEN_NUSLAY=...   # опционально
    export BOT_TOKEN_NEV=...      # опционально
    export BOT_TOKEN_RPB=...      # опционально
    python deploy_hf.py

Скрипт сам:
1. Проверит токен (whoami)
2. Создаст приватный Space (Docker) и приватный Dataset для бэкапов БД
3. Пропишет секреты Space
4. Запушит код
5. Дождётся сборки и покажет статус
"""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

HF_API = "https://huggingface.co/api"
SPACE_NAME = os.environ.get("HF_SPACE_NAME", "rpb-bots")
DATASET_NAME = os.environ.get("HF_DATASET_NAME", "rpb-bot-backups")
TOKEN = os.environ.get("HF_TOKEN", "").strip()
ROOT = os.path.dirname(os.path.abspath(__file__))


def api(method: str, path: str, body=None):
    url = HF_API + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {TOKEN}")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read().decode("utf-8", "ignore")
            return r.status, (json.loads(raw) if raw.strip().startswith(("{", "[")) else raw)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "ignore")
        return e.code, raw
    except Exception as e:
        return 0, str(e)


def whoami():
    status, data = api("GET", "/whoami-v2")
    if status != 200 or not isinstance(data, dict):
        print(f"❌ Не удалось проверить HF_TOKEN: {status} {data}")
        sys.exit(1)
    name = data.get("name")
    print(f"✅ Аккаунт Hugging Face: {name}")
    return name


def ensure_repo(kind: str, name: str):
    body = {"type": kind, "name": name, "private": True}
    if kind == "space":
        body["sdk"] = "docker"
    status, data = api("POST", "/repos/create", body)
    if status in (200, 201):
        print(f"✅ Создан {kind}: {name}")
    elif status == 409 or "already" in str(data).lower():
        print(f"ℹ️ {kind} уже существует: {name}")
    else:
        print(f"⚠️ {kind} {name}: {status} {str(data)[:200]}")


def set_secret(repo_id: str, key: str, value: str):
    status, data = api("POST", f"/spaces/{repo_id}/secrets", {"key": key, "value": value})
    if status in (200, 201):
        print(f"  🔐 {key}")
    else:
        print(f"  ⚠️ {key}: {status} {str(data)[:120]}")


def git_push(user: str):
    space_id = f"{user}/{SPACE_NAME}"
    remote = f"https://{user}:{TOKEN}@huggingface.co/spaces/{space_id}"
    cmds = [
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", f"{user}@users.noreply.huggingface.co"],
        ["git", "config", "user.name", user],
        ["git", "add", "-A"],
        ["git", "commit", "-qm", "Deploy bots: NuSlay + NEV + RP Country"],
    ]
    subprocess.run(["git", "remote", "remove", "hf"], cwd=ROOT, capture_output=True)
    for cmd in cmds:
        r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
        if r.returncode not in (0, 1) and cmd[1] != "commit":
            print(f"⚠️ {' '.join(cmd)}: {r.stderr[:200]}")
    subprocess.run(["git", "remote", "add", "hf", remote], cwd=ROOT, check=True)
    print("📤 Пуш кода в Space (может занять пару минут из-за шрифтов)...")
    r = subprocess.run(
        ["git", "-c", "http.postBuffer=524288000", "push", "-f", "hf", "main"],
        cwd=ROOT, capture_output=True, text=True,
    )
    print(r.stdout[-1500:] if r.stdout else "")
    if r.returncode != 0:
        print("STDERR:", r.stderr[-1500:])
        sys.exit(1)
    return space_id


def build_status(space_id: str):
    status, data = api("GET", f"/spaces/{space_id}/runtime")
    if status == 200 and isinstance(data, dict):
        return data.get("stage"), data.get("hardware", {}).get("current")
    return None, None


def main():
    if not TOKEN:
        print("❌ Задай HF_TOKEN (токен Write с huggingface.co/settings/tokens)")
        sys.exit(1)
    user = whoami()
    ensure_repo("dataset", DATASET_NAME)
    ensure_repo("space", SPACE_NAME)

    repo_id = f"{user}/{SPACE_NAME}"
    print("🔐 Секреты Space:")
    set_secret(repo_id, "ADMIN_IDS", os.environ.get("ADMIN_IDS", "8839678195"))
    for key in ("BOT_TOKEN_NUSLAY", "BOT_TOKEN_NEV", "BOT_TOKEN_RPB", "ANTHROPIC_KEY"):
        val = os.environ.get(key, "").strip()
        if val:
            set_secret(repo_id, key, val)
    set_secret(repo_id, "HF_TOKEN", TOKEN)
    set_secret(repo_id, "HF_BACKUP_REPO", f"{user}/{DATASET_NAME}")

    space_id = git_push(user)
    print("⏳ Ждём сборку Space...")
    for _ in range(60):
        stage, hw = build_status(space_id)
        print(f"  stage={stage} hardware={hw}")
        if stage in ("RUNNING", "RUNNING_BUILDING"):
            break
        if stage in ("BUILD_ERROR", "RUNTIME_ERROR", "CONFIG_ERROR"):
            break
        time.sleep(15)
    slug = space_id.replace("/", "-").lower().replace("_", "-")
    print(f"\n🌐 Space: https://huggingface.co/spaces/{space_id}")
    print(f"🩺 Healthcheck: https://{slug}.hf.space/")
    print("⏰ Чтобы Space не засыпал — поставь бесплатный пингер (UptimeRobot) на healthcheck раз в 5 минут.")


if __name__ == "__main__":
    main()