# 安全政策

## 报告漏洞

如果你发现了安全漏洞（凭据泄露、远程代码执行、可被利用的注入等），请**不要**开公开 issue。

请通过 [GitHub Security Advisory](https://github.com/DoonaSuzy/WIPO_and_Brand/security/advisories/new)
私下报告。我们会在 7 天内回应，并在修复发布后向你致谢（除非你希望匿名）。

## 已知风险与本项目的应对

### 1. WIPO 响应解密密钥

`wipo_client.py` 需要一个 48 字节密钥才能解密 WIPO BrandDB 的响应体，
其中 16 字节前缀来自 WIPO 前端实现，未在公开文档中提供。

**本项目的处理方式**：该前缀可通过环境变量 `WIPO_DECRYPT_KEY_PREFIX` 覆盖，
代码里只保留一个内置回退值。如果你的使用场景对该值敏感，请：

1. 设置环境变量，不要改代码；
2. 不要把设置了该变量的 `.env` 提交进仓库（`.gitignore` 已排除 `.env` 与 `config.json`）；
3. 意识到该值一旦进入 git 历史就无法真正清除，只能重写历史。

### 2. `config.json` 含 SMTP 凭据

邮箱密码以明文存放在 `config.json`。该文件**已被 `.gitignore` 排除**，
仓库历史中不存在它（可通过 `git log --all --diff-filter=A -- config.json` 验证）。

请单独备份该文件，不要放进容器镜像或公开发布。

### 3. WIPO 服务条款

本项目调用的 `api.branddb.wipo.int` 是 WIPO Global Brand Database 的私有接口。
本项目与 WIPO 无任何隶属或授权关系，接口随时可能变更或停止服务。
在生产环境依赖前请自行评估合规风险。

## 依赖安全

依赖版本在 `pyproject.toml` 中锁死。升级请走 PR，CI 会在 Python 3.11 / 3.12 / 3.13
三个版本上跑完整测试。
