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
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip().lower()
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
            v = v[1:-1]
        result[k] = v
    return result

def _parse_version_tuple(v: str):
    if not v:
        return None
    parts = re.findall(r"\d+", v)
    if not parts:
        return None
    return tuple(int(p) for p in parts)

def is_newer_version(latest: str, current: str) -> bool:
    if not latest:
        return False
    if not current or current == "نامشخص":
        return True
    lv, cv = _parse_version_tuple(latest), _parse_version_tuple(current)
    if lv is not None and cv is not None:
        return lv > cv
    return latest != current

def get_current_version_info() -> dict:
    try:
        if LOCAL_VERSION_FILE.exists():
            kv = _parse_kv_text(LOCAL_VERSION_FILE.read_text(encoding="utf-8"))
            return {
                "version": kv.get("version", "نامشخص"),
                "description": kv.get("description", ""),
            }
    except Exception:
        pass
    return {"version": "نامشخص", "description": ""}

def get_current_version() -> str:
    return get_current_version_info()["version"]

def _write_local_version_file(version: str, description: str):
    try:
        content = f"version={version}\ndescription={description}\n"
        tmp = LOCAL_VERSION_FILE.with_suffix(".tmp")
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, LOCAL_VERSION_FILE)
    except Exception as e:
        _log(f"⚠️ خطا در نوشتن version.txt محلی: {e}")

async def _fetch_manifest_from_worker() -> dict:
    if not UPDATE_MANIFEST_URL:
        return {"error": "UPDATE_MANIFEST_URL تنظیم نشده"}
    url = f"{UPDATE_MANIFEST_URL}?_={int(time.time())}"
    headers = {
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache",
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json, text/plain, */*",
    }
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            r = await client.get(url, headers=headers)
            if r.status_code == 404:
                return {"error": "مانیفست version.json پیدا نشد"}
            r.raise_for_status()

            raw_text = r.text
            try:
                data = json.loads(raw_text)
            except json.JSONDecodeError as je:
                _log(f"⚠️ پاسخ Worker معتبر نبود")
                return {"error": f"پاسخ Worker قابل‌پارس نبود: {je}"}

            if "version" not in data:
                return {"error": "فرمت مانیفست نامعتبر است (کلید version یافت نشد)"}
            if "files" not in data or not isinstance(data["files"], list):
                return {"error": "فرمت مانیفست نامعتبر است (کلید files یافت نشد)"}
            return {
                "version": data.get("version", ""),
                "description": data.get("description", ""),
                "files": data.get("files", []),
            }
    except httpx.HTTPStatusError as e:
        return {"error": f"HTTP {e.response.status_code} از Worker"}
    except Exception as e:
        return {"error": str(e)}

async def get_latest_version_info() -> dict:
    now = time.time()
    async with MANIFEST_CACHE_LOCK:
        cached = _manifest_cache["data"]
        age = now - _manifest_cache["ts"]
        if cached is not None and age < MANIFEST_CACHE_TTL:
            if cached.get("error"):
                return cached
            return {"version": cached.get("version", ""), "description": cached.get("description", "")}

        result = await _fetch_manifest_from_worker()

        if result.get("error") and cached and not cached.get("error"):
            _manifest_cache["ts"] = now - (MANIFEST_CACHE_TTL * 0.5)
            return {"version": cached.get("version", ""), "description": cached.get("description", "")}

        _manifest_cache["data"] = result
        _manifest_cache["ts"] = now
        if result.get("error"):
            return result
        return {"version": result.get("version", ""), "description": result.get("description", "")}

async def get_latest_version() -> dict:
    return await get_latest_version_info()

def _check_writable() -> str | None:
    try:
        probe = APP_DIR / ".rvg_write_test"
        probe.write_text("ok")
        probe.unlink()
        return None
    except Exception as e:
        return str(e)

def load_update_history() -> list:
    try:
        if HISTORY_FILE.exists():
            data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
    except Exception:
        pass
    return []

def _save_update_history_entry(entry: dict):
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        hist = load_update_history()
        hist.insert(0, entry)
        hist = hist[:200]
        HISTORY_FILE.write_text(json.dumps(hist, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        _log(f"⚠️ خطا در ذخیره‌ی تاریخچه‌ی بروزرسانی: {e}")

async def _download_one_file(client: httpx.AsyncClient, entry: dict) -> tuple[bool, str]:
    rel = entry.get("path", "").lstrip("/")
    url = entry.get("url", "")
    expected_sha1 = entry.get("sha1")
    if not rel or not url:
        return False, "ورودی مانیفست ناقص است"
    target = (APP_DIR / rel).resolve()
    if not str(target).startswith(str(APP_DIR.resolve())):
        return False, f"مسیر غیرمجاز رد شد: {rel}"
    try:
        r = await client.get(url, timeout=30)
        r.raise_for_status()
        content = r.content
        if expected_sha1:
            actual = hashlib.sha1(content).hexdigest()
            if actual != expected_sha1:
                return False, f"عدم تطابق sha1 برای {rel}"
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp_target = target.with_name(target.name + ".rvgtmp")
        tmp_target.write_bytes(content)
        os.replace(tmp_target, target)
        return True, ""
    except Exception as e:
        return False, str(e)

async def perform_update() -> bool:
    update_state["running"] = True
    update_state["progress"] = 1
    _log(f"شروع بروزرسانی | MANIFEST={UPDATE_MANIFEST_URL or 'خالی!'} | APP_DIR={APP_DIR}")

    write_err = _check_writable()
    if write_err:
        _log(f"❌ عدم دسترسی نوشتن روی {APP_DIR}: {write_err}")
        _log("فایل‌سیستم این کانتینر فقط-خواندنی است. آپدیت را از طریق Redeploy پنل انجام دهید.")
        update_state["running"] = False
        return False

    if not UPDATE_MANIFEST_URL:
        update_state["running"] = False
        return False

    old_version = get_current_version()
    update_state["progress"] = 5
    manifest = await _fetch_manifest_from_worker()
    if manifest.get("error"):
        update_state["running"] = False
        return False

    new_version = manifest.get("version", "")
    new_description = manifest.get("description", "")
    files = manifest.get("files", [])
    update_state["progress"] = 15

    if not files:
        update_state["running"] = False
        return False

    try:
        written, failed = 0, 0
        fail_msgs = []
        async with httpx.AsyncClient(follow_redirects=True) as client:
            total = len(files)
            for i, entry in enumerate(files, start=1):
                ok, err = await _download_one_file(client, entry)
                if ok:
                    written += 1
                else:
                    failed += 1
                    fail_msgs.append(f"{entry.get('path','?')}: {err}")
                update_state["progress"] = 15 + int((i / total) * 75)

        update_state["progress"] = 92

        if written == 0:
            update_state["running"] = False
            return False

        _write_local_version_file(new_version, new_description)

        update_state["progress"] = 100
        _manifest_cache["data"] = None
        _manifest_cache["ts"] = 0.0

        _save_update_history_entry({
            "time": time.time(),
            "from_version": old_version,
            "to_version": new_version,
            "description": new_description,
            "status": "ok",
            "note": (f"{failed} فایل با خطا رد شد" if failed else None),
        })
        
        # خروج امن برای ری‌استارت توسط کانتینر
        os._exit(0)
        return True

    except Exception as exc:
        update_state["running"] = False
        return False