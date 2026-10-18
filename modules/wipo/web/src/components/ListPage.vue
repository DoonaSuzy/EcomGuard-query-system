<script setup>
import { computed } from 'vue'
import { useStore, setSearchTerm, isSelected, toggleSelected, selectAll, clearSelection } from '../store'
import { deleteTrademark, deleteTrademarksBatch } from '../api'
import { formatDate, joinNiceClass } from '../utils'
import { showToast } from '../toast'
import { showModal } from '../modal'
import { loadTrademarks } from '../actions'

/**
 * 商标清单页：搜索 + 表格 + 批量操作。
 *
 * 表格的列序被 E2E 写死了（0 勾选 / 1 名称 / 2 申请人 / 3 注册号 /
 * 4 尼斯分类 / 5 备注 / 6 时间 / 7 操作），改顺序不如加列。
 *
 * 空态元素 `#empty-state` 始终存在于 DOM 里、靠 v-if 控制里面那句话——
 * 用例断言的是「有结果时 #empty-state is_hidden」，所以不能拿 v-if 把
 * 容器本身摘掉（摘掉也能通过 is_hidden，但视觉上会闪）。
 */
const { state, selectedCount } = useStore()

const emit = defineEmits(['edit'])

const filtered = computed(() => {
  const term = state.searchTerm.toLowerCase().trim()
  if (!term) return state.trademarks
  return state.trademarks.filter((tm) => {
    const name = (tm.brandName || '').toLowerCase()
    const applicant = (tm.applicant || '').toLowerCase()
    return name.includes(term) || applicant.includes(term)
  })
})

const hasTrademarks = computed(() => state.trademarks.length > 0)

// 全选框的勾选态：全部行都选中才算勾上，否则是未勾选（不用 indeterminate，
// 旧版没有这个语义，加了会让 uncheck('#select-all') 的行为难以预测）。
const allSelected = computed(
  () => hasTrademarks.value && filtered.value.length > 0 && filtered.value.every((tm) => isSelected(tm.id)),
)

const batchDeleteLabel = computed(() => `批量删除 (${selectedCount.value})`)

function updateSearch(event) {
  setSearchTerm(event.target.value)
}

function handleRowToggle(id, event) {
  toggleSelected(id, event.target.checked)
}

function handleSelectAll(event) {
  selectAll(event.target.checked)
}

function confirmDeleteOne(tm) {
  showModal('确认删除', `确定要删除商标 "${tm.brandName}" 吗？此操作不可撤销。`, async () => {
    try {
      const result = await deleteTrademark(tm.id)
      if (result.success) {
        showToast('删除成功', `商标 "${tm.brandName}" 已删除`, 'success')
        loadTrademarks()
      } else {
        showToast('删除失败', result.error || '未知错误', 'error')
      }
    } catch {
      showToast('请求失败', '请检查网络连接', 'error')
    }
  })
}

function confirmBatchDelete() {
  const count = selectedCount.value
  if (count === 0) return

  showModal('确认删除', `确定要删除选中的 ${count} 个商标吗？此操作不可撤销。`, async () => {
    try {
      const result = await deleteTrademarksBatch([...state.selectedIds])
      if (result.success) {
        showToast('删除成功', `已删除 ${result.deletedCount} 个商标`, 'success')
        clearSelection()
        loadTrademarks()
      } else {
        showToast('删除失败', result.error || '未知错误', 'error')
      }
    } catch {
      showToast('请求失败', '请检查网络连接', 'error')
    }
  })
}

function editTrademark(tm) {
  emit('edit', tm)
}
</script>

<template>
  <section id="page-list" class="page" :class="{ active: state.currentPage === 'list' }">
    <div class="card">
      <div class="card-header">
        <h2>商标清单</h2>
        <div class="list-actions">
          <div class="search-box">
            <svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
              <circle cx="11" cy="11" r="8" stroke="currentColor" stroke-width="2" />
              <path
                d="M21 21l-4.35-4.35"
                stroke="currentColor"
                stroke-width="2"
                stroke-linecap="round"
              />
            </svg>
            <input
              type="text"
              id="search-input"
              placeholder="搜索商标..."
              :value="state.searchTerm"
              @input="updateSearch"
            />
          </div>
          <button
            class="btn btn-danger btn-sm"
            id="btn-batch-delete"
            :disabled="selectedCount === 0"
            @click="confirmBatchDelete"
          >
            {{ batchDeleteLabel }}
          </button>
        </div>
      </div>
      <div class="table-container">
        <table class="data-table">
          <thead>
            <tr>
              <th>
                <input
                  type="checkbox"
                  id="select-all"
                  :checked="allSelected"
                  @change="handleSelectAll"
                />
              </th>
              <th>商标名称</th>
              <th>申请人</th>
              <th>注册号</th>
              <th>尼斯分类</th>
              <th>备注</th>
              <th>添加时间</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody id="trademark-table-body">
            <tr
              v-for="tm in filtered"
              :key="tm.id"
              :data-id="tm.id"
              :class="{ selected: isSelected(tm.id) }"
            >
              <td>
                <input
                  type="checkbox"
                  :checked="isSelected(tm.id)"
                  @change="handleRowToggle(tm.id, $event)"
                />
              </td>
              <td class="brand-name">{{ tm.brandName || '-' }}</td>
              <td>{{ tm.applicant || '-' }}</td>
              <td>{{ tm.registrationNumber || '-' }}</td>
              <td>{{ joinNiceClass(tm.niceClass) || '-' }}</td>
              <td>{{ tm.notes || '-' }}</td>
              <td>{{ formatDate(tm.createdAt) }}</td>
              <td class="actions">
                <button class="btn-icon" title="编辑" @click="editTrademark(tm)">
                  <svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                    <path
                      d="M11 4H4a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2v-7"
                      stroke="currentColor"
                      stroke-width="2"
                      stroke-linecap="round"
                      stroke-linejoin="round"
                    />
                    <path
                      d="M18.5 2.5a2.121 2.121 0 013 3L12 15l-4 1 1-4 9.5-9.5z"
                      stroke="currentColor"
                      stroke-width="2"
                      stroke-linecap="round"
                      stroke-linejoin="round"
                    />
                  </svg>
                </button>
                <button class="btn-icon danger" title="删除" @click="confirmDeleteOne(tm)">
                  <svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                    <path
                      d="M3 6h18M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2"
                      stroke="currentColor"
                      stroke-width="2"
                      stroke-linecap="round"
                      stroke-linejoin="round"
                    />
                  </svg>
                </button>
              </td>
            </tr>
          </tbody>
        </table>
        <div class="empty-state" id="empty-state" v-if="filtered.length === 0">
          <svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
            <path
              d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2"
              stroke="currentColor"
              stroke-width="2"
              stroke-linecap="round"
              stroke-linejoin="round"
            />
          </svg>
          <p>{{ state.searchTerm ? '未找到匹配的商标' : '暂无商标数据' }}</p>
          <span>请在"商标录入"页面添加商标</span>
        </div>
      </div>
    </div>
  </section>
</template>
