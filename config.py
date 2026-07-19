# config.py
import os

class Config:
    """
    مرکز فرماندهی متغیرهای محیطی (Environment Variables)
    """
    PORT = int(os.environ.get("PORT", "8000"))
    
    # تشخیص هوشمند دامنه عمومی بر اساس پلتفرم
    PUBLIC_DOMAIN = (
        os.environ.get("PUBLIC_DOMAIN") or 
        os.environ.get("RAILWAY_PUBLIC_DOMAIN") or 
        os.environ.get("RENDER_EXTERNAL_HOSTNAME") or 
        "localhost:8000"
    )
    
    # کلید امنیتی سشن‌ها
    SECRET_KEY = os.environ.get("SECRET_KEY", "")
    
    # تنظیمات ذخیره‌سازی (برای پایداری روی PaaSهای بدون دیسک)
    REDIS_URL = os.environ.get("REDIS_URL", os.environ.get("REDIS_PRIVATE_URL", ""))
    
    # مسیرهای فایل سیستم
    APP_DIR = os.environ.get("APP_DIR", os.getcwd())
    DATA_DIR = os.environ.get("DATA_DIR", "/data")
    
    # تنظیمات آپدیتر
    UPDATE_MANIFEST_URL = os.environ.get("UPDATE_MANIFEST_URL", "https://rvg-update.arvin341az.workers.dev/version.json")
    MANIFEST_CACHE_TTL = float(os.environ.get("MANIFEST_CACHE_TTL", "120"))

config = Config()
