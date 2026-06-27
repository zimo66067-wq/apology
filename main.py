"""
apology-backend — 网页访问追踪服务

接口：
  POST   /api/visit                 记录一次访问（含设备信息），返回 count + visit_id
  PATCH  /api/visit/{visit_id}      更新本次访问的已读页码
  GET    /api/count                 查询总访问次数
  GET    /api/health                健康检查
  GET    /api/admin?key=xxx         管理后台（HTML 页面，需要密钥）
  GET    /                          前端页面 index.html

环境变量（在 Render 的 Environment 里设置）：
  DATABASE_URL   SQLite 连接串，默认 sqlite:///./visits.db
  ADMIN_KEY      管理后台密钥，默认 xinyu2025（建议改掉）
"""

import json
import os
import re
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import Body, Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import Visit, get_db, init_db

# 与前端 slides 数组长度保持一致，影响管理后台的页码格子数量
TOTAL_SLIDES = 16

ADMIN_KEY = os.getenv("ADMIN_KEY", "xinyu2025")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="访问追踪服务", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── 工具函数 ──────────────────────────────────────────

def detect_device(ua: str) -> str:
    """从 User-Agent 粗略判断设备类型"""
    if re.search(r"iPad|Tablet", ua, re.I):
        return "tablet"
    if re.search(r"Mobile|Android|iPhone|iPod|Windows Phone", ua, re.I):
        return "mobile"
    return "desktop"


# 品牌规则：(UA 中的识别关键词, 显示品牌名, 型号字符串中要去掉的前缀)
_BRAND_RULES = [
    (["HUAWEI"],               "华为",    ["HUAWEI ", "Huawei "]),
    (["HONOR"],                "荣耀",    ["HONOR ", "Honor "]),
    (["XIAOMI", "REDMI", "POCO"], "小米", ["Xiaomi ", "xiaomi ", "Redmi ", "redmi ", "POCO "]),
    (["OPPO"],                 "OPPO",   ["OPPO "]),
    (["VIVO"],                 "vivo",   ["vivo ", "VIVO "]),
    (["REALME"],               "Realme", ["realme ", "Realme ", "REALME "]),
    (["ONEPLUS"],              "一加",   ["OnePlus ", "ONEPLUS "]),
    (["SAMSUNG", "; SM-"],     "三星",   ["Samsung ", "SAMSUNG "]),
    (["MEIZU"],                "魅族",   ["Meizu ", "MEIZU "]),
    (["NOKIA"],                "诺基亚", ["Nokia ", "NOKIA "]),
    (["SONY"],                 "索尼",   ["Sony ", "SONY "]),
    (["PIXEL", "NEXUS"],       "Google", ["Pixel ", "pixel ", "Nexus "]),
    (["MOTOROLA", "MOTO "],    "摩托罗拉", ["Motorola ", "moto ", "Moto "]),
    (["LENOVO"],               "联想",   ["Lenovo ", "LENOVO "]),
    (["ASUS"],                 "华硕",   ["ASUS ", "Asus "]),
]


def _match_brand(text: str):
    """返回 (品牌显示名, 型号前缀列表) 或 (None, [])"""
    t = text.upper()
    for keywords, brand, prefixes in _BRAND_RULES:
        if any(kw.upper() in t for kw in keywords):
            return brand, prefixes
    return None, []


def _strip_prefix(model: str, prefixes: list) -> str:
    """去掉型号字符串开头的品牌前缀，让显示更简洁"""
    for p in prefixes:
        if model.lower().startswith(p.lower()):
            return model[len(p):].strip()
    return model


def parse_device_info(ua: str) -> str:
    """从 User-Agent 提取可读的设备型号 / 系统信息"""
    if not ua:
        return "Unknown"

    # iPhone
    m = re.search(r"iPhone OS ([\d_]+)", ua)
    if m:
        return f"iPhone · iOS {m.group(1).replace('_', '.')}"

    # iPad
    m = re.search(r"iPad.*?OS ([\d_]+)", ua)
    if m:
        return f"iPad · iOS {m.group(1).replace('_', '.')}"

    # Android — 提取版本号和型号
    m = re.search(r"Android ([\d.]+);\s*([^;)]+?)(?:\s+Build|[;)])", ua)
    if m:
        android_ver = m.group(1)
        raw_model   = m.group(2).strip()
        brand, prefixes = _match_brand(ua + " " + raw_model)
        if brand:
            clean_model = _strip_prefix(raw_model, prefixes)
            return f"{brand} · {clean_model}" if clean_model else brand
        return f"Android {android_ver} · {raw_model}" if raw_model else f"Android {android_ver}"

    if re.search(r"Android", ua, re.I):
        m = re.search(r"Android ([\d.]+)", ua)
        return f"Android {m.group(1)}" if m else "Android"

    # Windows 桌面
    if re.search(r"Windows", ua):
        browser = ("Edge"    if re.search(r"Edg/", ua) else
                   "Chrome"  if "Chrome"  in ua else
                   "Firefox" if "Firefox" in ua else "Browser")
        return f"Windows · {browser}"

    # Mac 桌面
    if re.search(r"Macintosh", ua):
        browser = ("Chrome" if "Chrome" in ua else
                   "Safari" if "Safari" in ua else "Browser")
        return f"Mac · {browser}"

    if re.search(r"Linux", ua):
        return "Linux · Browser"

    return "Unknown"


def get_ip(request: Request) -> str:
    fwd = request.headers.get("X-Forwarded-For")
    return fwd.split(",")[0].strip() if fwd else (request.client.host or "")


# ── 请求 / 响应模型 ───────────────────────────────────

class VisitCreate(BaseModel):
    user_agent:   Optional[str]       = None
    pages_viewed: Optional[List[int]] = None


class PagesUpdate(BaseModel):
    pages_viewed: List[int]


class VisitResponse(BaseModel):
    count:    int
    visit_id: str


class CountResponse(BaseModel):
    count: int


class HealthResponse(BaseModel):
    status: str


# ── 接口 ──────────────────────────────────────────────

@app.post("/api/visit", response_model=VisitResponse, summary="记录一次访问")
def record_visit(
    request: Request,
    body: VisitCreate = Body(default_factory=VisitCreate),
    db: Session = Depends(get_db),
):
    ua     = body.user_agent or request.headers.get("user-agent", "")
    pages  = sorted(set(body.pages_viewed or [0]))
    visit  = Visit(
        visit_id    = str(uuid.uuid4()),
        ip          = get_ip(request),
        user_agent  = ua,
        device_type = detect_device(ua),
        pages_viewed = json.dumps(pages),
        max_page    = max(pages),
    )
    db.add(visit)
    db.commit()
    count = db.query(Visit).count()
    return {"count": count, "visit_id": visit.visit_id}


@app.patch("/api/visit/{visit_id}", summary="更新已读页码")
def update_pages(
    visit_id: str,
    body: PagesUpdate,
    db: Session = Depends(get_db),
):
    v = db.query(Visit).filter(Visit.visit_id == visit_id).first()
    if not v:
        raise HTTPException(status_code=404, detail="visit not found")
    pages = sorted(set(body.pages_viewed))
    v.pages_viewed = json.dumps(pages)
    v.max_page     = max(pages) if pages else 0
    db.commit()
    return {"ok": True}


@app.get("/api/count", response_model=CountResponse, summary="查询总访问次数")
def get_count(db: Session = Depends(get_db)):
    return {"count": db.query(Visit).count()}


@app.get("/api/health", response_model=HealthResponse, summary="健康检查")
def health():
    return {"status": "ok"}


@app.get("/api/admin", response_class=HTMLResponse, summary="管理后台", include_in_schema=False)
def admin_panel(key: str = "", db: Session = Depends(get_db)):
    if key != ADMIN_KEY:
        return HTMLResponse(
            "<html><body style='font-family:sans-serif;padding:2rem'>"
            "<h3>密钥错误</h3><p>在 URL 后加 <code>?key=你的密钥</code></p>"
            "</body></html>",
            status_code=403,
        )

    visits = db.query(Visit).order_by(Visit.created_at.desc()).all()

    device_icon = {"mobile": "📱", "tablet": "📟", "desktop": "💻"}

    rows = ""
    for v in visits:
        pages    = set(json.loads(v.pages_viewed or "[]"))
        cst      = v.created_at + timedelta(hours=8)
        time_str = cst.strftime("%m-%d %H:%M")
        icon     = device_icon.get(v.device_type or "desktop", "💻")
        chips    = "".join(
            f'<span class="chip on">{i+1}</span>' if i in pages
            else f'<span class="chip">{i+1}</span>'
            for i in range(TOTAL_SLIDES)
        )
        rows += f"""<tr>
          <td>{icon} {parse_device_info(v.user_agent or "")}</td>
          <td class="muted">{v.ip or "-"}</td>
          <td>{time_str}</td>
          <td class="center">{v.max_page + 1}&thinsp;/&thinsp;{TOTAL_SLIDES}</td>
          <td class="chips">{chips}</td>
        </tr>"""

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>访客记录</title>
  <style>
    *{{box-sizing:border-box;margin:0;padding:0}}
    body{{font-family:-apple-system,BlinkMacSystemFont,'PingFang SC',sans-serif;
         background:#f7f4ef;color:#333;padding:1.5rem;font-size:.9rem}}
    h2{{font-size:1rem;font-weight:500;color:#888;margin-bottom:1.2rem}}
    table{{width:100%;border-collapse:collapse;background:#fff;
           border-radius:10px;overflow:hidden;
           box-shadow:0 1px 12px rgba(0,0,0,.06)}}
    thead th{{background:#f0ece4;padding:.6rem 1rem;text-align:left;
              font-size:.75rem;color:#999;font-weight:500;white-space:nowrap}}
    tbody td{{padding:.65rem 1rem;border-top:1px solid #f2ede7;vertical-align:middle}}
    .muted{{color:#ccc;font-size:.75rem;font-family:monospace}}
    .center{{text-align:center;white-space:nowrap}}
    .chips{{white-space:normal;line-height:1.9}}
    .chip{{display:inline-block;width:20px;height:20px;line-height:20px;
           text-align:center;border-radius:4px;font-size:.62rem;
           background:#eee;color:#ccc;margin:1px}}
    .chip.on{{background:#c4a882;color:#fff;font-weight:600}}
    @media(max-width:600px){{
      body{{padding:1rem .75rem}}
      td,th{{padding:.5rem .6rem}}
      .chip{{width:17px;height:17px;line-height:17px;font-size:.58rem}}
    }}
  </style>
</head>
<body>
  <h2>访客记录 &middot; 共 {len(visits)} 次访问</h2>
  <table>
    <thead>
      <tr><th>设备</th><th>IP</th><th>时间（北京）</th><th>最远页</th><th>已看页码</th></tr>
    </thead>
    <tbody>
      {rows or '<tr><td colspan="5" style="text-align:center;color:#ccc;padding:2.5rem">暂无记录</td></tr>'}
    </tbody>
  </table>
</body>
</html>"""
    return HTMLResponse(html)


# ── 根路径提供前端页面 ────────────────────────────────
@app.get("/", include_in_schema=False)
def serve_index():
    return FileResponse("index.html")
