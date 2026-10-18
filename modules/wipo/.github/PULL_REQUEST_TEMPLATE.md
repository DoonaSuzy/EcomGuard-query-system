## 提交前自查

- [ ] `python -m ruff check app tests tests_new cli.py` 无输出（All checks passed）
- [ ] `python -m pytest tests_new -q` 全绿
- [ ] `python -m pytest tests -q` 全绿（改了前端或 Web 层时必须跑）
- [ ] 新增/修改的接口同步更新了 `docs/api-schema.json`
- [ ] 新增的已知坑已写进 `AGENTS.md`
- [ ] 提交信息遵循 Conventional Commits（`fix:` / `feat:` / `docs:` / `test:` / `perf:` / `refactor:`）

## 这个 PR 做了什么

<!-- 一两句话说明动机与效果，不要只罗列改了哪些文件 -->

## 改动类型

- [ ] 缺陷修复
- [ ] 新功能
- [ ] 文档
- [ ] 测试
- [ ] 重构（不改变外部行为）

## 验证方式

<!-- 贴关键命令与输出，例如 110 passed -->

## 风险与回滚

<!-- 哪些地方可能受影响；出问题时如何回退 -->
