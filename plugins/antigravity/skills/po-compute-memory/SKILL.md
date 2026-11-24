---
name: po-compute-memory
description: >-
  Manage persistent Research Memories & Preferences, remote SSH/GPU cluster
  nodes (`nvidia-smi` monitoring, remote experiment commands, SCP file
  transfer), and multi-format document export (offline Claude Artifacts-style
  interactive HTML, styled Word `.docx`, publication PDF, and Google Drive sync)
  via PaperOrchestra Studio MCP tools.
---

# PaperOrchestra Compute, Memory & Document Export

Use `po_compute_and_memory` and `po_export_document` on `paper-orchestra-studio_paper_orchestra`.

## Workflows

1. **Long-Term Research Memories & Preferences (`po_compute_and_memory`)**:
   - `list_memories`, `save_memory`, `delete_memory`: Persist lab conventions (e.g., colorblind palettes, significance reporting thresholds, domain notation) across future paper runs and SOPs.
   - `get_preferences`, `save_preferences`: Set default `output_language`, `citation_style`, `delivery_format`, and `custom_rules`.

2. **Remote SSH & GPU Cluster Management (`po_compute_and_memory`)**:
   - `list_hosts`, `save_host`, `delete_host`: Register SSH/GPU servers (validated host/port/username; pasted PEM keys saved cleanly with `0600` permissions).
   - `test_host`: Probe OS & multi-GPU utilization/VRAM telemetry via `nvidia-smi`.
   - `exec_ssh` / `scp_transfer`: Execute training/evaluation commands in `remote_work_dir` (secret-redacted) and upload/download datasets, scripts, or checkpoints safely.

3. **Settings, Providers & Setup (`po_settings_and_setup` / `po_studio_bridge`)**:
   - Inspect/update SQLite paths (`get_settings`, `update_settings`), validate `providers.json` (`validate_providers_json`), inspect 20-platform boolean credential readiness (`env_readiness`, never leaks keys), discover `scripts/setup-backend.*` (`setup_info`), or check/open the Desktop Studio app via `po_studio_bridge`.

4. **Artifacts HTML, Word, PDF & Google Drive Export (`po_export_document`)**:
   - `status`: Check Pandoc, Chrome, Google Drive Desktop mount, and configured `PO_DEFAULT_PDF_DIR` / `PO_DEFAULT_HTML_DIR`.
   - `export`: Export Markdown (`input_md` or raw `markdown_content`) to offline interactive Claude Artifacts-style HTML (`format='html', interactive_html=true` — left TOC sidebar, Paper/Light/Dark themes, colored provenance pills, MathML, offline SVG Mermaid diagrams), Word (`docx`), or publication `pdf`. When `out_path` is omitted, honors `PO_DEFAULT_HTML_DIR` / `PO_DEFAULT_PDF_DIR` without overwriting existing files.
   - `export_google_drive`: Copy `.docx`/`.pdf` directly to local Google Drive Desktop (`PaperOrchestra Exports`).
