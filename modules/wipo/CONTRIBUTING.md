# 参与贡献

感谢你愿意为这个项目出力。本文件说明协作约定，读完后可以省掉大量来回沟通。

## 开始之前

1. 读 [AGENTS.md](AGENTS.md)。那不是给人看的客套话——那里记录了一堆**代码里看不出来的坑**，
   比如「改 `app/api/v1/` 的 `API_PREFIX` 会一次性打断前端和 110 项 E2E」。
2. 确认你的问题没有被 [README 常见问题](README.md#常见问题)和现有
   [issue](https://github.com/DoonaSuzy/WIPO_and_Brand/issues)覆盖。

## 环境准备

```bash
git clone git@github.com:DoonaSuzy/WIPO_and_Brand.git
cd WIPO_and_Brand
python -m venv venv
venv\Scripts\activate            # Linux/macOS: source venv/bin/activate
pip install -e ".[dev]"
python -m playwright install chromium
```

数据库默认连本机 MySQL（`wipo_trademark`，root 空密码）。**测试不需要 MySQL**——
测试套件会自动把数据源切到临时 SQLite 库（见 `tests/conftest.py`）。

## 提交前必须全绿

```bash
python -m ruff check app tests tests_new cli.py    # 代码检查
python scripts/check_deps_consistency.py           # 依赖双源一致性
python -m pytest tests_new -q                      # 契约 + 服务层（约 5 秒）
python -m pytest tests -q                          # E2E（约 45 秒）
```

> `verify_fixes.py` 与 `verify_new_stack.py` 是**缺陷回归断言**，不是覆盖率工具：
> 每一条对应一个修过的真实缺陷。改了监控逻辑请一并更新。

## 写 E2E 用例：断言必须自动等待

页面数据全部由 async fetch 加载（`static/js/app.js:126` 的 `loadTrademarks()`、
`web/src/App.vue` 初始渲染 `{{ state.trademarks.length }}` 即 0）。
`page.reload()` 只保证 DOM 就绪，**不保证 fetch 返回**。

```python
# 错误 —— 查询的是那一瞬间的快照，慢一点就红
assert page.locator('#trademark-table-body tr').count() == 3

# 正确 —— Playwright 自动轮询直到满足或超时
expect(page.locator('#trademark-table-body tr')).to_have_count(3)
expect(page.locator('#total-count')).to_have_text('2')
```

这不是风格偏好，是踩过的坑（2026-10-16）：`test_trademark_count_displayed`
在 `reload()` 后立刻读 `#total-count`，5 次全量 E2E 有 1 次读到 `0`，
探针复刻 6 次中 1 次失败且失败那次耗时与正常无差异——加 `wait_for_timeout` 治不了。
已全量改造为 `expect()` 写法，**新用例请沿用**。

## 提交信息

遵循 [Conventional Commits](https://www.conventionalcommits.org/)：

```
fix(scheduler): 目标日当月不再被跳过
feat(reports): 新增紧凑摘要导出
docs: 修正 README 中过时的入口命令
test: 补 alarms 三形态契约断言
```

## 分支策略

- `main` 是唯一长期分支，随时可部署
- 新工作从 `main` 切 `feat/xxx` 或 `fix/xxx`
- 合并用 squash，保持 `main` 历史一条一意图

## 改动范围

以下位置的改动会一次性影响多处，动之前请先读注释：

| 位置 | 为什么危险 |
|---|---|
| `app/core/response.py` 的响应外壳 | 前端 4 处判 `result.success`，110 项 E2E 断言同一外壳 |
| `app/api/v1/__init__.py` 的 `API_PREFIX` | `web/src/api.js` 写死 `/api` |
| `tests/` 里的 46 个 DOM 选择器 | Vue 组件必须保留这些 id/class，否则 E2E 全废 |
| `docs/api-schema.json` | 有 CI 漂移检测，改了路由不改它会红 |

## 报告安全问题

涉及凭据、密钥或远程风险的，请**私下**通过
[Security Advisory](https://github.com/DoonaSuzy/WIPO_and_Brand/security/advisories/new)
报告，不要开公开 issue。详见 [SECURITY.md](SECURITY.md)。
