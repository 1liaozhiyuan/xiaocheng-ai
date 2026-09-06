"""API 路由：SSE 流式聊天/开场 + 记忆面板 + 手动/闲置复盘。

SSE 事件约定（前端按 event 分轨渲染）：
  delta   {"text": "..."}          回复/开场增量
  meta    {"kind": "...", ...}     回复前元信息（本轮基调等）
  done    {}                        本轮结束
  error   {"message": "..."}       出错（流内报告后关闭）
"""
import asyncio
import json
import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

import config
from server.state import AppState

router = APIRouter(prefix="/api")


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def sse_response(gen) -> StreamingResponse:
    return StreamingResponse(gen, media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


class ChatIn(BaseModel):
    session_id: str
    message: str


def _memory_payload(state: AppState) -> dict:
    return {
        "active": state.store.all_active_memories(),
        "past": state.store.memories_by_status("superseded"),
        "archived": state.store.memories_by_status("archived"),
    }


@router.post("/session")
def create_session(request: Request):
    state: AppState = request.app.state.core
    s = state.new_session()
    first_met = state.store.mark_first_met()
    return {"session_id": s.id, "first_met": first_met}


@router.put("/memory/{memory_id}")
def edit_memory(memory_id: int, body: dict, request: Request):
    """记忆透明化：用户可直接修改 AI 记住的内容（市面陪伴产品标配能力）。"""
    state: AppState = request.app.state.core
    m = state.store.get_memory(memory_id)
    if not m or m["status"] != "active":
        raise HTTPException(404, "记忆不存在或不可编辑")
    content = str(body.get("content", "")).strip()
    if not content:
        raise HTTPException(400, "内容不能为空")
    state.store.update_memory(memory_id, content, m["importance"])
    try:
        state.vector_store.update(memory_id, content,
                                  {"category": m["category"],
                                   "importance": m["importance"]})
    except Exception:
        pass  # 索引更新失败不影响主数据，检索仍以 SQLite 为准
    return {"ok": True, "content": content}


@router.get("/export")
def export_data(request: Request, format: str = "json"):
    """全量数据导出：format=json（备份用）| md（可读版）。"""
    from fastapi.responses import Response
    state: AppState = request.app.state.core
    store = state.store
    memories = store.all_active_memories() + store.memories_by_status("superseded")         + store.memories_by_status("archived")
    profile = store.get_profile()
    diary = store.recent_diary(365)
    emotions = store.recent_emotion_snapshots(365)
    sessions = [{"session_id": s["session_id"], "summary": s["summary"]}
                for s in store.recent_summaries(1000)]
    conversations = []
    for s in sessions:
        msgs = store.session_messages(s["session_id"])
        conversations.append({"session_id": s["session_id"], "messages": msgs})

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    if format == "md":
        lines = [f"# 小澄的记忆档案（导出于 {stamp}）", ""]
        lines += ["## 用户画像", ""]
        lines += [f"- **{k}**: {v}" for k, v in profile.items()] or ["（空）"]
        lines += ["", "## 长期记忆", ""]
        for m in memories:
            status = {"active": "当前", "superseded": "过往", "archived": "已淡忘"}.get(m["status"], m["status"])
            lines.append(f"- [{status}/{m['category']}] {m['content']}")
        lines += ["", "## 小澄的日记", ""]
        for d in diary:
            lines += [f"### {d['date']}", d["content"], ""]
        lines += ["## 情绪记录", ""]
        lines += [f"- {e['date']}：{e['label']}（{e['score']}/10）{e['note'] or ''}" for e in emotions]
        lines += ["", "## 对话记录", ""]
        for conv in conversations:
            lines += [f"### 会话 {conv['session_id']}", ""]
            for m in conv["messages"]:
                who = "我" if m["role"] == "user" else "小澄"
                lines.append(f"**{who}**：{m['content']}")
            lines.append("")
        content = chr(10).join(lines)
        return Response(content, media_type="text/markdown; charset=utf-8",
                        headers={"Content-Disposition":
                                 f"attachment; filename=xiaocheng-archive-{stamp}.md"})
    payload = {"exported_at": stamp, "profile": profile, "memories": memories,
               "diary": diary, "emotions": emotions, "conversations": conversations}
    return Response(json.dumps(payload, ensure_ascii=False, indent=2),
                    media_type="application/json",
                    headers={"Content-Disposition":
                             f"attachment; filename=xiaocheng-backup-{stamp}.json"})


@router.get("/usage")
def usage(request: Request, days: int = 30):
    """Token 用量统计：按用途×模型聚合 + 按日趋势。"""
    state: AppState = request.app.state.core
    return {"summary": state.store.usage_summary(days),
            "daily": state.store.usage_daily(days)}


@router.get("/diary")
def diary(request: Request):
    """小澄的日记：她以自己的视角记录与 ta 的相处（睡前整理时生成）。"""
    state: AppState = request.app.state.core
    return {"entries": state.store.recent_diary(30)}


@router.get("/greeting")
async def greeting(request: Request, session_id: str):
    """主动开场（流式）。一个会话只开场一次，重复请求返回 skipped 事件。"""
    state: AppState = request.app.state.core
    session = state.revive_session(session_id)
    if session is None:
        raise HTTPException(404, "session 不存在")
    if session.greeted or not config.PROACTIVE_GREETING:
        async def _skip():
            yield sse("skipped", {})
            yield sse("done", {})
        return sse_response(_skip())
    session.greeted = True
    state.touch(session)

    async def _gen():
        parts = []
        try:
            async for delta in state.proactive.greeting_stream():
                parts.append(delta)
                yield sse("delta", {"text": delta})
        except Exception as e:
            yield sse("error", {"message": str(e)})
        if not any(p.strip() for p in parts):
            yield sse("skipped", {})
        yield sse("done", {})

    return sse_response(_gen())


@router.post("/chat")
async def chat(body: ChatIn, request: Request):
    state: AppState = request.app.state.core
    session = state.revive_session(body.session_id)
    if session is None:
        raise HTTPException(404, "session 不存在")
    message = body.message.strip()
    if not message:
        raise HTTPException(400, "消息不能为空")
    state.touch(session)
    state.store.add_message(session.id, "user", message)

    async def _gen():
        # 策略判断与记忆检索在 reply 内并行（省首字延迟）；
        # meta 事件（守护/认真模式标注）在流尾补发，仅作 UI 标注不影响内容
        strategy_task = asyncio.get_running_loop().create_task(
            state.strategist.decide(
                message, state.store.recent_messages(session.id, limit=6)))
        reply_parts = []
        try:
            async for delta in state.companion.reply(session.id, message,
                                                     strategy_task=strategy_task):
                reply_parts.append(delta)
                yield sse("delta", {"text": delta})
        except Exception as e:
            yield sse("error", {"message": str(e)})
            return
        reply = "".join(reply_parts).strip()
        if reply:
            state.store.add_message(session.id, "assistant", reply)
            task = state.memory_manager.schedule_turn(
                session.id,
                state.store.recent_messages(session.id, limit=config.EXTRACT_WINDOW),
            )
            # 在本轮 SSE 流内等待抽取完成，推送 💭 事件（前端即时提示并刷新面板）
            try:
                saved = await task
                for m in saved or []:
                    yield sse("memory", {"content": m["content"]})
            except Exception as e:
                yield sse("memory", {"error": f"抽取失败：{type(e).__name__}"})
        try:
            strategy = await strategy_task
            yield sse("meta", {"emotion": strategy.emotion,
                               "severity": strategy.severity})
        except Exception:
            pass
        yield sse("done", {})

    return sse_response(_gen())


@router.get("/memory")
def memory(request: Request):
    state: AppState = request.app.state.core
    return _memory_payload(state)


@router.get("/profile")
def profile(request: Request):
    state: AppState = request.app.state.core
    p = state.store.get_profile()
    return {"profile": p, "followup_flag": "关注标记" in p}


@router.get("/emotions")
def emotions(request: Request):
    """最近的情绪快照（时间正序），供前端画心情走势。"""
    state: AppState = request.app.state.core
    return {"snapshots": state.store.recent_emotion_snapshots(14),
            "trend": state.store.emotion_trend()}


@router.get("/messages")
def messages(request: Request, session_id: str):
    """会话消息重放（前端刷新后恢复对话流），带时间戳。"""
    state: AppState = request.app.state.core
    rows = state.store.recent_messages(session_id, limit=100)
    return {"messages": [{"role": r["role"], "text": r["content"],
                          "time": r["created_at"][11:16] if r.get("created_at") else ""}
                         for r in rows]}


@router.delete("/memory/{memory_id}")
def forget(memory_id: int, request: Request):
    state: AppState = request.app.state.core
    m = state.store.get_memory(memory_id)
    if not m or m["status"] != "active":
        raise HTTPException(404, "记忆不存在或不可删除")
    state.store.archive_memory(memory_id)
    state.vector_store.delete(memory_id)
    return {"ok": True}


@router.post("/review")
async def review(request: Request, session_id: str = ""):
    """「睡前整理」：复盘 + 画像巩固（页面按钮触发，或闲置定时器调用）。"""
    state: AppState = request.app.state.core
    target = session_id
    if not target:
        # 无指定时挑最久未复盘且有足够消息的活跃会话
        candidates = [s for s in state.sessions.values()
                      if not s.reviewed
                      and state.store.count_messages(s.id) >= 4]
        if not candidates:
            return {"ok": True, "skipped": "没有待整理的会话"}
        target = max(candidates, key=lambda s: s.last_active).id

    result = await state.reviewer.review(target)
    profile = await state.profile_agent.consolidate()
    if target in state.sessions:
        state.sessions[target].reviewed = True
    return {"ok": True, "review": result, "profile": profile}


@router.get("/session/new_id")
def new_id():
    return {"session_id": f"s_{uuid.uuid4().hex[:8]}"}
