"""FastAPI 应用工厂：纯 API 服务 + 前后端分离支持。

开发模式（前后端分离）：
    后端：python server/app.py            （API 在 8001）
    前端：cd frontend && npm run dev      （Vite 5173，/api 代理到 8001）

生产模式（同源托管）：
    cd frontend && npm run build          （产出 frontend/dist）
    python server/app.py                  （自动检测并托管 dist，单服务运行）
"""
import sys
from pathlib import Path

# 允许 `python server/app.py` 直接启动：把项目根加入模块搜索路径
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import asyncio

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import config
from server.api import router
from server.state import AppState

FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
IDLE_CHECK_INTERVAL = 60          # 闲置扫描周期（秒）
IDLE_REVIEW_AFTER = 30 * 60       # 闲置多久触发「睡前整理」


def create_app(core: AppState | None = None,
               enable_idle_review: bool = True) -> FastAPI:
    app = FastAPI(title="陪伴 Agent")
    app.state.core = core or AppState()

    # 开发模式下前端跑在 Vite 5173，跨域放行
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(router)

    if (FRONTEND_DIST / "index.html").exists():
        # 生产模式：托管构建产物（同源，无需 CORS）
        app.mount("/assets", StaticFiles(directory=str(FRONTEND_DIST / "assets")),
                  name="assets")

        @app.get("/")
        def index():
            return FileResponse(FRONTEND_DIST / "index.html")

        @app.get("/favicon.svg")
        def favicon():
            return FileResponse(FRONTEND_DIST / "favicon.svg")
    else:
        @app.get("/")
        async def index():
            return JSONResponse({
                "message": "前端未构建。开发模式请运行 npm run dev（frontend/ 目录，"
                           "页面在 http://localhost:5173）；或先 npm run build 后重启本服务。",
            })

    @app.on_event("startup")
    async def _start_idle_reviewer():
        if not enable_idle_review:
            return
        app.state.idle_task = asyncio.get_running_loop().create_task(
            _idle_review_loop(app.state.core)
        )

    return app


async def _idle_review_loop(state: AppState) -> None:
    """Web 没有明确的「退出」——闲置半小时的会话自动触发睡前整理。"""
    import time
    while True:
        await asyncio.sleep(IDLE_CHECK_INTERVAL)
        try:
            for session in list(state.sessions.values()):
                idle = time.time() - session.last_active
                if (not session.reviewed and idle > IDLE_REVIEW_AFTER
                        and state.store.count_messages(session.id) >= 4):
                    await state.reviewer.review(session.id)
                    await state.profile_agent.consolidate()
                    session.reviewed = True
        except asyncio.CancelledError:
            return
        except Exception as e:  # 定时器失败只记日志，不影响服务
            print(f"（闲置整理失败：{type(e).__name__}: {e}）")


app = create_app(enable_idle_review="--no-idle" not in sys.argv)

if __name__ == "__main__":
    import uvicorn
    config.ensure_dirs()
    uvicorn.run(app, host="127.0.0.1", port=config.SERVER_PORT)
