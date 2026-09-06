<script setup>
defineProps({
  profile: { type: Object, required: true },
  emotion: { type: Object, required: true },
  followupFlag: { type: Boolean, default: false },
});
</script>

<template>
  <div v-if="followupFlag" class="followup">
    ⚠️ 有关注标记：上次会话出现过重度危机信号，小澄开场时会主动关心。
  </div>
  <div v-if="!Object.keys(profile).length && !emotion.snapshots.length" class="empty">
    画像还没有内容，多聊聊吧
  </div>
  <div v-for="(v, k) in profile" :key="k" class="profile-row">
    <div class="k">{{ k }}</div>
    <div class="v">{{ v }}</div>
  </div>
  <template v-if="emotion.snapshots.length">
    <div class="k chart-title">最近心情走势</div>
    <div class="mood-chart">
      <div v-for="(s, i) in emotion.snapshots" :key="i" class="mood-col">
        <div class="mood-bar-wrap">
          <div class="mood-bar" :style="{ height: (s.score / 10) * 100 + '%' }"
               :class="{ low: s.score < 4, mid: s.score >= 4 && s.score < 6 }"></div>
        </div>
        <div class="mood-date">{{ s.date.slice(5) }}</div>
        <div class="mood-label">{{ s.label }}</div>
      </div>
    </div>
    <div v-if="emotion.trend" class="mood-trend">
      近 {{ emotion.snapshots.length }} 次平均 {{ emotion.trend.toFixed(1) }} / 10
    </div>
  </template>
</template>
