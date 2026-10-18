<script setup lang="ts">
/**
 * 判定徽章：把后端的 verdict 码渲染成中文 + 色调
 * verdict 语义（与 credit_check.py 对齐）：
 *   ok       在目标站查到且登记状态正常
 *   alert    查到但状态异常（注销/吊销/清算等）
 *   notfound 名称查不到——多为名称写法与登记全称不一致
 *   unknown  被风控拦住/解析失败/超时，无法判定
 */
import { computed } from 'vue'
import { VERDICT_LABEL, VERDICT_TONE } from '@/api/client'

const props = defineProps<{ verdict?: string | null; plain?: boolean }>()

const tone = computed(() => VERDICT_TONE[props.verdict ?? 'unknown'] ?? 'muted')
const label = computed(() => VERDICT_LABEL[props.verdict ?? 'unknown'] ?? props.verdict ?? '未知')
</script>

<template>
  <span v-if="plain" class="badge-plain" :class="tone">{{ label }}</span>
  <span v-else class="badge" :class="tone"><i class="bdot" />{{ label }}</span>
</template>

<style scoped>
.badge-plain { font-weight: 500; }
.badge-plain.ok { color: var(--c-ok); }
.badge-plain.alert { color: var(--c-alert); }
.badge-plain.warn { color: var(--c-warn); }
.badge-plain.muted { color: var(--c-muted); }
</style>
