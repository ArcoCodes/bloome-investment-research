# Bloome Investment Research

面向 Codex 与 Claude Code 的金融投研插件。它把可追溯的投研工作流、受控研究数据接口和统一报告契约打包成同一份共享实现，并为两个宿主生成各自的插件清单和 marketplace。

宿主使用用户现有账号完成规划、推理和写作，不需要额外的模型 API Key。Investment Research Skill 自带的研报结构、引用规则与 HTML 模板保持为最终输出标准。Codex 还会渲染 Bloome PiP / fullscreen 工作台；Claude Code 返回同一工作区的进度、证据和 `reportPath`，直接读取最终报告，不假装支持 Codex 专属面板。

## Codex 安装

当前仓库为 `ArcoCodes` 组织私有内测源。安装者需要先获得仓库访问权限，并在本机配置 GitHub 凭证。

```bash
codex plugin marketplace add ArcoCodes/bloome-investment-research
```

随后在 Codex Desktop 的 **Plugins** 页面选择 **Bloome Research** 并安装 **Bloome Investment Research**。Codex CLI 用户也可以通过 `/plugins` 安装。安装后需要新建一个任务，插件的 skill 和 MCP tools 才会载入。

## Claude Code 安装

同一仓库同时包含 Claude Code marketplace：

```bash
claude plugin marketplace add ArcoCodes/bloome-investment-research
claude plugin install bloome-investment-research@bloome-research
```

本地开发时可以跳过安装，直接运行：

```bash
claude --plugin-dir ./plugins/bloome-investment-research
```

修改插件组件后，在 Claude Code 中执行 `/reload-plugins`。

## 账号与研究额度

模型推理继续使用 Codex 或 Claude 的现有账号，不需要额外模型 Key。首次调用研究工具时，本地 MCP 会打开 Bloome Finance：用户通过 Google 或邮箱登录并授权当前设备，完成后工具自动继续，不需要复制长期 token，也不会把 MCP 改成远程服务。

每个完成验证的账号终身赠送 1 次研报额度。新 research workspace 的首次数据请求扣除 1 次；同一 run 内后续搜索和精确读取不重复扣费，成功执行 `validate_research_workspace` 后关闭 run。额度不足时前往 Bloome Finance 购买单次包或十次包。

本地开发可用 `BLOOME_FINANCE_URL` 指向另一套 Finance 服务。生产切换时必须撤销旧共享 beta token，并让上游研究数据服务只接受 Finance 后端持有的内部密钥。

## 使用

```text
研究 AI 推理需求对 NAND 价格周期的影响，并生成完整研报。
打开当前项目的 Bloome Research 工作台。
验证当前研报是否满足全部输出要求。
```

研究产物保存在当前项目的 `.bloome/research/` 下，不应提交到业务仓库。

## 仓库结构

```text
.agents/plugins/marketplace.json
.claude-plugin/marketplace.json
plugins/bloome-investment-research/
├── .codex-plugin/plugin.json
├── .claude-plugin/plugin.json
├── .mcp.json
├── plugin.config.json
├── skills/investment-research/
├── mcp/server.cjs
├── scripts/core.mjs
└── assets/
```

## 本地验证

```bash
cd plugins/bloome-investment-research
npm ci
npm run verify
npm run test:ui
claude plugin validate ../.. --strict
```

Codex 清单还应使用 `plugin-creator` 的 `validate_plugin.py` 检查。架构边界和新增宿主流程见 [`docs/architecture.md`](docs/architecture.md)。

## 分发边界

- 当前阶段：同一私有 GitHub 仓库同时作为 Codex 与 Claude Code marketplace，适用于小规模邀请制内测。
- 正式阶段：托管公网 MCP 服务、接入 Bloome 账号体系，并提交 Codex Plugins Directory 审核。
- 插件只访问用户主动指定的研究工作区；报告文件默认留在本地项目中。
