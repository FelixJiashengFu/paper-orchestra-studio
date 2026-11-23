import { openUrl } from "@tauri-apps/plugin-opener";
import DOMPurify from "dompurify";
import katex from "katex";
import "katex/dist/katex.min.css";
import {
  Check,
  ChevronDown,
  Cloud,
  Code2,
  Copy,
  Download,
  ExternalLink,
  Eye,
  FileCode2,
  FileSpreadsheet,
  FileText,
  FolderOpen,
  ListTree,
  Loader2,
  Maximize2,
  Minimize2,
  Moon,
  Sun,
  X,
} from "lucide-react";
import { marked } from "marked";
import { useEffect, useMemo, useRef, useState } from "react";
import { api, formatBytes } from "../api";
import type { ExportEnvironmentInfo } from "../types";

export interface DocumentArtifactViewProps {
  content: string;
  title?: string;
  defaultFilename?: string;
  maxHeight?: number;
  onToast?: (msg: string, isError?: boolean) => void;
}

interface TocHeading {
  id: string;
  text: string;
  level: 1 | 2 | 3;
}

type ArtifactSegment =
  | { kind: "html"; html: string }
  | { kind: "mermaid"; code: string };

function inferTitle(md: string, fallback?: string): string {
  const m = /^\s*#\s+(.+)$/m.exec(md);
  if (m) return m[1].replace(/[`*_]/g, "").trim().slice(0, 120);
  return fallback?.trim() || "研究交付文档";
}

function sanitizeFilename(raw: string): string {
  const s = (raw || "PaperOrchestra_Report")
    .replace(/[\\/:*?"<>|\x00-\x1f]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .replace(/^[.\s]+|[.\s]+$/g, "");
  return (s.slice(0, 72) || "PaperOrchestra_Report").trim();
}

function stripRawTags(s: string): string {
  return s.replace(/<[^>]+>/g, "");
}

function preprocessMarkdownMath(md: string): {
  maskedMd: string;
  mathHtml: Map<string, string>;
} {
  const mathHtml = new Map<string, string>();
  let seq = 0;

  // Step 1: isolate fenced code blocks and inline code spans so `$` inside code is untouched.
  const codeTokens: string[] = [];
  const maskCode = (txt: string) => {
    const token = `@@PO_CODE_${codeTokens.length}@@`;
    codeTokens.push(txt);
    return token;
  };
  let tmp = md.replace(/```[\s\S]*?```/g, (m) => maskCode(m));
  tmp = tmp.replace(/`[^`\n]+`/g, (m) => maskCode(m));

  const renderMathToken = (expr: string, displayMode: boolean): string => {
    const key = `PO_MATH_${seq++}_TOKEN`;
    try {
      const rendered = katex.renderToString(expr.trim(), {
        displayMode,
        throwOnError: false,
        strict: false,
      });
      mathHtml.set(key, rendered);
    } catch {
      mathHtml.set(key, `<code>${stripRawTags(expr)}</code>`);
    }
    return displayMode ? `\n\n<div>${key}</div>\n\n` : key;
  };

  // Display math: $$...$$ and \[...\]
  tmp = tmp.replace(/\$\$([\s\S]+?)\$\$/g, (_m, expr: string) => renderMathToken(expr, true));
  tmp = tmp.replace(/\\\[([\s\S]+?)\\\]/g, (_m, expr: string) => renderMathToken(expr, true));

  // Inline math: \(...\) and $...$ (avoiding literal currency like $100)
  tmp = tmp.replace(/\\\((.+?)\\\)/g, (_m, expr: string) => renderMathToken(expr, false));
  tmp = tmp.replace(
    /(^|[^\\$])\$(?!\s)([^$\n]+?)(?<!\s)\$(?!\d)/g,
    (full, prefix: string, expr: string) => {
      if (/^\d+(?:\.\d+)?$/.test(expr.trim())) return full;
      return `${prefix}${renderMathToken(expr, false)}`;
    }
  );

  // Restore code tokens
  tmp = tmp.replace(/@@PO_CODE_(\d+)@@/g, (_m, idx) => codeTokens[Number(idx)] ?? "");
  return { maskedMd: tmp, mathHtml };
}

function renderDocumentHtml(md: string): {
  segments: ArtifactSegment[];
  headings: TocHeading[];
  plainHtmlForClipboard: string;
} {
  const { maskedMd, mathHtml } = preprocessMarkdownMath(md);
  const rawHtml = marked.parse(maskedMd, { gfm: true, breaks: false, async: false }) as string;
  let cleanHtml = DOMPurify.sanitize(rawHtml, {
    ADD_TAGS: ["input"],
    ADD_ATTR: ["type", "checked", "disabled"],
  });

  cleanHtml = cleanHtml.replace(/PO_MATH_\d+_TOKEN/g, (token) => mathHtml.get(token) ?? token);

  const doc = new DOMParser().parseFromString(`<div id="root">${cleanHtml}</div>`, "text/html");
  const root = doc.getElementById("root");
  const headings: TocHeading[] = [];

  if (root) {
    let hIdx = 0;
    root.querySelectorAll("h1, h2, h3").forEach((el) => {
      const level = Number(el.tagName.slice(1)) as 1 | 2 | 3;
      const text = (el.textContent || "").trim();
      if (!text) return;
      const id = `da-sec-${++hIdx}`;
      el.setAttribute("id", id);
      headings.push({ id, text, level });
    });

    // Make checkboxes read-only styled task items cleanly
    root.querySelectorAll('input[type="checkbox"]').forEach((cb) => {
      cb.setAttribute("disabled", "true");
    });

    // Wrap tables cleanly for horizontal scroll
    root.querySelectorAll("table").forEach((tbl) => {
      const wrap = doc.createElement("div");
      wrap.className = "da-table-wrap";
      tbl.parentNode?.insertBefore(wrap, tbl);
      wrap.appendChild(tbl);
    });
  }

  const processedHtml = root ? root.innerHTML : cleanHtml;
  const plainHtmlForClipboard = processedHtml;

  // Split out ```mermaid code blocks into interactive components
  const segments: ArtifactSegment[] = [];
  const mermaidRegex = /<pre><code class="language-mermaid">([\s\S]*?)<\/code><\/pre>/gi;
  let lastIndex = 0;
  let match: RegExpExecArray | null;
  while ((match = mermaidRegex.exec(processedHtml)) !== null) {
    if (match.index > lastIndex) {
      segments.push({ kind: "html", html: processedHtml.slice(lastIndex, match.index) });
    }
    const tmpEl = doc.createElement("textarea");
    tmpEl.innerHTML = match[1];
    segments.push({ kind: "mermaid", code: tmpEl.value });
    lastIndex = match.index + match[0].length;
  }
  if (lastIndex < processedHtml.length) {
    segments.push({ kind: "html", html: processedHtml.slice(lastIndex) });
  }

  return { segments, headings, plainHtmlForClipboard };
}

interface ParsedFlow {
  nodes: Array<{ id: string; label: string }>;
  edges: Array<{ from: string; to: string; label: string }>;
}

function tryParseSimpleFlowchart(code: string): ParsedFlow | null {
  const lines = code
    .split("\n")
    .map((l) => l.trim())
    .filter((l) => l && !l.startsWith("%%"));
  if (lines.length === 0 || !/^(flowchart|graph)\s+/i.test(lines[0])) return null;

  const nodeMap = new Map<string, string>();
  const edges: Array<{ from: string; to: string; label: string }> = [];

  const cleanLabel = (rawId: string, bracketText?: string): string => {
    const label = (bracketText || rawId).replace(/^["'`({\[]+|["'`)\]}]+$/g, "").trim();
    return label || rawId;
  };

  const registerNode = (token: string): string => {
    const m = /^([A-Za-z0-9_.-]+)\s*(?:\[(.*?)\]|\((.*?)\)|\{(.*?)\}|"(.*?)")?$/.exec(token.trim());
    if (!m) return token.trim();
    const id = m[1];
    const lbl = cleanLabel(id, m[2] || m[3] || m[4] || m[5]);
    if (!nodeMap.has(id) || lbl !== id) nodeMap.set(id, lbl);
    return id;
  };

  for (const line of lines.slice(1)) {
    if (/^(subgraph|end|style|classDef|class|click|linkStyle)\b/i.test(line)) continue;
    const edgeMatch = /^(.+?)\s*(?:--+>?|==+>?|\.-+>?)\s*(?:\|([^|]+)\|\s*)?(.+?);?$/.exec(line);
    if (edgeMatch) {
      const from = registerNode(edgeMatch[1]);
      const to = registerNode(edgeMatch[3]);
      edges.push({ from, to, label: (edgeMatch[2] || "").trim() });
    }
  }

  if (nodeMap.size === 0 || edges.length === 0) return null;
  return {
    nodes: Array.from(nodeMap.entries()).map(([id, label]) => ({ id, label })),
    edges,
  };
}

function MermaidArtifactBlock({
  code,
  onToast,
}: {
  code: string;
  onToast?: (msg: string, isError?: boolean) => void;
}) {
  const [view, setView] = useState<"diagram" | "code">("diagram");
  const flow = useMemo(() => tryParseSimpleFlowchart(code), [code]);

  // Compute simple topological rank for clean horizontal layout
  const layout = useMemo(() => {
    if (!flow) return null;
    const rank = new Map<string, number>();
    flow.nodes.forEach((n) => rank.set(n.id, 0));
    for (let pass = 0; pass < flow.nodes.length; pass++) {
      for (const e of flow.edges) {
        rank.set(e.to, Math.max(rank.get(e.to) ?? 0, (rank.get(e.from) ?? 0) + 1));
      }
    }
    const cols = new Map<number, typeof flow.nodes>();
    for (const n of flow.nodes) {
      const r = rank.get(n.id) ?? 0;
      const list = cols.get(r) ?? [];
      list.push(n);
      cols.set(r, list);
    }
    const colKeys = Array.from(cols.keys()).sort((a, b) => a - b);
    const boxW = 164;
    const boxH = 44;
    const colGap = 54;
    const rowGap = 20;
    const padX = 20;
    const padY = 20;
    let maxRows = 1;
    cols.forEach((arr) => {
      if (arr.length > maxRows) maxRows = arr.length;
    });
    const width = Math.max(420, padX * 2 + colKeys.length * boxW + Math.max(0, colKeys.length - 1) * colGap);
    const height = Math.max(120, padY * 2 + maxRows * boxH + Math.max(0, maxRows - 1) * rowGap);
    const pos = new Map<string, { x: number; y: number }>();
    colKeys.forEach((ck, ci) => {
      const list = cols.get(ck) ?? [];
      const totalH = list.length * boxH + Math.max(0, list.length - 1) * rowGap;
      const startY = (height - totalH) / 2;
      list.forEach((node, ri) => {
        pos.set(node.id, {
          x: padX + ci * (boxW + colGap),
          y: startY + ri * (boxH + rowGap),
        });
      });
    });
    return { width, height, boxW, boxH, pos };
  }, [flow]);

  return (
    <div className="da-mermaid-card">
      <div className="da-mermaid-head">
        <span>结构流程图 (Mermaid)</span>
        <div className="row" style={{ gap: 6 }}>
          {layout && (
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              onClick={() => setView((v) => (v === "diagram" ? "code" : "diagram"))}
            >
              {view === "diagram" ? <Code2 size={12} /> : <Eye size={12} />}
              {view === "diagram" ? "源码" : "框图"}
            </button>
          )}
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={() => {
              void navigator.clipboard.writeText(code);
              onToast?.("已复制 Mermaid 代码");
            }}
          >
            <Copy size={12} /> 复制
          </button>
        </div>
      </div>

      {layout && view === "diagram" && flow ? (
        <div className="da-mermaid-svg-wrap">
          <svg viewBox={`0 0 ${layout.width} ${layout.height}`} width={layout.width} height={layout.height}>
            <defs>
              <marker id="da-arrow" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto">
                <polygon points="0 0, 8 3, 0 6" fill="#64748b" />
              </marker>
            </defs>
            {flow.edges.map((e, idx) => {
              const p1 = layout.pos.get(e.from);
              const p2 = layout.pos.get(e.to);
              if (!p1 || !p2) return null;
              const x1 = p1.x + layout.boxW;
              const y1 = p1.y + layout.boxH / 2;
              const x2 = p2.x;
              const y2 = p2.y + layout.boxH / 2;
              const cx = (x1 + x2) / 2;
              return (
                <g key={idx}>
                  <path
                    d={`M ${x1} ${y1} C ${cx} ${y1}, ${cx} ${y2}, ${x2} ${y2}`}
                    fill="none"
                    stroke="#64748b"
                    strokeWidth="1.6"
                    markerEnd="url(#da-arrow)"
                  />
                  {e.label && (
                    <text x={cx} y={(y1 + y2) / 2 - 6} textAnchor="middle" fontSize="10" fill="#475569">
                      {e.label}
                    </text>
                  )}
                </g>
              );
            })}
            {flow.nodes.map((n) => {
              const p = layout.pos.get(n.id);
              if (!p) return null;
              const short = n.label.length > 22 ? `${n.label.slice(0, 20)}…` : n.label;
              return (
                <g key={n.id} transform={`translate(${p.x}, ${p.y})`}>
                  <rect
                    width={layout.boxW}
                    height={layout.boxH}
                    rx="8"
                    fill="#f8fafc"
                    stroke="#3b82f6"
                    strokeWidth="1.4"
                  />
                  <text
                    x={layout.boxW / 2}
                    y={layout.boxH / 2 + 4}
                    textAnchor="middle"
                    fontSize="11.5"
                    fontWeight="600"
                    fill="#0f172a"
                  >
                    <title>{n.label}</title>
                    {short}
                  </text>
                </g>
              );
            })}
          </svg>
        </div>
      ) : (
        <pre className="da-mermaid-pre">
          <code>{code}</code>
        </pre>
      )}
    </div>
  );
}

export function DocumentArtifactView({
  content,
  title,
  defaultFilename,
  maxHeight = 580,
  onToast,
}: DocumentArtifactViewProps) {
  const [mode, setMode] = useState<"preview" | "source">("preview");
  const [paperLight, setPaperLight] = useState<boolean>(true);
  const [fullscreen, setFullscreen] = useState<boolean>(false);
  const [showToc, setShowToc] = useState<boolean>(true);
  const [exportOpen, setExportOpen] = useState<boolean>(false);
  const [exporting, setExporting] = useState<string>("");
  const [copied, setCopied] = useState<boolean>(false);
  const [envInfo, setEnvInfo] = useState<ExportEnvironmentInfo | null>(null);
  const [lastExportBanner, setLastExportBanner] = useState<{
    kind: "local" | "drive";
    path: string;
    label: string;
    url?: string;
  } | null>(null);

  const inlineMenuRef = useRef<HTMLDivElement | null>(null);
  const modalMenuRef = useRef<HTMLDivElement | null>(null);
  const bodyRef = useRef<HTMLDivElement | null>(null);
  const modalBodyRef = useRef<HTMLDivElement | null>(null);

  const resolvedTitle = useMemo(() => inferTitle(content, title), [content, title]);
  const fileStem = useMemo(
    () => sanitizeFilename(defaultFilename || resolvedTitle),
    [defaultFilename, resolvedTitle]
  );

  const { segments, headings, plainHtmlForClipboard } = useMemo(
    () => renderDocumentHtml(content),
    [content]
  );

  const charCount = content.length;
  const readMinutes = Math.max(1, Math.round(charCount / 450));

  useEffect(() => {
    let cancelled = false;
    api
      .getExportEnvironment()
      .then((info) => {
        if (!cancelled) setEnvInfo(info);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!exportOpen) return;
    const onDocClick = (e: MouseEvent) => {
      const target = e.target as Node;
      const inInline = inlineMenuRef.current?.contains(target) ?? false;
      const inModal = modalMenuRef.current?.contains(target) ?? false;
      if (!inInline && !inModal) {
        setExportOpen(false);
      }
    };
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, [exportOpen]);

  useEffect(() => {
    if (!fullscreen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setFullscreen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [fullscreen]);

  async function handleCopyMarkdown() {
    await navigator.clipboard.writeText(content);
    setCopied(true);
    setExportOpen(false);
    onToast?.("已复制 Markdown 源码");
    window.setTimeout(() => setCopied(false), 1800);
  }

  async function handleCopyRichText() {
    setExportOpen(false);
    try {
      const wrapped = `<html><body>${plainHtmlForClipboard}</body></html>`;
      if (typeof ClipboardItem !== "undefined" && navigator.clipboard.write) {
        const item = new ClipboardItem({
          "text/html": new Blob([wrapped], { type: "text/html" }),
          "text/plain": new Blob([content], { type: "text/plain" }),
        });
        await navigator.clipboard.write([item]);
        onToast?.("已复制富文本排版，可直接 ⌘V 粘贴至 Word / Google Docs / Notion");
        return;
      }
    } catch {
      // fallback below
    }
    await navigator.clipboard.writeText(content);
    onToast?.("已复制到剪贴板");
  }

  async function handleExportLocal(fmt: "docx" | "pdf") {
    setExportOpen(false);
    const defaultDir =
      fmt === "pdf" && envInfo?.defaultPdfDir ? `${envInfo.defaultPdfDir}/` : "";
    const defaultPath = `${defaultDir}${fileStem}.${fmt}`;
    const dest = await api.pickSaveFile(defaultPath, [
      fmt === "docx"
        ? { name: "Word 文档 (.docx)", extensions: ["docx"] }
        : { name: "PDF 文档 (.pdf)", extensions: ["pdf"] },
    ]);
    if (!dest) return;
    setExporting(fmt);
    try {
      const res = await api.exportMarkdownDocument(content, resolvedTitle, fmt, dest);
      setLastExportBanner({
        kind: "local",
        path: res.path,
        label: `已导出 ${fmt.toUpperCase()}（${formatBytes(res.bytes)} · ${res.engine}）`,
      });
      onToast?.(`已导出 ${fmt.toUpperCase()}：${res.path}`);
    } catch (e) {
      onToast?.(`导出 ${fmt.toUpperCase()} 失败：${e}`, true);
    } finally {
      setExporting("");
    }
  }

  async function handleExportGoogleDrive(fmt: "docx" | "pdf") {
    setExportOpen(false);
    setExporting(`drive-${fmt}`);
    try {
      const res = await api.exportMarkdownToGoogleDrive(
        content,
        resolvedTitle,
        fmt,
        envInfo?.driveRoot
      );
      setLastExportBanner({
        kind: "drive",
        path: res.path,
        label: `已同步至 Google Drive (${res.fileName}, ${formatBytes(res.bytes)})`,
        url: res.searchUrl,
      });
      onToast?.(`已导出并自动同步到 Google Drive：${res.fileName}`);
    } catch (e) {
      onToast?.(`导出到 Google Drive 失败：${e}`, true);
    } finally {
      setExporting("");
    }
  }

  async function handleExportGoogleDocs() {
    setExportOpen(false);
    setExporting("gdocs");
    try {
      // 1. Copy formatted HTML so pasting into the newly opened Google Doc preserves headings, tables & lists
      try {
        const wrapped = `<html><body>${plainHtmlForClipboard}</body></html>`;
        if (typeof ClipboardItem !== "undefined" && navigator.clipboard.write) {
          await navigator.clipboard.write([
            new ClipboardItem({
              "text/html": new Blob([wrapped], { type: "text/html" }),
              "text/plain": new Blob([content], { type: "text/plain" }),
            }),
          ]);
        }
      } catch {
        await navigator.clipboard.writeText(content);
      }

      // 2. Also export a clean OOXML .docx into Google Drive if Drive Desktop is connected
      if (envInfo?.available) {
        const res = await api.exportMarkdownToGoogleDrive(
          content,
          resolvedTitle,
          "docx",
          envInfo.driveRoot
        );
        setLastExportBanner({
          kind: "drive",
          path: res.path,
          label: `已导出 .docx 到 Google Drive 并复制富文本（可在 Google Docs 直接按 ⌘V 或在 Drive 中用 Google 文档打开）`,
          url: res.searchUrl,
        });
      }

      await openUrl("https://docs.google.com/document/create");
      onToast?.(
        envInfo?.available
          ? "已打开新建 Google Docs（请按 ⌘V 粘贴完整排版，同时 .docx 已同步至云端硬盘）"
          : "已复制完整排版并打开 Google Docs，在新文档中按 ⌘V 即可粘贴"
      );
    } catch (e) {
      onToast?.(`打开 Google Docs 失败：${e}`, true);
    } finally {
      setExporting("");
    }
  }

  function jumpToHeading(id: string) {
    const container = fullscreen ? modalBodyRef.current : bodyRef.current;
    const target = container?.querySelector(`#${CSS.escape(id)}`) as HTMLElement | null;
    target?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function renderProse(scrollRef: React.RefObject<HTMLDivElement | null>, isModal: boolean) {
    return (
      <div
        ref={scrollRef}
        className={`da-body ${paperLight ? "da-paper" : "da-dark"} ${isModal ? "is-modal" : ""}`}
        style={isModal ? undefined : { maxHeight }}
      >
        {mode === "source" ? (
          <pre className="da-source-pre">{content}</pre>
        ) : (
          <article className="da-prose">
            {segments.map((seg, idx) =>
              seg.kind === "mermaid" ? (
                <MermaidArtifactBlock key={idx} code={seg.code} onToast={onToast} />
              ) : (
                <div
                  key={idx}
                  className="da-html-chunk"
                  dangerouslySetInnerHTML={{ __html: seg.html }}
                />
              )
            )}
          </article>
        )}
      </div>
    );
  }

  function renderToolbar(inModal: boolean) {
    return (
      <div className="da-header">
        <div className="da-title-cluster">
          <FileText size={15} className="da-doc-icon" />
          <div className="da-title-text">
            <strong className="da-title" title={resolvedTitle}>
              {resolvedTitle}
            </strong>
            <span className="da-meta">
              Markdown Artifact · {charCount.toLocaleString()} 字 · 约 {readMinutes} 分钟阅读
            </span>
          </div>
        </div>

        <div className="da-actions">
          <div className="da-seg" role="group" aria-label="视图切换">
            <button
              type="button"
              className={`da-seg-btn ${mode === "preview" ? "active" : ""}`}
              onClick={() => setMode("preview")}
            >
              <Eye size={12} /> 排版阅读
            </button>
            <button
              type="button"
              className={`da-seg-btn ${mode === "source" ? "active" : ""}`}
              onClick={() => setMode("source")}
            >
              <FileCode2 size={12} /> 源码
            </button>
          </div>

          <button
            type="button"
            className="btn btn-sm"
            onClick={() => setPaperLight((v) => !v)}
            title={paperLight ? "切换为深色随主题底色" : "切换为浅色纸张护眼底色"}
          >
            {paperLight ? <Moon size={12} /> : <Sun size={12} />}
            {paperLight ? "深色" : "纸张"}
          </button>

          <button
            type="button"
            className="btn btn-sm"
            onClick={() => void handleCopyMarkdown()}
            title="复制 Markdown"
          >
            {copied ? <Check size={12} /> : <Copy size={12} />}
            {copied ? "已复制" : "复制"}
          </button>

          <div className="da-export-wrap" ref={inModal ? modalMenuRef : inlineMenuRef}>
            <button
              type="button"
              className="btn btn-primary btn-sm"
              disabled={exporting !== ""}
              onClick={() => setExportOpen((v) => !v)}
            >
              {exporting !== "" ? <Loader2 size={12} className="spin" /> : <Download size={12} />}
              {exporting !== "" ? "导出中…" : "导出"}
              <ChevronDown size={12} />
            </button>
            {exportOpen && (
              <div className="da-export-menu" role="menu">
                <div className="da-menu-group">导出到本地文件</div>
                <button
                  type="button"
                  className="da-menu-item"
                  onClick={() => void handleExportLocal("docx")}
                >
                  <FileSpreadsheet size={13} /> Word 文档 (.docx)
                </button>
                <button
                  type="button"
                  className="da-menu-item"
                  onClick={() => void handleExportLocal("pdf")}
                >
                  <FileText size={13} /> PDF 文档 (.pdf)
                </button>
                <button
                  type="button"
                  className="da-menu-item"
                  onClick={() => void handleCopyRichText()}
                >
                  <Copy size={13} /> 复制富文本 (直接粘贴)
                </button>

                <div className="da-menu-group">Google Workspace 云端</div>
                <button
                  type="button"
                  className="da-menu-item"
                  onClick={() => void handleExportGoogleDocs()}
                >
                  <ExternalLink size={13} /> 导出到 Google Docs
                </button>
                <button
                  type="button"
                  className="da-menu-item"
                  disabled={!envInfo?.available}
                  title={
                    envInfo?.available
                      ? `自动保存到 ${envInfo.exportDir}`
                      : "未检测到本机 Google Drive 同步文件夹"
                  }
                  onClick={() => void handleExportGoogleDrive("docx")}
                >
                  <Cloud size={13} /> 存至 Google Drive (.docx)
                </button>
                <button
                  type="button"
                  className="da-menu-item"
                  disabled={!envInfo?.available}
                  onClick={() => void handleExportGoogleDrive("pdf")}
                >
                  <Cloud size={13} /> 存至 Google Drive (.pdf)
                </button>
                {envInfo && !envInfo.available && (
                  <div className="da-menu-hint">
                    未检测到 Google Drive 桌面端同步盘，可先点「导出到 Google Docs」直接生成云文档。
                  </div>
                )}
              </div>
            )}
          </div>

          <button
            type="button"
            className="btn btn-sm"
            onClick={() => setFullscreen((v) => !v)}
            title={inModal ? "退出全屏阅读 (Esc)" : "Claude Artifacts 沉浸大窗阅读 + 目录导航"}
          >
            {inModal ? <Minimize2 size={13} /> : <Maximize2 size={13} />}
            {inModal ? "收起" : "大窗"}
          </button>
        </div>
      </div>
    );
  }

  return (
    <>
      <div className="da-card">
        {renderToolbar(false)}

        {lastExportBanner && (
          <div className="da-export-banner">
            <span className="da-banner-text" title={lastExportBanner.path}>
              ✓ {lastExportBanner.label}
            </span>
            <div className="row" style={{ gap: 6 }}>
              <button
                type="button"
                className="btn btn-sm"
                onClick={() => void api.openPathInSystem(lastExportBanner.path)}
              >
                <ExternalLink size={12} /> 打开
              </button>
              <button
                type="button"
                className="btn btn-sm"
                onClick={() => void api.revealPathInExplorer(lastExportBanner.path)}
              >
                <FolderOpen size={12} /> Finder 显示
              </button>
              {lastExportBanner.url && (
                <button
                  type="button"
                  className="btn btn-primary btn-sm"
                  onClick={() => void openUrl(lastExportBanner.url!)}
                >
                  <Cloud size={12} /> 在云端 Drive 中打开
                </button>
              )}
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                onClick={() => setLastExportBanner(null)}
              >
                <X size={12} />
              </button>
            </div>
          </div>
        )}

        {renderProse(bodyRef, false)}
      </div>

      {fullscreen && (
        <div
          className="da-modal-backdrop"
          role="dialog"
          aria-modal="true"
          aria-label={resolvedTitle}
          onClick={(e) => {
            if (e.target === e.currentTarget) setFullscreen(false);
          }}
        >
          <div className="da-modal">
            {renderToolbar(true)}
            <div className="da-modal-split">
              {headings.length > 1 && mode === "preview" && (
                <aside className={`da-toc ${showToc ? "" : "is-collapsed"}`}>
                  <div className="da-toc-head">
                    <span className="row" style={{ gap: 5 }}>
                      <ListTree size={13} /> 目录大纲
                    </span>
                    <button
                      type="button"
                      className="btn btn-ghost btn-sm"
                      onClick={() => setShowToc((v) => !v)}
                    >
                      {showToc ? "隐藏" : "展开"}
                    </button>
                  </div>
                  {showToc && (
                    <nav className="da-toc-list">
                      {headings.map((h) => (
                        <button
                          key={h.id}
                          type="button"
                          className={`da-toc-item level-${h.level}`}
                          onClick={() => jumpToHeading(h.id)}
                          title={h.text}
                        >
                          {h.text}
                        </button>
                      ))}
                    </nav>
                  )}
                </aside>
              )}
              {renderProse(modalBodyRef, true)}
            </div>
          </div>
        </div>
      )}
    </>
  );
}
