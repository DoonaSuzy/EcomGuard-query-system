<script setup lang="ts">
/**
 * 定时巡检页
 * ===========
 * 用户原话：「营业执照没有看到能设置每日检查或者每周每月的」。
 * 事实是后端有能力（install_schedule_task.ps1 注册了 Windows 计划任务），
 * 但**界面上完全没有入口**，而且那个计划任务指向的是外部仓库的 .bat，
 * 收件人还是占位地址。所以这一页不只是「加个表单」，
 * 它替代的是那条界面上看不见、也无法核对配得对不对的计划任务。
 *
 * 几个刻意的取舍：
 *  - 节奏只给「每日/每周/每月」，不给 cron：目标用户是销售主管，
 *    cron 对他们只是负担，而本项目只需要这三种。
 *  - 「每月几号」上限 28：2/4/6/9/11 月没有 29-31 号，必须定义
 *    「顺延月末」还是「跳过」，两种都有人要、都无法无歧义地解释。
 *  - 编辑用弹窗内的完整表单（不是行内改一个字段）：改节奏时 4 个字段
 *    （freq/at_time/weekday/day）互相约束，拆开改必然出现中间非法态。
 *  - 每次改完都重新拉列表，不本地改内存：next_run_at 是后端算的，
 *    前端不该猜——猜错的表现是「界面显示周一跑，实际周二才跑」。
 */
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  useSchedulesStore, emptyForm, fromSchedule, toPayload, splitNames, WEEKDAYS,
  type ScheduleForm,
} from '@/stores/schedules'
import { useToastStore } from '@/stores/toast'
import { STATE_LABEL } from '@/api/client'
import type { components } from '@/types'
import EmptyState from '@/components/EmptyState.vue'

type ScheduleOut = components['schemas']['ScheduleOut']

const router = useRouter()
const store = useSchedulesStore()
const toast = useToastStore()

// 弹窗开关单独用布尔量：早前用 editing 的哨兵值（null=新建 / ''=关闭）兼任开合，
// 而初始值就是 null，条件写成 `editing !== ''` → 首次进入页面弹窗直接盖在页面上。
// 浏览器实测踩到：点「新建」被 .modal-mask 拦住。两个概念分开即可。
const formOpen = ref(false)
const editing = ref<string | null>(null)   // null = 新建，否则 = 正在编辑的 schedule_id
const form = ref<ScheduleForm>(emptyForm())
const previewAt = ref('')
const saving = ref(false)
const formErr = ref('')

const names = computed(() => splitNames(form.value.namesText))

/**
 * 调度行的「上次结果」色调。
 *
 * 原实现是 `queued || done ? 'ok' : 'warn'`，把 failed 也归到 warn，
 * 而 failed 在别处（JobsView / JobDetailView / OverviewView）都是 alert 红。
 * 同一个状态在两张表里颜色不同，用户会以为看错了。这里对齐成：
 * done/queued/running → ok，skipped → warn，failed → alert。
 */
function stateTone(state: string): string {
  if (state === 'done' || state === 'queued' || state === 'running') return 'ok'
  if (state === 'failed') return 'alert'
  return 'warn'
}

/** 与后端 ScheduleIn 的约束对齐，前端先拦一道给出中文提示 */
const formProblem = computed(() => {
  const f = form.value
  if (!f.name.trim()) return '请填写调度名称'
  if (f.name.trim().length > 100) return '调度名称最多 100 字'
  if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(f.at_time)) return '触发时刻需形如 09:30'
  if (!names.value.length) return '请至少填写一个企业全称'
  if (names.value.length > 200) return `一次最多 200 家，当前 ${names.value.length} 家`
  if (f.minDelay < 0 || f.maxDelay < 0) return '间隔秒数不能为负'
  if (f.maxDelay < f.minDelay) return '最大间隔不能小于最小间隔'
  if (f.maxDelay > 120) return '最大间隔上限 120 秒'
  if (f.mailTo.trim()) {
    const addrs = f.mailTo.split(/[,;，；\s]+/).filter(Boolean)
    const bad = addrs.filter((a) => !/^[^@\s,;，；]+@[^@\s,;，；]+\.[^@\s,;，；]+$/.test(a))
    if (bad.length) return `收件人格式不正确：${bad.join('、')}`
  }
  if (f.webhook.trim() && !/^https?:\/\/[^\s/]+\.[^\s/]+/.test(f.webhook.trim())) {
    return 'Webhook 需要是带主机的地址（形如 https://qyapi.weixin.qq.com/...）'
  }
  return ''
})

/** 输入变化就重算预览；失败静默（打字中间态必然 422） */
async function refreshPreview() {
  const f = form.value
  previewAt.value = await store.preview(f.freq, f.at_time, f.weekday, f.day)
}

function openCreate() {
  editing.value = null
  formOpen.value = true
  form.value = emptyForm()
  formErr.value = ''
  void refreshPreview()
}

function openEdit(s: ScheduleOut) {
  editing.value = s.schedule_id
  formOpen.value = true
  form.value = fromSchedule(s)
  formErr.value = ''
  void refreshPreview()
}

function closeForm() {
  formOpen.value = false
  editing.value = null
  formErr.value = ''
}

async function save() {
  if (formProblem.value) {
    formErr.value = formProblem.value
    toast.error(formProblem.value)
    return
  }
  saving.value = true
  formErr.value = ''
  try {
    const payload = toPayload(form.value)
    if (editing.value) {
      const out = await store.update(editing.value, payload)
      toast.success(`已保存，下次 ${out?.human ?? ''} 触发`)
    } else {
      const out = await store.create(payload)
      toast.success(`已创建，下次 ${out?.human ?? ''} 触发`)
    }
    editing.value = null
    formOpen.value = false
  } catch (e) {
    formErr.value = (e as Error).message
    toast.error(`保存失败：${formErr.value}`)
  } finally {
    saving.value = false
  }
}

async function toggle(s: ScheduleOut) {
  try {
    await store.toggle(s.schedule_id, !s.enabled)
    toast.info(`已${s.enabled ? '停用' : '启用'}「${s.name}」`)
  } catch (e) {
    toast.error((e as Error).message)
  }
}

async function runNow(s: ScheduleOut) {
  try {
    const job = await store.runNow(s.schedule_id)
    toast.success(`已提交作业，共 ${job?.total ?? 0} 家`)
    if (job) router.push(`/jobs/${job.job_id}`)
  } catch (e) {
    toast.error((e as Error).message)
  }
}

async function remove(s: ScheduleOut) {
  if (!confirm(`删除调度「${s.name}」？已产生的历史作业不受影响。`)) return
  try {
    await store.remove(s.schedule_id)
    toast.info('已删除')
  } catch (e) {
    toast.error((e as Error).message)
  }
}

onMounted(() => {
  void store.load()
  void refreshPreview()
})
</script>

<template>
  <div class="page-head">
    <h1>定时巡检</h1>
    <p>按每日 / 每周 / 每月自动跑一遍名单，异常才推送。到点由服务内部触发，不需要 Windows 计划任务。</p>
  </div>

  <div class="card">
    <div class="card-title">
      <h2>已配置（{{ store.enabledCount }} / {{ store.list.length }} 启用）</h2>
      <button class="btn primary sm" @click="openCreate">＋ 新建定时巡检</button>
    </div>

    <p v-if="store.error" class="field-err" role="alert">{{ store.error }}</p>

    <div v-if="!store.list.length && !store.loading" class="empty-wrap">
      <EmptyState title="还没有定时巡检" hint="建一条「每周一 09:00」，系统就会自动查询并在有异常时推送。" />
    </div>

    <table v-else class="tbl">
      <thead>
        <tr>
          <th>名称</th><th>节奏</th><th>下次触发</th><th class="num">名单</th>
          <th>状态</th><th>上次结果</th><th class="act">操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="s in store.list" :key="s.schedule_id">
          <td>
            <div class="nm">{{ s.name }}</div>
            <div class="sub mono">{{ s.schedule_id }}</div>
          </td>
          <td>{{ s.human }}</td>
          <td class="mono">{{ s.enabled ? (s.next_run_at ?? '—') : '已停用' }}</td>
          <td class="num">{{ s.count }}</td>
          <td>
            <span class="badge" :class="s.enabled ? 'ok' : 'muted'">{{ s.enabled ? '启用' : '停用' }}</span>
          </td>
          <td>
            <template v-if="s.last_state">
              <span class="badge" :class="stateTone(s.last_state)">
                {{ STATE_LABEL[s.last_state] || s.last_state }}
              </span>
              <router-link v-if="s.last_job_id" :to="`/jobs/${s.last_job_id}`" class="lnk mono">
                {{ s.last_job_id.slice(0, 18) }}
              </router-link>
              <div v-if="s.last_error" class="err" :title="s.last_error">{{ s.last_error }}</div>
            </template>
            <span v-else class="sub">尚未执行</span>
          </td>
          <td class="act">
            <button class="btn sm" :disabled="store.busy === s.schedule_id" @click="runNow(s)">立刻执行</button>
            <button class="btn sm" :disabled="store.busy === s.schedule_id" @click="toggle(s)">
              {{ s.enabled ? '停用' : '启用' }}
            </button>
            <button class="btn sm" @click="openEdit(s)">编辑</button>
            <button class="btn sm danger" @click="remove(s)">删除</button>
          </td>
        </tr>
      </tbody>
    </table>
  </div>

  <!-- 新建 / 编辑弹窗 -->
  <div v-if="formOpen" class="modal-mask" @click.self="closeForm">
    <div class="modal" role="dialog" aria-modal="true" aria-label="定时巡检配置">
      <div class="modal-head">
        <h2>{{ editing ? '编辑定时巡检' : '新建定时巡检' }}</h2>
        <button class="btn sm" @click="closeForm" aria-label="关闭">✕</button>
      </div>

      <div class="modal-body">
        <div class="field">
          <label for="s-name">名称</label>
          <input id="s-name" v-model="form.name" type="text" placeholder="例如：华东区每日巡检" maxlength="100" />
        </div>

        <div class="field">
          <label>节奏</label>
          <div class="row wrap">
            <label class="check"><input v-model="form.freq" type="radio" value="daily" @change="refreshPreview" /><span>每日</span></label>
            <label class="check"><input v-model="form.freq" type="radio" value="weekly" @change="refreshPreview" /><span>每周</span></label>
            <label class="check"><input v-model="form.freq" type="radio" value="monthly" @change="refreshPreview" /><span>每月</span></label>
          </div>
        </div>

        <div class="row">
          <div v-if="form.freq === 'weekly'" class="field grow">
            <label for="s-wd">星期</label>
            <select id="s-wd" v-model.number="form.weekday" @change="refreshPreview">
              <option v-for="(w, i) in WEEKDAYS" :key="i" :value="i">{{ w }}</option>
            </select>
          </div>
          <div v-if="form.freq === 'monthly'" class="field grow">
            <label for="s-day">每月几号</label>
            <select id="s-day" v-model.number="form.day" @change="refreshPreview">
              <option v-for="d in 28" :key="d" :value="d">{{ d }} 日</option>
            </select>
          </div>
          <div class="field grow">
            <label for="s-time">触发时刻</label>
            <input id="s-time" v-model="form.at_time" type="time" @change="refreshPreview" />
          </div>
        </div>

        <p class="preview">
          下次触发：<strong>{{ previewAt || '—' }}</strong>
          <span class="hint">（每月只支持 1–28 日：其余日期在部分月份不存在）</span>
        </p>

        <div class="field">
          <label for="s-names">企业名单 <span class="counter">{{ names.length }} / 200</span></label>
          <textarea id="s-names" v-model="form.namesText" placeholder="一行一个企业全称，# 开头为注释" spellcheck="false" />
        </div>

        <div class="field">
          <label for="s-mail">告警邮件收件人</label>
          <input id="s-mail" v-model="form.mailTo" type="text" placeholder="留空则不发邮件，多个用逗号分隔" />
          <div class="hint">全中文逗号、分号、空格都会自动归一；填错地址会当场拒绝，而不是跑完才发现没发出去。</div>
        </div>

        <div class="field">
          <label for="s-hook">Webhook（可选）</label>
          <input id="s-hook" v-model="form.webhook" type="text" placeholder="企业微信 / 钉钉 / 飞书机器人地址" />
        </div>

        <details class="adv">
          <summary>抓取节奏（高级）</summary>
          <div class="row">
            <div class="field grow">
              <label for="s-min">最小间隔（秒）</label>
              <input id="s-min" v-model.number="form.minDelay" type="number" min="0" max="60" step="0.5" />
            </div>
            <div class="field grow">
              <label for="s-max">最大间隔（秒）</label>
              <input id="s-max" v-model.number="form.maxDelay" type="number" min="0" max="120" step="0.5" />
            </div>
          </div>
          <p class="hint">目标站有瑞数风控，间隔太短容易触发验证码。默认 3.5–6.5 秒。</p>
        </details>

        <label class="check">
          <input v-model="form.alwaysNotify" type="checkbox" />
          <span>全部正常也推送（用于例行周报）</span>
        </label>

        <div v-if="formErr" class="field-err" role="alert">{{ formErr }}</div>
      </div>

      <div class="modal-foot">
        <button class="btn sm" @click="closeForm">取消</button>
        <button class="btn primary" :disabled="saving" @click="save">
          {{ saving ? '保存中…' : editing ? '保存修改' : '创建' }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.empty-wrap { padding: 28px 0; }
.tbl { width: 100%; border-collapse: collapse; font-size: 13px; }
.tbl th, .tbl td { padding: 9px 10px; text-align: left; border-bottom: 1px solid var(--c-border); vertical-align: top; }
.tbl th { font-weight: 600; color: var(--c-text-dim); font-size: 12px; white-space: nowrap; }
.tbl .num { text-align: right; font-variant-numeric: tabular-nums; }
.tbl .act { white-space: nowrap; }
.tbl .act .btn { margin-left: 4px; }
.nm { font-weight: 600; }
.sub { color: var(--c-text-dim); font-size: 11.5px; }
.err { color: var(--c-alert); font-size: 11.5px; margin-top: 2px; max-width: 260px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.lnk { margin-left: 6px; font-size: 11.5px; }
.counter { color: var(--c-text-dim); font-weight: 400; font-size: 12px; margin-left: 6px; }

.modal-mask { position: fixed; inset: 0; background: rgba(0,0,0,.38); display: flex; align-items: center; justify-content: center; padding: 20px; z-index: 60; }
.modal { background: var(--c-surface); border-radius: var(--radius); width: min(720px, 100%); max-height: 90vh; display: flex; flex-direction: column; box-shadow: 0 12px 40px rgba(0,0,0,.22); }
.modal-head, .modal-foot { padding: 14px 18px; display: flex; align-items: center; justify-content: space-between; }
.modal-head h2 { font-size: 16px; margin: 0; }
.modal-foot { justify-content: flex-end; gap: 8px; border-top: 1px solid var(--c-border); }
.modal-body { padding: 4px 18px 18px; overflow: auto; }
.row { display: flex; gap: 10px; align-items: flex-end; }
.row.wrap { flex-wrap: wrap; align-items: center; }
.grow { flex: 1; }
.preview { background: var(--c-surface-2); border-radius: var(--radius-sm); padding: 8px 10px; font-size: 12.5px; margin: 2px 0 12px; }
.hint { color: var(--c-text-dim); font-size: 12px; margin: 4px 0 0; }
textarea { min-height: 130px; }
.adv { margin: 6px 0 12px; }
.adv summary { cursor: pointer; font-size: 12.5px; color: var(--c-text-dim); }
.field-err { color: var(--c-alert); font-size: 12.5px; background: var(--c-alert-bg); border-radius: var(--radius-sm); padding: 8px 10px; margin-bottom: 10px; }
</style>
