# 更新说明（与原版相比）

这里的「原版」指本仓库最初导入的代码（提交 `c68cd16`）：Google 的 PaperOrchestra 流水线，以及最初版本的 PaperOrchestra Studio 桌面应用。

**一句话总结**：可以接入几乎任何模型平台，文献检索多了 7 个可选来源并严格遵守限速，桌面应用重新设计，新增连接测试、用量统计、引用核查和 Overleaf 导出。
**原有功能和输出全部保持不变**，新功能都是附加的，出错只会打印警告，不会让论文生成失败。

使用说明见：

- 桌面应用：[`paper-orchestra-desktop/README.md`](paper-orchestra-desktop/README.md)
- 命令行与配置参考：[`paper-orchestra/docs/PROVIDERS_zh.md`](paper-orchestra/docs/PROVIDERS_zh.md)

---

## 模型接入

- **支持 20 个内置平台**：原版的 OpenAI、Anthropic、Gemini、DeepSeek，加上通义千问、Kimi、智谱 GLM、腾讯混元 HY、百度文心 ERNIE、蚂蚁数科 Ling、美团 LongCat、硅基流动、OpenRouter、xAI、Groq、Mistral、Together，以及本地的 Ollama、LM Studio、vLLM。预设平台填 Key 即用。
- **新写法 `平台:模型名`**：例如 `qwen:qwen-max`、`ollama:qwen2.5:14b`；只按第一个冒号拆分，支持别名（`kimi`、`glm`、`dashscope` 等）。不带前缀的旧写法按原版规则识别，照常可用。
- **自定义平台 `providers.json`**：声明中转站、自建网关或其他接口，支持 OpenAI / Anthropic / Gemini 三种协议，可设置接口地址、请求头、默认模型、是否支持图片、要去掉的参数等。密钥可以引用环境变量（`api_key_env`、`${VAR}`）。文件格式有误时给出中文提示，并指出是第几个平台。示例见 `paper-orchestra/providers.example.json`。
- **auto 分工可以自定义**：
  - `PO_AUTO_MODEL`：所有 `auto` 任务统一用一个模型；
  - `PO_TASK_MODEL_<任务>`：单独指定某个任务（大纲、正文、评审等 12 个任务）；
  - `PO_DEFAULT_PROVIDER`：认不出平台的模型名发到哪里；
  - 只配了预设平台或自定义平台（没有官方四家的 Key）时，`auto` 自动使用该平台的默认模型。
  - 优先级：工作台填的具体模型 → `PO_TASK_MODEL_<任务>` → `PO_AUTO_MODEL` → 按 Key 自动分工。桌面应用工作台的「模型」一栏改为「按任务指定模型」，可直接修改并标出每步实际生效的来源（2026-11-19：替换了只看统一模型的「auto 分工预览」，并去掉和它重复的 Writer / Reflection / Plotting 三个整体模型框；命令行的 `--writer_model_name` 等参数照旧可用）。
  - **每一步都能自选**（2026-11-19）：除原来 12 项外，又拆出文献相关性筛选、参考图检索、图表风格优化、示意图代码兜底、图注撰写 5 个细分步骤（没填时跟随所属那一步，行为不变），加上「文献联网检索」（Gemini 模型、`deepseek-flash` 的 DeepSeek 联网搜索，或 `off`）和「示意图生成」（图像模型，`PO_TASK_MODEL_IMAGE`），共 19 步。
- **DeepSeek**（2026-11-19）：`deepseek-flash` 按 DeepSeek 官方文档改为能看图的模型（之前一律当纯文本，会丢掉图片），并能用 DeepSeek 联网搜索找文献；其它 DeepSeek 模型仍按纯文本处理。
- **2026-11-19 平台调整**：新增腾讯混元 HY（TokenHub，`hunyuan:hy4`）、百度文心 ERNIE（千帆 v2，`qianfan:ernie-5.1`）、蚂蚁数科 Ling（DTMaaS，`antdigital:ling-3.1-flash`）、美团 LongCat（`longcat:LongCat-2.5`）；后两个不参与 `auto`。豆包、MiniMax、云知声移出内置列表，`.env` 里原有的 Key 不删，只是不再自动识别（需要时可写进 `providers.json`）。
- **每个平台通用的两个变量**：`<NAME>_BASE_URL` 改接口地址，`<NAME>_DEFAULT_MODEL` 改默认模型。
- **图片输入自动适配**：纯文本模型自动去掉图片；不确定是否支持图片的平台会先带图请求，失败后去图重试并记住。
- **图片太大时自动缩小**（2026-11-19）：按各平台视觉文档的像素和大小上限，在发送前把超限的图片等比缩小，而不是整张丢掉（例如美团 LongCat 单张超过 5,242,880 像素会返回 400）；仍报错时按报错给出的上限再缩小重发。自定义平台可写 `image_max_*` 字段。一次请求的图片合计限制在约 12 MB，上传卡住 2 分钟即缩小重发（修复格式检查把 12 张整页大图发给千问后卡住一个多小时的问题）。
- **图像模型**：写明 `openai:gpt-image-1` 这类 `平台:模型名` 时，可以用 OpenAI 兼容的图像接口画示意图；只写模型名时保持原版规则（仅 Gemini）。图像模型填 `auto` 或留空时，有 Gemini Key 就用 `gemini-3-pro-image`，没有就用 Matplotlib 代码画（2026-11-19 修正：之前 `auto` 一律退回 Matplotlib）。
- **启动前检查密钥**：命令行在第一次调用模型之前，就会检查每个要用到的模型有没有 Key，缺了直接告诉你该填哪个变量。

## 文献检索

- **8 个检索来源**：原版的 Semantic Scholar、arXiv，加上 OpenAlex、Crossref、dblp、Europe PMC、PubMed、CORE。用 `PO_SCHOLAR_SOURCES` 选择和排序。
- **严格遵守限速**：
  - Semantic Scholar 每秒最多 1 次（默认间隔 1.1 秒，可用 `S2_MIN_INTERVAL_SEC` 调整），**在线程和进程之间共享**，同时跑多篇论文也不会超限；
  - arXiv 每 3 秒 1 次（arXiv 官方要求），其他来源各有自己的限速；
  - 收到 HTTP 429 时按 `Retry-After` 退避（最多 60 秒）；连续 3 次 429 暂停该来源 60 秒，连续 3 次连不上暂停 5 分钟。
  - 2026-11-19：429 后自动放慢、成功后逐步恢复；连续 2 次 5xx 暂停该来源 2 分钟；要求等待超过 10 秒时先改问其它来源；超时显示为灰色的重试提示；逐篇核实默认最多 15 分钟（`PO_ENRICH_TIME_BUDGET_MIN`）。
- **默认结果和原版一致**：候选论文仍先查 Semantic Scholar（结果少时补查 arXiv），逐篇核实仍以 Semantic Scholar 为准；其他来源只在 Semantic Scholar 被关闭、连不上或结果太少时补充。
- **记录文献出处**：每条参考文献的 DOI、链接、来源写入 `literature_agent_output/references_meta.json`，`references.bib` 保持原样（不加 doi / url 字段，避免某些 `.bst` 样式出错）。
- `autoraters/citation_f1.py` 也改用同一个 Semantic Scholar 限速器。

## 界面（桌面应用）

- **全新设计**：编辑出版风格（暖白纸色背景、墨蓝主色、衬线标题），支持浅色和深色模式；新的应用图标、侧边栏标志和网页图标。
- **「环境与密钥」重做**，分为：本地环境 / 模型服务（官方接口、预设平台卡片、`providers.json` 编辑器，带校验、示例和格式化）/ 路由（统一 auto 模型、默认平台、按任务指定）/ 文献检索（开关、排序、Key）/ 其他环境变量 / 邮件通知 / 诊断。
- **保存设置更稳妥**：写 `.env` 时保留注释、引号、行内注释和原来的换行符，正确处理带引号和多行的值；如果文件在读取后被别的程序改过，会拒绝覆盖并提示；在 macOS 上新建的 `.env` 只有本人可读。
- **工作台**：模型栏是 19 步「按任务指定模型」（支持 `平台:模型名`，和环境页同一份设置，标出每步实际生效的来源）；缺 Key、平台没有默认模型、纯文本模型会丢掉图片等情况会提前提示。
- **导入填写单**（2026-11-19）：从 PDF / Markdown / Word 填写单自动拆出标题、模板、绘图设置、研究思路和实验记录，预览勾选后再填入，不用再一块块复制粘贴。
- **文献截止日期改为自动**（2026-11-19）：工作台不再让人填，每次开始运行时自动取到当月为止（含本月新发表的文献）；旧填写单里的截止日期会被安静地跳过。
- **运行与日志**：阶段进度条，可按阶段或关键词筛选日志，自动滚动。
- **成果与 PDF**：按「论文 / 过程记录 / 核查与用量」分组浏览。
- **侧边栏**：显示 Python、LaTeX、模型密钥、文献检索是否就绪。
- **自动查找仓库**：在 Windows 和 macOS 上多找几个常见位置（如 `桌面\PaperOrchestra_源码\paper-orchestra`、`~/Documents/paper-orchestra`）。

## 新功能

- **测试连接**（「环境与密钥 → 诊断」）：向每个已配置平台发一次极小的请求（最多约 8 个输出 token），并测试每个文献来源，显示状态、延迟、中文错误原因。命令行版本：`python tools/check_providers.py --ping --scholar [--json]`。
- **用量统计**（「成果与 PDF → 核查与用量」）：每次运行写出 `usage_report.json`，记录各模型的调用次数、失败次数和 token 用量；运行中实时更新，运行失败或被停止也会保留。
- **实时费用（加元）**（2026-11-19）：按各平台官方标价和加拿大央行汇率，把所有模型的花费换算成加元。运行页实时显示「本次已花约 C$…」，运行列表显示每次的费用和累计，用量页按模型列出明细。区分缓存命中、长上下文档位、生成图片和联网搜索；不额外调用模型，价目表和汇率一天最多联网更新一次（免费公开数据，离线用缓存）。是按标价的估算，免费额度和折扣不计。
- **引用核查**（同上）：最终 PDF 生成后离线核对正文引用和参考文献，找出缺失的键（PDF 中会显示为「[?]」）、未被引用的条目，并把每条文献标为「已核验 / 仅元数据 / 未核验」。结果写入 `citation_audit.json` 和中文报告 `citation_audit.md`。
- **导出 Overleaf 包**：把最终 LaTeX 源码、参考文献、样式文件和图表打包成 zip，上传到 Overleaf 即可编译。只放主文档，不混入中间草稿和编译垃圾。
- **Windows 打包脚本** `paper-orchestra-desktop/scripts/build-windows.ps1`：检查 Node、Rust、MSVC、WebView2，一条命令生成 setup.exe 和 MSI。
- **计算化学期刊模板（5 套）**：在「模板」下拉框的「期刊模板」组里选择。
  - ACS（achemso，JCTC / J. Phys. Chem. A/B/C / JCIM，默认 JCTC，改一行即可切换）；
  - AIP *J. Chem. Phys.*（REVTeX 4.2）；
  - RSC *PCCP*（按 RSC 版式仿制的两栏模板，配 rsc.bst）；
  - Elsevier *Computational Materials Science*（elsarticle，含 Highlights 与 Graphical Abstract，改 `\journal{}` 可用于其他 Elsevier 期刊）；
  - *npj Computational Materials*（Nature 风格投稿格式，配 naturemag.bst）。
  每套都自带期刊的 cls/bst（来源与许可证见各文件夹的 `SOURCES.md`）、`guidelines.md`、TOC 图/图文摘要占位框（没有图片也能编译）、`\ce{}` 化学式和 `\SI{}` 单位（缺包时有兜底定义），以及期刊要求的声明（数据可用性、利益冲突、作者贡献、Supporting Information/ESI）。
- **写作提示词跟着期刊走**：选期刊模板时，大纲、文献综述、正文、润色和模拟审稿都会读模板里的 `venue.json`，改用该期刊的章节顺序（没有单独的 Related Work，文献讨论放进 Introduction）、摘要字数、写作风格和审稿关注点；声明类内容没有材料时写「To be completed by the authors.」，不编造。会议模板（CVPR、ICLR）的提示词一字不变。
- **从各家 API 拉取最新模型**（「环境与密钥」里的按钮，命令行 `python tools/check_providers.py --list-models`）：调用每个已配置平台免费的模型列表接口，结果合并进所有模型下拉框并保存；本机的 Ollama / LM Studio / vLLM 没配置也会探测，正在运行就把本地模型（例如 `ollama:gemma4:12b-it-qat`）列出来。
- **auto 默认模型更新到当前版本**：Claude `claude-sonnet-5-5`，DeepSeek `deepseek-v4-pro` / `deepseek-flash`，OpenAI `gpt-6.2-sol` / `gpt-6.1-terra`，Gemini `gemini-4-argon` / `gemini-3.8-flash`；图像模型 `gemini-3-pro-image`（旧的 `gemini-3-pro-image-preview` 已停用，会自动换成正式版）。
- **诊断页「测试模型」可以直接改**：「测试连接」结果表里每个平台的模型格子都能改（例如免费档没有额度时换成 flash 版），清空即恢复平台默认；只影响测试，不改写论文用的模型。命令行：`--ping-model 平台=模型`。
- **Nature Portfolio 期刊模板与投稿体检（来自 `Nature-Paper-Skills`）**：
  - 新增 3 套 Nature 系列期刊模板：`journal-nature`（*Nature*）、`journal-nature-methods`（*Nature Methods*）、`journal-nature-biotech`（*Nature Biotechnology*），复用 `naturemag.bst` 与可独立编译的投稿版式。
  - 期刊模式流水线增强：在 `utils/venue_profile.py` 与 `plotting_agent` 中接入 Claim-Driven 结构约束、去 AI 味与严格破折号/分号配额、统计报告完整性与 Nature 图表字体/色板规范（会议模板提示词保持一字不变）。
  - 投稿工具新增「论文体检 (Nature)」（命令行 `python tools/submission_tools.py manuscript-audit RUN_DIR`）：离线检查正文与摘要字数（剥离注释、草稿标记和浮动体）、AI 写作痕迹与标点节奏、图表引用与图注开头、草稿占位符、统计要素与期刊必备声明。

## 修复

- **日志泄露密钥**：
  - 原版启动时会打印 Semantic Scholar 的 Key（`Using S2 API Key: ...`，`citation_f1.py` 里也有一处），现在只显示「set / not set」；
  - 模型接口出错时，原版会把接口返回的错误原文打进日志，有的中转站会在错误里回显密钥，现在会打码；
  - 命令行对所有输出（包括报错堆栈）做密钥打码，桌面应用在显示和保存日志前再打码一次。
- **Semantic Scholar 限流**：原版只在逐篇核实时每次等 1 秒，而候选搜索是 10 个线程并行的，容易超过 1 次/秒而收到 429；现在所有请求共用一个限速器。
- **arXiv 请求间隔**：原版连续请求没有间隔，不符合 arXiv 每 3 秒 1 次的要求；现在会自动等待。
- **示意图为空**：原版调用图像模型没拿到图片时直接返回空结果；现在会改用 Matplotlib 代码画一张示意图（原版在没有 Gemini Key 时用的就是这个办法）。

- **限流 / 繁忙不再算「失败」**：文献源返回 HTTP 202（dblp 排队中）、408、429、5xx 时，正式检索会等一会儿再试（202 以前直接当成失败）；诊断页把这些显示成黄色的「暂时繁忙」。模型平台超时或过载（5xx、529、Gemini 的 DEADLINE_EXCEEDED）同样显示为「暂时繁忙」；测试 Gemini 时让它少思考，并在时间够时自动再试一次。额度不足的错误会提示换个测试模型。
- **Gemini「测试连接」报 not found for API version v1**：google-genai 3.x 默认走 v1 接口，预览版模型只在 v1beta 上；用 API Key 时现在固定走 v1beta（Vertex AI 不受影响，设了 `GOOGLE_GENAI_API_VERSION` 时以它为准）。

## 兼容性保证

改动遵守一条原则：**原有功能和输出必须和以前完全一样**。具体来说：

- 没有修改任何智能体的提示词、流程控制、LaTeX 模板，也没有改变命令行参数的含义；
- 原有输出文件的名字、位置、内容不变；新文件（`usage_report.json`、`citation_audit.json`、`citation_audit.md`、`references_meta.json`）都是额外生成的；
- 所有新步骤都包在 `try/except` 里，出错只打印一条警告，论文照常生成；
- 原版的 `.env` 写法、模型名写法、`auto` 分工规则照常有效；只认 `GEMINI_API_KEY`，不会因为电脑上有 `GOOGLE_API_KEY` 而改变行为；
- 运行日志里桌面应用用来判断进度的那几行没有变化。

性能上唯一的差别：文献检索阶段变慢了。在端到端测试（模拟环境）中，整次运行从约 16 秒变为约 47 秒，多出来的时间几乎都在文献检索阶段，主要是 arXiv 每次请求之间的 3 秒等待。这是有意为之：严格遵守限速，换来的是不再被限流。

已知但**未改动**的原版问题（保持原样以免影响输出）：

- 绘图时两张图在不同线程里共用 matplotlib 的全局状态，偶尔会让其中一张图尺寸不对或空白（`utils/paper_banana_utils.py`）；
- 文献检索和绘图同时进行，桌面应用的阶段标签可能在这两者之间来回切换，但最终都会正常走到「完成」；
- PaperBanana 的参考图库（`PaperBananaBench`，约 266 MB）和 NeurIPS 配图风格指南都不在仓库里，所以「参考图检索」从未运行、「套风格」用的是空白指南（原版也一样）。2026-11-19 起不再每张图刷一条 `Warn`，改为每次运行各提示一行 `Note:`（灰色，不算警告）；默认不下载该图库。

---

## 守护原有行为的测试

所有测试都在本地运行，不访问真实的模型平台和学术数据库（使用本地模拟服务器）。

### 一、Python 单元测试（90 个）

```bash
cd paper-orchestra
python -m unittest discover -s tests
```

| 测试文件 | 守护的原有行为 |
|---|---|
| `tests/test_providers.py` | 原版模型名照旧路由（`claude-*`、`gpt-*`、`o3`、`deepseek-*`、`gemini-*`，以及认不出的名字默认发给 Gemini）；带标签或前后缀的旧名字（如 `gpt-oss:20b`、`azure-gpt-4o`）仍发往原平台；`GOOGLE_API_KEY` 不算 Gemini Key；只写模型名时图像生成仍然只用 Gemini；DeepSeek 的纯文本请求和原版一字不差；文献候选搜索的结果数量和理由文字与原版一致；逐篇核实只要 Semantic Scholar 能回答就只用它；限速器跨线程共享且退避有上限 |
| `tests/test_references_meta.py` | 新增的 DOI / 链接字段不进入 `model_dump()`，写作提示词不变；`references.bib` 与原版 `_generate_bibtex` 逐字节相同；`references_meta.json` 写失败不影响其他输出 |
| `tests/test_run_reports.py` | 两个 `write_single_paper` 的参数、返回值、异常原样透传；报告写入失败绝不让运行失败；运行失败时仍写用量报告、不写引用核查 |
| `tests/test_pipeline_reports.py` | 用模拟智能体跑完整的 `paper_writer` 和 `paper_writer_with_plotting`：原有输出文件齐全（含 `time_breakdown.json`），`references.bib` 不含 doi 字段，返回值不变；重跑失败时会删掉旧的核查结果 |
| `tests/test_usage_tracker.py` | 统计用量不改变请求结果：失败的请求照常抛出原来的异常；统计代码本身出错也不会影响调用；多线程计数准确 |
| `tests/test_log_redaction.py` | 普通日志文字原样输出；只打码密钥；`providers.json` 损坏时打码功能也不会出错 |
| `tests/test_citation_audit.py` | （新功能）各种 `\cite` 写法的解析、状态判定、找不到文件时的回退 |
| `tests/test_check_providers.py` | （新功能）连通性检查的输出格式、超时、坏配置文件、错误信息打码 |

### 二、端到端回归测试（与原版逐项对比）

```bash
cd paper-orchestra
python tests/e2e/run_e2e.py              # 当前代码，5 个场景 + 连通性检查
python tests/e2e/run_e2e.py --baseline   # 另外在临时 git worktree 里跑原版 c68cd16，并逐项对比
```

- 用和桌面应用完全相同的参数、工作目录和环境变量，以子进程方式运行 `paper_writing_cli.py`；
- 模型由本地模拟服务器扮演（识别 21 种阶段请求），学术 API 也是模拟的，任何真实网络请求都会被拦截并判为失败；
- 有 `pdflatex` 时用真实 LaTeX 编译（没有时可用 `--latex stub`，会醒目提示）。

| 场景 | 内容 |
|---|---|
| `legacy` | 只用原版支持的写法：`OPENAI_API_KEY` + `OPENAI_BASE_URL`，`gpt-*` 模型 |
| `noplot` | 关闭绘图，提供一张用户图片和 `info.json` |
| `faults` | 故意让所有新报告出错（统计和核查抛异常、报告路径都是文件夹），论文必须照常完成 |
| `custom` | 通过 `providers.json` 声明的自定义平台 |
| `auto` | 所有模型为 `auto`，配合 `PO_AUTO_MODEL` |
| `check_providers` | 连通性检查的输出格式，不加 `--ping` 时不发任何请求，每次 ping 不超过 8 个输出 token |

每次运行检查数十项，包括：

- 退出码为 0、没有报错堆栈、日志里没有出现测试密钥；
- 原有每个输出文件的内容：大纲、`references.bib`（且没有 doi / url 字段）、`citation_map.json`、绘图结果和评审轮次、初稿、润色日志（v1 接受、v2 回退）、最终 `.tex`、`time_breakdown.json`；
- `final_paper.pdf`：与润色目录里的 PDF 相同，引用、交叉引用、图片全部正常；
- 每个阶段调用模型的次数、每个角色用的模型、发给视觉阶段的图片数量；
- 桌面应用的进度判断（从 `runner.rs` 里取出 `detect_stage()` 回放日志）最终走到「完成」；
- 新报告的格式与桌面应用的读取代码（`src/runReports.ts`）完全对得上，用量数字与模拟服务器实际收到的请求一致。

`--baseline` 对比结果：原版和当前代码都通过；`legacy` 场景 13 个输出文件、`noplot` 场景 11 个输出文件**内容完全相同**；
当前代码只多出 `usage_report.json`、`citation_audit.json`、`citation_audit.md`、`literature_agent_output/references_meta.json` 四个文件；**没有回归**。
这个对比也经过验证：在副本里故意植入 3 个错误，它能逐一报出来。

### 三、桌面应用测试

```bash
cd paper-orchestra-desktop
npm run build                 # TypeScript 类型检查 + 前端构建
cd src-tauri && cargo test    # Rust 单元测试 56 个（另有 2 个需要真实环境的测试默认跳过）
```

守护原有行为的 Rust 测试：

- `sqlite_crud_and_cascade_work`、`resolve_safe_subpath_blocks_traversal`、`env_round_trip_preserves_comments_and_updates_keys`：原版就有的三个测试，保持通过；
- `write_preserves_comments_quoting_and_line_endings`、`keeps_inline_comments_and_empty_placeholders`、`multi_line_values_are_kept_verbatim`、`refuses_to_save_over_files_changed_since_read`：保存设置不会弄坏已有的 `.env`；
- `providers_json_*`、`matches_python_registry_order`、`custom_provider_overrides_builtin_of_same_name`、`google_api_key_is_not_a_gemini_key`：桌面端对 `providers.json` 和密钥的判断与 Python 完全一致；
- `run_log_lines_mask_keys_but_keep_run_folder_paths`：日志打码不影响运行目录路径的显示；
- `repo_dir_candidates_per_os`：在各系统上查找仓库的顺序；
- 导出相关（`selects_final_sources_and_excludes_build_junk`、`symlinks_are_not_followed`、`refuses_unsafe_destinations` 等）：导出只读取运行目录，不修改其中任何文件。
