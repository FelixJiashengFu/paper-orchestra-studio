# PaperOrchestra Studio — WorkBuddy / CodeBuddy Plugin

Tencent WorkBuddy and CodeBuddy plugin (v1.2.0) with the same feature set as the Claude Code, Antigravity, Codex and Qoder bundles: the shared zero-dependency Python MCP server (**22 model tools + 1 app-only snapshot / 6 resources / 5 prompts**), the Studio desktop bridge server (71 tools, Node), **12 workflow skills**, and the `paper-orchestra-auditor` subagent.

## 目录结构 / Layout

```text
plugins/workbuddy/
├── .codebuddy-plugin/plugin.json   # 插件清单
├── .mcp.json                       # 两个 stdio MCP 服务（${CODEBUDDY_PLUGIN_ROOT} / ${CODEBUDDY_PLUGIN_DATA}）
├── server/paper_orchestra_mcp.py
├── mcp/{server,studio,tools}.mjs
├── assets/ui/{workbench,tool-tree}.html
├── agents/paper-orchestra-auditor.md
└── skills/<12 skills>/SKILL.md
```

## 安装 / Install

仓库根目录带有 `.codebuddy-plugin/marketplace.json`，可以把整个仓库作为一个插件市场添加。

- **WorkBuddy 桌面端**：插件 → 添加市场（选择本仓库目录或 GitHub 地址）→ 安装 `paper-orchestra-studio`。也可以用 `python3 plugins/scripts/install.py --platform workbuddy` 生成 zip 后本地安装。
- **CodeBuddy CLI**：

```bash
codebuddy --plugin-dir /path/to/paper-orchestra-studio/plugins/workbuddy     # 仅当前会话
codebuddy plugin install paper-orchestra-studio@paper-orchestra-studio      # 添加市场之后
```

依赖：`PATH` 中有 `python3`（3.9+）；桌面桥接服务需要 `node` 20+。Windows 上若没有 `python3`，把 `.mcp.json` 里的 `"command": "python3"` 改成 `"python"`。

## 说明 / Notes

- 插件数据目录由 `${CODEBUDDY_PLUGIN_DATA}` 提供，升级插件不会丢失任务记录。
- 宿主不渲染 MCP Apps 时，`po_open_workbench` / `po_open_tool_tree` 返回文字摘要。
- 校验：`python3 plugins/scripts/verify_platforms.py`。
