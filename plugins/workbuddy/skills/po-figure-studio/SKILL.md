---
name: po-figure-studio
description: >
  This skill should be used when the user asks to "plot scientific figures",
  "create a schematic diagram SVG", "draw a concept illustration",
  "revise only part of a figure with a bounding box", or "restore an earlier
  figure version" (科研绘图生成、矢量结构图、概念图、框选局部精修、版本回退).
---

# PaperOrchestra Figure Studio

Create, version, locally revise, and restore scientific figures via `po_figure_studio`.

## Instructions

- **List Bundles (`action='list'`)**: Inspect existing figure bundles under `<dir_path>/figures/bundles`.
- **Register Image (`action='ensure_bundle'`)**: Wrap an existing `.png`/`.jpg`/`.pdf` and optional script (`code_file`) into a versioned bundle.
- **Create Figure (`action='create'`)**: Generate figures via `mode='data_code'` (Python/Matplotlib), `mode='schematic_svg'` (vector schematic), or `mode='concept_image'` (conceptual illustration).
- **Region-Circle Local Revision (`action='revise'`)**: Apply targeted edits with `instruction` and normalized `bbox` in `[0, 1]` either as object `{"x":0.06,"y":0.09,"w":0.28,"h":0.24}` or string `"0.06,0.09,0.28,0.24"` so uncircled regions stay intact.
- **Restore Version (`action='restore'`)**: Roll back to any saved version (`v1`, `v2`, ...) cleanly.
