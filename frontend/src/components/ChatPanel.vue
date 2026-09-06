<script setup>
import { ref, watch, nextTick } from "vue";

const props = defineProps({
  messages: { type: Array, required: true },
  streaming: { type: Boolean, default: false },
  canStop: { type: Boolean, default: false },   // 回复流式中 → 发送按钮变「停止」
});
const emit = defineEmits(["send", "stop"]);

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

function send() {
  const text = draft.value.trim();
  if (!text) return;
  emit("send", text);
  draft.value = "";
  nearBottom.value = true;
  if (ta.value) ta.value.style.height = "auto";
}
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
    <div v-if="streaming" class="status-line">小澄正在认真想…</div>
    <div class="inputbar">
      <!-- 输入永远可用：流式回复期间也能先把下一句打好 -->
      <textarea ref="ta" rows="1" v-model="draft" placeholder="和小澄说点什么…（Enter 发送，Shift+Enter 换行）"
                @keydown.enter.exact.prevent="send"
                @input="autoGrow"></textarea>
      <button v-if="asrSupported" class="mic" :class="{ rec: recognizing }"
              :title="recognizing ? '正在聆听，再说一遍结束' : '语音输入'"
              @click="toggleMic">{{ recognizing ? "●" : "🎤" }}</button>
      <button v-if="canStop" class="stop" title="停止生成"
              @click="emit('stop')">■ 停止</button>
      <button v-else class="primary" @click="send"
              :disabled="!draft.trim()">发送</button>
    </div>
  </section>
</template>
