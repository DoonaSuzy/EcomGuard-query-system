<script setup lang="ts">
/**
 * 提交巡检页
 * =========
 * 关键交互取舍：
 *  - 企业名一行一个；粘贴 Excel/记事本整块文本直接可用（split 换行/逗号/分号都吃）。
 *  - 重复名称就地去重并告知，而不是让后端 422——后端去重是静默的，用户看不到少了哪条。
 *  - 前端先按「200 家上限」预校验：后端 max_length=200，超了只会得到一句 422 英文
 *    （FastAPI 的 ListTooLong），不如这里给中文提示。
 *  - 提交成功后跳详情页，让用户立刻看到进度；草稿保留，避免误提交后要重填。
 *  - KeepAlive 缓存本页（见 App.vue），切走再回来输入不丢。
 */
import { computed, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { useRouter } from 'vue-router'
import { client, extractErrorMessage } from '@/api/client'
import { useDraftStore, useJobsStore } from '@/stores/jobs'
import { useToastStore } from '@/stores/toast'

const router = useRouter()
const draft = useDraftStore()
const jobs = useJobsStore()
const toast = useToastStore()
const { namesText, webhook, mailTo, alwaysNotify, minDelay, maxDelay } = storeToRefs(draft)

const MAX_NAMES = 200
const submitting = ref(false)
const submitError = ref('')

/**
 * 用户是否已经动过表单。
 *
 * formProblem 是 computed，空名单时立刻就有值；如果直接 `v-if="formProblem"`
 * 渲染，页面刚打开就顶着一条红字「请至少填写一个企业全称」——用户还没动手就先被
 * 骂一句。所以只在「输入过内容」或「点过提交」之后才把错误显出来，
 * 按钮文案（「请先填写企业名单」）本身已经承担了引导作用。
 */
const touched = ref(false)

watch([namesText, mailTo, webhook, minDelay, maxDelay], () => {
  touched.value = true
})

/** 把粘贴进来的整块文本切成名称列表 */
const parsed = computed(() => {
  const out: string[] = []
  const seen = new Set<string>()
  let dupCount = 0
  let commentCount = 0
  for (const raw of namesText.value.split(/[\r\n,;，；]+/)) {
    const n = raw.trim()
    if (!n) continue
    if (n.startsWith('#')) { commentCount++; continue }
    if (seen.has(n)) { dupCount++; continue }
    seen.add(n)
    out.push(n)
  }
  return { out, dupCount, commentCount }
})

const valid = computed(() => parsed.value.out.length > 0 && parsed.value.out.length <= MAX_NAMES)
const overLimit = computed(() => parsed.value.out.length > MAX_NAMES)

/** 与后端 CheckRequest 的校验规则对齐，前端先拦一道 */
const formProblem = computed(() => {
  if (!parsed.value.out.length) return '请至少填写一个企业全称'
  if (overLimit.value) return `一次最多提交 ${MAX_NAMES} 家，当前 ${parsed.value.out.length} 家`
  if (minDelay.value < 0 || maxDelay.value < 0) return '间隔秒数不能为负'
  if (maxDelay.value < minDelay.value) return '最大间隔不能小于最小间隔'
  if (maxDelay.value > 120) return '最大间隔上限 120 秒'
  if (mailTo.value.trim() && !/^[^@\s,，]+@[^@\s,，]+\.[^@\s,，]+([,，][^@\s,，]+@[^@\s,，]+\.[^@\s,，]+)*$/.test(mailTo.value.trim())) {
    return '收件人格式不正确，多个邮箱请用逗号分隔'
  }
  if (webhook.value.trim() && !/^https?:\/\//.test(webhook.value.trim())) {
    return 'Webhook 需要以 http:// 或 https:// 开头'
  }
  return ''
})

async function submit() {
  if (formProblem.value) {
    // 点了提交就必须给反馈，不能再被 touched 吞掉
    touched.value = true
    toast.error(formProblem.value)
    return
  }
  submitting.value = true
  submitError.value = ''
  try {
    const { data, error, response } = await client.POST('/api/jobs', {
      body: {
        names: parsed.value.out,
        webhook: webhook.value.trim() || null,
        mail_to: mailTo.value.trim() || null,
        always_notify: alwaysNotify.value,
        min_delay: Number(minDelay.value),
        max_delay: Number(maxDelay.value),
      },
    })

    if (error) {
      const msg = extractErrorMessage(error, response.status)
      submitError.value = msg
      toast.error(`提交失败：${msg}`)
      return
    }
    if (!data) {
      // 202 但空体：后端契约变了或被中间层截断，别静默跳转
      submitError.value = '服务端返回空响应，请刷新后重试'
      toast.error(submitError.value)
      return
    }
    jobs.prepend(data)
    toast.success(`已提交 ${parsed.value.out.length} 家，作业已排队`)
    router.push(`/jobs/${data.job_id}`)
  } catch (e) {
    // 网络层失败（后端没起 / 断网）是 throw 而不是 { error }，不接就冒到 ErrorBoundary
    const msg = extractErrorMessage(e)
    submitError.value = msg
    toast.error(`提交失败：${msg}`)
  } finally {
    submitting.value = false
  }
}

function fillDemo() {
  if (namesText.value.trim() && !confirm('将追加示例企业到当前输入，确定？')) return
  namesText.value = namesText.value
    ? `${namesText.value}\n石家庄三鹿集团股份有限公司\n福建亲亲股份有限公司`
    : '石家庄三鹿集团股份有限公司\n福建亲亲股份有限公司'
}

function clearNames() {
  namesText.value = ''
  toast.info('已清空企业列表')
}
</script>

<template>
  <div class="page-head">
    <h1>提交巡检</h1>
    <p>一行一个企业全称。建议粘贴工商登记全称，简称可能查不到（判定为「未收录」）。</p>
  </div>

  <div class="grid cols-2 submit-grid">
    <div class="card">
      <div class="card-title"><h2>企业名单</h2><span class="counter" :class="{ over: overLimit }">{{ parsed.out.length }} / {{ MAX_NAMES }}</span></div>

      <textarea
        v-model="namesText"
        placeholder="例如：&#10;石家庄三鹿集团股份有限公司&#10;福建亲亲股份有限公司"
        aria-label="企业全称列表"
        spellcheck="false"
      />

      <div class="meta-line">
        <span v-if="parsed.dupCount" class="tag warn">已去除 {{ parsed.dupCount }} 个重复名称</span>
        <span v-if="parsed.commentCount" class="tag muted">忽略 {{ parsed.commentCount }} 行注释（# 开头）</span>
        <span class="grow" />
        <button class="btn sm" @click="fillDemo">填入示例</button>
        <button class="btn sm" :disabled="!namesText" @click="clearNames">清空</button>
      </div>

      <p class="hint">支持换行、逗号、分号分隔；自动按首尾空白裁剪并去重。<code>#</code> 开头的行视为注释，不会提交。</p>
    </div>

    <div class="card">
      <div class="card-title"><h2>推送与节奏</h2></div>

      <div class="field">
        <label for="mail">告警邮件收件人</label>
        <input id="mail" v-model="mailTo" type="text" placeholder="留空则不发送邮件，多个用逗号分隔" />
        <div class="hint">作业结束且有异常时才发；全正常会用一条「无异常」简报（可关闭）。</div>
      </div>

      <div class="field">
        <label for="hook">Webhook（可选）</label>
        <input id="hook" v-model="webhook" type="text" placeholder="企业微信 / 钉钉 / 飞书机器人地址" />
      </div>

      <div class="field">
        <label>抓取间隔（秒）</label>
        <div class="row">
          <div style="flex: 1">
            <input v-model.number="minDelay" type="number" min="0" max="60" step="0.5" aria-label="最小间隔秒数" />
          </div>
          <span class="tilde">~</span>
          <div style="flex: 1">
            <input v-model.number="maxDelay" type="number" min="0" max="120" step="0.5" aria-label="最大间隔秒数" />
          </div>
        </div>
        <div class="hint">两家之间的随机等待。目标站有瑞数风控，间隔太短容易触发验证码。</div>
      </div>

      <label class="check">
        <input v-model="alwaysNotify" type="checkbox" />
        <span>全部正常也推送（用于例行周报）</span>
      </label>

      <div v-if="touched && formProblem" class="field-err" role="alert">{{ formProblem }}</div>
      <div v-else-if="submitError" class="field-err" role="alert">服务端拒绝：{{ submitError }}</div>

      <button class="btn primary block lg" :disabled="submitting || !valid" @click="submit">
        {{ submitting ? '提交中…' : valid ? `提交巡检（${parsed.out.length} 家）` : '请先填写企业名单' }}
      </button>
      <p class="hint center">抓取由单 worker 串行执行，每家约 {{ minDelay }}–{{ maxDelay }} 秒加页面耗时，请耐心等待。</p>
    </div>
  </div>
</template>

<style scoped>
.submit-grid { align-items: start; }
textarea { min-height: 240px; }
.counter { font-variant-numeric: tabular-nums; color: var(--c-text-dim); font-size: 12.5px; }
.counter.over { color: var(--c-alert); font-weight: 600; }
.meta-line { display: flex; align-items: center; gap: 8px; margin: 8px 0 4px; flex-wrap: wrap; }
.grow { flex: 1; }
.tag { font-size: 12px; padding: 2px 8px; border-radius: 999px; }
.tag.warn { color: var(--c-warn); background: var(--c-warn-bg); }
.tag.muted { color: var(--c-muted); background: var(--c-muted-bg); }
.hint { color: var(--c-text-dim); font-size: 12px; margin: 6px 0 0; }
.hint.center { text-align: center; }
.hint code { background: var(--c-surface-2); padding: 1px 4px; border-radius: 3px; }
.tilde { color: var(--c-text-dim); padding-bottom: 8px; }
.field-err { color: var(--c-alert); font-size: 12.5px; background: var(--c-alert-bg); border-radius: var(--radius-sm); padding: 8px 10px; margin-bottom: 10px; }
.btn.lg { padding: 11px 14px; font-size: 15px; margin-top: 4px; }
</style>
