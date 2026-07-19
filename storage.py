# storage.py
import json
import os
import secrets
import logging
from pathlib import Path

logger = logging.getLogger("uvicorn.error")

class StorageManager:
    """
    موتور ذخیره‌سازی هوشمند.
    """
    def __init__(self, data_dir: str, redis_url: str):
        self.data_dir = Path(data_dir)
        self.redis_url = redis_url
        self.use_redis = bool(redis_url)
        self.redis_client = None

        if not self.use_redis:
            try:
                self.data_dir.mkdir(parents=True, exist_ok=True)
            except OSError:
                logger.warning(f"⚠️ عدم دسترسی به {self.data_dir}، تغییر مسیر ذخیره‌سازی به پوشه محلی ./data")
                self.data_dir = Path("./data")
                self.data_dir.mkdir(parents=True, exist_ok=True)

        self.state_file = self.data_dir / "rvg_state.json"
        self.secret_file = self.data_dir / ".rvg_secret"

    async def connect(self):
        if self.use_redis:
            try:
                import redis.asyncio as redis
                self.redis_client = redis.from_url(self.redis_url, decode_responses=True)
                await self.redis_client.ping()
                logger.info("✅ به دیتابیس Redis متصل شد. داده‌ها کاملاً پایدار خواهند بود.")
            except Exception as e:
                logger.error(f"❌ خطای اتصال به Redis: {e}. بازگشت به ذخیره‌سازی محلی (فایل).")
                self.use_redis = False

    async def load_state(self) -> dict:
        if self.use_redis and self.redis_client:
            try:
                data = await self.redis_client.get("rvg_state")
                if data:
                    return json.loads(data)
                return {}
            except Exception as e:
                logger.error(f"خطا در خواندن از Redis: {e}")
                return {}
        else:
            if self.state_file.exists():
                try:
                    return json.loads(self.state_file.read_text(encoding="utf-8"))
                except Exception as e:
                    logger.error(f"خطا در خواندن فایل وضعیت: {e}")
            return {}

    async def save_state(self, state_data: dict):
        if self.use_redis and self.redis_client:
            try:
                await self.redis_client.set("rvg_state", json.dumps(state_data))
            except Exception as e:
                logger.error(f"خطا در ذخیره در Redis: {e}")
        else:
            try:
                tmp = self.state_file.with_suffix(".tmp")
                tmp.write_text(json.dumps(state_data, ensure_ascii=False, indent=2), encoding="utf-8")
                os.replace(tmp, self.state_file)
            except Exception as e:
                logger.error(f"خطا در ذخیره فایل وضعیت: {e}")

    async def get_or_create_secret(self, env_secret: str) -> str:
        if env_secret:
            return env_secret
        
        logger.warning("⚠️ SECRET_KEY در متغیرهای محیطی تنظیم نشده است!")
        
        if self.use_redis and self.redis_client:
            secret = await self.redis_client.get("rvg_secret")
            if secret: return secret
        else:
            if self.secret_file.exists():
                return self.secret_file.read_text(encoding="utf-8").strip()
        
        new_secret = secrets.token_urlsafe(32)
        if self.use_redis and self.redis_client:
            await self.redis_client.set("rvg_secret", new_secret)
        else:
            try:
                self.secret_file.write_text(new_secret, encoding="utf-8")
            except Exception as e:
                logger.error(f"خطا در ذخیره کلید امنیتی: {e}")
        
        logger.info("🔑 یک SECRET_KEY موقت ایجاد و ذخیره شد.")
        return new_secret
