# PaperOrchestra Studio for Meta Muse（用 Muse 自己的额度，不调其他 LLM API）

这是 PaperOrchestra Studio 的 Muse 定制版插件包，面向 Meta 的 **Muse Code**（`muse` 命令行，Muse for Mac 附带）。功能与其他平台完全一致（24 个 `po_*` 工具、71 个桌面桥接工具、12 个工作流技能），外加一处关键区别：

> **所有需要大模型的步骤都交给你已登录的 Muse 自己完成**，不调用 OpenAI、Anthropic、Gemini、DeepSeek 等任何其他 LLM API，也不需要任何 API Key。

## 它是怎么做到的

插件用 `server/muse_launcher.py` 启动共享的 MCP 服务，并设置 `PO_HOST_AGENT=muse`。此后后端每一次模型调用（论文流水线、Research Studio SOP、数据分析、绘图代码、LaTeX Copilot、摘要润色）都变成一次无界面调用：

```
muse exec --json --prompt-file <提示词> --workspace <空临时目录> --no-session-log \
          --preset empty --disable-shell --disable-write --disable-web-tools \
          --disable-reminders --disable-request-user-input --no-foreign-personal-context
```

* 用的是 `muse` 自己保存的登录（`~/.config/muse/auth.json`），本插件不读取它，也不向 `muse` 传任何密钥；环境里即使有 `META_API_KEY` 也会被剔除。
* Shell、写文件、联网工具全部关闭，沙箱与审批保持开启，工作目录是用完即删的空目录：Muse 在这里只当「文本模型」用。
* `.env` 里其他厂商的 Key 会被忽略；即使某个项目里写着 `claude-…` 或 `gpt-…` 之类的模型名，也改由 Muse 完成（严格模式，默认开启）。
* 联网搜索（Gemini / DeepSeek 的搜索工具）与图像生成模型同样关闭：文献改走学术检索源（Semantic Scholar、OpenAlex、arXiv、Crossref 等，它们不是 LLM API），示意图改用 Matplotlib / SVG 代码绘制。

## 安装

前提：已安装并登录 Muse Code（`muse --version` 能运行），本机有 `python3` 和 `node`，并且后端仓库在 `~/paper-orchestra-studio`（或用下面的配置文件指定位置）。

```bash
muse plugins validate plugins/muse --json     # 应输出 "valid": true
muse plugins install  plugins/muse --scope user
muse plugins approve  paper-orchestra-studio  # 信任其中的技能与两个 MCP 服务
```

重新打开 Muse，让它调用 `po_check_environment`：返回里的 `hostAgent.name` 应为 `muse`、`cliFound` 为 `true`。

### 可选配置：`~/.config/muse/paper-orchestra.env`

Muse 启动 MCP 服务时只传很少几个环境变量，所以其他平台写在 `env` 里的设置，在这里写进这个文件（每行 `KEY=VALUE`）：

```
PAPER_ORCHESTRA_REPO=~/paper-orchestra-studio/paper-orchestra
PAPER_ORCHESTRA_PYTHON=/opt/anaconda3/bin/python3
PO_DEFAULT_PDF_DIR=/path/to/pdf
PO_DEFAULT_HTML_DIR=/path/to/html
MUSE_DEFAULT_MODEL=muse-spark-1.2
PO_MUSE_REASONING_EFFORT=medium
```

| 变量 | 作用 |
| --- | --- |
| `MUSE_DEFAULT_MODEL` | 所有步骤用的模型；不填则用 Muse 自己当前设置的模型 |
| `PO_MUSE_REASONING_EFFORT` | `none`…`ultra`；不填则用 Muse 自己的设置 |
| `PO_MUSE_CONCURRENCY` | 同时进行的调用数，默认 2 |
| `PO_MUSE_TIMEOUT` | 单次调用超时秒数，默认 1800 |
| `PO_MUSE_MAX_STEPS` | 单次调用的模型步数上限，默认 8 |
| `MUSE_CLI` | `muse` 可执行文件路径（默认从 PATH 和 `~/.local/bin` 查找） |
| `PO_HOST_AGENT_STRICT=0` | 放宽：Muse 仍是默认，但显式写出的其他厂商模型名恢复走各自 API |
| `PO_HOST_AGENT=off` | 关闭本模式 |

不要把任何 LLM Key 写进这个文件：本模式一个都不会用。

### 不用插件、只注册 MCP 服务

把下面这段并入 `~/.config/muse/settings.json`（键名是驼峰的 `mcpServers`；不要同时保留旧的 `mcp_servers`，两者并存时 Muse 会整段丢弃）：

```json
"mcpServers": {
  "paper-orchestra-studio": {
    "type": "stdio",
    "command": "python3",
    "args": ["/absolute/path/to/plugins/muse/server/muse_launcher.py"],
    "env": {"PO_HOST_AGENT": "muse"},
    "mode": "optional"
  }
}
```

技能可用 `muse skills install plugins/muse/skills/<技能名>` 逐个安装。

## 用量与费用须知

* 一次完整的论文流水线会发出几十到上百次模型调用，全部计入你的 Muse 账号。开始前可在终端运行 `muse usage` 查看额度。
* Muse Code 每次调用都会带上它自己的系统提示词与技能目录。在 Muse Code 1.4.25 上实测，一句话的提示词也要约 2.2 万输入 Token（已用 `--preset empty` 压到最低，不加是 3 万）。按订阅次数计费时无所谓，按 Token 计费时要留意。
* 连通性检查（`po_check_providers`）对 Muse 只运行 `muse --version`，不发送提示词，不消耗额度。
* 用量报告里 Muse 的 Token 数来自 `muse exec --json` 的 `run.usage` 事件；价目表里没有 Muse，所以不估算金额。

## 与其他平台的差异

| 项目 | 其他平台 | Muse 版 |
| --- | --- | --- |
| 后端大模型 | `.env` / `providers.json` 里的各家 API | 已登录的 `muse` 命令行，仅此一家 |
| 图片输入 | 支持视觉的模型可看图 | 仅文本（图片会被省略并注明） |
| 联网搜索找文献 | Gemini / DeepSeek 搜索工具可选 | 关闭，走学术检索源 |
| 图像生成 | Gemini 图像模型可选 | 关闭，用代码绘图 |
| 审计子智能体 | 子智能体（或技能） | 技能 `po-submission-auditor`（Muse 插件没有子智能体格式） |
| 额外技能 | 无 | `po-muse-host`：说明并自检本模式 |

Studio 桌面应用自己发起的运行仍按桌面应用的设置走。如果希望桌面应用也只用 Muse，在 `paper-orchestra/.env` 里加一行 `PO_HOST_AGENT=muse`（这会同时影响其他平台的插件）。
