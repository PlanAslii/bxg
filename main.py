# main.py
import asyncio
import logging
from datetime import datetime
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
import uvicorn

# ایمپورت‌های ماژول‌های داخلی
from config import config
from state import LINKS, LINKS_LOCK, stats, hourly_traffic, storage, save_state, logger

SECRET_KEY = ""

# ==============================================================================
# چرخه حیات (Lifespan)
# ==============================================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    global SECRET_KEY
    await storage.connect()
    
    # لود کردن دیتای ذخیره شده
    state_data = await storage.load_state()
    async with LINKS_LOCK:
        LINKS.update(state_data.get("LINKS", {}))
    stats.update(state_data.get("stats", {"total_requests": 0, "total_errors": 0, "total_bytes": 0, "uptime_start": datetime.now().isoformat()}))
    hourly_traffic.update(state_data.get("hourly_traffic", {f"{h:02d}:00": 0 for h in range(24)}))
    
    SECRET_KEY = await storage.get_or_create_secret(config.SECRET_KEY)
    
    logger.info(f"🚀 سرور راه‌اندازی شد. (تعداد کانفیگ‌ها: {len(LINKS)})")
    
    # اضافه کردن میدل‌ور سشن به صورت داینامیک پس از تولید Secret
    app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, max_age=86400 * 7)
    
    yield
    
    # ذخیره هنگام خاموش شدن
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
# اضافه کردن روت‌ها
# ==============================================================================
from relay_vless import websocket_tunnel
from xhttp_siz10 import router as xhttp_router
from central import router as central_router
from pages import router as pages_router

app.include_router(xhttp_router)
app.include_router(central_router)
app.include_router(pages_router)

@app.websocket("/vless/{uuid}")
async def vless_ws(websocket, uuid: str):
    await websocket_tunnel(websocket, uuid)

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=config.PORT, reload=False)
