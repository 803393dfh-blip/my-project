# GitHub Release Flow Verifier

## 简介
这是一个示例脚本，用于验证 GitHub 仓库的发布流程（环境 → 分支 → 文件 → PR → 合并目标 → 合并方式），并在 `mcpmark/release_verification/verification_report.txt` 生成标准化报告（用于 CI/人工审阅）。

## 文件列表
- `verify_release.py` — 主脚本
- `config_example.json` — 可选：替换默认 CONFIG 的示例
- `.mcp_env.template` — 本地环境变量模板（请重命名为 `.mcp_env` 并填写）
- `requirements.txt` — Python 依赖
- `.github/workflows/verify.yml` — (可选) GitHub Actions workflow

## 依赖
```bash
pip install -r requirements.txt
