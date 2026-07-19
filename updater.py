# updater.py
import asyncio, os, time, traceback, re, json, hashlib
from pathlib import Path
from collections import deque
import httpx
from config import config

UPDATE_MANIFEST_URL = config.UPDATE_MANIFEST_URL
APP_DIR = Path(config.APP_DIR)
LOCAL_VERSION_FILE = APP_DIR / "version.txt"
DATA_DIR = Path(config.DATA_DIR)
HISTORY_FILE = DATA_DIR / "update_history.json"

REPO = UPDATE_MANIFEST_URL
BRANCH = "cf-worker-manifest"

update_log: deque = deque(maxlen=300)
update_state = {"running": False, "progress": 0}

_manifest_cache: dict = {"data": None, "ts": 0.0}
MANIFEST_CACHE_LOCK = asyncio.Lock()
MANIFEST_CACHE_TTL = config.MANIFEST_CACHE_TTL

def _log(msg: str):
    update_log.append({"time": time.time(), "msg": msg})
    print(f"[UPDATER] {msg}", flush=True)

def _parse_kv_text(text: str) -> dict:
    result = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"): continue
        if "=" not in line: continue
        k, v = line.split("=", 1)
        k = k.strip().lower()
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'): v = v[1:-1]
        result[k] = v
    return result

def _parse_version_tuple(v: str):
    if not v: return None
    parts = re.findall(r"\d+", v)
    if not parts: return None
    return tuple(int(p) for p in parts)

def is_newer_version(latest: str, current: str) -> bool:
    if not latest: return False
    if not current or current == "نامشخص": return True
    lv, cv = _parse_version_tuple(latest), _parse_version_tuple(current)
    if lv is not None and cv is not None: return lv > cv
    return latest != current

def get_current_version_info() -> dict:
    try:
        if LOCAL_VERSION_FILE.exists():
            kv = _parse_kv_text(LOCAL_VERSION_FILE.read_text(encoding="utf-8"))
            return {"version": kv.get("version", "نامشخص"), "description": kv.get("description", "")}
    except Exception: pass
    return {"version": "نامشخص", "description": ""}

def get_current_version() -> str: return get_current_version_info()["version"]

def _write_local_version_file(version: str, description: str):
    try:
        content = f"version={version}\ndescription={description}\n"
        tmp = LOCAL_VERSION_FILE.with_suffix(".tmp")
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, LOCAL_VERSION_FILE)
    except Exception as e: _log(f"⚠️ خطا در نوشتن version.txt محلی: {e}")

async def _fetch_manifest_from_worker() -> dict:
    if not UPDATE_MANIFEST_URL: return {"error": "UPDATE_MANIFEST_URL تنظیم نشده"}
    url = f"{UPDATE_MANIFEST_URL}?_={int(time.time())}"
    headers = {"Cache-Control": "no-cache", "User-Agent": "Mozilla/5.0"}
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            r = await client.get(url, headers=headers)
            if r.status_code == 404: return {"error": "مانیفست پیدا نشد"}
            r.raise_for_status()
            data = json.loads(r.text)
            return {
                "version": data.get("version", ""),
                "description": data.get("description", ""),
                "files": data.get("files", []),
            }
    except Exception as e: return {"error": str(e)}

async def get_latest_version_info() -> dict:
    now = time.time()
    async with MANIFEST_CACHE_LOCK:
        cached = _manifest_cache["data"]
        age = now - _manifest_cache["ts"]
        if cached is not None and age < MANIFEST_CACHE_TTL:
            return cached if cached.get("error") else {"version": cached.get("version", ""), "description": cached.get("description", "")}
        result = await _fetch_manifest_from_worker()
        _manifest_cache["data"] = result
        _manifest_cache["ts"] = now
        return result if result.get("error") else {"version": result.get("version", ""), "description": result.get("description", "")}

def _check_writable() -> str | None:
    try:
        probe = APP_DIR / ".rvg_write_test"
        probe.write_text("ok")
        probe.unlink()
        return None
    except Exception as e: return str(e)

async def perform_update() -> bool:
    update_state["running"] = True
    update_state["progress"] = 1
    
    write_err = _check_writable()
    if write_err:
        update_state["running"] = False
        return False

    manifest = await _fetch_manifest_from_worker()
    if manifest.get("error"):
        update_state["running"] = False
        return False

    new_version = manifest.get("version", "")
    files = manifest.get("files", [])
    update_state["progress"] = 15

    if not files:
        update_state["running"] = False
        return False

    try:
        written = 0
        async with httpx.AsyncClient(follow_redirects=True) as client:
            total = len(files)
            for i, entry in enumerate(files, start=1):
                url = entry.get("url", "")
                rel = entry.get("path", "").lstrip("/")
                if not url or not rel: continue
                target = (APP_DIR / rel).resolve()
                r = await client.get(url, timeout=30)
                r.raise_for_status()
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(r.content)
                written += 1
                update_state["progress"] = 15 + int((i / total) * 75)
        
        if written > 0:
            _write_local_version_file(new_version, manifest.get("description", ""))
            os._exit(0) # خروج برای ریستارت
        
        update_state["running"] = False
        return True
    except Exception:
        update_state["running"] = False
        return False
