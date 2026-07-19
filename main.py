import asyncio
import os
import sys
import uuid
import logging
from datetime import datetime
from collections import deque
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
import uvicorn

# ایمپورت ماژول‌های جدید کلاد-نیتیو
from config import config
from storage import StorageManager

# لاگر
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("uvicorn.error")

# ==============================================================================
# وضعیت و دیتا (Global State)
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
SECRET_KEY = "" # در هنگام استارتاپ مقداردهی می‌شود

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
        state = {
            "LINKS": LINKS,
            "stats": stats,
            "hourly_traffic": hourly_traffic
        }
        await storage.save_state(state)

# ==============================================================================
# چرخه حیات (Lifespan)
# ==============================================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    global SECRET_KEY
    await storage.connect()
    
    # لود کردن دیتا
    state = await storage.load_state()
    async with LINKS_LOCK:
        LINKS.update(state.get("LINKS", {}))
    stats.update(state.get("stats", {"total_requests": 0, "total_errors": 0, "total_bytes": 0, "uptime_start": datetime.now().isoformat()}))
    hourly_traffic.update(state.get("hourly_traffic", {f"{h:02d}:00": 0 for h in range(24)}))
    
    SECRET_KEY = await storage.get_or_create_secret(config.SECRET_KEY)
    
    logger.info(f"🚀 سرور راه‌اندازی شد. (تعداد کانفیگ‌ها: {len(LINKS)})")
    
    # اضافه کردن میدل‌ور سشن به صورت داینامیک پس از تولید Secret
    app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, max_age=86400 * 7)
    
    yield
    
    # در هنگام خاموش شدن
    await save_state()
    logger.info("🛑 سرور متوقف شد و وضعیت ذخیره شد.")

# ==============================================================================
# اپلیکیشن اصلی
# ==============================================================================
app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==============================================================================
# روت‌های پراکسی (جدا شده)
# ==============================================================================
from relay_vless import websocket_tunnel
from xhttp_siz10 import router as xhttp_router
app.include_router(xhttp_router)

@app.websocket("/vless/{uuid}")
async def vless_ws(websocket, uuid: str):
    await websocket_tunnel(websocket, uuid)

# ==============================================================================
# روت‌های پنل مدیریت
# ==============================================================================
from central import router as central_router
from pages import router as pages_router
app.include_router(central_router)
app.include_router(pages_router)

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=config.PORT, reload=False)