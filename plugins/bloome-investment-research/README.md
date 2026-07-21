# Bloome Investment Research Plugin

Codex-native evidence-first investment research with a responsive Bloome workbench.

- Codex is the reasoning runtime; no separate model key is required.
- `RESEARCH_API_TOKEN` authenticates access to the private Bloome research gateway during beta.
- The bundled MCP server provides research tools and the native workbench UI.
- Bloome styling applies to the workbench only.
- `skills/investment-research/assets/template.html` remains the final report's visual source of truth.

## Verify

```bash
npm test
npm run test:ui
python3 ~/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py .
```

## Try in Codex

```text
研究 AI 推理需求对 NAND 价格周期的影响，并生成完整研报。
打开当前项目的 Bloome Research 工作台。
验证当前研报是否满足全部输出要求。
```
