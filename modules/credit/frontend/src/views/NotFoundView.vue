<script setup lang="ts">
import EmptyState from '@/components/EmptyState.vue'
import { useRouter } from 'vue-router'

const router = useRouter()
</script>

<template>
  <div class="page-head">
    <h1>页面不存在</h1>
    <p>地址可能输错了，或者该作业已被清理。</p>
  </div>
  <div class="card">
    <EmptyState
      icon="🧭"
      title="没有找到这个页面"
      desc="返回总览看看最近的巡检作业，或重新提交一次巡检。"
      action-text="回到总览"
      @action="router.push('/overview')"
    />
  </div>
</template>
