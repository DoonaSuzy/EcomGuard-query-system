<script setup>
import { watch } from 'vue'
import { fetchReportDetail } from '../api'
import { useStore, setActiveReport } from '../store'
import { formatDate, errorCount } from '../utils'
import { showToast } from '../toast'
import { loadReports } from '../actions'
import { downloadText, reportToCsv, reportToJson, reportCsvName } from '../download'
import AlertItem from './AlertItem.vue'

/**
 * 检查报告页：历史报告列表 + 详情卡。
 *
 * 列表顺序完全交给后端（`GET /api/reports` 已按文件名倒序），前端不重排——
 * 用例断言「最新的一份在最前」。
 *
 * 整条 `.report-item` 都能点开详情：旧的 HTML 是把 click 绑在最外层 div 上，
 * 「查看详情」按钮只是视觉提示。这里照搬这个结构，没有单独给按钮绑
 * 处理器，否则点按钮会冒泡触发两次请求。
 */
const { state } = useStore()

async function viewReport(filename) {
  try {
    const data = await fetchReportDetail(filename)
    if (data.success) {
      setActiveReport({ filename, detail: data.data })
    }
  } catch {
    showToast('加载失败', '无法加载报告详情', 'error')
  }
}

function closeReport() {
  setActiveReport(null)
}

/**
 * 报告下载：JSON（原始结构）/ CSV（逐条明细）。
 *
 * 数据源是详情接口的完整返回（里面已含 alerts 与 results）。列表行上的
 * 下载按钮**不要求先点开详情**——没加载过就现拉一次，否则用户得先点
 * 「查看详情」再下载，多一步且容易以为按钮坏了。
 *
 * 不新增后端下载路由，是因为契约文件 `docs/api-schema.json` 的 routes 与
 * categories 两份清单要与真实路由逐条比对（`tests/test_api_schema.py`），
 * 为一个纯格式化动作去改契约不划算。
 */
async function downloadReport(filename, format) {
  if (!filename) return
  try {
    // 只有当前打开的正是这一份时才复用缓存，否则现拉。
    let detail = state.activeReport?.filename === filename ? state.activeReport.detail : null
    if (!detail) {
      const data = await fetchReportDetail(filename)
      if (!data.success) {
        showToast('下载失败', data.error || '无法获取报告详情', 'error')
        return
      }
      detail = data.data
    }
    if (format === 'json') {
      downloadText(filename, reportToJson(detail), 'application/json;charset=utf-8')
    } else {
      downloadText(reportCsvName(filename), reportToCsv(detail), 'text/csv;charset=utf-8', true)
    }
  } catch {
    showToast('下载失败', '无法获取报告详情', 'error')
  }
}

// 进入报告页刷新一次列表。旧版是 switchPage 里做的；这里用 watch 保持
// 同样的一进入就刷新，同时避免了在 App.vue 里散落开关后面的逻辑。
watch(
  () => state.currentPage,
  (page) => {
    if (page === 'reports') loadReports()
  },
  { immediate: true },
)

function alertCountStyle(count) {
  return { color: count > 0 ? 'var(--warning-color)' : 'var(--success-color)' }
}
</script>

<template>
  <section id="page-reports" class="page" :class="{ active: state.currentPage === 'reports' }">
    <div class="card">
      <div class="card-header">
        <h2>历史报告</h2>
      </div>
      <div class="reports-list" id="reports-list">
        <div class="empty-state" v-if="state.reports.length === 0">
          <svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
            <path
              d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"
              stroke="currentColor"
              stroke-width="2"
              stroke-linecap="round"
              stroke-linejoin="round"
            />
            <path
              d="M14 2v6h6"
              stroke="currentColor"
              stroke-width="2"
              stroke-linecap="round"
              stroke-linejoin="round"
            />
          </svg>
          <p>暂无报告</p>
          <span>执行一次检查后将生成报告</span>
        </div>
        <div
          class="report-item"
          v-for="report in state.reports"
          :key="report.filename"
          @click="viewReport(report.filename)"
        >
          <div class="report-info">
            <div class="report-icon">
              <svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                <path
                  d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"
                  stroke="currentColor"
                  stroke-width="2"
                  stroke-linecap="round"
                  stroke-linejoin="round"
                />
                <path
                  d="M14 2v6h6"
                  stroke="currentColor"
                  stroke-width="2"
                  stroke-linecap="round"
                  stroke-linejoin="round"
                />
              </svg>
            </div>
            <div class="report-details">
              <div class="report-filename">{{ report.filename }}</div>
              <div class="report-meta">
                {{ formatDate(report.timestamp) }} | 共 {{ report.total }} 个商标 |
                <span :style="alertCountStyle(report.alertsCount)"
                  >{{ report.alertsCount }} 条告警</span
                >
              </div>
            </div>
          </div>
          <div class="report-actions">
            <!-- 下载按钮必须 @click.stop：整条 .report-item 的 click 绑的是
                 「打开详情」，不阻止冒泡会先落盘再触发一次详情请求。
                 这里用 class 而不是 id：按钮在 v-for 里，多份报告会渲染多个
                 同名 id，HTML 不允许重复 id，用 class 才能精确点到某一行。 -->
            <button
              class="btn btn-outline btn-sm js-report-download"
              :data-report="report.filename"
              data-format="json"
              title="下载报告原始 JSON"
              @click.stop="downloadReport(report.filename, 'json')"
            >
              下载 JSON
            </button>
            <button
              class="btn btn-outline btn-sm js-report-download"
              :data-report="report.filename"
              data-format="csv"
              title="下载报告明细 CSV"
              @click.stop="downloadReport(report.filename, 'csv')"
            >
              下载 CSV
            </button>
            <button class="btn btn-outline btn-sm">查看详情</button>
          </div>
        </div>
      </div>
    </div>

    <div class="card" id="report-detail-card" v-if="state.activeReport">
      <div class="card-header">
        <h2 id="report-detail-title">{{ state.activeReport.filename }}</h2>
        <div class="report-detail-actions">
          <!-- 详情卡里也放一份下载：用户点开报告后最自然的动作就是导出。 -->
          <button
            class="btn btn-outline btn-sm js-report-download"
            :data-report="state.activeReport.filename"
            data-format="json"
            title="下载报告原始 JSON"
            @click="downloadReport(state.activeReport.filename, 'json')"
          >
            下载 JSON
          </button>
          <button
            class="btn btn-outline btn-sm js-report-download"
            :data-report="state.activeReport.filename"
            data-format="csv"
            title="下载报告明细 CSV"
            @click="downloadReport(state.activeReport.filename, 'csv')"
          >
            下载 CSV
          </button>
          <button class="btn btn-ghost" id="btn-close-report" @click="closeReport">
            <svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
              <path
                d="M18 6L6 18M6 6l12 12"
                stroke="currentColor"
                stroke-width="2"
                stroke-linecap="round"
              />
            </svg>
            返回
          </button>
        </div>
      </div>
      <div class="report-detail" id="report-detail">
        <div class="report-summary">
          <div class="report-summary-item">
            <div class="value">
              {{ state.activeReport.detail.total || (state.activeReport.detail.results || []).length }}
            </div>
            <div class="label">检查总数</div>
          </div>
          <div class="report-summary-item">
            <div class="value">{{ (state.activeReport.detail.alerts || []).length }}</div>
            <div class="label">告警数</div>
          </div>
          <div class="report-summary-item">
            <div class="value">{{ errorCount(state.activeReport.detail.errors) }}</div>
            <div class="label">错误数</div>
          </div>
          <div class="report-summary-item">
            <div class="value">{{ formatDate(state.activeReport.detail.timestamp) }}</div>
            <div class="label">检查时间</div>
          </div>
        </div>
        <h3 style="margin: 24px 0 16px; font-size: 16px">告警详情</h3>
        <div class="alerts-list">
          <AlertItem
            v-for="(alert, index) in state.activeReport.detail.alerts || []"
            :key="alert.brandName || index"
            :alert="alert"
          />
          <div class="empty-state" style="padding: 20px" v-if="(state.activeReport.detail.alerts || []).length === 0">
            <p>无告警</p>
          </div>
        </div>
      </div>
    </div>
  </section>
</template>
