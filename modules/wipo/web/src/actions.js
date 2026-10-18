import { fetchTrademarks, fetchReports } from './api'
import { setTrademarks, setReports } from './store'
import { showToast } from './toast'

/**
 * 跨页面复用的两个「重新拉数据」动作。
 *
 * 放在这里而不是某个组件里，是因为录入页提交后、清单页删除后都要刷新商标，
 * 报告页也要单独刷新——写在组件里就会变成两份一样的 fetch。
 */

export async function loadTrademarks() {
  try {
    const data = await fetchTrademarks()
    if (data.success) {
      setTrademarks(data.data)
    }
  } catch {
    showToast('加载失败', '无法加载商标数据', 'error')
  }
}

export async function loadReports() {
  try {
    const data = await fetchReports()
    if (data.success) {
      setReports(data.data)
    }
  } catch {
    showToast('加载失败', '无法加载报告列表', 'error')
  }
}
