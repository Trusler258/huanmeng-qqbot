"""幻梦后台控制面板 · 独立 API 服务

设计要点（见 docs/panel_plan.md）：
  - 与 bot.service **完全平级的独立进程**，端口 59300，只绑 127.0.0.1
  - 不 import bot.py / 不建 NapCat WS 连接 —— 只读它的文件和 systemd 状态
  - 默认拒绝：除登录接口外，所有 /api/* 都要求有效 token
  - 四层防御：网络层(127.0.0.1) → 隧道层(CF Tunnel)
              → 应用层(JWT + 递增封禁) → 操作层(审计 + 二次确认 + 自愈回滚)
  - v2.3.0 起：可写面扩展到全部配置/提示词/数据/运行控制，
    并由 selfheal 看门狗保证"改崩了能自己退回来"
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from panel import config, selfheal
from panel.routers import (
    assistant,
    auth,
    commands,
    config_editor,
    database,
    economy,
    earthquake,
    features,
    games,
    groups,
    licenses,
    logs,
    media,
    memory,
    messages,
    overview,
    plugins,
    prompts,
    social,
    system,
)

log = logging.getLogger("panel")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动/关闭钩子"""
    cfg = config.load()
    log.info("幻梦面板 API 启动 → http://%s:%d", cfg.bind_host, cfg.port)
    if not cfg.secret_configured:
        log.warning(
            "⚠️ 面板未初始化管理员密码，/api/auth/login 将拒绝所有登录。"
            "请执行: python -m panel.cli init"
        )
    # 崩溃自愈看门狗：只有面板独立进程才能做这件事
    await selfheal.get_state().start()
    yield
    await selfheal.get_state().stop()
    log.info("幻梦面板 API 已停止")


app = FastAPI(
    title="幻梦 Bot 控制面板 API",
    version=config.PANEL_API_VERSION,
    description="幻梦 QQ Bot 后台管理接口。所有写操作均记审计日志，"
                "并可经操作栈一键回滚。",
    lifespan=lifespan,
    docs_url="/api/docs",          # 仅本机可达，公网需过隧道 + Token
    redoc_url=None,
    openapi_url="/api/openapi.json",
)

# 前端 dev server 跨域（生产由 nginx 同源托管，不依赖 CORS）
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def _security_headers(request: Request, call_next):
    """统一安全响应头"""
    resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception):
    """统一错误响应——不把内部堆栈吐给前端"""
    log.exception("未处理异常 %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"ok": False, "error": "服务器内部错误", "detail": str(exc)[:200]},
    )


# ── 路由挂载 ──────────────────────────────────────────────
# 注意：auth 之外的路由内部都统一挂了 require_user 依赖，
# 见 panel/routers/__init__.py 的说明。漏挂的接口不会放行（默认拒绝）。
for r in (
    auth.router,
    overview.router,
    messages.router,
    social.router,
    memory.router,
    features.router,
    commands.router,
    logs.router,
    economy.router,
    games.router,
    earthquake.router,
    system.router,
    plugins.router,
    # v2.3.0 新增
    groups.router,
    media.router,
    database.router,
    config_editor.router,
    prompts.router,
    # v2.3.1 AI 助手
    assistant.router,
    # v2.3.6 许可码
    licenses.router,
):
    app.include_router(r, prefix="/api")


@app.get("/api/health", tags=["系统"])
async def health():
    """健康检查——无需认证，供 nginx/uptime 探活"""
    return {"ok": True, "service": "huanmeng-panel", "version": config.PANEL_API_VERSION}