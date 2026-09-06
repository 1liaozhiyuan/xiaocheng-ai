<script setup>
defineProps({
  entries: { type: Array, required: true },   // [{date, content}] 按日期倒序
});
</script>

<template>
  <div v-if="!entries.length" class="empty">
    小澄的日记还是空的 —— 聊完天点「🌙 小憩一下」，她就会写下当天的心情
  </div>
  <div v-for="e in entries" :key="e.date" class="diary-entry">
    <div class="diary-date">{{ e.date }}</div>
    <div class="diary-text">{{ e.content }}</div>
  </div>
</template>
