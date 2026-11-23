# PaperOrchestra Studio

**本地 AI 科研论文生成流水线与 LaTeX 桌面工作台** · *Local AI-Native Scientific Writing Pipeline & Tauri LaTeX Workbench*

基于 Google 开源的多智能体论文框架 [`google-research/paper-orchestra`](https://github.com/google-research/paper-orchestra) 深度扩展而来：保留原有零回退兼容性的同时，提供跨平台（macOS / Windows）桌面工作台、20+ 模型平台按步路由、8 源学术检索、双向 SyncTeX 实时编辑器、16 套会议/期刊 LaTeX 模板，以及离线投稿核查与论文体检工具。

---

## 核心特性

* **20+ 模型平台与 19 步分任务路由**
  * 内置 OpenAI、Anthropic、Gemini、DeepSeek、通义千问、Kimi、智谱 GLM、腾讯混元、百度文心、硅基流动、OpenRouter、Ollama / LM Studio / vLLM 等平台，并支持 `providers.json` 自定义网关与中转站；
  * 支持工作台按步骤单独指定模型（大纲、文献筛选、绘图规划/风格/图像生成、正文写作、内容润色、模拟审稿等），自动记录 Token 用量与实时费用估算（`usage_report.json`），密钥仅保存于本机 `paper-orchestra/.env` 并在日志中自动打码。
* **8 源限速文献检索与双层引文核查**
  * 集成 Semantic Scholar、arXiv、OpenAlex、Crossref、dblp、Europe PMC、PubMed、CORE，共享限速器防 429；
  * 每次运行自动产出离线 `citation_audit.json / .md`，并可在桌面端一键联网对照 Crossref / arXiv / Semantic Scholar 核验 DOI、作者与年份（`cite-online`）。
* **16 套会议与期刊 LaTeX 模板**
  * **会议模板（8 套）**：`CVPR 2025`、`ICLR 2025`、`NeurIPS 2026`、`ICML 2026`、`AAAI 2027`、`ACL / ARR`、`IEEE Conference`、`ACM sigconf`；
  * **期刊模板（8 套）**：*Nature*、*Nature Methods*、*Nature Biotechnology*、*npj Computational Materials*、*ACS (JCTC / JPC / JCIM)*、*AIP J. Chem. Phys.*、*RSC PCCP*、*Elsevier Comput. Mater. Sci.*；
  * 期刊模板由 `venue.json` 驱动写作风格（如无单独 Related Work、Claim-Driven 结构、去 AI 味与统计报告规范），会议模板提示词保持原版一字不变。
* **AI-Native LaTeX 工作台与投稿体检（SyncTeX）**
  * 内置 CodeMirror + pdf.js 的「编辑并实时预览」窗口，支持代码与 PDF 双向 SyncTeX 定位；
  * 集成 **论文体检 (Nature)**（剥离注释/浮动体的字数统计、AI 写作痕迹与破折号/分号节奏检查、图表引用与图注核对、草稿占位符扫描、统计要素与期刊声明核查）、**arXiv 投稿包**、**双盲检查**、**LaTeX 日志诊断**、**latexdiff 改动对比**与 **Overleaf 一键导出**。

---

## 仓库结构

```text
paper-orchestra-studio/
├── paper-orchestra/            # Python 多智能体论文流水线、模板库、核查/投稿工具与单元测试
├── paper-orchestra-desktop/    # Tauri (Rust + React/TypeScript) 桌面端应用与打包脚本
├── LICENSE                     # Apache License 2.0
└── THIRD_PARTY_NOTICES.md      # 上游与第三方开源归属声明（Google / Lattice / Nature-Paper-Skills / TeX 样式）
```

---

## 快速开始

### 1. 准备 Python 后端环境（Python 3.11+）

```bash
cd paper-orchestra
python3 -m venv .venv
.venv/bin/python -m pip install --prefer-binary -r requirements.txt
cp .env.example .env
```

* 建议同时安装 TeX 发行版（macOS 推荐 MacTeX / BasicTeX，Windows 推荐 TeX Live 或 MiKTeX）以本地编译 PDF。
* 命令行用法与环境变量参考：[paper-orchestra/README.md](paper-orchestra/README.md) · [paper-orchestra/docs/PROVIDERS_zh.md](paper-orchestra/docs/PROVIDERS_zh.md)。

### 2. 运行或打包桌面应用（Node 20+ / Rust）

```bash
cd paper-orchestra-desktop
npm install
npm run tauri dev          # 开发模式运行
```

生成安装包：
* **macOS**：`./scripts/build-macos.sh`（可选 `universal` 或 `PO_MAC_DMG=1`）
* **Windows**：`powershell -ExecutionPolicy Bypass -File .\scripts\build-windows.ps1`

完整图文使用与配置说明见：[paper-orchestra-desktop/README.md](paper-orchestra-desktop/README.md) 与 [paper-orchestra/CHANGELOG_zh.md](paper-orchestra/CHANGELOG_zh.md)。

---

## 测试

```bash
# Python 单元测试（全部离线运行，无需真实 API Key）
paper-orchestra/.venv/bin/python -m unittest discover -s paper-orchestra/tests -v

# 前端类型检查与 Rust 单元测试
cd paper-orchestra-desktop
npx tsc --noEmit
cargo test --manifest-path src-tauri/Cargo.toml
```

---

## 开源许可与致谢

本项目采用 **Apache License 2.0** 发布，详情见 [LICENSE](LICENSE) 与 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
感谢 [`google-research/paper-orchestra`](https://github.com/google-research/paper-orchestra)、[`leo1oel/lattice`](https://github.com/leo1oel/lattice) 以及 [`Boom5426/Nature-Paper-Skills`](https://github.com/Boom5426/Nature-Paper-Skills) 的开源贡献。
