# PaperOrchestra Studio for Qoder

This Qoder Desktop plugin connects Qoder to the local PaperOrchestra Studio application. It reuses Studio's projects, model routing, providers, literature library, research workflows, figures, LaTeX tools, audits and exports.

## Requirements

- Qoder Desktop with local Plugin support.
- Node.js 20 or newer. Qoder supplies its Node runtime to the bundled MCP server.
- A PaperOrchestra Studio desktop build that includes the local Codex/plugin bridge. The bridge protocol and `codex-bridge.json` filename are retained for compatibility with Studio.
- The Python backend and any TeX tools already configured in Studio.

The standard Windows installation is `%LOCALAPPDATA%\PaperOrchestra Studio\paper-orchestra-desktop.exe`. The MCP server can also start Studio automatically. For a custom installation, set `PAPER_ORCHESTRA_STUDIO_APP` to the absolute executable path. If multiple Studio data directories are in use, set the same absolute `PAPER_ORCHESTRA_STUDIO_DATA_DIR` for Studio and Qoder.

## Use

Install `paper-orchestra-studio-0.1.0-qoder.zip` as a local Plugin. In a new Qoder session, make sure the Plugin and its `paper-orchestra-studio` Skill are selected for the active Agent. Then ask, for example:

- List my PaperOrchestra Studio projects.
- Check Studio's templates, model routing and TeX environment.
- Audit my latest manuscript and check its references.
- Profile this data file in PaperOrchestra Studio.

Use `studio_status` to diagnose the local connection and `open_studio` to bring the desktop application to the requested tab.

## Capabilities

| Area | Available operations |
| --- | --- |
| Projects and models | List projects and templates, import intake forms, update manuscript materials, add figures, inspect providers and per-step model routing. |
| Writing pipeline | Start, inspect, stop or resume runs; read logs; locate final PDF, citation and usage reports. |
| Research workshops | Paper reading, literature review, topic ideation, reproduction, mathematical modeling, data profiling and reproducible analysis. |
| Literature and figures | Multi-source search, DOI/PDF metadata, library records, knowledge graphs, citations, figure creation, partial revision and version recovery. |
| LaTeX and submission | Read/edit/restore/compile source, SyncTeX, Copilot, citation tracking, offline and online audits, Nature checks, blind checks, Lean, latexdiff, arXiv and Overleaf export. |
| Memory and compute | Saved preferences and skills, environment checks and read-only diagnostics for registered SSH/GPU hosts. Credential management and interactive SSH/SCP remain in Studio. |
| Artifacts and export | Open the interactive reader and export Markdown documents to PDF, DOCX, HTML or a configured Google Drive sync directory. |

Background workshop operations return a `jobId`; query `job_status` until completion. Paper pipeline runs are persisted by Studio and can be tracked with `list_runs`. Closing Studio ends the tasks it hosts.

## Local security boundary

The bundled MCP server communicates only with Studio's loopback bridge and uses the current user's private bridge token. Studio's configured providers and online literature services still make their normal outbound requests. Provider keys, SSH credentials and backend paths remain managed by Studio.
