<script setup>
import { ref, watch, nextTick } from "vue";

const props = defineProps({
  messages: { type: Array, required: true },
  streaming: { type: Boolean, default: false },
  canStop: { type: Boolean, default: false },   // 回复流式中 → 发送按钮变「停止」
  listening: { type: Boolean, default: false }, // 有未回应消息：「正在听」，无时限
  allowDuringStream: { type: Boolean, default: false },
});
const emit = defineEmits(["send", "stop", "trigger"]);

const draft = ref("");
const box = ref(null);
const ta = ref(null);
const nearBottom = ref(true);   // 智能滚动：用户翻历史时不打扰

function onScroll() {
  const el = box.value;
  if (!el) return;
  nearBottom.value = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
}

watch(() => props.messages.map(m => m.text).join("|"), async () => {
  if (!nearBottom.value) return;   // 用户在翻历史，不强行拉底
  await nextTick();
  if (box.value) box.value.scrollTop = box.value.scrollHeight;
});

function autoGrow() {
  const el = ta.value;
  if (!el) return;
  el.style.height = "auto";
  el.style.height = Math.min(el.scrollHeight, 140) + "px";
}

// ── 语音输入（ASR）：Chrome/Edge 的 Web Speech API，免费零依赖 ──
const asrSupported = !!(window.SpeechRecognition || window.webkitSpeechRecognition);
const recognizing = ref(false);
let recog = null;
function toggleMic() {
  if (recognizing.value) { recog.stop(); return; }
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  recog = new SR();
  recog.lang = "zh-CN";
  recog.interimResults = true;
  recognizing.value = true;
  recog.onresult = (e) => {
    let text = "";
    for (const r of e.results) text += r[0].transcript;
    draft.value = text;
  };
  recog.onend = () => { recognizing.value = false; };
  recog.onerror = () => { recognizing.value = false; };
  recog.start();
}

// ── 击键活跃感知：持续输入 = 还在说；停顿且已清空 = 说完了 ──
const typingActive = ref(false);      // 正在击键（含反悔窗口）
let idleTimer = null;
let armTimer = null;
const IDLE_MS = 2500;                 // 停下击键多久算「说完了」
const ARM_MS = 1500;                  // 自动交棒前的反悔窗口

function onTyping() {
  typingActive.value = true;
  clearTimeout(idleTimer);
  clearTimeout(armTimer);
  // 停止击键 IDLE_MS 后检查：缓冲有消息 且 输入框已清空 → 进入反悔窗口
  idleTimer = setTimeout(() => {
    typingActive.value = false;
    if (props.listening && !props.streaming && !draft.value.trim()) {
      armTimer = setTimeout(() => emit("trigger"), ARM_MS);
    }
  }, IDLE_MS);
}

function cancelArm() {  // 反悔：反悔窗口内任何击键取消自动交棒
  clearTimeout(armTimer);
  armTimer = null;
}

function send() {
  const text = draft.value.trim();
  if (!text) {
    // 空消息 Enter/点击 = 交棒：让小澄回应已说完的内容（回应时机由用户掌控）
    if (props.listening && !props.streaming) emit("trigger");
    return;
  }
  if (props.streaming && !props.allowDuringStream) return;
  emit("send", text);
  draft.value = "";
  nearBottom.value = true;
  if (ta.value) ta.value.style.height = "auto";
}
import { onBeforeUnmount } from "vue";
onBeforeUnmount(() => {
  clearTimeout(idleTimer);
  clearTimeout(armTimer);
  if (recog) { try { recog.stop(); } catch {} }
});
</script>

<template>
  <section class="chat">
    <div class="messages" ref="box" @scroll="onScroll">
      <template v-for="(m, i) in messages" :key="i">
        <div v-if="m.role === 'divider'" class="divider"><span>{{ m.text }}</span></div>
        <div v-else :class="['msg', m.role]">
          <div class="bubble">
            {{ m.text }}<span v-if="m.typing" class="cursor">▍</span>
          </div>
          <div class="msg-foot">
            <span v-if="m.meta" class="meta">{{ m.meta }}</span>
            <span v-if="m.time" class="time">{{ m.time }}</span>
          </div>
        </div>
      </template>
    </div>
    <div v-if="listening && !streaming && typingActive" class="status-line">👂 小澄正在听你说…</div>
    <div v-else-if="listening && !streaming && !typingActive" class="status-line">🤔 你停下来了…要说完了吗？（继续输入可取消，按 Enter 立即回应）</div>
    <div v-else-if="streaming" class="status-line">小澄正在认真想…</div>
    <div class="inputbar">
      <!-- 输入永远可用：流式回复期间也能先把下一句打好 -->
      <textarea ref="ta" rows="1" v-model="draft" placeholder="和小澄说点什么…（Enter 发送，Shift+Enter 换行；说完后停顿片刻小澄会自动回应，或清空按 Enter 立即交棒）"
                @keydown.enter.exact.prevent="send"
                @keydown="onTyping"
                @input="onTyping(); autoGrow()"></textarea>
      <button v-if="asrSupported" class="mic" :class="{ rec: recognizing }"
              :title="recognizing ? '正在聆听，再说一遍结束' : '语音输入'"
              @click="toggleMic">{{ recognizing ? "●" : "🎤" }}</button>
      <button v-if="canStop" class="stop" title="停止生成"
              @click="emit('stop')">■ 停止</button>
      <button v-else class="primary" @click="send"
              :disabled="!draft.trim() && !(listening && !streaming)">
        {{ !draft.trim() && listening && !streaming ? (typingActive ? "💬 该你说了" : "⏳ 即将回应…") : "发送" }}
      </button>
    </div>
  </section>
</template>
