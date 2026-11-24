# Integrations

Companion tools that talk to a running PaperOrchestra Studio desktop app through its local Codex/plugin bridge (`codex_bridge.rs`).

| Folder | What it is | How to run |
| --- | --- | --- |
| `qoder-plugin/` | Qoder Desktop plugin: an MCP server (`mcp/server.mjs`) plus the `paper-orchestra-studio` skill, exposing Studio's projects, runs, research workshops, LaTeX and submission tools. | Zip the folder and install it as a local Qoder plugin. See its README. |
| `live-workbench/` | 实时论文工作台: a local Node server and web panel that polls Studio and shows projects, runs and logs live. | `node server.mjs --open` (Node 22+), or `start-panel.cmd` on Windows. |
| `tool-tree/` | Single-page map of the Studio × Qoder tools and how they work together. | Open `index.html` in a browser. |
| `canvas-console/` | Canvas collaboration console component (`.canvas.tsx`). | Load it in a canvas host that supports `.canvas.tsx` files. |

Set `PAPER_ORCHESTRA_STUDIO_APP` / `PAPER_ORCHESTRA_STUDIO_DATA_DIR` when Studio is installed somewhere other than the default location.
