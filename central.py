# central.py
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import PlainTextResponse
import uuid
import asyncio

from config import config
from state import (
    LINKS, LINKS_LOCK, stats, hourly_traffic, connections, error_logs, activity_log,
    save_state, log_activity, now_ir
)

router = APIRouter()

def require_auth(request: Request):
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401, detail="Unauthorized")

@router.get("/api/status")
async def api_status(request: Request):
    require_auth(request)
    return {
        "connections": len(connections),
        "total_requests": stats["total_requests"],
        "total_bytes": stats["total_bytes"],
        "uptime": stats["uptime_start"],
        "hourly": hourly_traffic,
        "recent_errors": list(error_logs)[-5:]
    }

@router.get("/api/links")
async def api_get_links(request: Request):
    require_auth(request)
    async with LINKS_LOCK:
        return {"links": list(LINKS.values())}

@router.post("/api/links")
async def api_add_link(request: Request):
    require_auth(request)
    data = await request.json()
    new_uuid = str(uuid.uuid4())
    link_obj = {
        "uuid": new_uuid,
        "label": data.get("label", "بدون نام"),
        "enabled": True,
        "used_bytes": 0,
        "limit_bytes": int(data.get("limit_bytes", 0)),
        "expire_date": data.get("expire_date", ""),
        "created_at": now_ir().isoformat()
    }
    async with LINKS_LOCK:
        LINKS[new_uuid] = link_obj
    await save_state()
    log_activity("create_config", f"کانفیگ جدید: {link_obj['label']}", "success")
    return {"status": "ok", "uuid": new_uuid}

@router.delete("/api/links/{uid}")
async def api_del_link(uid: str, request: Request):
    require_auth(request)
    async with LINKS_LOCK:
        if uid in LINKS:
            lbl = LINKS[uid]['label']
            del LINKS[uid]
            await save_state()
            log_activity("delete_config", f"حذف کانفیگ: {lbl}", "warning")
            return {"status": "ok"}
    raise HTTPException(404, "not found")

@router.post("/api/links/{uid}/toggle")
async def api_toggle_link(uid: str, request: Request):
    require_auth(request)
    async with LINKS_LOCK:
        if uid in LINKS:
            LINKS[uid]["enabled"] = not LINKS[uid]["enabled"]
            st = "فعال" if LINKS[uid]["enabled"] else "غیرفعال"
            await save_state()
            log_activity("toggle_config", f"وضعیت {LINKS[uid]['label']} تغییر کرد به {st}")
            return {"status": "ok", "enabled": LINKS[uid]["enabled"]}
    raise HTTPException(404)

@router.post("/api/links/{uid}/reset")
async def api_reset_link(uid: str, request: Request):
    require_auth(request)
    async with LINKS_LOCK:
        if uid in LINKS:
            LINKS[uid]["used_bytes"] = 0
            await save_state()
            log_activity("reset_traffic", f"ریست ترافیک: {LINKS[uid]['label']}")
            return {"status": "ok"}
    raise HTTPException(404)

@router.get("/api/connections")
async def api_connections(request: Request):
    require_auth(request)
    return {"connections": connections}

@router.delete("/api/connections/{conn_id}")
async def api_kill_connection(conn_id: str, request: Request):
    require_auth(request)
    if conn_id in connections:
        connections.pop(conn_id, None)
        log_activity("kill_connection", f"قطع دستی اتصال {conn_id}", "warning")
        return {"status": "ok"}
    raise HTTPException(404)

@router.get("/api/logs")
async def api_logs(request: Request):
    require_auth(request)
    return {"logs": list(activity_log)}

@router.get("/sub/{uid}")
async def get_subscription(uid: str, request: Request):
    async with LINKS_LOCK:
        link = LINKS.get(uid)
    if not link:
        raise HTTPException(404, "Not Found")
    
    domain = config.PUBLIC_DOMAIN
    
    conf_vless = f"vless://{uid}@{domain}:443?encryption=none&security=tls&sni={domain}&type=ws&host={domain}&path=%2Fvless%2F{uid}#{link['label']}-WS"
    conf_xhttp_pu = f"vless://{uid}@{domain}:443?encryption=none&security=tls&sni={domain}&type=xhttp&host={domain}&path=%2Fxhttp-siz10%2Fpacket-up%2F{uid}&mode=packet-up#{link['label']}-XPU"
    conf_xhttp_su = f"vless://{uid}@{domain}:443?encryption=none&security=tls&sni={domain}&type=xhttp&host={domain}&path=%2Fxhttp-siz10%2Fstream-up%2F{uid}&mode=stream-up#{link['label']}-XSU"

    lines = [conf_vless, conf_xhttp_pu, conf_xhttp_su]
    import base64
    b64 = base64.b64encode("\n".join(lines).encode()).decode()
    return PlainTextResponse(b64)
