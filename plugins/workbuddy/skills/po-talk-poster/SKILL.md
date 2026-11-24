---
name: po-talk-poster
description: >
  This skill should be used when the user asks to "make slides from my
  paper", "prepare a conference talk", "write a speaker script",
  "rehearse Q&A", "make a poster", or "write a plain-language summary"
  (论文转幻灯片、会议报告、演讲稿、答辩问答预演、学术海报、通俗摘要与宣传稿).
---

# PaperOrchestra Talk, Poster & Outreach

Turn a finished manuscript or run into a slide deck, a timed speaker script, a rehearsal question bank, a poster, and short outreach text, using `paper-orchestra-studio` MCP tools.

## Instructions

1. **Gather source material**: `po_inspect_run(action='summary')` for the run inventory, `read_artifact` for `outline.json` and the main `.tex`, and `po_figure_studio(action='list')` for existing figure versions. Ask for the talk length, audience, and venue format (talk, spotlight, poster size) before drafting.

2. **Slides**:
   - Budget about one slide per minute. Fixed spine: title, problem and why it matters, gap, key idea in one picture, method (two to three slides), main result, supporting results, limitations, takeaway.
   - One message per slide written as a full-sentence title; body is a figure or at most four short lines.
   - Reuse paper figures; request simplified variants with `po_figure_studio` (`data_code` or `schematic_svg` routes, or a `bbox` local revision to enlarge labels) instead of shrinking dense paper figures.
   - Write the deck as Beamer LaTeX (`aspectratio=169`) or as Markdown. Compile Beamer through `po_latex_workbench(action='compile')` when the source lives in a run directory, or export Markdown with `po_export_document(format='pdf'|'html')`.

3. **Speaker script**: per slide, 90 to 130 spoken words, with a running time total checked against the slot. Mark the two slides to skip if time runs short.

4. **Q&A rehearsal**: produce twelve questions in three groups (clarifying, sceptical, hostile). Answer each from the manuscript only, citing section or figure; questions the paper cannot answer are listed as open with a suggested honest reply.

5. **Poster**:
   - Layout for the stated size (A0 portrait or 48x36 in landscape by default): title band, three columns, reading order left to right, central main-result figure at least one third of the area, under 600 words, QR code placeholder for the paper link.
   - Deliver as LaTeX (`tikzposter` or `beamerposter`) compiled with `po_latex_workbench`, plus a plain-text content sheet.

6. **Outreach text**: a 150-word plain-language summary, a three-post thread, and a one-paragraph lab-website blurb. No result may be stronger than the abstract states it.

## Rules

- Numbers on slides and posters must match the manuscript exactly; re-check each against the source before delivery.
- Do not add results, baselines, or citations that are not in the paper.
