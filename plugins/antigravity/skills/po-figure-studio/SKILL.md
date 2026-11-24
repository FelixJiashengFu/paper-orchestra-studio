---
name: po-figure-studio
description: >-
  Manage and create scientific figures with PaperOrchestra Figure Studio via
  MCP: 3-route generation (`data_code` Matplotlib scripts, `schematic_svg`
  vector diagrams, `concept_image` raster illustrations), versioned figure
  bundles, normalized bounding-box (`bbox`) region-circle local revision, and
  version rollback. Use when the user asks to plot experimental data, draw a
  schematic architecture, revise only a circled sub-region of a figure, or
  manage figure versions.
---

# PaperOrchestra Figure Studio

Operate versioned scientific figures and localized region revisions via `po_figure_studio` on `paper-orchestra-studio_paper_orchestra`.

## Operations

- **`list`**: Inspect all figure bundles under `<dir_path>/figures/bundles`.
- **`ensure_bundle`**: Register an existing standalone image (`.png`/`.jpg`/`.pdf`) and optional plotting script (`code_file`) into a versioned bundle so subsequent revisions track `v1 -> v2 -> ...`.
- **`create`**: Generate a new publication figure using one of three routes:
  - `data_code`: Executable Python/Matplotlib script (accepts `data_file` + `prompt`).
  - `schematic_svg`: Clean vector block/flow architecture SVG.
  - `concept_image`: Vision/image-model conceptual illustration (`image_model` + optional `ref_image`).
- **`revise`**: Modify an existing bundle with `instruction`. To restrict changes strictly to one circled area (e.g., legend overlap or axis label), pass normalized `bbox` in `[0, 1]` either as object `{"x":0.06,"y":0.09,"w":0.28,"h":0.24}` or string `"0.06,0.09,0.28,0.24"` (or open `po_open_workbench(tab='figures')` for interactive drag selection in Antigravity).
- **`restore`**: Roll back a bundle's active image/code to a prior `version` (`v1`, `v2`, ...) and sync back to `base_dir` if provided.

After generating or revising an image, inspect the resulting `.png` directly with `read_file` or interactively inside `po_open_workbench(tab='figures')`.
