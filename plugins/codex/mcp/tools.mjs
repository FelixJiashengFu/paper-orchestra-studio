import { isAbsolute } from "node:path";

const string = (description = "") => ({ type: "string", description });
const bool = (description = "") => ({ type: "boolean", description });
const integer = (minimum = 0, maximum) => ({ type: "integer", minimum, ...(maximum === undefined ? {} : { maximum }) });
const number = (minimum = 0, maximum) => ({ type: "number", minimum, ...(maximum === undefined ? {} : { maximum }) });
const choice = (...values) => ({ type: "string", enum: values });
const array = (items) => ({ type: "array", items });
const object = (properties, required = []) => ({ type: "object", properties, required, additionalProperties: false });
const path = string("Absolute path on the machine running Studio.");
const relativeFile = string("File path relative to the Studio run or its editable LaTeX folder.");
const id = string("The ID returned by Studio.");
const model = string("Studio model name or provider:model. Omit for Studio's auto routing.");
const runDir = string("Absolute runDir returned by Studio.");
const format = choice("docx", "pdf", "html");
const jobId = { ...id, pattern: "^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$" };
const tool = (name, description, properties = {}, required = [], options = {}) => ({
  name,
  description,
  inputSchema: object(properties, required),
  annotations: {
    readOnlyHint: options.read === true,
    destructiveHint: options.destructive === true,
    openWorldHint: options.online === true,
  },
  command: options.command ?? name,
  args: options.args ?? ((args) => args),
  background: options.background === true,
  local: options.local === true,
});
const read = { read: true };
const online = { online: true, background: true };
const background = { background: true };
const figure = (args, options = {}) => ({ command: "run_figure_studio", args: (input) => ({ args: args(input) }), ...options });
const library = (args, options = {}) => ({
  command: "run_library_tool",
  args: (input) => ({ args: args(input), itemsPayload: input.items }),
  ...options,
});
const latex = (args, options = {}) => ({ command: "run_latex_copilot", args: (input) => ({ args: args(input) }), ...options });

const localPath = (value, key) => {
  if (typeof value !== "string" || !isAbsolute(value)) throw new Error(`${key} must be an absolute path.`);
  return value;
};
const optional = (flag, value) => value === undefined || value === "" ? [] : [flag, String(value)];
const switchOn = (flag, enabled) => enabled ? [flag] : [];
const input = (args) => ({ input: args });

const projectFields = {
  title: string(),
  templateName: string("Exact template ID from check_environment."),
  writerModel: model,
  reflectionModel: model,
  plottingModel: model,
  imageModel: model,
  researchCutoff: string("YYYY-MM, or empty for Studio's current-month default."),
  usePlotting: bool(),
  plottingMaxCriticRounds: integer(1, 10),
  ideaMarkdown: string(),
  experimentalLogMarkdown: string(),
};
const libraryItem = {
  id, projectId: id, title: string(), folder: string(), authors: string(), year: integer(1),
  venue: string(), doi: string(), arxivId: string(), url: string(), pdfPath: path,
  bibtex: string(), gb7714: string(), abstractText: string(), aiNotes: string(),
  keyClaims: array(object({ claim: string(), evidence: string(), location: string() })),
  tags: array(string()), readStatus: string(),
};
const bbox = object({ x: number(0, 1), y: number(0, 1), w: number(0, 1), h: number(0, 1) }, ["x", "y", "w", "h"]);

export const tools = [
  tool("studio_status", "Check whether the local PaperOrchestra Studio app and its Codex bridge are available.", {}, [], { local: true, ...read }),
  tool("open_studio", "Open Studio and navigate to a view. Use it for interactive LaTeX/PDF, Figure Bench, Artifacts, credentials and SSH/SCP.",
    { tab: choice("workspace", "studio", "library", "runs", "artifacts", "settings") }, [], { local: true }),
  tool("list_jobs", "Find recent Codex-started jobs while Studio is still open.", {}, [], { local: true, ...read }),
  tool("job_status", "Read a Studio job's status, recent stage/log/usage events and final result.", { jobId }, ["jobId"], { local: true, ...read }),
  tool("cancel_job", "Request cancellation of a running SOP, figure creation/revision, data analysis or custom skill job.",
    { jobId }, ["jobId"], { local: true }),

  tool("get_settings", "Read the Studio backend, interpreter, output and database locations.", {}, [], read),
  tool("check_environment", "List Studio's available templates, enabled providers, model routing, scholar sources and TeX readiness.", {}, [], read),
  tool("check_connections", "Check configured provider/scholar status. Opt into live pings or listing models using flags.",
    { ping: bool("Send a small model request, which may incur provider charges."), scholar: bool("Query configured literature APIs."),
      listModels: bool("Query providers for model catalogs."), pingModels: { type: "object", additionalProperties: string() } },
    [], { ...online, args: ({ ping = false, scholar = false, ...opts }) => ({ ping, scholar, ...opts }) }),

  tool("list_projects", "List existing Studio projects and their run counts.", {}, [], read),
  tool("get_project", "Read a project, its current updatedAt version, figures and manuscript inputs.", { id }, ["id"], read),
  tool("create_project", "Create a project in Studio.", { title: string(), templateName: projectFields.templateName }, ["title", "templateName"]),
  tool("update_project", "Update only the supplied project fields. Read the project first for expectedUpdatedAt.",
    { id, expectedUpdatedAt: string(), patch: object(projectFields) }, ["id", "expectedUpdatedAt", "patch"]),
  tool("delete_project", "Delete a project and its Studio database records. Requires the user's explicit request for that project.",
    { id }, ["id"], { destructive: true }),
  tool("import_cvpr_example", "Import Studio's CVPR example as a project."),
  tool("add_project_figure", "Attach a local figure and caption to a project.", { projectId: id, sourcePath: path, caption: string() },
    ["projectId", "sourcePath"], { args: ({ projectId, sourcePath, caption = "" }) => input({ projectId, sourcePath: localPath(sourcePath, "sourcePath"), caption }) }),
  tool("update_project_figure_caption", "Replace the caption of a saved project figure.", { figureId: id, caption: string() }, ["figureId", "caption"]),
  tool("delete_project_figure", "Remove a saved figure from the specified project.", { figureId: id }, ["figureId"], { destructive: true }),
  tool("parse_intake_form", "Extract project fields from a PDF, DOCX, Markdown or text intake form.", { path }, ["path"],
    { ...read, ...background, args: ({ path }) => ({ path: localPath(path, "path") }) }),
  tool("start_run", "Start the configured paper pipeline with the project's idea, experimental log, figures, template and model routing.",
    { projectId: id }, ["projectId"], { online: true }),
  tool("resume_run", "Resume a previous pipeline run from its checkpoint.", { runId: id }, ["runId"], { online: true }),
  tool("stop_run", "Stop the specified pipeline run.", { runId: id }, ["runId"]),
  tool("delete_run", "Remove a run from Studio and apply its native deletion behavior. Requires the user's explicit request.",
    { runId: id }, ["runId"], { destructive: true }),
  tool("list_runs", "List a project's runs, stages, states and output paths.", { projectId: id }, ["projectId"], read),
  tool("get_run_logs", "Read persisted pipeline logs after a log ID, with a limited batch.",
    { runId: id, afterId: integer(0), limit: integer(1, 200) }, ["runId"],
    { ...read, args: ({ runId, afterId = 0, limit = 60 }) => ({ runId, afterId, limit }) }),

  tool("get_run_artifacts_summary", "List available final PDF, LaTeX, figures, citation, usage and refinement artifacts.", { runDir }, ["runDir"], read),
  tool("read_run_text_artifact", "Read a text artifact inside a registered run, such as citation_audit.json or usage_report.json.",
    { runDir, relativePath: relativeFile }, ["runDir", "relativePath"], read),
  tool("copy_run_pdf", "Copy a registered run's final PDF to a new absolute destination.", { runDir, destPath: path }, ["runDir", "destPath"]),
  tool("export_run_latex", "Create an Overleaf-compatible ZIP from Studio's LaTeX export source.", { runDir, destPath: path }, ["runDir", "destPath"]),

  tool("run_submission_tool", "Check manuscript length and style, double blindness, logs, citations or Lean proofs, or create an arXiv source package or latexdiff PDF.",
    { operation: choice("manuscript-audit", "blind", "diagnose", "cite-online", "lean-audit", "versions", "arxiv", "diff"),
      directory: path, oldTex: path, newTex: path, outputPath: path,
      keepComments: bool(), mergeInputs: bool(), findPackages: bool(), onlyChangedPages: bool() },
    ["operation"], { ...online, args: (a) => {
      const { operation } = a;
      const args = [operation];
      if (operation === "diff") {
        args.push(localPath(a.oldTex, "oldTex"), localPath(a.newTex, "newTex"), "--out", localPath(a.outputPath, "outputPath"));
        args.push(...switchOn("--only-changed-pages", a.onlyChangedPages));
      } else {
        args.push(localPath(a.directory, "directory"));
        if (operation === "arxiv") {
          args.push("--out", localPath(a.outputPath, "outputPath"));
          args.push(...switchOn("--keep-comments", a.keepComments), ...switchOn("--merge-inputs", a.mergeInputs));
        }
        if (operation === "diagnose") args.push(...switchOn("--find-packages", a.findPackages));
      }
      return { args };
    } }),

  tool("live_edit_open", "Discover a registered run's editable LaTeX/BibTeX paths, main file, write state and PDF paths.", { runDir }, ["runDir"], read),
  tool("live_edit_read", "Read an editable TeX/BibTeX file and its mtimeMs for safe editing.", { runDir, file: relativeFile }, ["runDir", "file"], read),
  tool("live_edit_save", "Save a LaTeX/BibTeX replacement with conflict detection and the Studio backup.",
    { runDir, file: relativeFile, content: string(), expectedMtimeMs: integer() }, ["runDir", "file", "content", "expectedMtimeMs"]),
  tool("live_edit_restore", "Restore the original from Studio's edit backup.", { runDir, file: relativeFile }, ["runDir", "file"], { destructive: true }),
  tool("live_edit_compile", "Compile the editable source into Studio's scratch preview, including a SyncTeX map and diagnostic issues.",
    { runDir, mainFile: relativeFile }, ["runDir", "mainFile"], background),
  tool("live_edit_sync_view", "Map a one-based TeX line and column to the last compiled preview's PDF box.",
    { runDir, mainFile: relativeFile, file: relativeFile, line: integer(1), column: integer(1) },
    ["runDir", "mainFile", "file", "line", "column"], read),
  tool("live_edit_sync_edit", "Map a PDF preview point (one-based page, x/y in points from top left) to its source file and line.",
    { runDir, mainFile: relativeFile, page: integer(1), x: number(), y: number() },
    ["runDir", "mainFile", "page", "x", "y"], read),
  tool("latex_copilot_edit", "Ask Studio's provider for a LaTeX edit proposal. Review the returned changes before writing.",
    { file: path, instruction: string(), selectionStart: integer(1), selectionEnd: integer(1), errorLog: string(), model },
    ["file", "instruction"], latex((a) => [
      "edit", "--file", localPath(a.file, "file"), "--instruction", a.instruction,
      ...optional("--selection-start", a.selectionStart), ...optional("--selection-end", a.selectionEnd),
      ...optional("--error-log", a.errorLog), ...optional("--model", a.model),
    ], online)),
  tool("trace_latex_citations", "Trace LaTeX citations back to Studio literature and audit metadata.", { runDir, texFile: path }, ["runDir"],
    latex((a) => ["trace-citations", "--run-dir", localPath(a.runDir, "runDir"), ...optional("--tex-file", a.texFile)], read)),

  tool("run_sop_workflow", "Run a Studio SOP and stream its stages and token usage. Data analysis has a separate executable tool.",
    { sop: choice("paper_reading", "lit_review", "topic_ideation", "reproduction", "math_modeling"),
      payload: object({ topic: string(), question: string(), notes: string(), files: array(path), queryOnline: bool(),
        extra: { type: "object" }, preferencesMarkdown: string(), libraryItems: array(object(libraryItem)) }),
      model },
    ["sop", "payload"], online),
  tool("run_data_profile", "Profile a local table with Studio, including numeric summaries and missing data.", { filePath: path }, ["filePath"],
    { ...read, args: ({ filePath }) => ({ filePath: localPath(filePath, "filePath") }) }),
  tool("run_data_analysis", "Run Studio's reproducible data analysis and return its reports, cleaning artifacts, scripts and figures.",
    { filePath: path, question: string(), model }, ["filePath", "question"], online),

  tool("list_figures", "List Studio Figure Bench bundles and available versions.", { directory: path }, [],
    figure((a) => ["list", "--dir", a.directory ?? ""], read)),
  tool("register_figure", "Import an image into a versioned Figure Bench bundle, optionally with an existing source script.",
    { directory: path, image: path, title: string(), codeFile: path, description: string() }, ["image"],
    figure((a) => ["ensure-bundle", "--dir", a.directory ?? "", "--image", localPath(a.image, "image"),
      ...optional("--title", a.title), ...optional("--code-file", a.codeFile), ...optional("--description", a.description)])),
  tool("create_figure", "Generate a versioned scientific plot, SVG schematic, or concept image with Studio.",
    { outDir: path, mode: choice("data_code", "schematic_svg", "concept_image"), prompt: string(), title: string(),
      dataFile: path, refImage: path, model, imageModel: model }, ["mode", "prompt"],
    figure((a) => ["create", "--out-dir", a.outDir ?? "", "--mode", a.mode, "--prompt", a.prompt,
      ...optional("--title", a.title), ...optional("--data-file", a.dataFile), ...optional("--ref-image", a.refImage),
      ...optional("--model", a.model), ...optional("--image-model", a.imageModel)], online)),
  tool("revise_figure", "Create a new Figure Bench version. Supply a normalized top-left rectangle for a localized edit.",
    { bundleDir: path, instruction: string(), bbox, baseDir: path, model, imageModel: model }, ["bundleDir", "instruction"],
    figure((a) => {
      const box = a.bbox;
      if (box && (box.w <= 0 || box.h <= 0 || box.x + box.w > 1 || box.y + box.h > 1)) {
        throw new Error("The rectangle must lie within the figure and have positive dimensions.");
      }
      return ["revise", "--bundle", localPath(a.bundleDir, "bundleDir"), "--instruction", a.instruction,
        ...optional("--bbox", box && [box.x, box.y, box.w, box.h].join(",")), ...optional("--base-dir", a.baseDir),
        ...optional("--model", a.model), ...optional("--image-model", a.imageModel)];
    }, online)),
  tool("restore_figure", "Set a previous Figure Bench version as active and synchronize its configured output.",
    { bundleDir: path, version: string(), baseDir: path }, ["bundleDir", "version"],
    figure((a) => ["restore", "--bundle", localPath(a.bundleDir, "bundleDir"), "--version", a.version, ...optional("--base-dir", a.baseDir)], { destructive: true })),

  tool("list_library_items", "List saved Studio literature with optional folder filter and pagination.",
    { folder: string(), offset: integer(0), limit: integer(1, 100) }, [], read),
  tool("save_library_item", "Save a new or existing Studio literature item. For an update, include all fields to retain.",
    libraryItem, ["title"], { args: input }),
  tool("delete_library_item", "Delete a specified saved literature record.", { id }, ["id"], { destructive: true }),
  tool("search_library_online", "Search Studio's configured scholarly sources, optionally expanding the query.",
    { query: string(), limit: integer(1, 40), expand: bool(), model }, ["query"],
    library((a) => ["search-online", "--query", a.query, "--limit", String(a.limit ?? 15),
      ...switchOn("--expand", a.expand), ...optional("--model", a.model)], online)),
  tool("import_library_pdf", "Extract metadata and notes from a local research PDF. Save the returned item explicitly.",
    { pdfPath: path, model, noLlm: bool() }, ["pdfPath"],
    library((a) => ["import-pdf", "--pdf", localPath(a.pdfPath, "pdfPath"),
      ...optional("--model", a.model), ...switchOn("--no-llm", a.noLlm)], online)),
  tool("import_library_doi", "Resolve DOI metadata for a study. Save the resulting item explicitly.", { doi: string() }, ["doi"],
    library((a) => ["import-doi", "--doi", a.doi], online)),
  tool("build_library_graph", "Build a citation and concept graph, reading route and comparison matrix from supplied records.",
    { items: array({ type: "object" }) }, ["items"], library(() => ["build-graph"], background)),
  tool("export_library_bib", "Generate BibTeX or GB/T 7714 from supplied literature items.",
    { items: array({ type: "object" }), format: choice("bibtex", "gb7714") }, ["items", "format"],
    library((a) => ["export-bib", "--format", a.format], read)),

  tool("list_research_memories", "Read Studio's persistent research notes.", {}, [], read),
  tool("save_research_memory", "Save an explicitly requested long-term research note.",
    { id, title: string(), category: string(), content: string(), enabled: bool() }, ["title", "content"],
    { args: ({ enabled = true, ...a }) => input({ ...a, enabled }) }),
  tool("delete_research_memory", "Delete a specified saved research note.", { id }, ["id"], { destructive: true }),
  tool("get_research_preferences", "Read the writing and citation preferences used by Studio's research workflows.", {}, [], read),
  tool("save_research_preferences", "Set Studio's writing and citation preferences. Read the current values before changing selected fields.",
    { outputLanguage: string(), citationStyle: string(), deliveryFormat: string(), customRules: string() },
    ["outputLanguage", "citationStyle", "deliveryFormat", "customRules"], { args: (prefs) => ({ prefs }) }),
  tool("list_ssh_hosts", "List Studio's configured compute hosts and recorded GPU status.", {}, [], read),
  tool("test_ssh_host", "Run Studio's built-in connectivity and GPU diagnostics for a registered compute host.",
    { id }, ["id"], online),

  tool("list_skills", "List the built-in and user-imported skills available in Studio.", {}, [], read),
  tool("get_skill", "Read one Studio skill by ID before running or updating it.", { id }, ["id"], read),
  tool("delete_skill", "Delete a specified user-imported Studio skill.", { skillId: id }, ["skillId"], { destructive: true }),
  tool("save_skill", "Save a Studio custom workflow supplied by the user.",
    { id, name: string(), description: string(), category: string(), content: string() },
    ["name", "content"], { args: input }),
  tool("import_skill_from_file", "Import a local SKILL.md (or Markdown file) into Studio's skill library.", { path }, ["path"],
    { args: ({ path }) => ({ path: localPath(path, "path") }) }),
  tool("import_skill_from_url", "Import a user-supplied GitHub skill URL into Studio's skill library.", { url: string() }, ["url"], online),
  tool("run_custom_skill", "Run a Studio skill with a prompt and optional local files, using Studio's model and research preferences.",
    { skillId: id, prompt: string(), attachedFiles: array(path), model }, ["skillId", "prompt"], {
      ...online, args: ({ attachedFiles = [], ...a }) => ({ ...a, attachedFiles: attachedFiles.map((file) => localPath(file, "attachedFiles")) }),
    }),

  tool("get_export_environment", "Check Studio's default PDF and Google Drive export locations and available exporters.", {}, [], read),
  tool("export_markdown_document", "Render Markdown with Studio to a new Word, PDF or HTML file at an explicit path.",
    { markdown: string(), title: string(), format, outputPath: path }, ["markdown", "format", "outputPath"],
    { ...background, args: ({ title = "", ...a }) => ({ title, ...a }) }),
  tool("export_markdown_to_google_drive", "Write a Markdown document to the configured local synced Google Drive folder.",
    { markdown: string(), title: string(), format: choice("docx", "pdf"), driveDir: path }, ["markdown", "format"],
    { ...online, args: ({ title = "", ...a }) => ({ title, ...a }) }),
];

export function validate(value, schema, field = "arguments") {
  if (!schema || Object.keys(schema).length === 0) return;
  const type = Array.isArray(value) ? "array" : value === null ? "null" : typeof value;
  if (schema.type && type !== schema.type && !(schema.type === "integer" && Number.isSafeInteger(value))) {
    throw new Error(`${field} must be ${schema.type}.`);
  }
  if (schema.enum && !schema.enum.includes(value)) throw new Error(`Invalid ${field}.`);
  if (schema.pattern && !new RegExp(schema.pattern).test(value)) throw new Error(`Invalid ${field}.`);
  if (type === "number" && (!Number.isFinite(value) || value < (schema.minimum ?? -Infinity) || value > (schema.maximum ?? Infinity))) {
    throw new Error(`${field} is out of range.`);
  }
  if (type === "array") value.forEach((item, index) => validate(item, schema.items, `${field}[${index}]`));
  if (type === "object") {
    for (const key of schema.required ?? []) {
      if (!Object.hasOwn(value, key)) throw new Error(`${field}.${key} is required.`);
    }
    for (const [key, item] of Object.entries(value)) {
      const properties = schema.properties ?? {};
      if (Object.hasOwn(properties, key)) validate(item, properties[key], `${field}.${key}`);
      else if (schema.additionalProperties === false) throw new Error(`Unknown field ${field}.${key}.`);
      else if (typeof schema.additionalProperties === "object") validate(item, schema.additionalProperties, `${field}.${key}`);
    }
  }
}
