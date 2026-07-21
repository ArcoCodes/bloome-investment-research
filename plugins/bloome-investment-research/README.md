# Bloome Investment Research Plugin

Cross-runtime, evidence-first investment research for Codex and Claude Code.

- Codex or Claude Code is the reasoning runtime; no separate model key is required.
- `RESEARCH_API_TOKEN` authenticates access to the private Bloome research gateway during beta.
- The shared MCP server provides the same research and validation tools in both hosts.
- Codex renders the native PiP/fullscreen workbench. Claude Code returns progress, evidence, artifacts, and `reportPath` without injecting the complete HTML report into model context.
- Bloome styling applies to the optional workbench only.
- `skills/investment-research/assets/template.html` remains the final report's visual source of truth.

Runtime manifests are generated from `plugin.config.json`; do not edit `.codex-plugin/plugin.json`, `.claude-plugin/plugin.json`, or either marketplace by hand.

## Verify

```bash
npm run verify
npm run test:ui
python3 ~/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py .
claude plugin validate ../.. --strict
```

## Try in either host

```text
研究 AI 推理需求对 NAND 价格周期的影响，并生成完整研报。
打开当前项目的 Bloome Research 工作台。
验证当前研报是否满足全部输出要求。
```

Use `npm run generate:manifests` after release metadata changes. See the repository's `docs/architecture.md` for extension boundaries and the release flow.
