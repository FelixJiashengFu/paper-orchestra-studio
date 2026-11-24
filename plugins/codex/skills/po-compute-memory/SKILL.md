---
name: po-compute-memory
description: >
  This skill should be used when the user asks to "save research preferences or
  long-term memory", "check or test an SSH GPU server", "run a command or copy
  files on a remote GPU host", or "export a report to interactive HTML, Word,
  PDF, or Google Drive" (长期科研记忆、SSH/GPU 算力监控与传输、导出交互式 HTML/Word/PDF/Google Drive).
---

# PaperOrchestra Compute, Memory & Document Export

Manage long-term lab context, remote GPU nodes, and publication-grade document exports via `po_compute_and_memory` and `po_export_document`.

## Instructions

1. **Research Memories & Preferences (`po_compute_and_memory`)**:
   - Store and toggle standing rules (`list_memories`, `save_memory`, `delete_memory`, `get_preferences`, `save_preferences`) so future paper runs and SOPs follow lab conventions consistently.
2. **SSH & GPU Hosts (`po_compute_and_memory`)**:
   - Register hosts (`save_host`), verify connectivity & parse `nvidia-smi` GPU load/VRAM (`test_host`), run commands (`exec_ssh`, secret-redacted), and transfer datasets/weights (`scp_transfer` `upload`/`download`).
3. **Settings, Providers & Desktop Bridge (`po_settings_and_setup`, `po_studio_bridge`)**:
   - Inspect/update SQLite settings (`get_settings`, `update_settings`), validate `providers.json` (`validate_providers_json`), inspect 20-platform boolean credential readiness (`env_readiness`), discover `scripts/setup-backend.*` (`setup_info`), or check/open the Desktop Studio app via `po_studio_bridge`.
4. **Interactive HTML, Word, PDF & Google Drive Export (`po_export_document`)**:
   - Check Pandoc/Chrome/Google Drive readiness via `action='status'`.
   - Export Markdown (`input_md` or `markdown_content`) to offline Claude Artifacts-style interactive HTML (`format='html', interactive_html=true` with sticky TOC, Paper/Light/Dark themes, colored provenance pills, MathML, and offline SVG Mermaid renderer), styled Word (`.docx`), publication PDF, or local Google Drive (`export_google_drive`). Never overwrite existing files when choosing automatic filenames.
