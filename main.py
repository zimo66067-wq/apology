"""
======================================================
apology-backend — 网页访问计数服务
======================================================

接口说明：
  POST  /api/visit   访问计数 +1，记录 IP 和时间戳，返回当前总数
  GET   /api/count   查询当前总访问次数
  GET   /api/health  健康检查

启动方式：
  uvicorn main:app --reload --port 8000

环境变量（见 .env.example）：
  DATABASE_URL   SQLite 连接串，默认 sqlite:///./visits.db
======================================================
"""

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import Visit, get_db, init_db


# ── 生命周期：启动时建表 ──────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="访问计数服务", lifespan=lifespan)

# ── CORS：允许所有来源（部署后可按需收窄） ──────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── 响应模型 ─────────────────────────────────────────
class CountResponse(BaseModel):
    count: int


class HealthResponse(BaseModel):
    status: str


# ── 接口实现 ─────────────────────────────────────────

@app.post("/api/visit", response_model=CountResponse, summary="记录一次访问")
def record_visit(request: Request, db: Session = Depends(get_db)):
    """
    每次调用：
    1. 向 visits 表写入一条记录（含来源 IP、时间戳）
    2. 返回当前总访问次数
    """
    # 获取来源 IP（反向代理时读 X-Forwarded-For）
    forwarded_for = request.headers.get("X-Forwarded-For")
    ip = forwarded_for.split(",")[0].strip() if forwarded_for else request.client.host

    db.add(Visit(ip=ip))
    db.commit()

    count = db.query(Visit).count()
    return {"count": count}


@app.get("/api/count", response_model=CountResponse, summary="查询总访问次数")
def get_count(db: Session = Depends(get_db)):
    """返回 visits 表的总行数，即历史总访问次数。"""
    count = db.query(Visit).count()
    return {"count": count}


@app.get("/api/health", response_model=HealthResponse, summary="健康检查")
def health():
    """服务存活探针，始终返回 {"status": "ok"}。"""
    return {"status": "ok"}
