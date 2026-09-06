<script setup>
import { ref } from "vue";

const props = defineProps({
  items: { type: Array, required: true },
  forgettable: { type: Boolean, default: false },
  tagLabel: { type: String, default: "" },
  tagField: { type: String, default: "" },   // 过往视图：用 created_at 前五位做日期标签
});
const emit = defineEmits(["forget", "edit"]);

const confirmingId = ref(null);   // 删除二次确认：先点 ✕ 变「确认?」，再点才执行
let confirmTimer = null;
const editingId = ref(null);      // 内联编辑态：✎ 展开输入框，✓ 保存
const editText = ref("");

function tagOf(m) {
  if (props.tagLabel) return props.tagLabel;
  if (props.tagField && m.created_at) return m.created_at.slice(5, 10);
  return `${m.category} · ${Number(m.importance ?? 0).toFixed(1)}`;
}

function onDelClick(m) {
  if (confirmingId.value === m.id) {
    confirmingId.value = null;
    clearTimeout(confirmTimer);
    emit("forget", m);
  } else {
    confirmingId.value = m.id;
    clearTimeout(confirmTimer);
    confirmTimer = setTimeout(() => (confirmingId.value = null), 3000);
  }
}

function startEdit(m) {
  editingId.value = m.id;
  editText.value = m.content;
}
function saveEdit(m) {
  const content = editText.value.trim();
  editingId.value = null;
  if (content && content !== m.content) emit("edit", { id: m.id, content });
}
</script>

<template>
  <div v-if="!items.length" class="empty">暂无记录</div>
  <div v-for="m in items" :key="m.id" class="mem-item">
    <span class="tag">{{ tagOf(m) }}</span>
    <template v-if="editingId === m.id">
      <input v-model="editText" class="edit-input" autofocus
             @keydown.enter="saveEdit(m)" @keydown.esc="editingId = null">
      <button class="del" title="保存" @click="saveEdit(m)">✓</button>
    </template>
    <template v-else>
      <span class="text">{{ m.content }}</span>
      <button v-if="forgettable" class="del" title="修改这条记忆"
              @click="startEdit(m)">✎</button>
      <button v-if="forgettable" class="del" :class="{ warn: confirmingId === m.id }"
              :title="confirmingId === m.id ? '再点一次确认删除' : '忘记这条'"
              @click="onDelClick(m)">
        {{ confirmingId === m.id ? "确认?" : "✕" }}
      </button>
    </template>
  </div>
</template>
