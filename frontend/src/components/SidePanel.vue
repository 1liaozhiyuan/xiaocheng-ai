<script setup>
import { ref } from "vue";
import ProfileTab from "./ProfileTab.vue";
import MemoryTab from "./MemoryTab.vue";
import DiaryTab from "./DiaryTab.vue";

defineProps({
  memory: { type: Object, required: true },
  profile: { type: Object, required: true },
  emotion: { type: Object, required: true },
  diary: { type: Object, required: true },
  followupFlag: { type: Boolean, default: false },
  open: { type: Boolean, default: false },   // 移动端抽屉展开态
});
const emit = defineEmits(["forget", "edit"]);
const tab = ref("profile");
const tabs = [
  { id: "profile", label: "画像" },
  { id: "active", label: "当前记忆" },
  { id: "past", label: "过往" },
  { id: "diary", label: "日记" },
  { id: "archived", label: "归档" },
];
</script>

<template>
  <aside class="panel" :class="{ open }">
    <div class="tabs">
      <button v-for="t in tabs" :key="t.id" :class="{ on: tab === t.id }"
              @click="tab = t.id">{{ t.label }}</button>
    </div>
    <div class="panel-body">
      <ProfileTab v-if="tab === 'profile'" :profile="profile" :emotion="emotion"
                  :followup-flag="followupFlag" />
      <MemoryTab v-else-if="tab === 'active'" :items="memory.active" forgettable
                 @forget="emit('forget', $event)" @edit="emit('edit', $event)" />
      <MemoryTab v-else-if="tab === 'past'" :items="memory.past" tag-field="date" />
      <DiaryTab v-else-if="tab === 'diary'" :entries="diary.entries" />
      <MemoryTab v-else :items="memory.archived" tag-label="已淡忘" />
    </div>
  </aside>
</template>
