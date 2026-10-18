<script setup>
import { computed } from 'vue'

/**
 * 单条告警。检查页的结果列表与报告详情共用这一个组件——两处的 HTML
 * 结构在旧版里是同一段模板字符串（app.js 的 renderAlert），拆成组件后
 * 不会出现「改了检查页忘了改报告详情」的分叉。
 *
 * 渲染规则与旧版逐字节对齐，尤其是：
 * - 三个段落（异常状态 / 疑似抢注 / 待审核）只在对应数组非空时才出现；
 * - 「疑似抢注 (1)」里的空格与半角括号是 E2E 的文本断言目标；
 * - 抢注与待审核两段都只取前 5 条，超出显示「还有 N 条...」。
 */
const props = defineProps({
  alert: { type: Object, required: true },
})

const alertClass = computed(() =>
  props.alert.issues && props.alert.issues.includes('状态异常') ? 'danger' : 'warning',
)

const abnormalStatuses = computed(() => props.alert.abnormalStatuses || [])
const squattingCases = computed(() => props.alert.squattingCases || [])
const needsReviewCases = computed(() => props.alert.needsReviewCases || [])

const squattingPreview = computed(() => squattingCases.value.slice(0, 5))
const squattingRest = computed(() => Math.max(0, squattingCases.value.length - 5))
const reviewPreview = computed(() => needsReviewCases.value.slice(0, 5))
const reviewRest = computed(() => Math.max(0, needsReviewCases.value.length - 5))

function statusText(item) {
  return item.office ? `${item.status} (${item.office})` : item.status
}

function applicantText(item) {
  return `申请人: ${item.applicant || '未知'}`
}
</script>

<template>
  <div class="alert-item" :class="alertClass">
    <div class="alert-header">
      <span class="alert-brand">{{ alert.brandName }}</span>
      <div class="alert-badges">
        <span
          v-for="issue in alert.issues || []"
          :key="issue"
          class="badge"
          :class="issue === '状态异常' ? 'danger' : 'warning'"
          >{{ issue }}</span
        >
      </div>
    </div>

    <div class="alert-section" v-if="abnormalStatuses.length > 0">
      <div class="alert-section-title">异常状态</div>
      <div class="alert-cases">
        <div class="alert-case high" v-for="item in abnormalStatuses" :key="item.registrationNumber || item.status">
          <div class="alert-case-info">
            <div class="alert-case-brand">注册号 {{ item.registrationNumber || 'N/A' }}</div>
            <div class="alert-case-applicant">{{ statusText(item) }}</div>
          </div>
          <span class="risk-badge high">{{ item.status }}</span>
        </div>
      </div>
    </div>

    <div class="alert-section" v-if="squattingCases.length > 0">
      <div class="alert-section-title">疑似抢注 ({{ squattingCases.length }})</div>
      <div class="alert-cases">
        <div class="alert-case" :class="item.riskLevel" v-for="item in squattingPreview" :key="item.brandName">
          <div class="alert-case-info">
            <div class="alert-case-brand">{{ item.brandName }}</div>
            <div class="alert-case-applicant">{{ applicantText(item) }}</div>
          </div>
          <span class="risk-badge" :class="item.riskLevel">{{ item.riskLevel }}</span>
        </div>
        <div style="font-size: 12px; color: var(--text-tertiary); padding: 4px 12px" v-if="squattingRest > 0">
          还有 {{ squattingRest }} 条...
        </div>
      </div>
    </div>

    <div class="alert-section" v-if="needsReviewCases.length > 0">
      <div class="alert-section-title">待审核 ({{ needsReviewCases.length }})</div>
      <div class="alert-cases">
        <div class="alert-case" :class="item.riskLevel" v-for="item in reviewPreview" :key="item.brandName">
          <div class="alert-case-info">
            <div class="alert-case-brand">{{ item.brandName }}</div>
            <div class="alert-case-applicant">{{ applicantText(item) }}</div>
            <div class="alert-case-reason">{{ item.reason || '' }}</div>
          </div>
          <span class="risk-badge" :class="item.riskLevel">{{ item.riskLevel }}</span>
        </div>
        <div style="font-size: 12px; color: var(--text-tertiary); padding: 4px 12px" v-if="reviewRest > 0">
          还有 {{ reviewRest }} 条...
        </div>
      </div>
    </div>
  </div>
</template>
