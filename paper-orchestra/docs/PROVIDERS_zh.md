# 模型服务与文献检索源参考

本文写给**直接用命令行**运行 PaperOrchestra 的同学（`python paper_writing_cli.py ...`）。
用桌面应用 PaperOrchestra Studio 的话，下面这些设置都可以在「环境与密钥」页面里点选完成，
应用说明见 [`paper-orchestra-desktop/README.md`](../../paper-orchestra-desktop/README.md)。

所有配置都放在仓库根目录（本文件的上一级）：

- `.env`：密钥和开关，每行一个 `变量名=值`；
- `providers.json`（可选）：自定义平台，可以从 [`providers.example.json`](../providers.example.json) 复制一份开始改。

原版的写法（`OPENAI_API_KEY`、`claude-sonnet-4-6`、`--writer_model_name auto` 等）**全部照常可用**，本文介绍的都是新增能力。

---

## 1. 五分钟上手

**① 在 `.env` 里填一个平台的 Key**（任意一个即可，下面以通义千问为例）：

```env
DASHSCOPE_API_KEY=sk-你的密钥
SEMANTIC_SCHOLAR_API_KEY=你的S2密钥     # 可选，强烈建议
```

**② 先测一下连通性：**

```bash
python tools/check_providers.py --ping
```

**③ 运行：**

```bash
python paper_writing_cli.py \
  --raw_materials_dir datasets/my_paper/raw_materials \
  --latex_template_dir templates/cvpr2025 \
  --output_dir ./paper_output_demo \
  --use_plotting true \
  --writer_model_name qwen:qwen-max \
  --reflection_model_name qwen:qwen-max \
  --plotting_model_name qwen:qwen-max
```

也可以三个模型都保持 `auto`（默认值）：只配了千问时，`auto` 会自动用 `qwen:qwen-max`。

---

## 2. 模型名怎么写

所有模型参数（`--writer_model_name`、`--reflection_model_name`、`--plotting_model_name`、`--image_model_name`）都接受三种写法：

| 写法 | 例子 | 含义 |
|---|---|---|
| `auto` | `auto` | 按任务自动挑模型，见[第 5 节](#5-auto-自动分工) |
| `平台:模型名` | `qwen:qwen-max`、`ollama:qwen2.5:14b`、`myrelay:gpt-4o` | 明确指定平台（**推荐**） |
| 只写模型名 | `claude-sonnet-4-6`、`gpt-4.1`、`deepseek-chat` | 按名称识别平台（原版写法） |

**`平台:模型名` 的规则：**

- 只按**第一个**冒号拆分，所以 Ollama 的标签能原样保留：`ollama:qwen2.5:14b` → 平台 `ollama`，模型 `qwen2.5:14b`。
- 平台名不区分大小写，可以用别名（见[第 3 节](#3-内置平台)的表格）。
- 冒号前面不是已知平台时不拆分，整个字符串当作模型名。
- 例外：「云端预设平台名 + 本地尺寸标签」如 `qwen:7b`、`mistral:7b` 会被当成 Ollama 标签，不会发给云端。

**只写模型名时**，按下面的顺序决定发往哪里（前面的规则先生效）：

1. `providers.json` 里某个自定义平台的 `models` 列出了这个名字 → 发到该平台；
2. 形如 `llama3:8b` 的 Ollama 标签，且设置了 `OLLAMA_BASE_URL` / `LMSTUDIO_BASE_URL` / `VLLM_BASE_URL` → 发到那台本地服务；
3. **原版规则**：名字里含 `claude` → Anthropic，含 `deepseek` → DeepSeek，含 `gpt` / `o1` / `o3` / `o4` → OpenAI——前提是该平台有 Key；
   Gemini SDK 的名字（`gemini-*`、`models/...` 等）在有 Gemini Key 时发给 Gemini；
4. 没配本地地址的 Ollama 标签 → 默认的 `http://localhost:11434/v1`；
5. 含 `/` 的名字（如 `anthropic/claude-sonnet-4`）→ 网关：`PO_DEFAULT_PROVIDER`、已配置的自定义平台、OpenRouter、硅基流动、OpenAI，取第一个可用的；
6. 名称规则：`qwen*` → 通义千问，`kimi*` / `moonshot*` → Kimi，`glm*` → 智谱，`hy3` / `hy4` / `hy-*` / `hunyuan*` → 腾讯混元，`ernie*` → 百度文心，`ling-*` / `lingdt-*` → 蚂蚁数科，`longcat*` → 美团 LongCat，`grok*` → xAI，`mistral*` 等 → Mistral（该平台有 Key 时）；
   没 Key 时交给能接收任意模型名的网关（`PO_DEFAULT_PROVIDER`、自定义平台或 OpenAI）；
7. 都认不出：`PO_DEFAULT_PROVIDER` 或已配置的自定义平台；否则 Gemini（原版默认）；Gemini 也没 Key 时试 OpenRouter / 硅基流动 / OpenAI。

> 不确定一个名字会发到哪里时，直接写 `平台:模型名` 最省心。

---

## 3. 内置平台

### 官方四家（和原版相同）

| 平台名（别名） | 协议 | 密钥 | 接口地址（可用变量覆盖） | 图片输入 |
|---|---|---|---|---|
| `openai`（`oai`） | openai | `OPENAI_API_KEY` | 官方默认；`OPENAI_BASE_URL` | 运行时自动判断 |
| `anthropic`（`claude`） | anthropic | `ANTHROPIC_API_KEY` | 官方默认；`ANTHROPIC_BASE_URL` | 支持 |
| `deepseek` | openai | `DEEPSEEK_API_KEY` | `https://api.deepseek.com`；`DEEPSEEK_BASE_URL` | 不支持（自动去掉图片） |
| `gemini`（`google`、`vertex`） | gemini | `GEMINI_API_KEY`，或 `VERTEX_AI_PROJECT` + `VERTEX_AI_LOCATION` | 官方默认；`GEMINI_BASE_URL` | 支持 |

- 只认 `GEMINI_API_KEY`，**不认** `GOOGLE_API_KEY`（为别的工具设置的 `GOOGLE_API_KEY` 不会改变流水线行为）。
- 同时设置了 `VERTEX_AI_PROJECT` 和 `VERTEX_AI_LOCATION` 时优先走 Vertex AI（需要先 `gcloud auth application-default login`）。
- 有 Gemini 凭证时，文献检索的「找候选论文」一步固定用 `gemini-3-flash-preview` + Google 搜索（原版行为）。

### 预设平台（OpenAI 兼容，填 Key 即用）

| 平台名（别名） | 密钥（任填一个） | 默认接口地址 | 内置默认模型 |
|---|---|---|---|
| `qwen`（`dashscope`、`bailian`、`aliyun`） | `DASHSCOPE_API_KEY`、`QWEN_API_KEY` | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-max` |
| `moonshot`（`kimi`） | `MOONSHOT_API_KEY`、`KIMI_API_KEY` | `https://api.moonshot.cn/v1` | `moonshot-v1-128k` |
| `zhipu`（`glm`、`bigmodel`） | `ZHIPU_API_KEY`、`ZHIPUAI_API_KEY`、`GLM_API_KEY` | `https://open.bigmodel.cn/api/paas/v4` | `glm-4-plus` |
| `hunyuan`（`hy`、`tencent`、`tokenhub`） | `HUNYUAN_API_KEY`、`TOKENHUB_API_KEY` | `https://tokenhub.tencentmaas.com/v1` | `hy4` |
| `qianfan`（`ernie`、`baidu`、`wenxin`） | `QIANFAN_API_KEY`、`ERNIE_API_KEY` | `https://qianfan.baidubce.com/v2` | `ernie-5.1` |
| `antdigital`（`ant`、`ling`、`dtmaas`） | `ANTDIGITAL_API_KEY`、`DTMAAS_API_KEY` | `https://maas-api.antdigital.com/v1` | 无（测试连接用 `ling-3.1-flash`） |
| `longcat`（`meituan`） | `LONGCAT_API_KEY` | `https://api.longcat.chat/openai` | 无（测试连接用 `LongCat-2.5`） |
| `siliconflow`（`sf`） | `SILICONFLOW_API_KEY` | `https://api.siliconflow.cn/v1` | `deepseek-ai/DeepSeek-V3` |
| `openrouter` | `OPENROUTER_API_KEY` | `https://openrouter.ai/api/v1` | `anthropic/claude-sonnet-4` |
| `xai`（`grok`） | `XAI_API_KEY`、`GROK_API_KEY` | `https://api.x.ai/v1` | 无 |
| `groq` | `GROQ_API_KEY` | `https://api.groq.com/openai/v1` | `llama-3.3-70b-versatile` |
| `mistral` | `MISTRAL_API_KEY` | `https://api.mistral.ai/v1` | `mistral-large-latest` |
| `together` | `TOGETHER_API_KEY` | `https://api.together.xyz/v1` | 无 |
| `ollama` | 不需要（可选 `OLLAMA_API_KEY`） | `http://localhost:11434/v1` | 无 |
| `lmstudio` | 不需要（可选 `LMSTUDIO_API_KEY`） | `http://localhost:1234/v1` | 无 |
| `vllm` | 不需要（可选 `VLLM_API_KEY`） | `http://localhost:8000/v1` | 无 |

> 腾讯混元：原来的 `api.hunyuan.cloud.tencent.com` 平台已于 2026-09-30 停用，Key 要在腾讯云 TokenHub 控制台重新创建；旧的 `hunyuan-turbos-latest` 等模型名也已下线，改用 `hy4`、`hy3`。
> 百度文心：Key 在千帆控制台「API Key」页面创建（`bce-v3/` 开头），模型名如 `ernie-5.1`、`ernie-5.5-flash-preview`。
> 蚂蚁数科 Ling：默认走蚂蚁数科自己的 DTMaaS 平台（Key 在 maas.antdigital.com 创建，用支付宝账号登录），模型名是小写的 `ling-3.1-flash` 等。蚂蚁百灵平台（chat.ant-ling.com）的 Key 要把 `ANTDIGITAL_BASE_URL` 改成 `https://api.ant-ling.com/v1`，模型名如 `Ling-3.2-flash`。
> 美团 LongCat：Key 在 longcat.chat 的「API Keys」页面创建，当前模型 `LongCat-2.5`（支持图片输入，旧的 LongCat-Flash 系列已于 2026-05-29 下线）。
> 这两个平台没有内置默认模型，`auto` 不会自动改用它们；要用就写 `PO_AUTO_MODEL` 或按任务指定。
> 豆包（火山方舟）、MiniMax 和云知声已于 2026-11-19 移出内置列表。`.env` 里原有的 `ARK_API_KEY`、`MINIMAX_API_KEY`、`UNISOUND_API_KEY` 不会被删除，只是不再自动识别；需要时可在 `providers.json` 里把它们加成自定义平台（`base_url` 分别是 `https://ark.cn-beijing.volces.com/api/v3` 和 `https://api.minimaxi.com/v1`，`api_key` 写 `${ARK_API_KEY}` / `${MINIMAX_API_KEY}`）。

每个平台（包括自定义平台）都支持两个通用变量，`<NAME>` 是平台名转大写、非字母数字换成 `_`：

- `<NAME>_BASE_URL`：改接口地址，例如 `QWEN_BASE_URL=https://dashscope-intl.aliyuncs.com/compatible-mode/v1`；
- `<NAME>_DEFAULT_MODEL`：改默认模型，例如 `QWEN_DEFAULT_MODEL=qwen-plus`。默认模型用于 `auto` 回退和 `check_providers.py --ping`。

本地服务（Ollama / LM Studio / vLLM）：

- 写明 `ollama:模型名` 时不需要任何配置，直接连默认地址；
- 设置了 `OLLAMA_BASE_URL`（或 `LMSTUDIO_BASE_URL` / `VLLM_BASE_URL`）后，才算「已配置」，`auto` 回退和不带前缀的 `llama3:8b` 才会用到它。

> 表中的模型名只是示例，各平台经常更新，请以平台控制台为准。

---

## 4. 自定义平台：providers.json

### 位置

- 默认是仓库根目录的 `providers.json`；
- 或用 `PO_PROVIDERS_FILE` 指定别的文件（相对路径相对于仓库根目录）；
- 文件可以带 UTF-8 BOM（Windows 记事本保存的也能读）；空文件等于没有；修改后下一次调用自动生效。

### 文件结构

```json
{
  "providers": [],
  "auto_model": "",
  "task_models": {},
  "default_provider": ""
}
```

`providers` 数组里放一个或多个平台对象（字段见下表）。顶层也可以直接写成平台数组 `[ {...}, {...} ]`。三个顶层字段都可选，留空不生效：

| 顶层字段 | 等同于 | 说明 |
|---|---|---|
| `auto_model` | `PO_AUTO_MODEL` | 所有 `auto` 任务用这个模型；`.env` 里的变量优先 |
| `task_models` | `PO_TASK_MODEL_<任务>` | 按任务指定，如 `{"outline": "qwen:qwen-max"}`；任务名用小写 |
| `default_provider` | `PO_DEFAULT_PROVIDER` | 认不出平台的模型名发到这里 |

### 平台字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `name` | 字符串，**必填** | 平台名，调用时作前缀：`name:模型名`。与内置平台同名时会**替换**内置平台 |
| `type` | 字符串 | `openai`（默认，也可写 `openai-compatible`、`oai`）、`anthropic`（或 `claude`）、`gemini`（或 `google`）。也可以用 `kind` 作字段名 |
| `base_url` | 字符串 | 接口地址；`<NAME>_BASE_URL` 优先 |
| `api_key_env` | 字符串或字符串数组 | 从哪些环境变量读密钥（按顺序取第一个非空的）；另外总会读 `<NAME>_API_KEY` |
| `api_key` | 字符串 | 直接写密钥，支持 `${变量名}`。**不推荐**，见下方提醒 |
| `models` | 字符串数组 | 该平台提供的模型；不带前缀的同名模型会优先发到这里（包括 `auto` 选出的同名模型） |
| `default_model` | 字符串 | `auto` 回退到该平台时用的模型；不写则取 `models` 的第一个 |
| `vision` | `true` / `false` | 是否支持图片输入；不写则运行时自动判断 |
| `headers` | 对象（值为字符串） | 每个请求附加的 HTTP 头 |
| `drop_params` | 字符串数组 | 不发给该接口的参数，如 `["temperature", "top_p"]` |
| `key_optional` | `true` / `false` | 不需要密钥（本地服务）。注意：这样的自定义平台**始终算作已配置** |
| `aliases` | 字符串数组 | 其他可用的前缀名 |
| `image_max_pixels`、`image_max_edge`、`image_max_bytes`、`image_max_total_pixels` | 正整数 | 该平台对输入图片的上限：单张总像素、最长边像素、单张字节数、一次请求所有图片的总像素。超过时发送前自动等比缩小（见第 6 节）；不写则不缩，写错的值会被忽略并提示 |

密钥按这个顺序查找：`api_key`（展开 `${...}` 后非空）→ `api_key_env` 里的变量 → `<NAME>_API_KEY`。
`api_key` 引用了一个没设置的变量（如 `"${RELAY_TOKEN}"` 而 `.env` 里没有 `RELAY_TOKEN`）时视为没填，会继续往后找。

> **提醒**：`providers.json` 不在 `.gitignore` 里。密钥请写在 `.env`（已被忽略），`providers.json` 里只写 `api_key_env` 变量名。

### 示例

**OpenAI 兼容中转站**（最常见）：

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

`.env` 里加 `MY_RELAY_TOKEN=你的密钥`，然后 `--writer_model_name myrelay:gpt-4o`。

**Claude 协议的中转站**（不写 `api_key_env` 时自动读 `CLAUDE_RELAY_API_KEY`）：

```json
{ "name": "claude-relay", "type": "anthropic", "base_url": "https://claude-relay.example.com",
  "default_model": "claude-sonnet-4-6", "vision": true }
```

**实验室服务器上的 vLLM，不需要密钥、只收文字：**

```json
{ "name": "lab", "type": "openai", "base_url": "http://192.168.1.20:8000/v1",
  "key_optional": true, "vision": false, "models": ["example-lab-model"] }
```

**把所有 auto 任务交给千问，但正文写作用 Claude：**

```json
{ "auto_model": "qwen:qwen-max", "task_models": { "section_writing": "claude-sonnet-4-6" } }
```

[`providers.example.json`](../providers.example.json) 是一份可以直接复制的完整示例：里面的平台在设置对应密钥之前不会生效，留空的顶层字段也不生效。

### 校验

Python 读取时会检查每个字段的类型，出错会给出中文提示并指出是第几个平台，例如：

```
ValueError: provider 'myrelay' 的 type 必须是 ('openai', 'anthropic', 'gemini') 之一，收到 'cohere'
```

**文件格式错误时，所有用到模型的步骤都会失败**，所以改完建议跑一次 `python tools/check_providers.py` 确认。
桌面应用使用完全相同的校验规则（`paper-orchestra-desktop/src-tauri/src/providers.rs`），能在保存前发现问题。

---

## 5. auto 自动分工

模型参数为 `auto`（默认）时，每个子任务按下面的顺序决定模型：

1. `PO_TASK_MODEL_<任务>`（`.env`）
2. `providers.json` 的 `task_models`
3. `PO_AUTO_MODEL`（`.env`）
4. `providers.json` 的 `auto_model`
5. **原版规则**：按已配置的官方 Key，从下表每行里取第一个可用的
6. 一个官方 Key 都没有：第一个已配置的其他平台的默认模型（自定义平台优先），例如 `qwen:qwen-max`
7. 仍然没有：`gemini-3.1-pro-preview`

| 任务（`<任务>`） | 用于 | 优先顺序（原版规则） |
|---|---|---|
| `outline` 大纲规划 | Writer | deepseek-v4-pro → claude-sonnet-5-5 → gpt-6.2-sol → gemini-4-argon |
| `literature_query` 文献检索词 | Writer | deepseek-flash → claude-sonnet-5-5 → gpt-6.1-terra → gemini-3.8-flash |
| `literature_write` 文献综述 | Writer | claude-sonnet-5-5 → deepseek-flash → gpt-6.2-sol → gemini-4-argon |
| `section_writing` 正文写作 | Writer | claude-sonnet-5-5 → gpt-6.2-sol → gemini-4-argon → deepseek-v4-pro |
| `plot_plan` 绘图规划 | Plotting | claude-sonnet-5-5 → gpt-6.2-sol → gemini-4-argon → deepseek-flash |
| `plot_code` 绘图代码 | Plotting | deepseek-flash → claude-sonnet-5-5 → gpt-6.1-terra → gemini-4-argon |
| `plot_critic` 图表视觉评审 | Plotting | claude-sonnet-5-5 → gpt-6.2-sol → gemini-4-argon → deepseek-flash |
| `peer_review` 同行评审 | Reflection | deepseek-v4-pro → claude-sonnet-5-5 → gpt-6.2-sol → gemini-4-argon |
| `content_refinement` 内容润色 | Reflection | claude-sonnet-5-5 → gpt-6.2-sol → gemini-4-argon → deepseek-v4-pro |
| `format_review` 版式质检 | Reflection | claude-sonnet-5-5 → gpt-6.2-sol → gemini-4-argon → deepseek-flash |
| `format_fix` 版式修复 | Reflection | claude-sonnet-5-5 → deepseek-flash → gpt-6.2-sol → gemini-4-argon |
| `citation_parse` 引用解析 | 辅助（只有评测脚本用，写论文不用） | deepseek-flash → claude-sonnet-5-5 → gpt-6.1-terra → gemini-3.8-flash |

（模型 id 按 2026-11-18 各平台模型列表更新。）

**细分步骤**（2026-11-19 起可单独指定）：没填时和它所属的那一步用同一个模型，行为和以前完全一样。

| 步骤（`<任务>`） | 做什么 | 没填时跟随 |
|---|---|---|
| `relevance_screen` 文献相关性筛选 | 剔除关键词检索带进来的离题论文 | `literature_query` |
| `plot_retrieval` 参考图检索 | 从示例图库里挑相似的图当参考 | `plot_code` |
| `plot_style` 图表风格优化 | 按学术风格润色绘图说明 | `plot_code` |
| `diagram_code` 示意图代码兜底 | 图像模型画不了时用 Matplotlib 画示意图 | `plot_code` |
| `plot_caption` 图注撰写 | 写图注 | `plot_critic` |

**两个特殊项**：

- `PO_TASK_MODEL_LITERATURE_SEARCH` 文献联网检索：没填时和原版一样，有 Gemini Key 就用 `gemini-3-flash-preview` + Google 搜索找论文，否则用 `literature_query` 生成关键词去学术库检索。可以填别的 Gemini 模型（还是 Google 搜索）、`deepseek-flash`（DeepSeek 的 `deepseek_web_search` / `deepseek_read_url` 联网工具，见 DeepSeek 文档 guides/web_tools），或 `off`（不联网，只用学术库）。其它平台的模型不能联网，会打印警告并改用学术库；DeepSeek 联网请求失败或没找到结果时，这一条检索自动改用学术库。用 Gemini 联网时不做相关性筛选（同原版），其余情况都会筛。
- `PO_TASK_MODEL_IMAGE` 示意图生成：图像模型。命令行 `--image_model_name` 写了具体模型时以它为准；为 `auto` 时用这里的值，仍没有则有 Gemini Key 用 `gemini-3-pro-image`，否则用 Matplotlib 代码画。

**DeepSeek 看图**：`deepseek-flash` 支持图片输入（DeepSeek 文档 guides/vision），按看图模型处理；其它 DeepSeek 模型仍按纯文本处理。

- 环境变量里任务名大写：`PO_TASK_MODEL_SECTION_WRITING=claude-sonnet-4-6`；`task_models` 里用小写。
- 这些覆盖**只对 `auto` 生效**：`--writer_model_name gpt-6.2-sol` 这类明确指定的模型不受影响（桌面应用里是旧项目还存着的 Writer / Reflection / Plotting 具体模型）。
- 桌面应用工作台的「按任务指定模型」面板列出全部 19 步，可以直接修改（写回 `.env`），并按同样的顺序标出每步实际生效的来源：按任务 / 跟随 / 统一 / 自动，或被旧项目的具体模型覆盖。
- 运行开始时会打印 `[Auto Model Routing]`，列出每个任务实际用的模型。
- 启动前会检查要用到的模型是否都有 Key，缺了会立即退出并提示该填哪个变量，例如
  `Error: model 'qwen:qwen-max' -> provider 'qwen' has no API key (set DASHSCOPE_API_KEY / QWEN_API_KEY).`

---

## 6. 图像模型与图片输入

**图像模型（`--image_model_name`，默认 `gemini-3-pro-image`）** 用来画概念示意图（数据图表由绘图代码模型写 Matplotlib 代码画，不经过图像模型）：

- 填 `auto` 或留空：有 Gemini Key（或 Vertex AI）就用 `gemini-3-pro-image`，没有就用 Matplotlib 代码画；
- 只写模型名时保持原版规则：名字含 `gemini` 且有 Gemini Key 才调用图像模型；
- 写明 `平台:模型名` 可以用其他平台的图像接口，例如 `openai:gpt-image-1`（OpenAI 兼容的 `/images` 接口）或 `gemini:imagen-...`；
- 不满足条件、或图像模型没返回图片时，改用 Matplotlib 代码画示意图（原版的兜底方式）。

**图片输入（视觉）**：绘图评审、PDF 版式检查等步骤会把图片发给模型。

- DeepSeek 等纯文本模型会自动去掉图片，只发文字；
- `vision` 未知的平台会先带图片请求，如果接口报「不支持图片」，就去掉图片重试，并在本次运行中记住这个模型；
- 确定不支持图片的自定义平台，写 `"vision": false` 可以省掉这次试探。

**图片太大时自动缩小**（2026-11-19）：各平台对单张图片的像素和大小有上限（例如美团 LongCat 单张最多 5,242,880 像素，超过就返回 HTTP 400）。

- 内置平台按各自视觉文档的上限，在发送前把超限的图片等比缩小（LongCat、混元、千问、GLM、DeepSeek、千帆、蚂蚁数科、xAI、Groq、Mistral）；没超限的图片原样发送；
- 接口仍因图片太大报错时，按报错里给出的上限再缩小重发（最多两次），并在本次运行中记住这个上限；仍不行才退回原来的「去掉图片重试」；
- 自定义平台可以在 `providers.json` 里写 `image_max_*` 字段（见第 4 节）；
- **一次请求的图片合计不超过约 12 MB**（`image_max_total_bytes` 或 `PO_IMAGE_REQUEST_MAX_MB` 可改，`0` 不限）：超过时每张按份额缩小。曾经一次发 12 张整页截图（约 24 MB）给千问，上传卡住一个多小时；
- 上传 2 分钟没有进展就算超时（以前要等 10 分钟一轮），并把图片缩小一半重发。

---

## 7. 学术检索源

| 来源（`PO_SCHOLAR_SOURCES` 中的名字） | Key / 变量 | 程序默认限速 | 适合学科 |
|---|---|---|---|
| `semantic_scholar`（别名 `s2`） | `SEMANTIC_SCHOLAR_API_KEY`（可选，建议）；`S2_MIN_INTERVAL_SEC` | 1 次 / 1.1 秒 | 综合，带引用关系 |
| `openalex` | `OPENALEX_EMAIL`（建议）、`OPENALEX_API_KEY`（可选） | 约 8 次/秒 | 全学科 |
| `crossref` | `CROSSREF_MAILTO`（建议）、`CROSSREF_PLUS_TOKEN`（可选） | 约 5 次/秒 | 全学科，DOI 元数据 |
| `arxiv` | 无 | 1 次 / 3 秒 | 物理、数学、计算机预印本 |
| `dblp` | 无 | 1 次/秒 | 计算机科学 |
| `europepmc`（别名 `epmc`） | 无 | 约 8 次/秒 | 生命科学、化学 |
| `pubmed` | `NCBI_API_KEY`（可选）、`NCBI_EMAIL`（可选） | 无 Key 约 3 次/秒，有 Key 接近 10 次/秒 | 生物医学 |
| `core` | `CORE_API_KEY`（**必填**，没有就自动跳过） | 1 次/秒 | 开放获取全文 |

**选择和排序**：`PO_SCHOLAR_SOURCES` 写逗号分隔的列表，按顺序查询，例如
`PO_SCHOLAR_SOURCES=semantic_scholar,openalex,crossref,arxiv`。不设置时使用上表全部来源（`core` 需有 Key）。写错的名字会被忽略并在日志里提示。

**Semantic Scholar 的 1 次/秒限制**：Semantic Scholar 的 API Key 规定所有接口合计每秒最多 1 次请求。程序用一个全局限速器保证这一点：

- 默认每 1.1 秒最多一次，可以用 `S2_MIN_INTERVAL_SEC` 调大（**不要小于 1**）；
- 限速在线程之间、**进程之间**共享（借助系统临时目录里的锁文件），同时跑两篇论文、或同时跑 `autoraters/citation_f1.py` 也不会超限；
- arXiv（每 3 秒 1 次）、dblp、PubMed、CORE 的限速同样跨进程共享；
- 没有 Key 时走公共额度，经常返回 HTTP 429。Key 免费申请：<https://www.semanticscholar.org/product/api>。

**自动跳过**：同一来源连续 3 次返回 HTTP 429 会暂停 60 秒；连续 3 次连接失败或超时会暂停 5 分钟，避免一个连不上的来源拖慢整次运行。

**补全更快**（2026-11-19）：

- 收到 429 时该来源自动放慢（间隔每次 ×1.5，最多到默认的 4 倍），之后连续 20 次成功再逐步恢复；
- 连续 2 次 HTTP 5xx 暂停该来源 2 分钟，其间用其它来源；
- 服务器要求等待超过 10 秒时不原地干等，先暂停该来源、改问其它来源；
- 超时会显示为灰色的「重试」提示，最后一次仍失败才算报错；
- 逐篇核实（Phase 1b）默认最多 15 分钟（`PO_ENRICH_TIME_BUDGET_MIN`，`0` 表示不限），超时后剩下的论文每个来源只问一次、不再等待，已经核实到的结果照常使用。

**流水线怎么用这些来源**（保证默认结果和原版一致）：

1. **找候选论文**：有 Gemini 凭证时用 Gemini + Google 搜索（原版）。否则用模型生成检索词，查 Semantic Scholar（每个检索词 6 条），结果少于 4 条时补查 arXiv（原版逻辑）；
   仍少于 4 条时，才用其余已启用的来源补充。
2. **逐篇核实**（Phase 1b）：每篇候选都到 Semantic Scholar 核实标题、作者、年份、摘要（原版逻辑）；只有 Semantic Scholar 被关闭或答不上来时，才依次问其他来源。
3. 核实到的 DOI、链接、来源会写进 `references_meta.json`（见第 9 节），**不会**写进 `references.bib`（有些 `.bst` 样式会原样打印这些字段，下划线会让 LaTeX 报错）。

---

## 8. 连通性检查：`tools/check_providers.py`

在仓库根目录运行：

```bash
python tools/check_providers.py                      # 只看配置：哪些平台有 Key、默认模型是什么（中文表格）
python tools/check_providers.py --ping               # 向每个已配置平台发一次极小的请求（最多 8 个输出 token）
python tools/check_providers.py --scholar            # 每个已启用的检索源各查一次
python tools/check_providers.py --ping --model qwen:qwen-max   # 只测这个模型（可重复写多个 --model）
python tools/check_providers.py --ping --scholar --json        # 输出一行 JSON，方便脚本处理
```

- `--timeout N`：每项检查的超时秒数，默认 30；所有检查并行进行。
- 官方四家没有内置默认模型，`--ping` 时分别用 `gpt-4.1`、`claude-sonnet-4-6`、`deepseek-chat`、`gemini-3-flash-preview`（即 auto 会用到的模型），可用 `<NAME>_DEFAULT_MODEL` 改。
- 已配置但没有默认模型的平台（xAI、本地服务等）不会被 ping，`error` 里会提示去设置默认模型或加 `--model`。
- 只要检查跑完了，退出码就是 0（不管结果好坏）；参数写错时为 2。
- 错误信息很短，密钥会被打码。

`--json` 的输出格式：

```json
{
  "providers": [
    {"name": "qwen", "label": "通义千问 · DashScope", "kind": "openai", "configured": true,
     "model": "qwen-max", "ok": true, "latency_ms": 812, "error": null}
  ],
  "scholar": [
    {"name": "semantic_scholar", "ok": true, "latency_ms": 640, "hits": 3, "error": null}
  ]
}
```

没有测试的项目，`ok` / `latency_ms` / `hits` 为 `null`；`ok: true` 且 `hits: 0` 表示来源可用但没搜到。

---

## 9. 运行目录里的新文件

这些文件都是**额外**生成的，原有输出文件的名字、位置和内容都不变。生成失败只会打印一条警告，不影响论文。

| 文件 | 何时写入 | 内容 |
|---|---|---|
| `usage_report.json` | 运行开始时清零，每次模型调用后更新，结束时（包括失败）再写一次 | LLM 调用次数、token 用量和按标价估算的费用（加元） |
| `literature_agent_output/references_meta.json` | 和 `references.bib` 同时写入 | 每条参考文献的标题、年份、DOI、链接、来源 |
| `citation_audit.json`、`citation_audit.md` | 最终 PDF 生成后（只在运行成功时） | 离线引用核查结果（`.md` 为中文报告） |

运行结束时日志里会多两行汇总：` >> Citation audit: ...` 和 ` >> Token usage: ...`。

**usage_report.json**：

```json
{
  "generated_at": "2026-11-18T10:52:02+08:00",
  "totals": {"calls": 40, "errors": 0, "input_tokens": 67295, "output_tokens": 9291},
  "by_model": [
    {"provider": "qwen", "model": "qwen-max", "calls": 40, "errors": 0,
     "input_tokens": 67295, "output_tokens": 9291}
  ]
}
```

`calls` 包含失败的请求，`errors` 是其中失败的次数。接口没返回用量时，调用照常计数，token 记为 0。

**费用估算**（2026-11-19 起）：报告里另有这些字段（旧版本的报告没有，桌面应用照常显示）：

- 每个模型一行里，非 0 时才出现的细分计数：`cache_read_tokens` / `cache_write_tokens` / `cache_write_1h_tokens`（命中或写入提示缓存的输入 token）、`image_output_tokens`（生成图片的 token）、`long_calls` / `long_input_tokens` / `long_output_tokens`（超过长上下文门槛、按高一档计价的调用）、`search_queries`（Google 搜索等联网检索次数）、`images`（图像接口返回的图片数）。它们都包含在 `input_tokens` / `output_tokens` 里，不重复计数；
- 有标价的模型多一个 `cost`：`{"cad": 加元, "amount": 原币金额, "currency": "USD"/"CNY", "source": 价格来源, "price_as_of": 核对日期}`；
- 顶层的 `cost`：`total`（加元合计）、`by_currency`、`fx`（汇率、日期、来源）、`prices`（价目表核对日期与来源）、`unpriced_models`（没有公开标价、未计入的模型）。

怎么算的：

- **不额外调用任何模型**，只用各平台本来就返回的 token 数；
- 单价：内置各平台官方价目页的标价（注明核对日期，已排好的调价按日期自动切换，例如 Gemini 3.8 Flash 2027-01-01 起翻倍）；内置表里没有的模型，用 LiteLLM 公开价目表（OpenRouter 用它自己的公开模型列表）；
- 汇率：加拿大央行每日汇率（Valet 接口，免费、无需 Key）；
- 价目表和汇率每次运行开始时在后台检查，**一天最多下载一次**，缓存在 `~/.cache/paper-orchestra/pricing.json`（`PO_PRICING_CACHE_DIR` 可改位置）；离线或下载失败时用缓存，没有缓存就用内置值。`PO_PRICING_ONLINE=0` 关闭联网更新；
- 结果是**按标价的估算**：免费额度（例如 Gemini 每月 5000 次免费搜索）、折扣、赠金和税费都不计，实际以各平台账单为准。

**references_meta.json**（键与 `references.bib` 里的引用键一致）：

```json
{
  "Wei2022ParameterefficientAdapters": {
    "title": "Parameter-Efficient Adapters for Vision Backbones", "year": 2022,
    "doi": "10.xxxx/xxxxx", "url": "https://www.semanticscholar.org/paper/...", "source": "semantic_scholar"
  }
}
```

`source` 可能是组合值，如 `semantic_scholar+crossref`（摘要来自第二个来源）。

**citation_audit.json**：

```json
{
  "generated_at": "...",
  "tex_file": "content_refinement_workdir/final_refined_paper.tex",
  "bib_file": "content_refinement_workdir/references.bib",
  "summary": {"cited": 8, "bib_entries": 8, "missing_in_bib": 0, "uncited": 0, "with_doi": 8, "verified": 8},
  "missing_in_bib": [],
  "uncited": [],
  "entries": [
    {"key": "...", "title": "...", "year": 2022, "doi": "...", "url": "...", "source": "semantic_scholar",
     "cited": true, "status": "verified"}
  ]
}
```

- `status`：`verified`（有 DOI，或有带链接的检索记录）、`metadata_only`（有检索记录但没有 DOI / 链接）、`unverified`（没有检索记录，如手工添加或模板自带，请人工确认）。
- 核查的是实际编译的 `.tex`：依次找 `content_refinement_workdir/final_refined_paper.tex`、`latex_writeup/raw_draft_paper.tex`、`latex_writeup/template.tex`。
- 识别 `\cite`、`\citep`、`\citet`、`\citeauthor`、`\citeyear`、`\nocite` 等命令（含可选参数和逗号分隔的多个键），`\nocite{*}` 视为引用全部条目。

想给以前的运行补一份核查报告：

```bash
python -c "from utils.citation_audit import audit_run; print(audit_run('paper_output_xxx')['summary'])"
```

---

## 10. 日志里的密钥

- 运行日志里出现的模型平台密钥会被替换成 `***`：包括 `.env` / `providers.json` 里配置的密钥值，以及形如 `sk-...`、`AIza...`、`Bearer ...` 的字符串；
  检索源请求出错时，网址里的 `api_key=...`、`token=...` 等参数也会被打码。
- 原版会打印 `Using S2 API Key: <你的密钥>`，现在改为 `Semantic Scholar API key: set / not set`。
- 即便如此，分享日志前也请自己再看一眼。

---

## 11. 环境变量速查

| 变量 | 作用 |
|---|---|
| `OPENAI_API_KEY`、`ANTHROPIC_API_KEY`、`DEEPSEEK_API_KEY`、`GEMINI_API_KEY` | 官方四家密钥（原版） |
| `VERTEX_AI_PROJECT`、`VERTEX_AI_LOCATION` | 用 Vertex AI 调 Gemini（原版） |
| `<NAME>_API_KEY` 及第 3 节表中的别名变量 | 各平台密钥，如 `DASHSCOPE_API_KEY` |
| `<NAME>_BASE_URL` | 改某个平台的接口地址，如 `OPENAI_BASE_URL` |
| `<NAME>_DEFAULT_MODEL` | 改某个平台的默认模型 |
| `PO_PROVIDERS_FILE` | 自定义平台文件路径（默认 `providers.json`） |
| `PO_AUTO_MODEL` | 所有 `auto` 任务统一用的模型 |
| `PO_TASK_MODEL_<任务>` | 单个 `auto` 任务用的模型 |
| `PO_DEFAULT_PROVIDER` | 认不出平台的模型名发到哪个平台 |
| `SEMANTIC_SCHOLAR_API_KEY` | Semantic Scholar 密钥（原版） |
| `S2_MIN_INTERVAL_SEC` | Semantic Scholar 最小请求间隔，默认 1.1 秒 |
| `PO_ENRICH_TIME_BUDGET_MIN` | 逐篇核实文献的时间预算（分钟），默认 15，`0` 不限 |
| `PO_IMAGE_REQUEST_MAX_MB` | 一次请求里所有图片合计的上限（MB），默认 12，`0` 不限 |
| `PO_PRICING_ONLINE` | `0` 时费用估算不联网更新价目表和汇率（默认联网，一天最多一次） |
| `PO_PRICING_CACHE_DIR` | 价目表和汇率缓存的位置，默认 `~/.cache/paper-orchestra` |
| `PO_SCHOLAR_SOURCES` | 启用哪些检索源、按什么顺序 |
| `OPENALEX_EMAIL`、`OPENALEX_API_KEY` | OpenAlex |
| `CROSSREF_MAILTO`、`CROSSREF_PLUS_TOKEN` | Crossref |
| `NCBI_API_KEY`、`NCBI_EMAIL` | PubMed |
| `CORE_API_KEY` | CORE（必填才启用） |
| `SMTP_EMAIL`、`SMTP_PASSWORD` | 完成后发邮件通知（原版） |
| `HTTPS_PROXY` | 需要代理才能访问的平台（Python 网络库会自动读取） |
