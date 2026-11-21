import json
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, HTTPServer

DATA_DIR = os.environ.get("DATA_DIR", "/data")
APP_DIR = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("PORT", "7860"))

HF_TOKEN = os.environ.get("HF_TOKEN", "").strip()
HF_BACKUP_REPO = os.environ.get("HF_BACKUP_REPO", "").strip()
BACKUP_EVERY = int(os.environ.get("BACKUP_EVERY_SEC", "600"))

ADMIN_IDS = os.environ.get("ADMIN_IDS", "8839678195")

BOTS = {
    "nuslay": {
        "dir": "bots/nuslay",
        "entry": "bot.py",
        "db": "nuslay.db",
        "token_env": "BOT_TOKEN_NUSLAY",
        "extra": {
            "CHAT_ID": os.environ.get("NUSLAY_CHAT_ID", "@HGDYFL"),
            "CHAT_TITLE": os.environ.get("NUSLAY_CHAT_TITLE", "Hooligan's Chat"),
            "EVENT_NAME": os.environ.get("NUSLAY_EVENT_NAME", "NuSlay"),
            "REMINDER_INTERVAL": os.environ.get("NUSLAY_REMINDER_INTERVAL", "7200"),
            "PORT": "8082",
        },
    },
    "nev": {
        "dir": "bots/nev",
        "entry": "bot.py",
        "db": "nev.db",
        "token_env": "BOT_TOKEN_NEV",
        "extra": {
            "OWNER_ID": os.environ.get("NEV_OWNER_ID", ADMIN_IDS.split(",")[0]),
            "THIRD_OWNER_ID": os.environ.get("NEV_OWNER_ID", ADMIN_IDS.split(",")[0]),
        },
    },
    "petworld": {
        "dir": "bots/petworld",
        "entry": "bot.py",
        "db": "petworld.db",
        "token_env": "BOT_TOKEN_PETWORLD",
        "extra": {
            "GAME_GROUP_ID": os.environ.get("PETWORLD_GAME_GROUP_ID", "0"),
            "PREMIUM_EMOJI": os.environ.get("PETWORLD_PREMIUM_EMOJI", "1"),
            "PREMIUM_EMOJI_MAX": os.environ.get("PETWORLD_PREMIUM_EMOJI_MAX", "30"),
            "PREMIUM_EMOJI_PACK": os.environ.get("PETWORLD_PREMIUM_EMOJI_PACK", "AnimatedEmoji"),
        },
    },
    "rpb": {
        "dir": "bots/rpb",
        "entry": "bot.py",
        "db": "rpb.db",
        "token_env": "BOT_TOKEN_RPB",
        "extra": {
            "PORT": "8081",
            "ANTHROPIC_KEY": os.environ.get("ANTHROPIC_KEY", ""),
        },
    },
}

procs: dict = {}
started_at = time.time()
logs: dict = {}


def log(msg: str) -> None:
    print(f"[runner] {msg}", flush=True)


def _db_snapshot(src_path: str, dst_path: str) -> bool:
    if not os.path.exists(src_path):
        return False
    try:
        src = sqlite3.connect(src_path)
        dst = sqlite3.connect(dst_path)
        with dst:
            src.backup(dst)
        src.close()
        dst.close()
        return True
    except Exception:
        traceback.print_exc()
        return False


def hf_upload(paths: list) -> None:
    """Бэкапы грузим как base64-текст: HF не принимает бинарники без Xet/LFS."""
    if not HF_TOKEN or not HF_BACKUP_REPO:
        return
    try:
        import base64
        from huggingface_hub import HfApi

        api = HfApi(token=HF_TOKEN)
        for path in paths:
            name = os.path.basename(path)
            if name.startswith("_snap_"):
                name = name[len("_snap_"):]
            b64_path = path + ".b64"
            with open(path, "rb") as f:
                import textwrap
                enc = base64.b64encode(f.read()).decode()
                with open(b64_path, "w") as out:
                    out.write("\n".join(textwrap.wrap(enc, 76)) + "\n")
            api.upload_file(
                path_or_fileobj=b64_path,
                path_in_repo=name + ".txt",
                repo_id=HF_BACKUP_REPO,
                repo_type="dataset",
            )
            os.remove(b64_path)
            for legacy in ("_snap_" + name, "_snap_" + name + ".txt"):
                try:
                    api.delete_file(path_in_repo=legacy, repo_id=HF_BACKUP_REPO, repo_type="dataset")
                except Exception:
                    pass
        log(f"backup uploaded: {[os.path.basename(p) for p in paths]}")
    except Exception as e:
        log(f"backup upload failed: {e}")


def hf_restore() -> None:
    if not HF_TOKEN or not HF_BACKUP_REPO:
        return
    try:
        from huggingface_hub import hf_hub_download

        os.makedirs(DATA_DIR, exist_ok=True)
        import base64

        for name in ("nuslay.db", "nev.db", "rpb.db", "petworld.db"):
            target = os.path.join(DATA_DIR, name)
            if os.path.exists(target):
                continue
            restored = False
            try:
                path = hf_hub_download(
                    repo_id=HF_BACKUP_REPO,
                    filename=name + ".txt",
                    repo_type="dataset",
                    token=HF_TOKEN,
                )
                with open(path) as f:
                    data = base64.b64decode("".join(f.read().split()))
                with open(target, "wb") as out:
                    out.write(data)
                restored = True
            except Exception:
                pass
            if not restored:
                try:
                    path = hf_hub_download(
                        repo_id=HF_BACKUP_REPO,
                        filename=name,
                        repo_type="dataset",
                        token=HF_TOKEN,
                    )
                    shutil.copy2(path, target)
                    restored = True
                except Exception:
                    pass
            if restored:
                log(f"restored {name}")
    except Exception as e:
        log(f"restore failed: {e}")


def keepalive_loop() -> None:
    """Самопинг публичного URL: не даёт бесплатному инстансу заснуть (Render и т.п.)."""
    url = (os.environ.get("RENDER_EXTERNAL_URL") or os.environ.get("SELF_URL") or "").strip()
    if not url:
        return
    import urllib.request
    target = url.rstrip("/") + "/"
    log(f"keepalive: {target} каждые 10 минут")
    while True:
        time.sleep(600)
        try:
            urllib.request.urlopen(target, timeout=20).read()
        except Exception:
            pass


def backup_loop() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    while True:
        time.sleep(BACKUP_EVERY)
        try:
            snap_paths = []
            for name, cfg in BOTS.items():
                src = os.path.join(DATA_DIR, cfg["db"])
                tmp = os.path.join(DATA_DIR, f"_snap_{cfg['db']}")
                if _db_snapshot(src, tmp):
                    snap_paths.append(tmp)
            hf_upload(snap_paths)
            for p in snap_paths:
                try:
                    os.remove(p)
                except OSError:
                    pass
        except Exception:
            traceback.print_exc()


def build_env(name: str, cfg: dict) -> dict:
    env = dict(os.environ)
    env["BOT_TOKEN"] = os.environ.get(cfg["token_env"], "")
    env["DB_PATH"] = os.path.join(DATA_DIR, cfg["db"])
    env["ADMIN_IDS"] = ADMIN_IDS
    env["ADMIN_ID"] = ADMIN_IDS.split(",")[0]
    # Экономия RSS: меньше арен glibc и агрессивнее возврат памяти ОС
    env["MALLOC_ARENA_MAX"] = "2"
    env["MALLOC_TRIM_THRESHOLD_"] = "131072"
    env["MALLOC_MMAP_THRESHOLD_"] = "131072"
    for k, v in cfg["extra"].items():
        env[k] = str(v)
    return env


def start_bot(name: str) -> None:
    cfg = BOTS[name]
    token = os.environ.get(cfg["token_env"], "").strip()
    if not token:
        log(f"{name}: пропущен (нет {cfg['token_env']})")
        return
    log_file = os.path.join(DATA_DIR, f"{name}.log")
    f = open(log_file, "ab", buffering=0)
    p = subprocess.Popen(
        [sys.executable, cfg["entry"]],
        cwd=os.path.join(APP_DIR, cfg["dir"]),
        env=build_env(name, cfg),
        stdout=f,
        stderr=subprocess.STDOUT,
    )
    procs[name] = {"proc": p, "started": time.time(), "restarts": procs.get(name, {}).get("restarts", 0)}
    log(f"{name}: запущен pid={p.pid} -> {log_file}")


def supervise_loop() -> None:
    while True:
        time.sleep(10)
        for name in list(BOTS):
            info = procs.get(name)
            if not info:
                if os.environ.get(BOTS[name]["token_env"], "").strip() and name not in procs:
                    start_bot(name)
                continue
            p = info["proc"]
            if p.poll() is None:
                continue
            if time.time() - info["started"] < 10:
                info["restarts"] += 1
            else:
                info["restarts"] = 0
            if info["restarts"] > 20:
                log(f"{name}: слишком много падений подряд, ждём 5 минут")
                time.sleep(300)
                info["restarts"] = 0
            log(f"{name}: процесс упал (code={p.returncode}), перезапуск")
            start_bot(name)


def tail(path: str, lines: int = 20) -> str:
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 4000))
            data = f.read().decode("utf-8", "ignore").splitlines()
        return "\n".join(data[-lines:])
    except Exception:
        return ""


def proc_mem_kb(pid) -> int:
    try:
        with open(f"/proc/{pid}/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1])
    except Exception:
        pass
    return 0


def log_tail(name: str, lines: int = 4) -> str:
    path = os.path.join(DATA_DIR, f"{name}.log")
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 3000))
            data = f.read().decode("utf-8", "ignore").splitlines()
        return "\n".join(data[-lines:])[-600:]
    except Exception:
        return ""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        payload = {
            "status": "ok",
            "uptime_sec": int(time.time() - started_at),
            "db": {
                n: (os.path.getsize(os.path.join(DATA_DIR, c["db"])) if os.path.exists(os.path.join(DATA_DIR, c["db"])) else 0)
                for n, c in BOTS.items()
            },
            "bots": {
                n: {
                    "alive": bool(procs.get(n) and procs[n]["proc"].poll() is None),
                    "pid": procs.get(n, {}).get("proc") and procs[n]["proc"].pid,
                    "mem_kb": proc_mem_kb(procs[n]["proc"].pid) if procs.get(n) and procs[n]["proc"].poll() is None else 0,
                }
                for n in BOTS
            },
            "mem_total_kb": sum(
                proc_mem_kb(procs[n]["proc"].pid)
                for n in BOTS if procs.get(n) and procs[n]["proc"].poll() is None
            ),
            "log_tail": {n: log_tail(n) for n in BOTS},
        }
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def main() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    hf_restore()
    threading.Thread(target=backup_loop, daemon=True).start()
    threading.Thread(target=keepalive_loop, daemon=True).start()
    threading.Thread(target=supervise_loop, daemon=True).start()
    for name in BOTS:
        start_bot(name)

    def shutdown(signum, frame):
        log("получен сигнал остановки, гасим ботов и делаем бэкап")
        for info in procs.values():
            try:
                info["proc"].terminate()
            except Exception:
                pass
        snap_paths = []
        for name, cfg in BOTS.items():
            src = os.path.join(DATA_DIR, cfg["db"])
            tmp = os.path.join(DATA_DIR, f"_snap_{cfg['db']}")
            if _db_snapshot(src, tmp):
                snap_paths.append(tmp)
        hf_upload(snap_paths)
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    server = HTTPServer(("0.0.0.0", PORT), Handler)
    log(f"healthcheck на :{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()