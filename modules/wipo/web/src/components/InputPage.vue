<script setup>
import { ref, reactive, computed } from 'vue'
import { createTrademark, updateTrademark, importFile, exportDataUrl } from '../api'
import { useStore, setEditingId } from '../store'
import { parseNiceClass, joinNiceClass } from '../utils'
import { showToast } from '../toast'
import { loadTrademarks } from '../actions'

/**
 * 商标录入页：表单 + 批量导入/导出。
 *
 * DOM 上的每个 id 都与旧版 templates/index.html 一一对应
 * （#trademark-form / #brandName / #brandName-count / #brandName-error /
 *  #btn-submit / #btn-text / #import-file / #import-status ...），
 * E2E 靠它们定位，不能改。
 *
 * 有两个地方与「直觉的 Vue 写法」不同，都是被测试逼出来的：
 *
 * 1. 重置按钮仍然是 `type="reset"`，但同时绑了 @click 手动清空响应式字段。
 *    原生 reset 只改 DOM 的 value、不派发 input 事件，v-model 收不到通知，
 *    `#brandName-count` 会停在旧值上（E2E 断言重置后必须是 '0/200'）。
 * 2. 品牌名长度校验读的是 `event.target.value` 而不是 v-model 的值——
 *    201 字符那条用例是用 JS 绕过 maxlength 直接写 el.value 再 dispatch
 *    input 的，只有监听原生 input 事件才拦得到。
 */
const { state } = useStore()

const form = reactive({
  brandName: '',
  applicant: '',
  registrationNumber: '',
  niceClass: '',
  notes: '',
})

const fieldError = ref('')
const submitting = ref(false)
const importStatus = ref('')
const fileInput = ref(null)

const charCount = computed(() => `${form.brandName.length}/200`)
const isEditing = computed(() => state.editingId !== null)
const btnText = computed(() => {
  if (submitting.value) return '提交中...'
  return isEditing.value ? '更新商标' : '添加商标'
})

function showFieldError(message) {
  fieldError.value = message
}

function clearFieldError() {
  fieldError.value = ''
}

function handleBrandNameInput(event) {
  const value = event.target.value
  form.brandName = value

  if (value.length > 0 && value.length < 2) {
    showFieldError('商标名称至少2个字符')
  } else if (value.length > 200) {
    showFieldError('商标名称长度不能超过200个字符')
  } else {
    clearFieldError()
  }
}

function validateForm() {
  const name = form.brandName || ''
  if (!name.trim()) {
    showFieldError('商标名称不能为空')
    return false
  }
  if (name.trim().length < 2) {
    showFieldError('商标名称至少2个字符')
    return false
  }
  if (name.trim().length > 200) {
    showFieldError('商标名称长度不能超过200个字符')
    return false
  }
  return true
}

async function handleSubmit() {
  if (!validateForm()) return

  const payload = {
    brandName: form.brandName.trim(),
    applicant: form.applicant.trim(),
    registrationNumber: form.registrationNumber.trim(),
    niceClass: parseNiceClass(form.niceClass),
    notes: form.notes.trim(),
  }

  submitting.value = true
  try {
    const result = isEditing.value
      ? await updateTrademark(state.editingId, payload)
      : await createTrademark(payload)

    if (result.success) {
      showToast(
        isEditing.value ? '更新成功' : '添加成功',
        `商标 "${payload.brandName}" 已${isEditing.value ? '更新' : '添加'}`,
        'success',
      )
      resetForm()
      // 刷新列表与顶栏计数；不 await，让 toast 立刻可见
      loadTrademarks()
    } else {
      // 后端错误要同时写「字段错误」和 toast：
      // 重名用例断言的是 toast，视觉上又希望错误贴在输入框下方。
      showFieldError(result.error || '操作失败')
      showToast('操作失败', result.error || '未知错误', 'error')
    }
  } catch {
    showToast('请求失败', '请检查网络连接', 'error')
  } finally {
    submitting.value = false
  }
}

function resetForm() {
  form.brandName = ''
  form.applicant = ''
  form.registrationNumber = ''
  form.niceClass = ''
  form.notes = ''
  clearFieldError()
  setEditingId(null)
}

/** 由清单页的「编辑」按钮调用：回填字段并切回本页。 */
function startEdit(trademark) {
  setEditingId(trademark.id)
  form.brandName = trademark.brandName || ''
  form.applicant = trademark.applicant || ''
  form.registrationNumber = trademark.registrationNumber || ''
  form.niceClass = joinNiceClass(trademark.niceClass)
  form.notes = trademark.notes || ''
  clearFieldError()
}

async function handleFileImport(event) {
  const file = event.target.files[0]
  if (!file) return

  importStatus.value = '上传中...'

  try {
    const result = await importFile(file)
    if (result.success) {
      importStatus.value = `成功导入 ${result.importedCount} 个商标`
      showToast('导入成功', `已导入 ${result.importedCount} 个商标`, 'success')
      loadTrademarks()
    } else {
      importStatus.value = result.error || '导入失败'
      showToast('导入失败', result.error || '未知错误', 'error')
    }
  } catch {
    importStatus.value = '导入失败'
    showToast('导入失败', '请检查文件格式', 'error')
  }

  // 清空 input，否则连续选同一个文件不会再触发 change
  event.target.value = ''
  setTimeout(() => {
    importStatus.value = ''
  }, 3000)
}

function triggerImport() {
  fileInput.value.click()
}

function exportData(format) {
  exportDataUrl(format)
}

// 供 App.vue 通过模板 ref 调用（清单页点「编辑」时）
defineExpose({ startEdit })
</script>

<template>
  <section id="page-input" class="page" :class="{ active: state.currentPage === 'input' }">
    <div class="card">
      <div class="card-header">
        <h2>添加商标</h2>
        <p class="subtitle">输入商标信息，系统将自动保存用于后续检查</p>
      </div>
      <form id="trademark-form" class="form" @submit.prevent="handleSubmit">
        <div class="form-group">
          <label for="brandName">商标名称 <span class="required">*</span></label>
          <input
            id="brandName"
            v-model="form.brandName"
            type="text"
            name="brandName"
            placeholder="例如：APPLE"
            maxlength="200"
            autocomplete="off"
            :class="{ error: fieldError }"
            @input="handleBrandNameInput"
          />
          <div class="form-hint">
            <span>2-200 个字符</span>
            <span class="char-count" id="brandName-count">{{ charCount }}</span>
          </div>
          <div class="form-error" id="brandName-error">{{ fieldError }}</div>
        </div>

        <div class="form-row">
          <div class="form-group">
            <label for="applicant">申请人</label>
            <input
              id="applicant"
              v-model="form.applicant"
              type="text"
              name="applicant"
              placeholder="公司全称"
              autocomplete="off"
            />
          </div>
          <div class="form-group">
            <label for="registrationNumber">注册号</label>
            <input
              id="registrationNumber"
              v-model="form.registrationNumber"
              type="text"
              name="registrationNumber"
              placeholder="可选"
              autocomplete="off"
            />
          </div>
        </div>

        <div class="form-group">
          <label for="niceClass">尼斯分类</label>
          <input
            id="niceClass"
            v-model="form.niceClass"
            type="text"
            name="niceClass"
            placeholder="多个分类用逗号分隔，例如：9, 35"
            autocomplete="off"
          />
          <div class="form-hint">常见分类：9(电子)、25(服装)、35(广告)、42(科技)</div>
        </div>

        <div class="form-group">
          <label for="notes">备注</label>
          <textarea
            id="notes"
            v-model="form.notes"
            name="notes"
            rows="3"
            placeholder="可选，添加备注信息"
          ></textarea>
        </div>

        <div class="form-actions">
          <button type="reset" class="btn btn-secondary" id="btn-cancel" @click="resetForm">
            <svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
              <path
                d="M3 12a9 9 0 019-9 9.75 9.75 0 016.74 2.74L21 8M21 3v5h-5M21 12a9 9 0 01-9 9 9.75 9.75 0 01-6.74-2.74L3 16M8 16H3v5"
                stroke="currentColor"
                stroke-width="2"
                stroke-linecap="round"
                stroke-linejoin="round"
              />
            </svg>
            重置
          </button>
          <!--
            注意：这里没有再绑 @click。Playwright 点击 type=submit 的按钮会触发
            原生 submit，而 Vue 在 form 上挂了 @submit.prevent；若按钮再挂一次
            @click="handleSubmit"，一次点击会发两个请求。
          -->
          <button
            type="submit"
            class="btn btn-primary"
            id="btn-submit"
            :disabled="submitting"
          >
            <svg
              v-show="!submitting"
              viewBox="0 0 24 24"
              fill="none"
              xmlns="http://www.w3.org/2000/svg"
            >
              <path
                d="M5 13l4 4L19 7"
                stroke="currentColor"
                stroke-width="2"
                stroke-linecap="round"
                stroke-linejoin="round"
              />
            </svg>
            <span id="btn-text">{{ btnText }}</span>
          </button>
        </div>
      </form>
    </div>

    <div class="card quick-add">
      <div class="card-header">
        <h2>批量导入</h2>
        <p class="subtitle">支持 JSON 和 CSV 格式批量导入商标数据</p>
      </div>
      <div class="import-area">
        <input
          ref="fileInput"
          type="file"
          id="import-file"
          accept=".json,.csv"
          hidden
          @change="handleFileImport"
        />
        <button class="btn btn-outline" id="btn-import" @click="triggerImport">
          <svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
            <path
              d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4M17 8l-5-5-5 5M12 3v12"
              stroke="currentColor"
              stroke-width="2"
              stroke-linecap="round"
              stroke-linejoin="round"
            />
          </svg>
          选择文件
        </button>
        <span class="import-status" id="import-status">{{ importStatus }}</span>
      </div>
      <div class="export-area">
        <button class="btn btn-outline" id="btn-export-json" @click="exportData('json')">
          导出 JSON
        </button>
        <button class="btn btn-outline" id="btn-export-csv" @click="exportData('csv')">
          导出 CSV
        </button>
      </div>
    </div>
  </section>
</template>
