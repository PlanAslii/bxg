# state.py
import asyncio
import logging
from datetime import datetime
from collections import deque

from config import config
from storage import StorageManager

logger = logging.getLogger("uvicorn.error")

# ==============================================================================
# وضعیت و دیتا (Global App State)
# ==============================================================================
LINKS = {}
LINKS_LOCK = asyncio.Lock()

stats = {
    "total_requests": 0,
    "total_errors": 0,
    "total_bytes": 0,
    "uptime_start": datetime.now().isoformat()
}

hourly_traffic = {f"{h:02d}:00": 0 for h in range(24)}
connections = {}
error_logs = deque(maxlen=50)
activity_log = deque(maxlen=100)

storage = StorageManager(config.DATA_DIR, config.REDIS_URL)

def now_ir():
    from datetime import timezone, timedelta
    tz = timezone(timedelta(hours=3, minutes=30))
    return datetime.now(tz)

def log_activity(action: str, details: str, level="info"):
    activity_log.appendleft({
        "time": now_ir().strftime("%Y-%m-%d %H:%M:%S"),
        "action": action,
        "details": details,
        "level": level
    })

def is_link_allowed(link: dict) -> bool:
    if not link:
        return False
    if not link.get("enabled", True):
        return False
    limit = link.get("limit_bytes", 0)
    if limit > 0 and link.get("used_bytes", 0) >= limit:
        return False
    exp = link.get("expire_date")
    if exp:
        try:
            if datetime.now() > datetime.fromisoformat(exp):
                return False
        except:
            pass
    return True

async def save_state():
    async with LINKS_LOCK:
        state_data = {
            "LINKS": LINKS,
            "stats": stats,
            "hourly_traffic": hourly_traffic
        }
        await storage.save_state(state_data)
