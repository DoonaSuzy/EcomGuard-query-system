/**
 * 两个业务系统与它们的页面 —— 侧边栏、路由、顶栏标题的**唯一数据源**。
 *
 * 为什么要集中到这一个文件：统一前两个系统各自在 App.vue 里写死自己的
 * 导航数组（WIPO 的 NAV_ITEMS、credit 的 NAV），文案/图标/顺序三份信息
 * 分散在两处，加一个系统要改两个壳。现在加系统只需在这里加一项。
 *
 * `page` 字段同时是：路由 name、侧栏 data-page、以及 WIPO 内部
 * state.currentPage 的取值——WIPO 的四个页面组件自带
 * `:class="{ active: state.currentPage === 'input' }"`，靠它自管显隐，
 * 所以宿主不能把 page 改成别的词，否则那四个页面会全部隐藏。
 */

export type SystemId = 'wipo' | 'credit'

export interface NavPage {
  /** 路由 name，同时是 WIPO state.currentPage 的取值 */
  page: string
  label: string
  /** 24x24 stroke 图标路径，与 WIPO 侧栏同一套画法 */
  icon: string
}

export interface NavSystem {
  id: SystemId
  /** 侧栏系统列表里的名字 */
  label: string
  /** 侧栏系统列表里的一行说明 */
  desc: string
  /** 点该系统时落到哪个页面 */
  home: string
  pages: NavPage[]
}

export const SYSTEMS: NavSystem[] = [
  {
    id: 'wipo',
    label: 'WIPO',
    desc: '商标监控',
    home: 'input',
    pages: [
      {
        page: 'input',
        label: '商标录入',
        icon: 'M11 4H4a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2v-7M18.5 2.5a2.121 2.121 0 013 3L12 15l-4 1 1-4 9.5-9.5z',
      },
      {
        page: 'list',
        label: '商标清单',
        icon: 'M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01',
      },
      {
        page: 'check',
        label: '状态检查',
        icon: 'M9 11l3 3L22 4M21 12v7a2 2 0 01-2 2H5a2 2 0 01-2-2V5a2 2 0 012-2h11',
      },
      {
        page: 'reports',
        label: '检查报告',
        icon: 'M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8zM14 2v6h6M16 13H8M16 17H8M10 9H8',
      },
    ],
  },
  {
    id: 'credit',
    label: '营业执照',
    desc: '状态巡检',
    home: 'overview',
    pages: [
      {
        page: 'overview',
        label: '总览',
        icon: 'M3 3h7v7H3zM14 3h7v7h-7zM14 14h7v7h-7zM3 14h7v7H3z',
      },
      {
        page: 'submit',
        label: '提交巡检',
        icon: 'M12 3a9 9 0 100 18 9 9 0 000-18zM12 8v8M8 12h8',
      },
      {
        page: 'schedules',
        label: '定时巡检',
        icon: 'M12 3a9 9 0 100 18 9 9 0 000-18zM12 7.5V12l3 1.8',
      },
      {
        page: 'jobs',
        label: '作业列表',
        icon: 'M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01',
      },
    ],
  },
]

/** 所有系统的页面名合集，用来判定「某个路由属于哪个系统」。 */
export const PAGE_TO_SYSTEM: Record<string, SystemId> = Object.fromEntries(
  SYSTEMS.flatMap((s) => s.pages.map((p) => [p.page, s.id])),
)

/**
 * 作业详情不在侧栏里（它是作业列表的下钻页），但它属于 credit 系统，
 * 必须显式登记，否则从详情页刷新时顶栏会认不出当前系统。
 */
export const EXTRA_PAGE_TO_SYSTEM: Record<string, SystemId> = {
  'job-detail': 'credit',
  'not-found': 'credit',
}

export function systemOf(routeName: string | null | undefined): SystemId {
  if (!routeName) return 'wipo'
  return PAGE_TO_SYSTEM[routeName] ?? EXTRA_PAGE_TO_SYSTEM[routeName] ?? 'wipo'
}

export function systemById(id: SystemId): NavSystem {
  return SYSTEMS.find((s) => s.id === id) ?? SYSTEMS[0]
}

/** 顶栏标题：详情页/兜底页在侧栏里没有条目，单独给文案。 */
export function titleOf(routeName: string | null | undefined): string {
  if (!routeName) return ''
  for (const sys of SYSTEMS) {
    const hit = sys.pages.find((p) => p.page === routeName)
    if (hit) return hit.label
  }
  if (routeName === 'job-detail') return '作业详情'
  if (routeName === 'not-found') return '页面不存在'
  return routeName
}
