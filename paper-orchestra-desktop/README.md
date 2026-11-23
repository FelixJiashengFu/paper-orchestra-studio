# PaperOrchestra Studio

PaperOrchestra Studio 是一个 **Windows / macOS 桌面应用**，给 Google 开源的多智能体论文写作框架
[PaperOrchestra](../paper-orchestra/README.md) 套上了中文图形界面：

> 写下研究思路和实验记录 → 点「启动生成」→ 得到一篇带文献综述、图表和参考文献的 LaTeX 论文与 PDF。

- 应用本身不带模型，也没有自己的服务器：模型请求从你的电脑直接发到你配置的平台。
- 密钥只保存在本机 `paper-orchestra/.env` 里，运行日志里出现的密钥会被自动打码。
- 支持 OpenAI、Claude、Gemini、DeepSeek 四家官方接口，以及通义千问、Kimi、智谱、腾讯混元、百度文心、蚂蚁数科、美团 LongCat、硅基流动、OpenRouter、本地 Ollama 等十几个平台，还可以接自己的中转站。

**目录**：[工作原理](#工作原理) · [快速开始](#快速开始) · [配置模型](#配置模型) · [文献检索来源](#文献检索来源) · [新功能](#新功能) · [自己打包安装程序](#自己打包安装程序) · [常见问题](#常见问题)

---

## 工作原理

整个项目由两个文件夹组成，建议放在一起：

```
PaperOrchestra_源码/
├── paper-orchestra/            ← Python 流水线（真正写论文的部分）
└── paper-orchestra-desktop/    ← 本应用（界面、项目管理、日志、成果预览）
```

应用在后台调用 `python paper_writing_cli.py`，流水线依次执行：

**大纲** → **文献检索 ∥ 绘图**（同时进行）→ **正文写作** → **润色**（编译 PDF、模拟同行评审、修改、版式检查）→ `final_paper.pdf`

应用有四个页面：

| 页面 | 用来做什么 |
|---|---|
| 工作台 | 新建项目，填写研究思路、实验记录、图片附件，选择模板和模型，启动生成 |
| 运行与日志 | 每次运行的阶段进度和完整日志，可以停止运行 |
| 成果与 PDF | 预览 PDF、LaTeX 源码、大纲、图表；**引用核查**、**用量统计**、**导出 Overleaf 包** |
| 环境与密钥 | 仓库位置、Python、模型密钥、文献检索来源、**连接测试** |

---

## 快速开始

你需要准备：

1. **Python 3.11 或更新版本**（用来运行流水线）；
2. **至少一个模型平台的 API Key**（任意一家都可以）；
3. **LaTeX**（可选，但强烈建议）：没有它就生成不了 PDF，润色阶段也会跳过，只能拿到 `.tex` 草稿（可以导出到 Overleaf 在线编译）。

### Windows

**① 安装 Python**

从 <https://www.python.org/downloads/> 安装 3.11 或更新版本，安装时勾选 **Add python.exe to PATH**。

**② 放好 `paper-orchestra` 文件夹**

放在下面任意一个位置，应用会自动找到（找的是里面有 `paper_writing_cli.py` 的那一层）：

- `D:\projects\paper-orchestra`
- `桌面\PaperOrchestra_源码\paper-orchestra`
- `C:\Users\<你的用户名>\paper-orchestra`，或「文档」「桌面」下的 `paper-orchestra`

放在别处也没关系，之后在应用的「环境与密钥 → 本地环境」里手动选择即可。

**③ 在仓库里建一个虚拟环境并安装依赖**

打开 PowerShell（把路径换成你自己的）：

```powershell
cd D:\projects\paper-orchestra
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

环境建在仓库里的 `.venv` 文件夹时，应用会自动使用 `.venv\Scripts\python.exe`，不用另外设置。
（不需要 `activate`，直接用 `.venv` 里的 `python.exe` 安装即可，也就不会碰到 PowerShell 的脚本执行限制。）

> 用 conda 也可以：`conda create -n paper_orchestra python=3.11`，装好依赖后在设置里把 Python 解释器选成
> `C:\Users\<你>\miniconda3\envs\paper_orchestra\python.exe`。

**④（可选）安装 LaTeX**

二选一：

- **MiKTeX**（<https://miktex.org/download>）：安装后打开 MiKTeX Console → Settings，把
  “Install missing packages on-the-fly” 设为 **Always**，否则编译时缺宏包会卡住或失败。
- **TeX Live**（<https://tug.org/texlive/>）：体积大，但宏包齐全，装好就能用。

装完后**重启应用**，侧边栏「运行环境」里的「LaTeX 编译」显示「可用」即成功。

**⑤ 安装并打开应用**

双击 `PaperOrchestra Studio_x.y.z_x64-setup.exe` 安装（没有安装包？见[自己打包安装程序](#自己打包安装程序)）。
如果弹出「Windows 已保护你的电脑」，点 **更多信息 → 仍要运行**（应用没有购买代码签名证书，这是正常提示）。

**⑥ 首次设置**（见下方[首次设置](#首次设置两个系统相同)）

### macOS

**① 安装 Python**

```bash
brew install python@3.11      # 或者从 python.org 下载安装包
```

**② 放好 `paper-orchestra` 文件夹**

自动查找的位置：`~/paper-orchestra`、`~/Documents/paper-orchestra`、`~/Desktop/paper-orchestra`、
`~/Desktop/PaperOrchestra_源码/paper-orchestra`、`~/Documents/PaperOrchestra_源码/paper-orchestra`。
放在别处就在设置里手动选择。

**③ 建虚拟环境并安装依赖**

```bash
cd ~/paper-orchestra
python3 -m venv .venv        # python3 --version 需为 3.11 或更新；否则写 python3.11
.venv/bin/python -m pip install --prefer-binary -r requirements.txt
```

应用会自动使用 `.venv/bin/python`。（`--prefer-binary` 让 macOS 11/12 也装现成的安装包，不去现场编译 OpenCV。）
venv 要放在 Mac 自带的硬盘上：exFAT 格式的移动硬盘不支持符号链接，venv 和 `node_modules` 放上去会出错。

**④（可选）安装 LaTeX**

安装 **MacTeX**（<https://tug.org/mactex/>，或 `brew install --cask mactex-no-gui`）。
应用会自动把 `/Library/TeX/texbin` 加进搜索路径，装完重启应用即可。
（BasicTeX 体积小，但缺很多会议模板需要的宏包，不建议新手使用。）
不想输管理员密码也可以装 **TinyTeX**（装在 `~/Library/TinyTeX`），应用同样能找到；
它的宏包要自己补（`tlmgr` 默认不在终端的搜索路径里，所以写全路径）：
`~/Library/TinyTeX/bin/*/tlmgr install lineno eso-pic silence cleveref enumitem caption courier times psnfss`。

**⑤ 打开应用**

把 `PaperOrchestra Studio.app` 拖进「应用程序」。在自己这台 Mac 上编译出来的应用不会被拦，可以直接打开。
从别处拷来或下载的未签名应用，第一次打开时 macOS 会拦截：

- **右键点击应用 → 打开 → 再点「打开」**；
- 如果没有「打开」按钮（macOS 15 及以后）：先双击一次，再到 **系统设置 → 隐私与安全性**，在页面底部点 **仍要打开**；
- 如果提示「已损坏，无法打开」，在终端执行：

  ```bash
  xattr -dr com.apple.quarantine "/Applications/PaperOrchestra Studio.app"
  ```

### 首次设置（两个系统相同）

1. 打开 **环境与密钥 → 本地环境**，确认：
   - 「PaperOrchestra 仓库目录」显示 **已找到 paper_writing_cli.py**；
   - 「Python 解释器」显示 **可用**；
   - 「论文输出根目录」默认是 `<仓库>/desktop_runs`，可以改。
2. 打开 **模型服务**，在任意一个平台卡片里填入 API Key，点右上角 **保存**。
3. 打开 **诊断 → 连接测试**，点 **测试连接**，看到「连接正常」就可以了。
4. 回到 **工作台**：点「新建项目」，填写你自己的研究思路和实验记录，然后点 **启动生成**。

一次完整运行要调用几十次模型，通常需要几十分钟，取决于模型速度和文献检索的限速。进度和日志在「运行与日志」里看，结果在「成果与 PDF」里看。

---

## 配置模型

所有模型设置都在 **环境与密钥 → 模型服务**，保存后写入仓库根目录的 `.env`（密钥）和 `providers.json`（自定义平台），只保存在本机。

工作台里有四个模型框：**写作 / 文献模型（Writer）**、**审稿 / 润色模型（Reflection）**、**绘图代码模型（Plotting）**、**图像模型（Image）**。每个框可以填：

- `auto`：按已配置的密钥自动分工（默认，推荐）；
- 模型名，例如 `claude-sonnet-4-6`、`gpt-4.1`；
- `平台:模型名`，例如 `qwen:qwen-max`。

### 官方四家

| 平台 | 密钥（.env 变量） | 可选：接口地址 | 模型示例 |
|---|---|---|---|
| Anthropic · Claude | `ANTHROPIC_API_KEY` | `ANTHROPIC_BASE_URL` | `claude-sonnet-4-6` |
| OpenAI | `OPENAI_API_KEY` | `OPENAI_BASE_URL`（填了就能当 OpenAI 兼容中转用） | `gpt-4.1`、`o3` |
| Google · Gemini | `GEMINI_API_KEY`，或 `VERTEX_AI_PROJECT` + `VERTEX_AI_LOCATION` | `GEMINI_BASE_URL` | `gemini-3.1-pro-preview` |
| DeepSeek | `DEEPSEEK_API_KEY` | `DEEPSEEK_BASE_URL` | `deepseek-chat`、`deepseek-reasoner`（纯文本，图片会自动省略） |

> - Gemini 只认 `GEMINI_API_KEY`，**不认** `GOOGLE_API_KEY`（和原版一致）。
> - 有 Gemini Key 时，文献检索会用 Gemini + Google 搜索找候选论文，示意图会用 Gemini 图像模型绘制（原版行为）；
>   没有时自动改用学术数据库 API 和 Matplotlib 画示意图。

### 预设平台（填 Key 即用）

这些平台都是 OpenAI 兼容接口，在「预设平台」卡片里填入密钥即可。模型名会随平台更新而变化，**以平台控制台为准**。

| 平台 | 密钥（.env 变量） | 模型写法示例 | `auto` 回退时默认用 |
|---|---|---|---|
| 通义千问 · DashScope | `DASHSCOPE_API_KEY`（或 `QWEN_API_KEY`） | `qwen:qwen-max` | `qwen-max` |
| Kimi · 月之暗面 | `MOONSHOT_API_KEY`（或 `KIMI_API_KEY`） | `moonshot:moonshot-v1-128k` | `moonshot-v1-128k` |
| 智谱 GLM | `ZHIPU_API_KEY`（或 `ZHIPUAI_API_KEY`、`GLM_API_KEY`） | `zhipu:glm-4-plus` | `glm-4-plus` |
| 腾讯混元 HY · TokenHub | `HUNYUAN_API_KEY`（或 `TOKENHUB_API_KEY`） | `hunyuan:hy4` | `hy4` |
| 百度文心 ERNIE · 千帆 | `QIANFAN_API_KEY`（或 `ERNIE_API_KEY`） | `qianfan:ernie-5.1` | `ernie-5.1` |
| 蚂蚁数科 Ling · DTMaaS | `ANTDIGITAL_API_KEY`（或 `DTMAAS_API_KEY`） | `antdigital:ling-3.1-flash` | 不参与（需自己指定） |
| 美团 LongCat | `LONGCAT_API_KEY` | `longcat:LongCat-2.5` | 不参与（需自己指定） |
| 硅基流动 · SiliconFlow | `SILICONFLOW_API_KEY` | `siliconflow:deepseek-ai/DeepSeek-V3` | `deepseek-ai/DeepSeek-V3` |
| OpenRouter | `OPENROUTER_API_KEY` | `openrouter:anthropic/claude-sonnet-4` | `anthropic/claude-sonnet-4` |
| xAI · Grok | `XAI_API_KEY`（或 `GROK_API_KEY`） | `xai:<模型名>` | 需自己填 |
| Groq | `GROQ_API_KEY` | `groq:llama-3.3-70b-versatile` | `llama-3.3-70b-versatile` |
| Mistral AI | `MISTRAL_API_KEY` | `mistral:mistral-large-latest` | `mistral-large-latest` |
| Together AI | `TOGETHER_API_KEY` | `together:<组织/模型名>` | 需自己填 |
| Ollama（本机） | 不需要；地址 `OLLAMA_BASE_URL`，默认 `http://localhost:11434/v1` | `ollama:qwen2.5:14b` | 需自己填 |
| LM Studio（本机） | 不需要；地址 `LMSTUDIO_BASE_URL`，默认 `http://localhost:1234/v1` | `lmstudio:<已加载的模型>` | 需自己填 |
| vLLM（自建） | 可选 `VLLM_API_KEY`；地址 `VLLM_BASE_URL`，默认 `http://localhost:8000/v1` | `vllm:<served-model-name>` | 需自己填 |

- 每个平台都可以用 `<平台名>_BASE_URL` 改接口地址、用 `<平台名>_DEFAULT_MODEL` 改默认模型（卡片的「高级选项」里就是这两项），例如 `QWEN_DEFAULT_MODEL=qwen-plus`。
- 「需自己填」的平台，如果想让 `auto` 用它，请在卡片里填写「默认模型」。

### 自定义平台（providers.json）

学校/实验室的中转站、自建网关、或者上表没有的平台，可以在 **模型服务 → providers.json** 里声明（会写到 `paper-orchestra/providers.json`）。直接复制下面这段，改成你的地址和模型名：

```json
{
  "providers": [
    {
      "name": "myrelay",
      "type": "openai",
      "base_url": "https://relay.example.com/v1",
      "api_key_env": "MY_RELAY_TOKEN",
      "models": ["gpt-4o"],
      "default_model": "gpt-4o"
    }
  ]
}
```

然后在 **其他环境变量** 里添加 `MY_RELAY_TOKEN = 你的密钥`，保存。之后模型框里写 `myrelay:gpt-4o` 就会走这个中转站。

- `type`：`openai`（默认，所有 OpenAI 兼容接口）、`anthropic`、`gemini`。
- `models` 里列出的模型名，不带前缀时也会优先发到这里（上例中直接写 `gpt-4o`，或 `auto` 选中了 `gpt-4o`，都会走中转站）。
  不想这样就删掉 `models` 这一行，只用 `myrelay:模型名` 调用。
- 不写 `api_key_env` 时，程序会自动读取 `<名字大写>_API_KEY`（上例为 `MYRELAY_API_KEY`）。
- 不建议把密钥直接写进 `api_key` 字段：`providers.json` 不在 `.gitignore` 里，容易被误传到网上。
- 编辑框旁边有「校验」按钮；格式不对时侧边栏会显示「providers.json 有误」，此时运行会直接失败。

完整字段说明、更多示例见 [`paper-orchestra/docs/PROVIDERS_zh.md`](../paper-orchestra/docs/PROVIDERS_zh.md)，可直接复制的示例文件是 [`paper-orchestra/providers.example.json`](../paper-orchestra/providers.example.json)。

### 「平台:模型名」写法

- **只按第一个冒号拆分**：`ollama:qwen2.5:14b` 表示平台 `ollama`、模型 `qwen2.5:14b`。
- 平台名可以用别名：`kimi` = `moonshot`，`glm` = `zhipu`，`dashscope` / `bailian` = `qwen`，`hy` / `tencent` = `hunyuan`，`ernie` / `baidu` = `qianfan`，`sf` = `siliconflow`，`grok` = `xai`，`claude` = `anthropic`。
- 不带前缀时按名称识别：含 `claude` → Anthropic，含 `deepseek` → DeepSeek，`gpt-*` / `o1` / `o3` / `o4` → OpenAI，`gemini-*` → Gemini（和原版规则一致）；
  `qwen-*`、`glm-*`、`kimi-*` 等也会发到对应平台（需要已填该平台的 Key）。
- 认不出的名字：选了「路由 → 默认平台」时发到那里；否则依次尝试已配置的自定义平台、Gemini（原版默认）、OpenRouter / 硅基流动 / OpenAI。
- 注意：`qwen:7b` 这种「平台名:数字b」会被当成 Ollama 本地模型标签，想用千问云端请写完整模型名，如 `qwen:qwen-max`。

### auto 智能分工

模型填 `auto` 时，流水线会给每个子任务挑模型：

1. 如果填了官方四家的 Key，按原版规则分工（例如大纲和审稿优先 DeepSeek-Reasoner，正文和润色优先 Claude）；
2. 一个官方 Key 都没有时，使用第一个已配置平台的默认模型（自定义平台优先）。

想自己指定，在 **模型服务 → 路由** 里设置（也可以直接写 `.env`）：

| 设置项 | .env 变量 | 作用 |
|---|---|---|
| 统一 auto 模型 | `PO_AUTO_MODEL=qwen:qwen-max` | 所有 `auto` 任务都用这个模型 |
| 按任务指定 | `PO_TASK_MODEL_<任务>=平台:模型名` | 只改某一个任务，优先于统一 auto 模型 |
| 默认平台 | `PO_DEFAULT_PROVIDER=myrelay` | 认不出平台的模型名发到这里 |

可指定的任务（`<任务>` 处写英文名，大写）：

| 任务 | 说明 | 任务 | 说明 |
|---|---|---|---|
| `OUTLINE` | 大纲规划 | `SECTION_WRITING` | 正文写作 |
| `LITERATURE_QUERY` | 文献检索词 | `PEER_REVIEW` | 同行评审 |
| `LITERATURE_WRITE` | 文献综述 | `CONTENT_REFINEMENT` | 内容润色 |
| `PLOT_PLAN` | 绘图规划 | `FORMAT_REVIEW` | 版式质检 |
| `PLOT_CODE` | 绘图代码 | `FORMAT_FIX` | 版式修复 |
| `PLOT_CRITIC` | 图表视觉评审 | `CITATION_PARSE` | 引用解析 |
| `RELEVANCE_SCREEN` | 文献相关性筛选（跟随检索词） | `PLOT_RETRIEVAL` | 参考图检索（跟随绘图代码） |
| `PLOT_STYLE` | 图表风格优化（跟随绘图代码） | `DIAGRAM_CODE` | 示意图代码兜底（跟随绘图代码） |
| `PLOT_CAPTION` | 图注撰写（跟随视觉评审） | `IMAGE` | 示意图生成（图像模型） |
| `LITERATURE_SEARCH` | 文献联网检索：Gemini 模型、`deepseek-flash` 或 `off` | | |

例：`PO_TASK_MODEL_SECTION_WRITING=claude-sonnet-4-6`、`PO_TASK_MODEL_PEER_REVIEW=deepseek:deepseek-reasoner`。
工作台的「模型」一栏就是这 19 步「按任务指定模型」（和环境页是同一份设置，对所有项目生效），每项旁边标出实际生效的来源：按任务 / 跟随 / 统一 / 自动；鼠标停在名字上能看到这一步做什么。标「跟随」的细分步骤没填时用它所属那一步的模型。原来的 Writer / Reflection / Plotting 三个整体模型框和「图像模型」框已经去掉（图像模型现在是面板里的「示意图生成」）；旧项目如果还存着具体模型，工作台会提示并可一键清除。

### 导入填写单

工作台右上角的「导入填写单」可以选一份 PDF、Markdown（.md / .txt）或 Word（.docx）填写单，自动拆出项目标题、LaTeX 模板、绘图设置、研究思路和实验记录（文献截止日期不再导入，每次运行自动收录到当月为止的文献；旧填写单里的 `PO:cutoff` 会被忽略），先列出每一栏「现在 → 导入」让你勾选，确认后才填入。认得两种写法：

- **标记行**（推荐，任何格式都行）：每一栏前面单独一行标记，到下一个标记为止：`<!-- PO:title -->`、`<!-- PO:template -->`、`<!-- PO:cutoff -->`、`<!-- PO:plotting -->`、`<!-- PO:idea -->`、`<!-- PO:log -->`、`<!-- PO:end -->`。打印成 PDF 时 HTML 注释会消失，PDF 里请写成看得见的 `[[PO:idea]]`。
- **编号 PDF 填写单**：带圆形编号、栏位标题、灰色说明和深色内容的那种版式，按标题认栏位、按颜色分开说明和内容。

---

## 文献检索来源

在 **环境与密钥 → 文献检索** 里可以开关来源、调整顺序、填写 Key。限速由程序自动遵守，不需要你操心。

| 来源 | Key / 环境变量 | 限速 | 适合学科 |
|---|---|---|---|
| Semantic Scholar | `SEMANTIC_SCHOLAR_API_KEY`（可选，**强烈建议申请**）；`S2_MIN_INTERVAL_SEC` | 每秒最多 1 次（默认间隔 1.1 秒） | 综合，带引用关系 |
| OpenAlex | `OPENALEX_EMAIL`（建议填）、`OPENALEX_API_KEY`（可选） | 约 8 次/秒 | 全学科 |
| Crossref | `CROSSREF_MAILTO`（建议填）、`CROSSREF_PLUS_TOKEN`（可选） | 约 5 次/秒 | 全学科，DOI 元数据 |
| arXiv | 不需要 | 每 3 秒 1 次（arXiv 官方要求） | 物理、数学、计算机预印本 |
| dblp | 不需要 | 每秒 1 次 | 计算机科学 |
| Europe PMC | 不需要 | 约 8 次/秒 | 生命科学、化学 |
| PubMed | `NCBI_API_KEY`（可选）、`NCBI_EMAIL`（可选） | 无 Key 约 3 次/秒，有 Key 接近 10 次/秒 | 生物医学 |
| CORE | `CORE_API_KEY`（**必填**，不填自动跳过） | 每秒 1 次 | 开放获取全文 |

**关于 Semantic Scholar 的 1 次/秒限制**：Semantic Scholar 的 API Key 规定所有接口合计每秒最多 1 次请求。程序里有一个全局限速器，
默认每 1.1 秒最多发一次，**多线程、甚至同时跑两篇论文也共享这个限额**，所以不会因为并发被封。

- 不要把「最小请求间隔」改到 1 秒以下，否则容易收到 HTTP 429（请求过多）。
- 没有 Key 时用的是公共额度，经常被限流；Key 免费，在 <https://www.semanticscholar.org/product/api> 申请。
- 某个来源连续 3 次返回 429 会暂停 60 秒；连续 3 次连不上会暂停 5 分钟，不会拖慢整个运行。

**这些来源怎么用**：流水线先找候选论文（有 Gemini Key 时用 Gemini + Google 搜索，否则用 Semantic Scholar，结果太少时补查 arXiv），
再逐篇到 Semantic Scholar 核实标题、作者、摘要。其他来源只在 Semantic Scholar 被关闭、连不上或结果太少时补充，
所以默认情况下参考文献和原版一致。只关心某个学科时，可以关掉用不到的来源加快速度（`.env` 里对应 `PO_SCHOLAR_SOURCES`）。

---

## 新功能

### 测试连接

位置：**环境与密钥 → 诊断 → 连接测试**。

- **测试连接**：向每个已配置的模型平台发一次极小的请求（最多约 8 个输出 token，费用可以忽略），显示状态、延迟和出错原因。
- **测试文献源**：在每个已启用的文献来源检索一次，显示是否可用和命中数量（命中 0 条但状态正常，说明来源可用、只是没搜到）。
- 测试使用**上次保存**的配置；有未保存的修改时会提示先保存。结果只显示在页面上，不写入任何文件。
- 错误信息会尽量翻译成中文（如「网络错误（域名解析失败）」「HTTP 401」），密钥会被打码。

命令行等价写法（在 `paper-orchestra` 目录下）：`python tools/check_providers.py --ping --scholar`。

### 用量统计

位置：**成果与 PDF → 核查与用量 → LLM 用量**。

显示这次运行的 LLM 调用次数、失败次数、输入 / 输出 token，以及按模型拆分的表格和占比。
运行中每 10 秒自动刷新；被「停止生成」中断的运行也会保留中断前的统计。
数据来自运行目录里的 `usage_report.json`。

**估算费用（加元）**：用量页多一格「估算费用」和一列「费用 (CAD)」（鼠标停在格子上可看原币金额、价格来源、联网搜索次数和缓存命中），表格下方写明用的是哪天的价目表和汇率。「运行与日志」页的运行详情里有「本次已花约 C$…」，运行中每 10 秒更新；左侧运行列表每一行显示该次的费用，顶部显示所有运行的累计。

- 按各平台官方标价和加拿大央行当天汇率估算，不额外调用模型；价目表和汇率一天最多联网更新一次，离线时用上次的缓存；
- 免费额度、折扣、赠金和税费不计，实际以各平台账单为准；没有公开标价的模型（例如本机的 Ollama）不计入，并在表格下方列出；
- 更早版本跑的运行没有费用记录，显示「—」。

> 某些中转站不返回 token 数，这时调用次数照常统计，token 显示为 0。

### 引用核查

位置：**成果与 PDF → 核查与用量 → 引用核查**。最终 PDF 生成后自动进行，**完全离线**。

- **摘要**：正文引用了多少个键、参考文献有多少条、多少条有 DOI、多少条已核验。
- **缺失条目**（红色）：正文 `\cite` 了、但 `.bib` 里没有的键，PDF 里会显示成「[?]」，需要修正。
- **未被引用**（黄色）：`.bib` 里有、正文没引用的条目，不会出现在参考文献列表里，一般无需处理。
- **每条文献的状态**：
  - **已核验**：有 DOI，或有带链接的文献检索记录；
  - **仅元数据**：有检索记录，但没有 DOI 和链接；
  - **未核验**：找不到对应的检索记录，**建议人工确认这篇文献是否真实存在**。
- 可以按状态筛选，点 DOI / 链接直接打开原文页面；「打开 citation_audit.md」查看中文报告。

> 「已核验」只说明这条文献在学术数据库里有记录，不代表正文引用它的方式一定准确。投稿前请自己通读参考文献。

### 导出 Overleaf 包

位置：**成果与 PDF** 页面顶部（运行结束后可用），或 **运行与日志** 里成功的运行。

把最终的 LaTeX 源码（主 `.tex`、`.bib`、样式文件、图表）打包成 `.zip`，默认保存到「下载」文件夹。
在 Overleaf 选择 **New Project → Upload Project**，上传这个 zip 就能在线编辑和编译。

- 只放一份主文档（通常是 `final_refined_paper.tex`），不会混入中间草稿、编译日志、审稿截图。
- 没装 LaTeX 的电脑也能用：运行结束后导出草稿，到 Overleaf 上编译出 PDF。

### 投稿工具

位置：**成果与 PDF → 投稿工具**（运行生成 LaTeX 源码后可用）。所有编译都在临时文件夹里进行，不会改动运行目录。

- **arXiv 投稿包**：按论文编译时真正读到的文件打包，带上 `.bbl`（arXiv 不跑 BibTeX），可选删掉 `%` 注释、把 `\input` 并进主文档；
  再在空文件夹里单独编译一遍，检查缺失文件、参考文献、能否编译、TODO 注释和 50 MB 上限，没有红色问题才导出 `.zip`。
- **双盲检查**：按会议模板判断是否需要匿名，查 `\author`、`\thanks`、致谢、个人仓库链接、「我们之前的工作」、PDF 元数据，
  以及字体是否嵌入、有没有 Type 3。
- **LaTeX 诊断**：整理编译日志里的错误（附中文说明）、缺的宏包（可联网查是哪个 TeX Live 包）、未定义的引用和交叉引用、超出页边的行。
- **改动对比**：用 latexdiff 比较任意两个版本（初稿、各轮润色、别的运行），生成标出增删的 PDF。
- **联网核查**（在「核查与用量」里）：把每条被引文献拿到 Crossref、arXiv、Semantic Scholar 核对，找出查不到、标题对不上、DOI 或作者不一致的条目；
  只发送文献标题和 DOI，结果存为运行目录里的 `citation_online_check.json`。

命令行等价写法（在 `paper-orchestra` 目录下）：`python tools/submission_tools.py {arxiv,blind,diagnose,versions,diff,cite-online} ...`。

新增模板：NeurIPS 2026、ICML 2026、AAAI 2027、ACL / ARR、IEEE 会议、ACM 会议 (sigconf)，双盲会议默认是匿名审稿版。

### 其他改进

- 运行日志里的 API Key 会被自动打码（应用和 Python 两侧都会处理）。
- 全新的界面设计和应用图标，浅色 / 深色模式均可用。

---

## 自己打包安装程序

只想使用应用的话，拿到别人打包好的安装包即可，这一节可以跳过。

### Windows

**需要先安装：**

1. **Node.js 18 或更新版本**（<https://nodejs.org/>，选 LTS）；
2. **Rust**（<https://rustup.rs/>，默认选项即可，使用 MSVC 工具链）；
3. **Visual Studio C++ 生成工具**（<https://visualstudio.microsoft.com/visual-cpp-build-tools/>，勾选「使用 C++ 的桌面开发」）；
4. **WebView2**：Windows 10/11 一般自带。

**打包：** 在 PowerShell 里进入 `paper-orchestra-desktop` 文件夹，运行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build-windows.ps1
```

脚本会检查上面的工具，然后依次执行 `npm install`、`npm run build`、`cargo test`、`npx tauri build`。常用参数：

- `-SkipTests`：跳过 Rust 测试，打包更快；
- `-SkipInstall`：跳过 `npm install`；
- `-Bundles nsis`：只生成 setup.exe（不生成 MSI）。

**产物位置**（脚本最后会打印准确路径）：

| 文件 | 位置 |
|---|---|
| 安装程序（推荐） | `src-tauri\target\release\bundle\nsis\PaperOrchestra Studio_0.1.0_x64-setup.exe` |
| MSI 安装包 | `src-tauri\target\release\bundle\msi\PaperOrchestra Studio_0.1.0_x64_en-US.msi` |
| 免安装 exe | `src-tauri\target\release\` 下的 `.exe`（需要电脑上有 WebView2） |

**创建桌面快捷方式：**

- 用 setup.exe 安装时，最后一页勾选 **Create desktop shortcut**（创建桌面快捷方式）。默认安装到 `%LOCALAPPDATA%\PaperOrchestra Studio`。
- 已经装好但没有快捷方式：在开始菜单找到 PaperOrchestra Studio → 右键 → **打开文件位置** → 右键该快捷方式 → **发送到 → 桌面快捷方式**。
- 免安装 exe：右键 exe →（Windows 11 先点「显示更多选项」）→ **发送到 → 桌面快捷方式**。

### macOS

**需要先安装：** Xcode 命令行工具（`xcode-select --install`）、Rust（<https://rustup.rs/>）、Node.js 18+。

**打包：** 在终端进入 `paper-orchestra-desktop` 文件夹：

```bash
./scripts/build-macos.sh              # 适配当前 Mac 的芯片（Apple 芯片或 Intel）
./scripts/build-macos.sh universal    # 通用版，两种芯片都能运行
```

默认只打包 `.app`：做 `.dmg` 的那一步要用脚本操控 Finder，没有授权或没人登录屏幕时会失败或卡住。
需要 dmg 时用 `PO_MAC_DMG=1 ./scripts/build-macos.sh`。

**产物位置：**

- 应用：`src-tauri/target/release/bundle/macos/PaperOrchestra Studio.app`
- 安装镜像（`PO_MAC_DMG=1` 时）：`src-tauri/target/release/bundle/dmg/PaperOrchestra Studio_0.1.0_<架构>.dmg`
- 通用版在 `src-tauri/target/universal-apple-darwin/release/bundle/` 下。

安装：`ditto "src-tauri/target/release/bundle/macos/PaperOrchestra Studio.app" "/Applications/PaperOrchestra Studio.app"`，
或打开 dmg 把应用拖进「应用程序」。应用没有签名，第一次打开请按[上面的方法](#macos)：**右键 → 打开**，
或到「系统设置 → 隐私与安全性」点「仍要打开」，或执行
`xattr -dr com.apple.quarantine "/Applications/PaperOrchestra Studio.app"`。

### 开发模式

```bash
npm install
npx tauri dev                       # 带热更新的开发窗口
cd src-tauri && cargo test          # Rust 单元测试
```

---

## 常见问题

**Q：设置页显示「未找到 paper_writing_cli.py」。**
仓库目录要选到**里面直接有** `paper_writing_cli.py` 的那一层（`paper-orchestra`），而不是外面的 `PaperOrchestra_源码`。

**Q：Python 显示「不可用」，或日志里出现 `ModuleNotFoundError`。**
Python 解释器要选**装了依赖的那个**，通常是 `<仓库>\.venv\Scripts\python.exe`（Windows）或 `<仓库>/.venv/bin/python`（macOS）。
用这个解释器重新执行一次 `python -m pip install -r requirements.txt`。

**Q：一启动就失败，日志里有 `No LLM API credentials` 或 `has no API key`。**
模型框里用到的平台还没有 Key。到「环境与密钥 → 模型服务」填好并**保存**，然后用「测试连接」确认。
如果用的是没有内置默认模型的平台（xAI、Together、本地模型等），还要在它的卡片里填「默认模型」，或者把模型框改成 `平台:模型名`。

**Q：测试连接显示 HTTP 401 / 403 / 404。**
401、403 通常是 Key 填错、余额不足或没开通该模型；404 多半是模型名写错，请到平台控制台复制准确的模型名。

**Q：测试连接显示「网络错误（域名解析失败 / 超时 / 代理）」。**
电脑连不上这个平台。国外平台在国内通常需要代理：在「其他环境变量」里添加 `HTTPS_PROXY = http://127.0.0.1:<代理端口>`，保存后再测。
也可以换用国内平台（通义千问、Kimi、智谱、DeepSeek 等）。

**Q：文献检索特别慢，或日志里很多 `HTTP 429`。**
去申请一个免费的 Semantic Scholar API Key 填上，限流会少很多。arXiv 规定每 3 秒只能查一次，这一步本来就需要一些时间。
在「文献检索」里关掉用不到的来源也能加快速度。

**Q：运行结束了，但没有 PDF（日志里有 `PDF Generation Failed`）。**
- 看侧边栏「运行环境」里的「LaTeX 编译」：「缺失」说明没装 LaTeX，「缺 bibtex」说明安装不完整；装好后**重启应用**。
- Windows 上用 MiKTeX 时，确认已开启「自动安装缺失宏包」。
- 如果输出路径里有中文或空格，可以把「论文输出根目录」换到纯英文路径（如 `D:\po_runs`）再试。
- 实在不行：在「成果与 PDF」里「导出 Overleaf 包」，到 Overleaf 上编译。

**Q：macOS 上提示「已损坏，无法打开」或「无法验证开发者」。**
这是未签名应用的正常拦截，按[macOS 安装说明](#macos)处理即可。

**Q：Windows 提示「Windows 已保护你的电脑」。**
点「更多信息」→「仍要运行」。

**Q：引用核查里有「未核验」的文献。**
说明这条文献在检索记录里找不到，可能是模型自己写出来的。请到 Google Scholar 搜一下标题，确认真实存在再保留。

**Q：用量统计里 token 全是 0。**
你用的中转站没有返回 token 数，调用次数仍然准确。具体费用以平台账单为准。

**Q：以前跑的项目看不到「引用核查」「用量」。**
这两项是新版本才有的，旧的运行目录里没有这些文件。重新运行一次即可。

**Q：侧边栏显示「providers.json 有误」。**
自定义平台的 JSON 写错了，这时所有运行都会失败。到「模型服务 → providers.json」点「校验」，按提示修改；
不需要自定义平台的话，清空内容保存即可。

**Q：我的密钥安全吗？**
密钥只写在本机 `paper-orchestra/.env` 里，应用不会上传。运行日志和测试结果里的密钥会被打码。
不要把 `.env` 发给别人或上传到 GitHub（仓库的 `.gitignore` 已经排除了它）。

**Q：项目和运行记录存在哪里？想重置怎么办？**
项目、设置和运行记录存在本地 SQLite 数据库里（路径显示在「环境与密钥 → 本地环境」底部）：
Windows 为 `%APPDATA%\com.paperorchestra.studio\paper_orchestra_studio.db`，
macOS 为 `~/Library/Application Support/com.paperorchestra.studio/paper_orchestra_studio.db`。
关闭应用后删除这个文件即可重置（生成的论文在「论文输出根目录」里，不会被删除）。

**Q：生成的文件都在哪？**
每次运行一个文件夹：`<论文输出根目录>/<项目名>_<时间>_<编号>/`，里面主要有：

| 文件 | 内容 |
|---|---|
| `final_paper.pdf` | 最终论文 |
| `content_refinement_workdir/final_refined_paper.tex` | 最终 LaTeX 源码 |
| `latex_writeup/` | 初稿与模板 |
| `literature_agent_output/references.bib` | 参考文献 |
| `literature_agent_output/references_meta.json` | 每条文献的 DOI、链接、来源（新） |
| `citation_audit.json` / `citation_audit.md` | 引用核查结果（新） |
| `usage_report.json` | LLM 用量（新） |

---

## 相关文档

- [模型与文献源参考（命令行用户）](../paper-orchestra/docs/PROVIDERS_zh.md)
- [更新说明：和原版相比改了什么](../CHANGELOG_zh.md)
- [PaperOrchestra 原版 README（英文）](../paper-orchestra/README.md)

---

## 第三方声明

「投稿工具」（arXiv 投稿包、双盲检查、LaTeX 诊断、改动对比 PDF、联网引用核查）和新增的会议模板改编自
[Lattice](https://github.com/leo1oel/lattice)（Copyright 2026 Leonardo and the Lattice contributors，Apache License 2.0）。
具体改编了哪些文件、改了什么，见 `paper-orchestra/THIRD_PARTY_NOTICES.md`。
