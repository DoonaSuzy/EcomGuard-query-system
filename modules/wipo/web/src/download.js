/**
 * 浏览器端文件下载与报告导出。
 *
 * 为什么不走后端新增下载接口：报告详情在前端已经是完整对象
 * （`state.activeReport.detail`），再让后端拼一遍 CSV 等于把同一份
 * 数据格式化两次；而且新增路由要同步改 `docs/api-schema.json` 的
 * routes 与 categories 两份清单（`tests/test_api_schema.py` 会逐条比对），
 * 为了一个纯展示层的导出动作去动契约不划算。
 */

/** 触发浏览器下载一段文本。带 BOM 让 Excel 正确识别 UTF-8 中文。 */
export function downloadText(filename, text, mime = 'text/plain;charset=utf-8', withBom = false) {
  const payload = withBom ? '\ufeff' + text : text
  const blob = new Blob([payload], { type: mime })
  downloadBlob(filename, blob)
}

/** 触发浏览器下载一个 Blob（报告 JSON / 商标导出都走这里）。 */
export function downloadBlob(filename, blob) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.style.display = 'none'
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  // 立刻 revoke 会让部分浏览器来不及读完 blob（下载到 0 字节），
  // 延迟释放；10s 足够覆盖慢盘写入，也不会长期占内存。
  setTimeout(() => URL.revokeObjectURL(url), 10_000)
}

/**
 * 一次导出 CSV 用的字段转义。
 *
 * 规则同 RFC 4180：含逗号 / 双引号 / 换行的字段整体加引号，内部双引号翻倍。
 * 商标名与申请人里出现逗号是常态（`notes` 就允许「含,逗号备注」），
 * 不转义会让列错位。
 */
function csvCell(value) {
  const text = value === null || value === undefined ? '' : String(value)
  if (/[",\r\n]/.test(text)) {
    return '"' + text.replace(/"/g, '""') + '"'
  }
  return text
}

function toCsvLine(cells) {
  return cells.map(csvCell).join(',')
}

/** 报告详情 → 美化后的 JSON 文本（就是接口返回的完整结构）。 */
export function reportToJson(detail) {
  return JSON.stringify(detail, null, 2)
}

/**
 * 报告详情 → 逐条明细 CSV。
 *
 * 一份报告里一个商标可能同时有「异常状态」和「疑似抢注/待审核」，
 * 所以这里把三类问题**展开成行**（而不是一个商标一行、把三类挤进
 * 三个单元格），否则导出的表格没法排序、没法按风险筛。
 */
export function reportToCsv(detail) {
  const header = [
    '商标名称',
    '问题类型',
    '注册号',
    '登记状态',
    '局',
    '申请人',
    '申请人国家',
    '尼斯分类',
    '风险等级',
    '说明',
  ]
  const lines = [toCsvLine(header)]

  for (const alert of detail?.alerts || []) {
    const brand = alert.brandName || ''

    for (const item of alert.abnormalStatuses || []) {
      lines.push(
        toCsvLine([
          brand,
          '状态异常',
          item.registrationNumber || '',
          item.status || '',
          item.office || '',
          '',
          '',
          '',
          'high',
          item.expiryDate ? `到期日 ${item.expiryDate}` : '',
        ]),
      )
    }

    for (const [kind, cases] of [
      ['疑似抢注', alert.squattingCases],
      ['待审核', alert.needsReviewCases],
    ]) {
      for (const item of cases || []) {
        lines.push(
          toCsvLine([
            brand,
            kind,
            item.registrationNumber || '',
            item.status || '',
            item.office || '',
            item.applicant || '',
            item.applicantCountryCode || '',
            (item.niceClass || []).join(' '),
            item.riskLevel || '',
            item.reason || '',
          ]),
        )
      }
    }
  }

  // 没有任何告警时也要产出表头行，避免下载到 0 字节文件，用户以为导出失败。
  return lines.join('\r\n')
}

/** 报告文件名 `report_20260805_090000.json` → 同名的 `.csv`。 */
export function reportCsvName(filename) {
  return String(filename || 'report').replace(/\.json$/i, '') + '.csv'
}
