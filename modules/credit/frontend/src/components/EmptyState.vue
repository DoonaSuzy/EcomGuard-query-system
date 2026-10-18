<script setup lang="ts">
/** 空态占位：给一句「为什么空」和「下一步做什么」，比空白区好用 */
defineProps<{
  icon?: string
  title: string
  desc?: string
  actionText?: string
}>()
const emit = defineEmits<{ action: [] }>()
</script>

<template>
  <div class="empty">
    <div class="ico" aria-hidden="true">{{ icon || '📭' }}</div>
    <h3>{{ title }}</h3>
    <p v-if="desc" class="desc">{{ desc }}</p>
    <button v-if="actionText" class="btn primary" @click="emit('action')">{{ actionText }}</button>
  </div>
</template>

<style scoped>
.empty h3 { margin-bottom: 4px; }
.empty .desc { margin: 0 0 14px; }
</style>
