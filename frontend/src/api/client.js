/* API 客户端：普通请求 + SSE 流式解析。
 * 开发模式走 Vite 代理（/api → 8001），生产模式同源托管，无需关心后端地址。
 * 所有流式请求支持 AbortController 中断（停止生成）。 */

export async function streamSSE(response, onEvent) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buf.indexOf("\n\n")) >= 0) {
      const block = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      let event = "message";
      let data = "";
      for (const line of block.split("\n")) {
        if (line.startsWith("event: ")) event = line.slice(7).trim();
        else if (line.startsWith("data: ")) data += line.slice(6);
      }
      if (data) {
        try { onEvent(event, JSON.parse(data)); } catch { /* 忽略坏帧 */ }
      }
    }
  }
}

const json = async (r) => {
  if (!r.ok) throw new Error(`${r.status}`);
  return r.json();
};

export const api = {
  createSession: () => fetch("/api/session", { method: "POST" }).then(json),

  greeting: (sessionId, handlers, signal) =>
    fetch(`/api/greeting?session_id=${sessionId}`, { signal }).then((r) =>
      streamSSE(r, handlers)),

  chat: (sessionId, message, handlers, signal) =>
    fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, message }),
      signal,
    }).then((r) => streamSSE(r, handlers)),

  memory: () => fetch("/api/memory").then(json),
  profile: () => fetch("/api/profile").then(json),
  emotions: () => fetch("/api/emotions").then(json),
  diary: () => fetch("/api/diary").then(json),
  messages: (sessionId) => fetch(`/api/messages?session_id=${sessionId}`).then(json),
  forget: (id) => fetch(`/api/memory/${id}`, { method: "DELETE" }).then(json),
  editMemory: (id, content) => fetch(`/api/memory/${id}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  }).then(json),
  review: (sessionId) =>
    fetch(`/api/review?session_id=${sessionId}`, { method: "POST" }).then(json),
};
