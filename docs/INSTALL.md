# 下载与安装 PaperOrchestra Studio

PaperOrchestra Studio 由两部分组成：**桌面应用**（界面）和 **Python 后端**（真正写论文、编译 PDF 的流水线）。两样都要装，总共大约 10 分钟。

所有文件都在 [Releases 页面](https://github.com/FelixJiashengFu/paper-orchestra-studio/releases/latest)。

| 文件 | 用途 |
| --- | --- |
| `PaperOrchestra-Studio_<版本>_x64-setup.exe` | Windows 10/11 桌面应用安装程序 |
| `PaperOrchestra-Studio_<版本>_universal.dmg` | macOS 11+ 桌面应用（Intel 与 Apple 芯片通用） |
| `PaperOrchestra-Backend-<版本>.zip` | Python 后端与一键安装脚本（必装） |
| `paper-orchestra-studio-<版本>-<平台>.zip` | 可选：智能体平台插件，`<平台>` 为 `claude-code` / `antigravity` / `codex` / `qoder` / `workbuddy`，装法见 [plugins/README.md](../plugins/README.md) |
| `paper-orchestra-studio-<版本>.mcpb` | 可选：Claude Desktop 一键扩展 |
| `PaperOrchestra-LiveWorkbench-<版本>.zip` | 可选：浏览器里的实时论文工作台 |
| `SHA256SUMS.txt` | 文件校验值 |

桌面应用的版本号独立于 Release 版本：v1.2.0 Release 里的桌面安装包文件名仍带 `0.1.0`，这是正常的。

## 准备工作

- **Python 3.11 或更新版本**
  - Windows：<https://www.python.org/downloads/>，安装时勾选 “Add python.exe to PATH”；或在 PowerShell 运行 `winget install Python.Python.3.12`
  - macOS：`brew install python@3.12`，或从 python.org 下载
- **TeX 发行版**（编译 PDF 用，不装也能打开 Studio，但出不了 PDF）
  - Windows：MiKTeX（`winget install MiKTeX.MiKTeX`），另装 Perl 供 latexmk 使用（`winget install StrawberryPerl.StrawberryPerl`）；或 TeX Live
  - macOS：MacTeX（`brew install --cask mactex-no-gui`）或 BasicTeX
- **至少一个大模型 API Key**：Anthropic、OpenAI、DeepSeek、Gemini 任选，或本地 Ollama。

## 第 1 步：安装 Python 后端（先装这个）

1. 下载 `PaperOrchestra-Backend-<版本>.zip` 并解压。
2. 运行安装脚本：
   - **Windows**：在解压出的 `PaperOrchestra-Backend` 文件夹空白处按住 Shift 右键 →「在此处打开 PowerShell 窗口」，运行
     ```powershell
     powershell -ExecutionPolicy Bypass -File .\setup-backend.ps1
     ```
   - **macOS / Linux**：在终端进入该文件夹，运行
     ```bash
     bash setup-backend.sh
     ```
3. 脚本会把后端装到 `用户目录/paper-orchestra`，创建独立的 `.venv` 环境并安装依赖，最后检查 TeX。看到 `Backend ready.` 即完成，解压的文件夹可以删掉。

想装到别处：Windows 加 `-Target D:\paper-orchestra`，macOS 加 `--target ~/somewhere`（之后要在 Studio 设置里填这个路径）。
以后升级后端：下载新版 zip 再运行一次脚本即可，`.venv`、`.env` 和 API Key 设置都会保留。

## 第 2 步：安装桌面应用

### Windows

1. 双击 `PaperOrchestra-Studio_<版本>_x64-setup.exe`。
2. 安装程序没有代码签名，Windows 可能弹出「Windows 已保护你的电脑」：点「更多信息」→「仍要运行」。
3. 默认装在 `%LOCALAPPDATA%\PaperOrchestra Studio`，不需要管理员权限。缺 WebView2 时安装程序会自动下载。

### macOS

1. 打开 `.dmg`，把 PaperOrchestra Studio 拖进「应用程序」。
2. 应用没有经过 Apple 公证，第一次打开会被拦下：在「应用程序」里**右键 → 打开 → 打开**；或到「系统设置 → 隐私与安全性」点「仍要打开」。
3. 如果提示「已损坏，无法打开」，在终端运行一次：
   ```bash
   xattr -dr com.apple.quarantine "/Applications/PaperOrchestra Studio.app"
   ```

## 第 3 步：首次配置

1. 打开 Studio，点左侧的「环境与算力」。
2. 后端装在默认位置时，「本地环境」里的 **PaperOrchestra 仓库目录**和 **Python 解释器**会自动填好（指向 `paper-orchestra/.venv` 里的 python）。如果你在装后端之前就打开过 Studio，或者装到了别处，请手动填上安装脚本最后打印的两个路径并保存。
3. 在同一页的预设平台里填入 API Key，左下角的环境监测会显示 Python、LaTeX 和各平台是否就绪。
4. 新建课题，选择期刊模板，就可以开始了。

## 常见问题

- **脚本提示找不到 Python**：装好 Python 后重新打开 PowerShell / 终端再运行；也可以用 `-Python C:\path\to\python.exe`（macOS 用 `--python /path/to/python3`）指定。
- **pip 安装失败**：多半是网络问题。国内网络可先设置镜像再重跑脚本：`pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple`。
- **PDF 编译失败、中文乱码**：确认 `xelatex` 能在终端里运行；含中文的稿件会自动改用 XeLaTeX 编译。
- **Python 后端启动很慢**：后端装在移动硬盘上时，首次加载 NumPy/Pandas 可能要一分多钟，属正常现象。

---

## English quick guide

1. Install Python 3.11+ and a TeX distribution (MiKTeX/TeX Live on Windows, MacTeX on macOS).
2. Download `PaperOrchestra-Backend-<version>.zip`, unzip it, and run `setup-backend.ps1` (Windows: `powershell -ExecutionPolicy Bypass -File .\setup-backend.ps1`) or `bash setup-backend.sh` (macOS/Linux). It installs the backend to `~/paper-orchestra` with its own `.venv`.
3. Install the desktop app: the `-setup.exe` on Windows (unsigned: *More info → Run anyway*), or the `.dmg` on macOS (not notarized: right-click → *Open*, or run `xattr -dr com.apple.quarantine "/Applications/PaperOrchestra Studio.app"`).
4. Open Studio → 环境与算力 (Environment). The repository folder and Python interpreter are detected automatically when the backend is in `~/paper-orchestra`; otherwise paste the two paths the setup script printed. Add your model API keys on the same page.
