# Bloome Investment Research

Codex 原生金融投研插件。它把可追溯的投研工作流、受控研究数据接口和 Bloome 白色自适应工作台打包成一个可安装的 marketplace。对话中只显示紧凑启动条，完整工作台由 Codex 原生 PiP panel 承载，研报阅读可继续展开为 fullscreen。

Codex 使用用户现有账号完成规划、推理和写作，不需要额外的模型 API Key。Investment Research Skill 自带的研报结构、引用规则与 HTML 模板保持为最终输出标准；Bloome 设计语言只用于插件工作台。

## 安装

当前仓库为 `ArcoCodes` 组织私有内测源。安装者需要先获得仓库访问权限，并在本机配置 GitHub 凭证。

```bash
codex plugin marketplace add ArcoCodes/bloome-investment-research
```

随后在 Codex Desktop 的 **Plugins** 页面选择 **Bloome Research** 并安装 **Bloome Investment Research**。Codex CLI 用户也可以通过 `/plugins` 安装。安装后需要新建一个任务，插件的 skill 和 MCP tools 才会载入。

## 内测数据访问

模型推理不需要额外密钥，但 Bloome 的受控研究数据服务需要独立的 beta token。不要把 token 写入仓库或插件源码。

在 `~/.codex/.env` 中添加：

```dotenv
RESEARCH_API_TOKEN=your-beta-token
```

如需切换测试服务，也可以设置：

```dotenv
RESEARCH_SEARCH_URL=https://your-research-gateway.example.com
```

重启 Codex Desktop 后生效。正式公开版本计划改为匿名试用额度和 Bloome 账号认证，不会要求用户手动维护长期 token。

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
plugins/bloome-investment-research/
├── .codex-plugin/plugin.json
├── .mcp.json
├── skills/investment-research/
├── mcp/server.cjs
├── scripts/core.mjs
└── assets/
```

## 本地验证

```bash
cd plugins/bloome-investment-research
npm ci
npm test
npm run test:ui
```

插件清单还可以使用 Codex 的 `plugin-creator` 验证器检查。

## 分发边界

- 当前阶段：私有 GitHub marketplace，适用于小规模邀请制内测。
- 正式阶段：托管公网 MCP 服务、接入 Bloome 账号体系，并提交 Codex Plugins Directory 审核。
- 插件只访问用户主动指定的研究工作区；报告文件默认留在本地项目中。
