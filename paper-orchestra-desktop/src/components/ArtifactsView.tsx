import {
  BookMarked,
  CheckCircle2,
  ChevronRight,
  ClipboardCheck,
  ExternalLink,
  FileArchive,
  FileCode2,
  FileDiff,
  FilePenLine,
  FileText,
  FolderOpen,
  Globe,
  EyeOff,
  Stethoscope,
  Send,
  Gauge,
  Image as ImageIcon,
  ListTree,
  Loader2,
  PenLine,
  RefreshCw,
  ShieldCheck,
  X,
  XCircle,
  type LucideIcon,
} from "lucide-react";
import { openUrl } from "@tauri-apps/plugin-opener";
import { Suspense, lazy, useEffect, useMemo, useState, type CSSProperties, type ReactNode } from "react";
import { api, base64ToBlobUrl, formatIsoShort, guessImageMime } from "../api";
import { providerLabel, scholarSourceLabel } from "../providerCatalog";
import {
  entryLink,
  formatCad,
  formatInt,
  formatMoney,
  parseCitationAudit,
  parseUsageReport,
  priceSourceLabel,
  totalTokens,
} from "../runReports";
import type {
  CitationAudit,
  CitationAuditEntry,
  CitationStatus,
  LatexExportBib,
  RunArtifactsSummary,
  RunRecord,
  UsageReport,
} from "../types";
import { RunDuration, StatusBadge } from "./RunsView";
import { DocumentArtifactView } from "./DocumentArtifactView";
import { FigureReviewBench } from "./FigureReviewBench";
import {
  ArxivPanel,
  BlindPanel,
  CiteOnlinePanel,
  DiagnosePanel,
  DiffPanel,
  LeanAuditPanel,
  ManuscriptAuditPanel,
} from "./SubmissionPanels";
import "./runs.css";
import "./live-editor.css";

// CodeMirror + pdf.js are big: load the editor only when it is opened.
const LiveEditor = lazy(() => import("./LiveEditor").then((m) => ({ default: m.LiveEditor })));

interface ArtifactsViewProps {
  run: RunRecord | null;
  /** "导出 Overleaf 包"; resolves to the written .zip path, or null when cancelled / failed. */
  onExportLatex?: (run: RunRecord, opts?: { quietSuccess?: boolean }) => Promise<string | null>;
  onToast?: (msg: string, isError?: boolean) => void;
}

type ArtifactSubTab =
  | "pdf"
  | "outline"
  | "citations"
  | "figures"
  | "refinement"
  | "latex"
  | "audit"
  | "usage"
  | "citeOnline"
  | "arxiv"
  | "blind"
  | "diagnose"
  | "manuscriptAudit"
  | "leanAudit"
  | "diff";

interface NavItem {
  id: ArtifactSubTab;
  label: string;
  icon: LucideIcon;
  ready: boolean;
  count?: number;
}

const PREVIEW_TITLES: Record<ArtifactSubTab, string> = {
  pdf: "最终论文",
  latex: "LaTeX 源码",
  outline: "论文大纲",
  citations: "引文映射",
  figures: "科研绘图审阅台",
  refinement: "润色评审记录",
  audit: "引用核查",
  usage: "LLM 用量",
  citeOnline: "联网引用核查",
  arxiv: "arXiv 投稿包",
  blind: "双盲投稿检查",
  diagnose: "LaTeX 诊断",
  manuscriptAudit: "论文体检 (Nature)",
  leanAudit: "Lean 4 形式化核验",
  diff: "改动对比 PDF",
};

/** Raw text of a report file plus its parsed form (null when it is not valid JSON). */
interface LoadedReport<T> {
  text: string;
  data: T | null;
}

export function ArtifactsView({ run, onExportLatex, onToast }: ArtifactsViewProps) {
  const [subTab, setSubTab] = useState<ArtifactSubTab>("pdf");
  const [summary, setSummary] = useState<RunArtifactsSummary | null>(null);
  const [loading, setLoading] = useState(false);
  const [pdfUrl, setPdfUrl] = useState<string | null>(null);
  const [outlineJson, setOutlineJson] = useState<string>("");
  const [citationJson, setCitationJson] = useState<string>("");
  const [worklogJson, setWorklogJson] = useState<string>("");
  const [formatWorklogJson, setFormatWorklogJson] = useState<string>("");
  const [plottingJson, setPlottingJson] = useState<string>("");
  const [figureUrls, setFigureUrls] = useState<Array<{ path: string; url: string }>>([]);
  const [selectedTex, setSelectedTex] = useState<string>("");
  const [texContent, setTexContent] = useState<string>("");
  const [errorMsg, setErrorMsg] = useState<string>("");
  const [usage, setUsage] = useState<LoadedReport<UsageReport> | null>(null);
  const [audit, setAudit] = useState<LoadedReport<CitationAudit> | null>(null);
  const [exporting, setExporting] = useState(false);
  const [exportedPath, setExportedPath] = useState<string | null>(null);
  /** What the finished export did with the .bib (re-read after it, not the summary from load time). */
  const [exportedBib, setExportedBib] = useState<LatexExportBib | null>(null);
  /** 「编辑并实时预览」 overlay is open. */
  const [liveEditing, setLiveEditing] = useState(false);
  /** Bumped after the editor closes so the source view re-reads the file. */
  const [texReload, setTexReload] = useState(0);

  useEffect(() => {
    setExportedPath(null);
    setLiveEditing(false);
  }, [run?.id]);

  async function refreshArtifacts() {
    if (!run) {
      setSummary(null);
      return;
    }
    setLoading(true);
    setErrorMsg("");
    try {
      const s = await api.getRunArtifactsSummary(run.runDir);
      setSummary(s);

      if (s.hasOutline) {
        setOutlineJson(await api.readRunTextArtifact(run.runDir, "outline.json"));
      } else {
        setOutlineJson("");
      }

      if (s.hasCitationMap) {
        setCitationJson(
          await api.readRunTextArtifact(run.runDir, "literature_agent_output/citation_map.json")
        );
      } else {
        setCitationJson("");
      }

      if (s.hasContentWorklog) {
        setWorklogJson(
          await api.readRunTextArtifact(
            run.runDir,
            "content_refinement_workdir/content_refinement_worklog.json"
          )
        );
      } else {
        setWorklogJson("");
      }

      if (s.hasFormatWorklog) {
        setFormatWorklogJson(
          await api.readRunTextArtifact(
            run.runDir,
            "content_refinement_workdir/format_refinement_worklog.json"
          )
        );
      } else {
        setFormatWorklogJson("");
      }

      if (s.hasPlottingResults) {
        setPlottingJson(await api.readRunTextArtifact(run.runDir, "plotting_results.json"));
      } else {
        setPlottingJson("");
      }

      // Newer report files: read on their own so an unreadable one never
      // hides the artifacts above (older runs simply do not have them).
      setUsage(
        s.hasUsageReport
          ? await loadReport(run.runDir, "usage_report.json", parseUsageReport)
          : null
      );
      setAudit(
        s.hasCitationAudit
          ? await loadReport(run.runDir, "citation_audit.json", parseCitationAudit)
          : null
      );

      if (s.latexFiles.length > 0 && (!selectedTex || !s.latexFiles.includes(selectedTex))) {
        const preferred =
          s.latexFiles.find((f) => f === "raw_draft_paper.tex") ||
          s.latexFiles.find((f) => f === "template.tex") ||
          s.latexFiles[0];
        setSelectedTex(preferred);
      }
    } catch (e) {
      setErrorMsg(String(e));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    refreshArtifacts();
  }, [run?.id, run?.status, run?.currentStage]);

  // usage_report.json changes after every model call: re-read it while the
  // run is going and its tab is open (stage changes alone are minutes apart).
  const isRunning = run?.status === "running";
  useEffect(() => {
    if (!run || !isRunning || subTab !== "usage") return;
    const runDir = run.runDir;
    let cancelled = false;
    const timer = window.setInterval(async () => {
      const next = await loadReport(runDir, "usage_report.json", parseUsageReport);
      if (!cancelled && next) setUsage(next);
    }, 10000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [run?.id, isRunning, subTab]);

  useEffect(() => {
    let urlToRevoke: string | null = null;
    let cancelled = false;
    async function loadPdf() {
      if (!summary?.finalPdfPath) {
        setPdfUrl(null);
        return;
      }
      try {
        const b64 = await api.readFileBase64(summary.finalPdfPath);
        if (cancelled) return;
        urlToRevoke = base64ToBlobUrl(b64, "application/pdf");
        setPdfUrl(urlToRevoke);
      } catch {
        setPdfUrl(null);
      }
    }
    loadPdf();
    return () => {
      cancelled = true;
      if (urlToRevoke) URL.revokeObjectURL(urlToRevoke);
    };
  }, [summary?.finalPdfPath]);

  useEffect(() => {
    const urls: string[] = [];
    let cancelled = false;
    async function loadImgs() {
      if (!summary?.generatedFigurePaths.length) {
        setFigureUrls([]);
        return;
      }
      const next: Array<{ path: string; url: string }> = [];
      for (const p of summary.generatedFigurePaths) {
        try {
          const b64 = await api.readFileBase64(p);
          if (cancelled) return;
          const url = base64ToBlobUrl(b64, guessImageMime(p));
          urls.push(url);
          next.push({ path: p, url });
        } catch {
          // ignore
        }
      }
      if (!cancelled) setFigureUrls(next);
    }
    loadImgs();
    return () => {
      cancelled = true;
      urls.forEach((u) => URL.revokeObjectURL(u));
    };
  }, [summary?.generatedFigurePaths]);

  useEffect(() => {
    if (!run || !selectedTex) {
      setTexContent("");
      return;
    }
    api
      .readRunTextArtifact(run.runDir, `latex_writeup/${selectedTex}`)
      .then(setTexContent)
      .catch(() => setTexContent(""));
  }, [run?.runDir, selectedTex, texReload]);

  if (!run) {
    return (
      <div className="card empty-state av-none">
        <span className="rv-empty-icon" aria-hidden="true">
          <FileText size={20} strokeWidth={1.6} />
        </span>
        <h2 className="empty-state-title">还没有运行产物</h2>
        <p>当前项目还没有可查看的运行产物。请先在工作台启动一次论文生成。</p>
      </div>
    );
  }

  const running = run.status === "running";
  const latexFiles = summary?.latexFiles ?? [];
  const exportSource = summary?.latexExportSource ?? null;
  const exportBib = summary?.latexExportBib ?? null;
  const exportTitle = running
    ? "运行结束后即可导出"
    : !exportSource
      ? "这次运行还没有 LaTeX 源码"
      : `把 ${exportSource}/ 中的最终 LaTeX 源码（.tex / .bib / 样式文件 / 图表）打包为 .zip，可直接上传到 Overleaf` +
        (exportBib
          ? `；${exportBib.file} 只放入正文引用的 ${exportBib.kept} 条（文献库中另有 ${exportBib.dropped} 条未引用，留在运行目录里）`
          : "");

  function handleLiveEditClosed() {
    setLiveEditing(false);
    setTexReload((n) => n + 1);
    void refreshArtifacts();
  }

  const liveEditTitle = running
    ? "流水线还在跑，跑完才能编辑"
    : `在这里直接改 ${exportSource ?? ""}/ 里的 LaTeX，右边实时显示编译出的 PDF`;

  function liveEditButton() {
    if (!exportSource) return null;
    return (
      <button
        type="button"
        className="btn btn-sm"
        onClick={() => setLiveEditing(true)}
        disabled={running || liveEditing}
        title={liveEditTitle}
      >
        <FilePenLine size={13} aria-hidden="true" />
        编辑并实时预览
      </button>
    );
  }

  async function handleExport() {
    if (!run || !onExportLatex) return;
    setExporting(true);
    try {
      // Success is shown inline below the header (with "在文件夹中显示"), so no toast.
      const written = await onExportLatex(run, { quietSuccess: true });
      if (written) {
        // The export re-checks the files (an edit since the view loaded can
        // turn the cited-only .bib off), so describe it from a fresh summary.
        const fresh = await api.getRunArtifactsSummary(run.runDir).catch(() => null);
        setExportedBib(fresh?.latexExportBib ?? null);
        if (fresh) setSummary(fresh);
        setExportedPath(written);
      }
    } finally {
      setExporting(false);
    }
  }

  const paperItems: NavItem[] = [
    { id: "pdf", label: "最终 PDF", icon: FileText, ready: !!summary?.finalPdfPath },
    {
      id: "latex",
      label: "LaTeX 源码",
      icon: FileCode2,
      ready: latexFiles.length > 0,
      count: latexFiles.length,
    },
  ];
  const processItems: NavItem[] = [
    { id: "outline", label: "论文大纲", icon: ListTree, ready: !!summary?.hasOutline },
    { id: "citations", label: "引文映射", icon: BookMarked, ready: !!summary?.hasCitationMap },
    {
      id: "figures",
      label: "生成图表",
      icon: ImageIcon,
      ready: figureUrls.length > 0,
      count: figureUrls.length,
    },
    {
      id: "refinement",
      label: "润色评审",
      icon: PenLine,
      ready: !!(summary?.hasContentWorklog || summary?.hasFormatWorklog),
    },
  ];
  const checkItems: NavItem[] = [
    { id: "audit", label: "引用核查", icon: ShieldCheck, ready: !!summary?.hasCitationAudit },
    { id: "citeOnline", label: "联网核查", icon: Globe, ready: !!summary?.hasCitationAudit },
    { id: "usage", label: "用量", icon: Gauge, ready: !!summary?.hasUsageReport },
  ];
  const hasLatex = !!exportSource;
  const submitItems: NavItem[] = [
    { id: "manuscriptAudit", label: "论文体检 (Nature)", icon: ClipboardCheck, ready: hasLatex },
    { id: "leanAudit", label: "Lean 4 核验", icon: ShieldCheck, ready: true },
    { id: "arxiv", label: "arXiv 投稿包", icon: Send, ready: hasLatex },
    { id: "blind", label: "双盲检查", icon: EyeOff, ready: hasLatex },
    { id: "diagnose", label: "LaTeX 诊断", icon: Stethoscope, ready: hasLatex },
    { id: "diff", label: "改动对比", icon: FileDiff, ready: hasLatex },
  ];

  function renderNavItem(item: NavItem) {
    const Icon = item.icon;
    const active = subTab === item.id;
    return (
      <li key={item.id}>
        <button
          type="button"
          className={`av-nav-item${active ? " is-active" : ""}`}
          aria-current={active ? "page" : undefined}
          onClick={() => setSubTab(item.id)}
        >
          <Icon size={15} strokeWidth={1.75} aria-hidden="true" />
          <span className="av-nav-text">{item.label}</span>
          {item.count !== undefined ? (
            <span className="av-nav-count">{item.count}</span>
          ) : (
            <>
              <span
                className={`av-avail${item.ready ? " is-ready" : ""}`}
                title={item.ready ? "已生成" : "尚未生成"}
                aria-hidden="true"
              />
              <span className="rv-sr">{item.ready ? "已生成" : "尚未生成"}</span>
            </>
          )}
        </button>
        {item.id === "latex" && active && latexFiles.length > 0 && (
          <ul className="av-nav-files" aria-label="LaTeX 文件">
            {latexFiles.map((f) => (
              <li key={f}>
                <button
                  type="button"
                  className={`av-nav-file${selectedTex === f ? " is-active" : ""}`}
                  aria-current={selectedTex === f ? "true" : undefined}
                  title={f}
                  onClick={() => setSelectedTex(f)}
                >
                  {f}
                </button>
              </li>
            ))}
          </ul>
        )}
      </li>
    );
  }

  const previewFile: Record<ArtifactSubTab, string> = {
    pdf: summary?.finalPdfPath ? basename(summary.finalPdfPath) : "final_paper.pdf",
    latex: selectedTex ? `latex_writeup/${selectedTex}` : "latex_writeup/",
    outline: "outline.json",
    citations: "literature_agent_output/citation_map.json",
    figures: "Plotting Agent 输出",
    refinement: "content_refinement_workdir/",
    audit: "citation_audit.json",
    usage: "usage_report.json",
    citeOnline: "citation_online_check.json",
    arxiv: "tools/submission_tools.py arxiv",
    blind: "tools/submission_tools.py blind",
    diagnose: "tools/submission_tools.py diagnose",
    manuscriptAudit: "tools/submission_tools.py manuscript-audit",
    leanAudit: "tools/submission_tools.py lean-audit",
    diff: "latexdiff",
  };

  const previewMeta =
    subTab === "figures" && figureUrls.length > 0
      ? `${figureUrls.length} 张`
      : subTab === "latex" && texContent
        ? `${countLines(texContent)} 行`
        : subTab === "audit" && audit?.data
          ? `文献库 ${formatInt(audit.data.entries.length)} 条 · 引用 ${formatInt(audit.data.summary.in_bibliography)} 条`
          : subTab === "usage" && usage?.data
            ? `${formatInt(usage.data.totals.calls)} 次调用`
            : "";

  const showPdfFrame = subTab === "pdf" && !!pdfUrl;

  return (
    <div className="av-layout">
      <header className="card av-head">
        <div className="av-head-main">
          <div className="av-title-row">
            <h2 className="av-title">运行产物</h2>
            <StatusBadge status={run.status} />
          </div>
          <div className="av-meta">
            <span>开始于 {formatIsoShort(run.startedAt)}</span>
            <span>
              {running ? "已用时" : "用时"} <RunDuration run={run} />
            </span>
            <span>{run.templateName}</span>
            <span className="rv-mono" title={run.writerModel}>
              {run.writerModel}
            </span>
          </div>
          <div className="av-path" title={run.runDir}>
            <FolderOpen size={12} aria-hidden="true" />
            <span className="av-path-text">{`\u200E${run.runDir}\u200E`}</span>
          </div>
        </div>

        <div className="rv-actions">
          <button type="button" className="btn btn-sm" onClick={refreshArtifacts} disabled={loading}>
            <RefreshCw size={13} className={loading ? "spin" : undefined} aria-hidden="true" />
            {loading ? "刷新中…" : "刷新产物"}
          </button>
          <button
            type="button"
            className="btn btn-sm"
            onClick={() => api.openPathInSystem(run.runDir)}
          >
            <FolderOpen size={13} aria-hidden="true" /> 打开文件夹
          </button>
          {onExportLatex && (
            <button
              type="button"
              className="btn btn-sm"
              onClick={handleExport}
              disabled={exporting || running || !exportSource}
              title={exportTitle}
            >
              {exporting ? (
                <Loader2 size={13} className="spin" aria-hidden="true" />
              ) : (
                <FileArchive size={13} aria-hidden="true" />
              )}
              {exporting ? "导出中…" : "导出 Overleaf 包"}
            </button>
          )}
          {liveEditButton()}
          {summary?.finalPdfPath && (
            <button
              type="button"
              className="btn btn-primary btn-sm"
              onClick={() => api.openPathInSystem(summary.finalPdfPath!)}
            >
              <ExternalLink size={13} aria-hidden="true" /> 用系统阅读器打开 PDF
            </button>
          )}
        </div>
      </header>

      {exportedPath && (
        <div className="callout callout-success av-notice" role="status">
          <CheckCircle2 size={15} aria-hidden="true" />
          <div className="av-notice-body">
            <div className="av-notice-title">
              已导出 Overleaf 包
              <span className="av-notice-path" title={exportedPath}>
                {`\u200E${exportedPath}\u200E`}
              </span>
            </div>
            <div className="av-notice-hint">
              在 Overleaf 中选择「New Project → Upload Project」，上传这个 .zip 即可在线编辑和编译。
              {exportedBib &&
                ` 包里的 ${exportedBib.file} 只含正文引用的 ${formatInt(exportedBib.kept)} 条，论文的参考文献列表与原来相同；完整文献库仍在运行目录中。`}
            </div>
          </div>
          <button
            type="button"
            className="btn btn-sm"
            onClick={() => api.revealPathInExplorer(exportedPath).catch(() => undefined)}
          >
            <FolderOpen size={13} aria-hidden="true" /> 在文件夹中显示
          </button>
          <button
            type="button"
            className="btn btn-ghost btn-sm btn-icon"
            aria-label="关闭提示"
            onClick={() => setExportedPath(null)}
          >
            <X size={13} aria-hidden="true" />
          </button>
        </div>
      )}

      {errorMsg && (
        <div className="rv-alert" role="alert">
          <XCircle size={15} aria-hidden="true" />
          <div className="rv-alert-body">
            <div className="rv-alert-title">读取运行产物失败</div>
            <div className="rv-alert-msg">{errorMsg}</div>
          </div>
        </div>
      )}

      <div className="av-body">
        <nav className="card av-nav" aria-label="产物导航">
          <div>
            <div className="av-nav-label">论文</div>
            <ul className="av-nav-list">{paperItems.map(renderNavItem)}</ul>
          </div>
          <div>
            <div className="av-nav-label">过程记录</div>
            <ul className="av-nav-list">{processItems.map(renderNavItem)}</ul>
          </div>
          <div>
            <div className="av-nav-label">核查与用量</div>
            <ul className="av-nav-list">{checkItems.map(renderNavItem)}</ul>
          </div>
          <div>
            <div className="av-nav-label">投稿工具</div>
            <ul className="av-nav-list">{submitItems.map(renderNavItem)}</ul>
          </div>
        </nav>

        <section className="card av-preview" aria-label={PREVIEW_TITLES[subTab]}>
          <header className="av-preview-head">
            <div className="av-preview-titles">
              <h3 className="av-preview-title">{PREVIEW_TITLES[subTab]}</h3>
              <div className="av-preview-file" title={previewFile[subTab]}>
                {previewFile[subTab]}
              </div>
            </div>
            {(previewMeta || (subTab === "latex" && exportSource)) && (
              <div className="le-preview-side">
                {previewMeta && <span className="av-preview-meta">{previewMeta}</span>}
                {subTab === "latex" && liveEditButton()}
              </div>
            )}
          </header>

          <div className={`av-preview-body${showPdfFrame ? " is-pdf" : ""}`}>
            {subTab === "pdf" &&
              (pdfUrl ? (
                <iframe className="av-pdf" src={pdfUrl} title="生成的论文 PDF" />
              ) : summary?.finalPdfPath ? (
                <EmptyPreview icon={<FileText size={20} strokeWidth={1.6} />} title="论文 PDF 已生成">
                  预览载入中。如未显示，请点右上角「用系统阅读器打开 PDF」。
                </EmptyPreview>
              ) : (
                <EmptyPreview icon={<FileText size={20} strokeWidth={1.6} />} title="论文 PDF 尚未生成">
                  {running ? (
                    <>
                      流水线仍在运行，完成后 <code>final_paper.pdf</code> 会自动出现在这里。
                    </>
                  ) : run.status === "failed" ? (
                    <>
                      本次运行没有生成 <code>final_paper.pdf</code>，可在「运行与日志」中查看原因。
                    </>
                  ) : (
                    <>
                      运行完成后，<code>final_paper.pdf</code> 会自动出现在这里。
                    </>
                  )}
                </EmptyPreview>
              ))}

            {subTab === "latex" && exportSource && exportSource !== "latex_writeup" && (
              <p className="le-av-source-note" role="note">
                这里是初稿文件夹 <code>latex_writeup</code>。论文最终稿在 <code>{exportSource}</code>，用「编辑并实时预览」查看和修改。
              </p>
            )}
            {subTab === "latex" &&
              (latexFiles.length === 0 ? (
                <EmptyPreview icon={<FileCode2 size={20} strokeWidth={1.6} />} title="暂无 LaTeX 源码">
                  写作阶段开始后，<code>latex_writeup/</code> 下的 .tex 文件会列在左侧导航中。
                </EmptyPreview>
              ) : !selectedTex ? (
                <EmptyPreview
                  icon={<FileCode2 size={20} strokeWidth={1.6} />}
                  title="选择左侧 LaTeX 文件查看源码"
                />
              ) : texContent ? (
                <CodeView text={texContent} lang="latex" />
              ) : (
                <EmptyPreview icon={<FileCode2 size={20} strokeWidth={1.6} />} title="文件为空或正在载入" />
              ))}

            {subTab === "outline" &&
              (outlineJson ? (
                <CodeView text={outlineJson} lang="json" />
              ) : (
                <EmptyPreview icon={<ListTree size={20} strokeWidth={1.6} />} title="大纲尚未生成">
                  大纲阶段完成后会写入 <code>outline.json</code>。
                </EmptyPreview>
              ))}

            {subTab === "citations" &&
              (citationJson ? (
                <CodeView text={citationJson} lang="json" />
              ) : (
                <EmptyPreview icon={<BookMarked size={20} strokeWidth={1.6} />} title="引文映射尚未生成">
                  文献检索完成后会写入 <code>citation_map.json</code>。
                </EmptyPreview>
              ))}

            {subTab === "figures" && (
              <div className="stack">
                <FigureReviewBench
                  baseDir={run.runDir}
                  readOnly={running}
                  onToast={onToast}
                  onFigureUpdated={() => void refreshArtifacts()}
                />
                {plottingJson && (
                  <details className="av-details" open={figureUrls.length === 0}>
                    <summary>
                      <ChevronRight size={13} className="av-chev" aria-hidden="true" />
                      绘图结果明细
                      <span className="av-sub-file">plotting_results.json</span>
                    </summary>
                    <CodeView text={plottingJson} lang="json" />
                  </details>
                )}
              </div>
            )}

            {subTab === "audit" &&
              (audit?.data ? (
                <AuditPanel
                  audit={audit.data}
                  runDir={run.runDir}
                  mdPath={summary?.citationAuditMdPath ?? null}
                  citedBibPath={summary?.citedBibPath ?? null}
                  exportBib={summary?.latexExportBib ?? null}
                  onToast={onToast}
                />
              ) : audit ? (
                <BrokenReport file="citation_audit.json" text={audit.text} />
              ) : (
                <EmptyPreview icon={<ShieldCheck size={20} strokeWidth={1.6} />} title={
                  running ? "最终 PDF 生成后会自动核查引用" : "这次运行没有引用核查结果"
                }>
                  {running ? (
                    <>
                      流水线生成 <code>final_paper.pdf</code> 后会离线比对正文中的引用与参考文献条目，结果写入{" "}
                      <code>citation_audit.json</code>。
                    </>
                  ) : (
                    <>
                      引用核查在最终 PDF 生成之后进行。本次运行没有到达这一步，或由较早的版本生成（当时还没有{" "}
                      <code>citation_audit.json</code>）。
                    </>
                  )}
                </EmptyPreview>
              ))}

            {subTab === "usage" &&
              (usage?.data ? (
                <UsagePanel report={usage.data} running={running} />
              ) : usage ? (
                <BrokenReport file="usage_report.json" text={usage.text} />
              ) : (
                <EmptyPreview icon={<Gauge size={20} strokeWidth={1.6} />} title={
                  running ? "等待第一次模型调用" : "这次运行没有用量记录"
                }>
                  {running ? (
                    <>
                      运行开始后会写入 <code>usage_report.json</code>，并在每次模型调用后更新，记录每个模型的调用次数与 token 用量。
                    </>
                  ) : (
                    <>
                      较早版本生成的运行不包含 <code>usage_report.json</code>。用当前版本重新运行，即可看到每个模型的调用次数与 token 用量。
                    </>
                  )}
                </EmptyPreview>
              ))}

            {subTab === "citeOnline" && <CiteOnlinePanel run={run} running={running} />}
            {subTab === "manuscriptAudit" && <ManuscriptAuditPanel run={run} running={running} />}
            {subTab === "leanAudit" && <LeanAuditPanel running={running} />}
            {subTab === "arxiv" && <ArxivPanel run={run} running={running} />}
            {subTab === "blind" && <BlindPanel run={run} running={running} />}
            {subTab === "diagnose" && <DiagnosePanel run={run} running={running} />}
            {subTab === "diff" && <DiffPanel run={run} running={running} />}

            {subTab === "refinement" && (
              <div className="av-sections">
                <section>
                  <div className="av-sub-head">
                    <h4 className="av-sub-title">内容润色</h4>
                    <span className="av-sub-file">content_refinement_worklog.json</span>
                  </div>
                  {worklogJson ? (
                    <CodeView text={worklogJson} lang="json" />
                  ) : (
                    <div className="av-sub-empty">暂未生成内容润色日志。</div>
                  )}
                </section>
                <section>
                  <div className="av-sub-head">
                    <h4 className="av-sub-title">版式微调</h4>
                    <span className="av-sub-file">format_refinement_worklog.json</span>
                  </div>
                  {formatWorklogJson ? (
                    <CodeView text={formatWorklogJson} lang="json" />
                  ) : (
                    <div className="av-sub-empty">暂未生成版式微调日志。</div>
                  )}
                </section>
              </div>
            )}
          </div>
        </section>
      </div>

      {liveEditing && (
        <Suspense
          fallback={
            <div className="le-overlay-loading" role="status">
              <Loader2 size={16} className="spin" aria-hidden="true" /> 正在打开编辑器…
            </div>
          }
        >
          <LiveEditor runDir={run.runDir} onClose={handleLiveEditClosed} />
        </Suspense>
      )}
    </div>
  );
}

/* --------------------------------------------------------------------------
   Report panels (usage_report.json, citation_audit.json)
   -------------------------------------------------------------------------- */

async function loadReport<T>(
  runDir: string,
  relativePath: string,
  parse: (text: string) => T | null
): Promise<LoadedReport<T> | null> {
  try {
    const text = await api.readRunTextArtifact(runDir, relativePath);
    return { text, data: parse(text) };
  } catch {
    return null;
  }
}

function BrokenReport({ file, text }: { file: string; text: string }) {
  return (
    <div className="av-sections">
      <div className="callout callout-warn">
        <div className="callout-title">无法解析 {file}</div>
        文件内容不是预期的 JSON 格式，下面显示原始内容。
      </div>
      {text.trim() && <CodeView text={text} lang="json" />}
    </div>
  );
}

type StatTone = "success" | "warn" | "danger";

function Stat({
  label,
  value,
  unit,
  tone,
  hint,
}: {
  label: string;
  /** A number is shown as an integer; a string as is (e.g. "C$1.23"). */
  value: number | string;
  unit?: string;
  tone?: StatTone;
  hint?: string;
}) {
  return (
    <div className={`stat${tone ? ` is-${tone}` : ""}`} title={hint}>
      <dt>{label}</dt>
      <dd>
        {typeof value === "number" ? formatInt(value) : value}
        {unit && <span className="stat-unit">{unit}</span>}
      </dd>
    </div>
  );
}

function sharePercent(part: number, whole: number): string {
  if (whole <= 0 || part <= 0) return "0%";
  const pct = (part / whole) * 100;
  return pct < 1 ? "<1%" : `${Math.round(pct)}%`;
}

const FX_SOURCE_LABEL: Record<string, string> = {
  bank_of_canada: "加拿大央行",
  builtin: "内置（离线时使用）",
};

/** One line on how the CAD estimate was made (prices, rates, what is left out). */
function costBasis(cost: NonNullable<UsageReport["cost"]>): string {
  const rates = Object.entries(cost.fx.rates)
    .filter(([code]) => code !== "CAD")
    .map(([code, v]) => `1 ${code} = ${v.toFixed(4)} CAD`)
    .join("，");
  const fx = `汇率：${FX_SOURCE_LABEL[cost.fx.source] ?? cost.fx.source}${
    cost.fx.date ? ` ${cost.fx.date}` : ""
  }${rates ? `（${rates}）` : ""}`;
  const checked = cost.prices.builtin_checked;
  const prices = `单价：各平台官方标价${checked ? `（核对于 ${checked}）` : ""}${
    cost.prices.sources.some((s) => s !== "official") ? "，部分来自公开价目表" : ""
  }`;
  return `${prices}；${fx}。`;
}

function UsagePanel({ report, running = false }: { report: UsageReport; running?: boolean }) {
  const t = report.totals;
  const grand = totalTokens(t);
  const cost = report.cost;
  const rows = useMemo(
    () =>
      [...report.by_model].sort(
        (a, b) => totalTokens(b) - totalTokens(a) || b.calls - a.calls || a.model.localeCompare(b.model)
      ),
    [report.by_model]
  );

  return (
    <div className="av-sections">
      <section aria-label="用量合计">
        <dl className="stat-grid">
          <Stat label="LLM 调用" value={t.calls} unit="次" />
          <Stat
            label="失败调用"
            value={t.errors}
            unit="次"
            tone={t.errors > 0 ? "warn" : undefined}
            hint="调用报错的次数（含重试前失败的请求）"
          />
          <Stat label="输入 tokens" value={t.input_tokens} />
          <Stat label="输出 tokens" value={t.output_tokens} />
          <Stat label="合计 tokens" value={grand} />
          {cost && (
            <Stat
              label="估算费用"
              value={formatCad(cost.total)}
              hint="按各平台标价估算并换算为加元；免费额度、折扣、赠金和税费不计"
            />
          )}
        </dl>
        <p className="av-note">
          {running && <>运行中，数字随每次调用更新，结束后为最终统计。</>}
          {report.generated_at && <>统计于 {formatIsoShort(report.generated_at)}。</>}
          未返回用量信息的调用按 0 token 计入；实际费用以各平台账单为准。
        </p>
        {cost && (
          <p className="av-note">
            费用按标价估算：{costBasis(cost)}免费额度、折扣、赠金和税费不计。
            {cost.unpriced_models.length > 0 && (
              <> 没有公开标价、未计入：{cost.unpriced_models.join("、")}。</>
            )}
          </p>
        )}
      </section>

      <section>
        <div className="av-sub-head">
          <h4 className="av-sub-title">按模型</h4>
          <span className="av-sub-file">{rows.length} 个模型</span>
        </div>
        {rows.length === 0 ? (
          <div className="av-sub-empty">本次运行没有记录到模型调用。</div>
        ) : (
          <div className="data-table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th scope="col">服务商</th>
                  <th scope="col">模型</th>
                  <th scope="col" className="is-num">调用</th>
                  <th scope="col" className="is-num">失败</th>
                  <th scope="col" className="is-num">输入 tokens</th>
                  <th scope="col" className="is-num">输出 tokens</th>
                  <th scope="col" className="is-num">合计</th>
                  <th scope="col">占比</th>
                  {cost && (
                    <th scope="col" className="is-num">
                      费用 (CAD)
                    </th>
                  )}
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => {
                  const total = totalTokens(r);
                  const share = grand > 0 ? Math.min(100, (total / grand) * 100) : 0;
                  return (
                    <tr key={`${r.provider}\u0000${r.model}`}>
                      <td title={r.provider}>{r.provider ? providerLabel(r.provider) : "—"}</td>
                      <td className="is-mono">
                        <span className="clip av-model" title={r.model || undefined}>
                          {r.model || "—"}
                        </span>
                      </td>
                      <td className="is-num">{formatInt(r.calls)}</td>
                      <td className={`is-num${r.errors > 0 ? " is-warn" : " is-muted"}`}>
                        {formatInt(r.errors)}
                      </td>
                      <td className="is-num">{formatInt(r.input_tokens)}</td>
                      <td className="is-num">{formatInt(r.output_tokens)}</td>
                      <td className="is-num is-strong">{formatInt(total)}</td>
                      <td>
                        <span className="share" style={{ "--share": `${share}%` } as CSSProperties}>
                          <span className="share-bar" aria-hidden="true" />
                          <span className="share-pct">{sharePercent(total, grand)}</span>
                        </span>
                      </td>
                      {cost && (
                        <td
                          className={`is-num${r.cost ? "" : " is-muted"}`}
                          title={
                            r.cost
                              ? `${formatMoney(r.cost.amount, r.cost.currency)} · ${priceSourceLabel(r.cost.source)}${
                                  r.search_queries ? ` · 含 ${r.search_queries} 次网页搜索` : ""
                                }${r.cache_read_tokens ? ` · ${formatInt(r.cache_read_tokens)} 输入 tokens 命中缓存` : ""}`
                              : "没有公开标价，未计入"
                          }
                        >
                          {r.cost ? formatCad(r.cost.cad) : "—"}
                        </td>
                      )}
                    </tr>
                  );
                })}
              </tbody>
              {rows.length > 1 && (
                <tfoot>
                  <tr>
                    <th scope="row" colSpan={2}>
                      合计
                    </th>
                    <td className="is-num">{formatInt(t.calls)}</td>
                    <td className="is-num">{formatInt(t.errors)}</td>
                    <td className="is-num">{formatInt(t.input_tokens)}</td>
                    <td className="is-num">{formatInt(t.output_tokens)}</td>
                    <td className="is-num">{formatInt(grand)}</td>
                    <td />
                    {cost && <td className="is-num is-strong">{formatCad(cost.total)}</td>}
                  </tr>
                </tfoot>
              )}
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

const CITATION_STATUS: Record<CitationStatus, { label: string; badge: string; hint: string }> = {
  verified: { label: "已核验", badge: "badge-succeeded", hint: "有 DOI，或有带链接的文献检索记录" },
  metadata_only: { label: "仅元数据", badge: "badge-neutral", hint: "有文献检索记录，但没有 DOI 或链接" },
  unverified: { label: "未核验", badge: "badge-warn", hint: "没有对应的检索记录，建议人工确认" },
};

type AuditFilter = "all" | "cited" | CitationStatus | "uncited";

/** At most this many keys are listed in a callout; the rest are counted. */
const KEY_LIST_LIMIT = 60;

function KeyList({ keys }: { keys: string[] }) {
  const shown = keys.slice(0, KEY_LIST_LIMIT);
  return (
    <ul className="av-keys">
      {shown.map((k) => (
        <li key={k}>
          <code>{k}</code>
        </li>
      ))}
      {keys.length > shown.length && <li className="av-keys-more">另有 {keys.length - shown.length} 个</li>}
    </ul>
  );
}

function AuditPanel({
  audit,
  runDir,
  mdPath,
  citedBibPath,
  exportBib,
  onToast,
}: {
  audit: CitationAudit;
  runDir: string;
  mdPath: string | null;
  citedBibPath: string | null;
  exportBib: LatexExportBib | null;
  onToast?: (msg: string, isError?: boolean) => void;
}) {
  const [filter, setFilter] = useState<AuditFilter>("all");
  // Library entries the text never cites are folded away by default: they are
  // not part of the paper's reference list.
  const [showUncited, setShowUncited] = useState(false);
  const [showAuditMd, setShowAuditMd] = useState(false);
  const [auditMdText, setAuditMdText] = useState("");
  const [auditMdBusy, setAuditMdBusy] = useState(false);

  useEffect(() => {
    setShowAuditMd(false);
    setAuditMdText("");
  }, [runDir]);
  const s = audit.summary;
  const entries = audit.entries;

  const counts = useMemo(() => {
    const c: Record<AuditFilter, number> = {
      all: entries.length,
      cited: 0,
      verified: 0,
      metadata_only: 0,
      unverified: 0,
      uncited: 0,
    };
    for (const e of entries) {
      c[e.status] += 1;
      c[e.cited ? "cited" : "uncited"] += 1;
    }
    return c;
  }, [entries]);

  const visible = useMemo(
    () =>
      entries.filter((e) =>
        filter === "all"
          ? true
          : filter === "cited"
            ? e.cited
            : filter === "uncited"
              ? !e.cited
              : e.status === filter
      ),
    [entries, filter]
  );
  const citedRows = visible.filter((e) => e.cited);
  const uncitedRows = visible.filter((e) => !e.cited);
  // Fold only below cited rows: a filter that leaves just uncited ones shows them.
  const fold = filter !== "uncited" && citedRows.length > 0 && uncitedRows.length > 0;

  const filters: Array<{ id: AuditFilter; label: string }> = [
    { id: "all", label: "全部" },
    { id: "cited", label: "已引用" },
    { id: "verified", label: CITATION_STATUS.verified.label },
    { id: "metadata_only", label: CITATION_STATUS.metadata_only.label },
    { id: "unverified", label: CITATION_STATUS.unverified.label },
    { id: "uncited", label: "未引用" },
  ];

  const renderRow = (e: CitationAuditEntry) => {
    const link = entryLink(e);
    const st = CITATION_STATUS[e.status];
    return (
      <tr key={e.key} className={e.cited ? undefined : "is-uncited"}>
        <td className="is-mono">
          <span className="clip av-key" title={e.key}>
            {e.key}
          </span>
        </td>
        <td className="is-wrap av-col-title">
          {e.title || <span className="is-muted">（无标题）</span>}
        </td>
        <td className="is-num">{e.year ?? "—"}</td>
        <td className="is-nowrap">{e.source ? scholarSourceLabel(e.source) : "—"}</td>
        <td className="is-mono">
          {link ? (
            <button
              type="button"
              className="av-link clip av-doi"
              title={`在浏览器中打开 ${link}`}
              onClick={() => openUrl(link).catch(() => undefined)}
            >
              {e.doi ?? e.url}
            </button>
          ) : (
            <span className="clip av-doi is-muted" title={e.doi ?? e.url ?? undefined}>
              {e.doi ?? e.url ?? "—"}
            </span>
          )}
        </td>
        <td className="is-nowrap">
          <span className="av-badges">
            <span className={`badge ${st.badge}`} title={st.hint}>
              {st.label}
            </span>
            {!e.cited && (
              <span
                className="badge badge-neutral"
                title="正文没有引用这个条目，它不会出现在论文的参考文献列表里"
              >
                未引用
              </span>
            )}
          </span>
        </td>
      </tr>
    );
  };

  return (
    <div className="av-sections">
      <section aria-label="核查摘要">
        <dl className="stat-grid">
          <Stat
            label="进入参考文献"
            value={s.in_bibliography}
            unit="条"
            hint={`论文末尾参考文献列表实际列出的条目：BibTeX 只输出正文引用了的条目（正文共引用 ${s.cited} 个不同的键）`}
          />
          <Stat
            label="文献库条目"
            value={s.bib_entries}
            hint={`文献检索阶段收集到的全部条目${audit.bib_file ? `（${audit.bib_file}）` : ""}，不等于参考文献列表`}
          />
          <Stat
            label="库中未引用"
            value={s.uncited}
            hint="文献库里有、但正文没有引用的条目；它们不会出现在论文里"
          />
          <Stat
            label="已核验"
            value={s.verified}
            tone={s.bib_entries > 0 && s.verified === s.bib_entries ? "success" : undefined}
            hint={CITATION_STATUS.verified.hint}
          />
          <Stat label="含 DOI" value={s.with_doi} />
          <Stat
            label="缺失条目"
            value={s.missing_in_bib}
            tone={s.missing_in_bib > 0 ? "danger" : undefined}
            hint="正文引用了、但 .bib 中没有的键：编译后显示为 [?]"
          />
        </dl>
        <div className="av-audit-meta">
          <span className="av-audit-files">
            {audit.tex_file && (
              <span>
                正文 <code>{audit.tex_file}</code>
              </span>
            )}
            {audit.bib_file && (
              <span>
                文献库 <code>{audit.bib_file}</code>
              </span>
            )}
            {audit.cited_bib_file && (
              <span>
                只含被引用条目 <code>{audit.cited_bib_file}</code>
              </span>
            )}
            {audit.generated_at && <span>核查于 {formatIsoShort(audit.generated_at)}</span>}
          </span>
          {(mdPath || citedBibPath) && (
            <span className="av-audit-actions">
              {citedBibPath && (
                <button
                  type="button"
                  className="btn btn-sm"
                  title="文献库中只含正文引用的条目的副本"
                  onClick={() =>
                    api
                      .openPathInSystem(citedBibPath)
                      .catch(() => api.revealPathInExplorer(citedBibPath).catch(() => undefined))
                  }
                >
                  <ExternalLink size={13} aria-hidden="true" /> 打开 references_cited.bib
                </button>
              )}
              {mdPath && (
                <>
                  <button
                    type="button"
                    className="btn btn-sm"
                    onClick={async () => {
                      if (showAuditMd) {
                        setShowAuditMd(false);
                        return;
                      }
                      if (!auditMdText) {
                        setAuditMdBusy(true);
                        try {
                          const txt = await api.readRunTextArtifact(runDir, "citation_audit.md");
                          setAuditMdText(txt);
                        } catch (e) {
                          onToast?.(`读取 citation_audit.md 失败：${e}`, true);
                          setAuditMdBusy(false);
                          return;
                        } finally {
                          setAuditMdBusy(false);
                        }
                      }
                      setShowAuditMd(true);
                    }}
                  >
                    {auditMdBusy ? <Loader2 size={13} className="spin" aria-hidden="true" /> : <FileText size={13} aria-hidden="true" />}
                    {showAuditMd ? "收起报告" : "阅读 & 导出报告"}
                  </button>
                  <button
                    type="button"
                    className="btn btn-sm"
                    onClick={() =>
                      api
                        .openPathInSystem(mdPath)
                        .catch(() => api.revealPathInExplorer(mdPath).catch(() => undefined))
                    }
                  >
                    <ExternalLink size={13} aria-hidden="true" /> 打开 citation_audit.md
                  </button>
                </>
              )}
            </span>
          )}
        </div>
      </section>

      {showAuditMd && auditMdText && (
        <DocumentArtifactView
          content={auditMdText}
          title="Citation Audit Report"
          defaultFilename="citation_audit"
          maxHeight={460}
          onToast={onToast}
        />
      )}

      <div className="av-callouts">
      {audit.missing_in_bib.length > 0 && (
        <div className="callout callout-danger">
          <div className="callout-title">
            正文引用了 {formatInt(audit.missing_in_bib.length)} 个 .bib 中没有的键
          </div>
          编译后这些引用会显示为「[?]」。请检查键名拼写，或把对应条目补进参考文献。
          <KeyList keys={audit.missing_in_bib} />
        </div>
      )}
      {audit.uncited.length > 0 && (
        <div className="callout callout-info">
          <div className="callout-title">
            文献库里有 {formatInt(audit.uncited.length)} 条没有被正文引用，它们不在论文里
          </div>
          这些是文献检索阶段收集到、写作时没有用上的条目。BibTeX 只输出被引用的条目，所以论文末尾的参考文献列表里没有它们，论文本身不受影响。
          {exportBib
            ? `导出 Overleaf 包时，${exportBib.file} 只放入被引用的 ${formatInt(exportBib.kept)} 条。`
            : audit.cited_bib_file
              ? `只含被引用条目的副本已另存为 ${audit.cited_bib_file}。`
              : ""}
          <KeyList keys={audit.uncited} />
        </div>
      )}
      {audit.missing_in_bib.length === 0 && audit.uncited.length === 0 && entries.length > 0 && (
        <div className="callout callout-success">正文中的引用与参考文献条目一一对应。</div>
      )}
      </div>

      <section>
        <div className="av-sub-head">
          <h4 className="av-sub-title">文献库</h4>
          {audit.bib_file && <span className="av-sub-file">{audit.bib_file}</span>}
        </div>
        <p className="av-note av-note-lead">
          文献检索阶段收集到的全部条目，不等于论文末尾的参考文献列表（那里只有正文引用的{" "}
          {formatInt(s.in_bibliography)} 条）。被引用的排在前面，未引用的收在最后。
        </p>
        {entries.length === 0 ? (
          <div className="av-sub-empty">没有找到参考文献条目。</div>
        ) : (
          <>
            <div className="av-filters" role="group" aria-label="按引用与核查状态筛选">
              {filters.map((f) => (
                <button
                  key={f.id}
                  type="button"
                  className={`chip${filter === f.id ? " on" : ""}`}
                  aria-pressed={filter === f.id}
                  onClick={() => setFilter(f.id)}
                >
                  {f.label} <span className="rv-chip-count">{counts[f.id]}</span>
                </button>
              ))}
            </div>
            <p className="av-note">
              {(["verified", "metadata_only", "unverified"] as CitationStatus[])
                .map((k) => `${CITATION_STATUS[k].label}：${CITATION_STATUS[k].hint}`)
                .join("；")}
              。
            </p>
            {visible.length === 0 ? (
              <div className="av-sub-empty">没有符合筛选条件的条目。</div>
            ) : (
              <div className="data-table-wrap">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th scope="col">键</th>
                      <th scope="col">标题</th>
                      <th scope="col" className="is-num">年份</th>
                      <th scope="col">来源</th>
                      <th scope="col">DOI / 链接</th>
                      <th scope="col">状态</th>
                    </tr>
                  </thead>
                  <tbody>
                    {citedRows.map(renderRow)}
                    {fold && (
                      <tr className="av-group-row">
                        <td colSpan={6}>
                          <button
                            type="button"
                            className="av-group-toggle"
                            aria-expanded={showUncited}
                            onClick={() => setShowUncited((v) => !v)}
                          >
                            <ChevronRight size={13} className="av-chev" aria-hidden="true" />
                            {showUncited ? "收起" : "展开"}未被正文引用的 {formatInt(uncitedRows.length)} 条
                            <span className="av-group-hint">不会出现在论文的参考文献列表里</span>
                          </button>
                        </td>
                      </tr>
                    )}
                    {(!fold || showUncited) && uncitedRows.map(renderRow)}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}
      </section>
    </div>
  );
}

/* --------------------------------------------------------------------------
   Preview helpers
   -------------------------------------------------------------------------- */

function EmptyPreview({
  icon,
  title,
  children,
}: {
  icon: ReactNode;
  title: string;
  children?: ReactNode;
}) {
  return (
    <div className="empty-state av-empty">
      <span className="rv-empty-icon" aria-hidden="true">
        {icon}
      </span>
      <h3 className="empty-state-title">{title}</h3>
      {children && <p>{children}</p>}
    </div>
  );
}

type CodeLang = "json" | "latex";

/** Beyond this size the viewer skips line numbers and highlighting. */
const HIGHLIGHT_LIMIT = 1_500_000;

function CodeView({ text, lang }: { text: string; lang: CodeLang }) {
  return useMemo(() => {
    const source = lang === "json" ? formatPrettyJson(text) : text;
    if (source.length > HIGHLIGHT_LIMIT) {
      return <pre className="av-code is-plain">{source}</pre>;
    }
    const rows = source.replace(/\r\n?/g, "\n").split("\n");
    if (rows.length > 1 && rows[rows.length - 1] === "") rows.pop();
    const tokenize = lang === "json" ? tokenizeJson : tokenizeLatex;
    const style = { "--av-gutter": `${Math.max(2, String(rows.length).length)}ch` } as CSSProperties;
    return (
      <pre className={`av-code lang-${lang}`} style={style}>
        {rows.map((row, i) => (
          <span key={i} className="av-code-line">
            <span className="av-code-text">{tokenize(row)}</span>
          </span>
        ))}
      </pre>
    );
  }, [text, lang]);
}

const JSON_TOKEN = /("(?:\\.|[^"\\])*")(\s*:)?|\b(true|false|null)\b|-?\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b/g;

function tokenizeJson(line: string): ReactNode {
  const out: ReactNode[] = [];
  let last = 0;
  let k = 0;
  for (const m of line.matchAll(JSON_TOKEN)) {
    const idx = m.index ?? 0;
    if (idx > last) out.push(line.slice(last, idx));
    if (m[1] !== undefined) {
      if (m[2] !== undefined) {
        out.push(
          <span key={k++} className="av-tk-key">
            {m[1]}
          </span>
        );
        out.push(m[2]);
      } else {
        out.push(
          <span key={k++} className="av-tk-str">
            {m[1]}
          </span>
        );
      }
    } else {
      out.push(
        <span key={k++} className={m[3] !== undefined ? "av-tk-lit" : "av-tk-num"}>
          {m[0]}
        </span>
      );
    }
    last = idx + m[0].length;
  }
  if (last < line.length) out.push(line.slice(last));
  return out;
}

const TEX_TOKEN = /\\(?:[a-zA-Z@]+\*?|.)|[{}[\]]|\$\$?/g;
const TEX_STRUCTURE = new Set([
  "documentclass",
  "usepackage",
  "title",
  "author",
  "maketitle",
  "begin",
  "end",
  "part",
  "chapter",
  "section",
  "subsection",
  "subsubsection",
  "paragraph",
  "caption",
  "label",
  "appendix",
  "bibliography",
  "bibliographystyle",
  "input",
  "include",
]);

/** Index of the first unescaped % (a comment), or -1. */
function texCommentStart(line: string): number {
  for (let i = 0; i < line.length; i++) {
    if (line[i] !== "%") continue;
    let slashes = 0;
    for (let j = i - 1; j >= 0 && line[j] === "\\"; j--) slashes++;
    if (slashes % 2 === 0) return i;
  }
  return -1;
}

function tokenizeLatex(line: string): ReactNode {
  const cut = texCommentStart(line);
  const code = cut >= 0 ? line.slice(0, cut) : line;
  const out: ReactNode[] = [];
  let last = 0;
  let k = 0;
  for (const m of code.matchAll(TEX_TOKEN)) {
    const idx = m.index ?? 0;
    const tok = m[0];
    if (idx > last) out.push(code.slice(last, idx));
    let cls = "av-tk-brace";
    if (tok[0] === "\\") {
      cls = TEX_STRUCTURE.has(tok.slice(1).replace(/\*$/, "")) ? "av-tk-struct" : "av-tk-cmd";
    } else if (tok[0] === "$") {
      cls = "av-tk-math";
    }
    out.push(
      <span key={k++} className={cls}>
        {tok}
      </span>
    );
    last = idx + tok.length;
  }
  if (last < code.length) out.push(code.slice(last));
  if (cut >= 0) {
    out.push(
      <span key={k++} className="av-tk-comment">
        {line.slice(cut)}
      </span>
    );
  }
  return out;
}

function basename(p: string): string {
  return p.split(/[\\/]/).pop() || p;
}

function countLines(s: string): number {
  if (!s) return 0;
  let n = 1;
  for (let i = 0; i < s.length; i++) if (s.charCodeAt(i) === 10) n++;
  if (s.endsWith("\n")) n--;
  return n;
}

function formatPrettyJson(raw: string): string {
  try {
    return JSON.stringify(JSON.parse(raw), null, 2);
  } catch {
    return raw;
  }
}
