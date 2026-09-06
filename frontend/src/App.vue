<script setup>
import { ref, reactive, computed, onMounted } from "vue";
import { api } from "./api/client.js";
import ChatPanel from "./components/ChatPanel.vue";
import SidePanel from "./components/SidePanel.vue";

const messages = ref([]);   // {role, text, meta?, typing?, time?}，divider 为分隔线
const streaming = ref(false);
const napping = ref(false);
const ready = ref(false);   // 初始加载完成前显示骨架屏
const memory = reactive({ active: [], past: [], archived: [] });
const profile = reactive({});
const emotion = reactive({ snapshots: [], trend: null });
const usage = reactive({ summary: [] });
const diary = reactive({ entries: [] });
const followupFlag = ref(false);
const panelOpen = ref(false);
let sessionId = null;
let greetingAbort = null;   // 开场流：用户先说话时立即中断，把话头交还给用户
let chatAbort = null;       // 回复流：停止按钮用
const canStop = computed(() => streaming.value && !!chatAbort);
const daysKnown = computed(() => {
  if (!profile.value?.["初次见面"] && !profile["初次见面"]) return null;
  const d = new Date(profile["初次见面"]);
  if (isNaN(d)) return null;
  return Math.floor((Date.now() - d.getTime()) / 86400000);
});

const nowHM = () =>
  new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });

// 通知自动淡出：最多同时 3 条，4 秒后消失，不再永久堆积遮挡界面
const notices = reactive([]);
function notice(text) {
  const item = reactive({ text, id: Date.now() + Math.random() });
  notices.push(item);
  if (notices.length > 3) notices.shift();
  setTimeout(() => {
    const i = notices.indexOf(item);
    if (i >= 0) notices.splice(i, 1);
  }, 4000);
}

async function loadPanels() {
  const [m, p, e, d, u] = await Promise.all([api.memory(), api.profile(), api.emotions(), api.diary(), api.usage()]);
  Object.assign(memory, m);
  Object.keys(profile).forEach((k) => delete profile[k]);
  Object.assign(profile, p.profile);
  followupFlag.value = p.followup_flag;
  emotion.snapshots = e.snapshots;
  emotion.trend = e.trend;
  diary.entries = d.entries;
  usage.summary = u.summary;
}

async function newSession() {
  sessionId = (await api.createSession()).session_id;
  localStorage.setItem("session_id", sessionId);
  // 开场白不阻塞初始化：异步流式填入，期间用户随时可以先开口
  const msg = reactive({ role: "assistant", text: "", typing: true, time: nowHM() });
  messages.value.push(msg);
  greetingAbort = new AbortController();
  api.greeting(sessionId, (event, data) => {
    if (event === "delta") { msg.text += data.text; }
  }, greetingAbort.signal)
    .catch(() => { /* 被中断或失败：开场是可选的 */ })
    .finally(() => {
      msg.typing = false;
      greetingAbort = null;
      if (!msg.text) {
        const i = messages.value.indexOf(msg);
        if (i >= 0) messages.value.splice(i, 1);
      }
    });
}

async function restoreSession() {
  const saved = localStorage.getItem("session_id");
  if (!saved) return false;
  let history;
  try { history = (await api.messages(saved)).messages; }
  catch { localStorage.removeItem("session_id"); return false; }
  if (!history.length) { localStorage.removeItem("session_id"); return false; }
  sessionId = saved;
  messages.value.push({ role: "divider", text: "以上是之前的对话" });
  for (const m of history.slice(-50)) {
    messages.value.push({ role: m.role, text: m.text, time: m.time });
  }
  return true;
}

async function send(text) {
  if (!text || streaming.value) return;
  // 用户在开场白打字时先开口：中断开场流，把半截开场保留在原地
  if (greetingAbort) {
    greetingAbort.abort();
    greetingAbort = null;
    messages.value.forEach((m) => { m.typing = false; });
  }
  messages.value.push({ role: "user", text, time: nowHM() });
  const msg = reactive({ role: "assistant", text: "", typing: true, meta: "",
                         time: nowHM() });
  messages.value.push(msg);
  streaming.value = true;
  chatAbort = new AbortController();

  try {
    await api.chat(sessionId, text, (event, data) => {
      if (event === "delta") { msg.text += data.text;  }
      else if (event === "meta") {
        if (data.severity === "high") msg.meta = "⚠️ 守护模式";
        else if (data.severity === "low") msg.meta = "💛 认真模式";
      } else if (event === "memory") {
        if (data.content) notice(`💭 记住了：${data.content}`);
        loadPanels();
      } else if (event === "error") {
        msg.text += `\n[出错了：${data.message}]`;
      }
    }, chatAbort.signal);
  } catch (e) {
    if (e.name === "AbortError") {
      notice("已停止生成");
    } else {
      msg.text += "\n[连接失败，请确认服务已启动]";
    }
  } finally {
    msg.typing = false;
    streaming.value = false;
    chatAbort = null;
    // 一个字都没生成就停止/失败时，移除空气泡（避免占位的空白消息）
    if (!msg.text) {
      const i = messages.value.indexOf(msg);
      if (i >= 0) messages.value.splice(i, 1);
    }
  }
}

function stopStream() {
  chatAbort?.abort();
}

async function editMemory(m) {
  try {
    await api.editMemory(m.id, m.content);
    await loadPanels();
    notice("已更新这条记忆");
  } catch { notice("更新失败，请稍后再试"); }
}

async function forget(m) {
  await api.forget(m.id);
  await loadPanels();
  notice("已忘记这条记忆");
}

async function nap() {
  napping.value = true;
  notice("🌙 正在整理今天的对话…");
  try {
    const r = await api.review(sessionId);
    if (r.review && r.review.summary) {
      notice(`🌙 整理完成：补记 ${r.review.saved} 条、清理重复 ${r.review.deduped} 条`
        + (r.review.emotion ? `、心情记录「${r.review.emotion.label}」` : ""));
      await loadPanels();
    } else {
      notice(r.skipped || "聊得太短，先多聊几句吧");
    }
  } finally {
    napping.value = false;
  }
}

onMounted(async () => {
  // 界面先出现（不等开场白）：骨架屏只闪一瞬，开场白流式往里填
  ready.value = true;
  try {
    const restored = await restoreSession();
    if (!restored) await newSession();
    await loadPanels();
  } catch {
    notice("服务连接失败：请确认 python server/app.py 正在运行");
  }
});
</script>

<template>
  <div v-if="!ready" class="boot">
    <div class="boot-icon">💗</div>
    <div class="boot-text">小澄正在醒来…</div>
  </div>
  <template v-else>
    <header>
      <h1>小澄</h1><span class="subtitle">{{ daysKnown === null ? "陪你聊天的朋友" : `认识第 ${daysKnown + 1} 天 · 陪你聊天的朋友` }}</span>
      <div class="spacer"></div>
      <button @click="nap" :disabled="napping">
        {{ napping ? "整理中…" : "🌙 小憩一下" }}
      </button>
      <button class="panel-toggle" @click="panelOpen = !panelOpen">记忆面板</button>
    </header>
    <main>
      <ChatPanel
        :messages="messages" :streaming="streaming" :can-stop="canStop"
        @send="send" @stop="stopStream" />
      <SidePanel
        :memory="memory" :profile="profile" :emotion="emotion"
        :followup-flag="followupFlag" :open="panelOpen" :diary="diary"
        :usage="usage" @forget="forget" @edit="editMemory" />
    </main>
    <div class="notice-stack">
      <transition-group name="notice-fade">
        <div v-for="n in notices" :key="n.id" class="notice">{{ n.text }}</div>
      </transition-group>
    </div>
  </template>
</template>
