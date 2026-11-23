import {
  Ban,
  Check,
  CheckCircle2,
  FileArchive,
  FileText,
  FolderOpen,
  Loader2,
  ScrollText,
  Search,
  Square,
  Trash2,
  X,
  XCircle,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api, formatIsoShort } from "../api";
import { formatCad, parseUsageReport } from "../runReports";
import {
  PIPELINE_STAGES,
  type RunLogEntry,
  type RunRecord,
} from "../types";
import "./runs.css";

interface RunsViewProps {
  runs: RunRecord[];
  selectedRunId: string | null;
  onSelectRun: (runId: string) => void;
  logs: RunLogEntry[];
  onStopRun: (runId: string) => Promise<void>;
  onDeleteRun: (runId: string) => Promise<void>;
  onOpenArtifacts: (runId: string) => void;
  /** "导出 Overleaf 包" for a finished run; resolves to the written path or null. */
  onExportLatex?: (run: RunRecord) => Promise<string | null>;
}

/* --------------------------------------------------------------------------
   Shared presentation helpers (also used by ArtifactsView)
   -------------------------------------------------------------------------- */

export function StatusBadge({ status }: { status: string }) {
  if (status === "running") {
    return (
      <span className="badge badge-running">
        <Loader2 size={11} className="spin" aria-hidden="true" /> 运行中
      </span>
    );
  }
  if (status === "succeeded") {
    return (
      <span className="badge badge-succeeded">
        <CheckCircle2 size={11} aria-hidden="true" /> 已完成
      </span>
    );
  }
  if (status === "failed") {
    return (
      <span className="badge badge-failed">
        <XCircle size={11} aria-hidden="true" /> 失败
      </span>
    );
  }
  if (status === "cancelled") {
    return (
      <span className="badge badge-cancelled">
        <Ban size={11} aria-hidden="true" /> 已取消
      </span>
    );
  }
  return <span className="badge badge-neutral">{status}</span>;
}

/** Elapsed time of a run; for a running run it counts up to now. */
export function RunDuration({ run }: { run: RunRecord }) {
  return <>{formatRunDuration(run)}</>;
}

function pad2(n: number): string {
  return String(n).padStart(2, "0");
}

function formatRunDuration(run: RunRecord): string {
  const start = Date.parse(run.startedAt);
  const end = run.finishedAt
    ? Date.parse(run.finishedAt)
    : run.status === "running"
      ? Date.now()
      : Number.NaN;
  if (Number.isNaN(start) || Number.isNaN(end)) return "—";
  const total = Math.max(0, Math.round((end - start) / 1000));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h > 0) return `${h} 小时 ${pad2(m)} 分`;
  if (m > 0) return `${m} 分 ${pad2(s)} 秒`;
  return `${s} 秒`;
}

/* --------------------------------------------------------------------------
   Log presentation
   -------------------------------------------------------------------------- */

const STAGE_META: Record<string, { en: string; zh: string }> = Object.fromEntries(
  PIPELINE_STAGES.map((s) => {
    const [en, ...rest] = s.label.split(" ");
    return [s.id, { en, zh: rest.join(" ") || en }];
  })
);
STAGE_META.completed = { en: "Completed", zh: "完成" };

function stageMeta(stage: string): { en: string; zh: string } {
  return STAGE_META[stage] ?? { en: stage, zh: stage || "未分阶段" };
}

type LineTone = "plain" | "head" | "ok" | "warn" | "error";

/** Lines the backend uses to detect stage transitions (see runner.rs detect_stage). */
const STAGE_MARKERS = [
  "=== Initial Baseline Review ===",
  "Starting dedicated formatting loop",
  "Agent SectionWritingAgent starting",
  "Starting run_plotting_agent",
  "Starting Fast Hybrid Literature Agent",
  "Starting run_literature_agent",
];
const RE_ERROR = /traceback|exception|\b(error|errors|failed|failure|fatal|critical)\b|错误|失败|异常/i;
const RE_WARN = /\b(warn|warning|warnings|deprecated)\b|警告/i;
const RE_OK = /final pdf saved to:|\bcompleted successfully\b|\bsucceeded\b/i;
const RE_HEAD = /^\s*(={3,}|-{3,}|\*{3,}|#{1,3}\s)|={3,}\s*$|^\s*(step|stage|phase)\s*\d/i;

function classifyLine(line: string): LineTone {
  if (RE_ERROR.test(line)) return "error";
  if (RE_WARN.test(line)) return "warn";
  if (RE_OK.test(line)) return "ok";
  if (RE_HEAD.test(line) || STAGE_MARKERS.some((m) => line.includes(m))) return "head";
  return "plain";
}

const TIME_FMT = new Intl.DateTimeFormat("zh-CN", {
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hourCycle: "h23",
});

/** Per-entry cache so streaming a new line does not re-parse the whole log. */
const lineInfoCache = new WeakMap<RunLogEntry, { tone: LineTone; time: string }>();

function lineInfo(entry: RunLogEntry): { tone: LineTone; time: string } {
  let info = lineInfoCache.get(entry);
  if (!info) {
    const d = new Date(entry.createdAt);
    info = {
      tone: classifyLine(entry.line),
      time: Number.isNaN(d.getTime()) ? "" : TIME_FMT.format(d),
    };
    lineInfoCache.set(entry, info);
  }
  return info;
}

type LogRow =
  | { kind: "break"; key: string; stage: string }
  | {
      kind: "line";
      key: string;
      n: number;
      time: string;
      tone: LineTone;
      entry: RunLogEntry;
    };

function highlight(text: string, q: string): ReactNode {
  if (!q) return text;
  const lower = text.toLowerCase();
  const out: ReactNode[] = [];
  let from = 0;
  let k = 0;
  let i = lower.indexOf(q);
  while (i !== -1) {
    if (i > from) out.push(text.slice(from, i));
    out.push(
      <mark key={k++} className="rv-hit">
        {text.slice(i, i + q.length)}
      </mark>
    );
    from = i + q.length;
    i = lower.indexOf(q, from);
  }
  if (from < text.length) out.push(text.slice(from));
  return out;
}

const STAGE_STATE_LABEL = {
  done: "已完成",
  active: "进行中",
  pending: "未开始",
  failed: "中断于此",
  stopped: "停止于此",
} as const;

/* -------------------------------------------------------------------------- */

/** How often a running run's usage_report.json is re-read for the live cost. */
const COST_POLL_MS = 10_000;

/**
 * Estimated cost in CAD of each run, read from its usage_report.json
 * (undefined: not read yet or no estimate, e.g. runs from older versions).
 * A finished run is read once; a running one every COST_POLL_MS.
 */
function useRunCosts(runs: RunRecord[]): Record<string, number | undefined> {
  const [costs, setCosts] = useState<Record<string, number | undefined>>({});
  const readFor = useRef(new Map<string, string>()); // run id -> status it was last read with

  const runKey = runs.map((r) => `${r.id}:${r.status}`).join("|");
  useEffect(() => {
    let cancelled = false;

    /** False when this effect was cleaned up before the read finished. */
    async function readCost(run: RunRecord): Promise<boolean> {
      let value: number | undefined;
      try {
        const text = await api.readRunTextArtifact(run.runDir, "usage_report.json");
        value = parseUsageReport(text)?.cost?.total;
      } catch {
        value = undefined; // no report (yet)
      }
      if (cancelled) return false;
      setCosts((prev) => (prev[run.id] === value ? prev : { ...prev, [run.id]: value }));
      return true;
    }

    async function readChanged(): Promise<void> {
      for (const run of runs) {
        if (cancelled) return;
        if (readFor.current.get(run.id) === run.status) continue;
        // Marked as read only once the result is kept, so a read cut short
        // by a re-render is done again by the next effect.
        if (await readCost(run)) readFor.current.set(run.id, run.status);
      }
    }

    void readChanged();
    const running = runs.filter((r) => r.status === "running");
    const timer =
      running.length > 0
        ? window.setInterval(() => {
            for (const run of running) void readCost(run);
          }, COST_POLL_MS)
        : null;
    return () => {
      cancelled = true;
      if (timer !== null) window.clearInterval(timer);
    };
    // runKey covers every id and status change of `runs`.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runKey]);

  return costs;
}

export function RunsView({
  runs,
  selectedRunId,
  onSelectRun,
  logs,
  onStopRun,
  onDeleteRun,
  onOpenArtifacts,
  onExportLatex,
}: RunsViewProps) {
  const [stageFilter, setStageFilter] = useState<string>("all");
  const [exportingRunId, setExportingRunId] = useState<string | null>(null);
  const [textFilter, setTextFilter] = useState("");
  const [autoScroll, setAutoScroll] = useState(true);
  const consoleRef = useRef<HTMLDivElement | null>(null);
  const costs = useRunCosts(runs);
  const costTotal = useMemo(() => {
    let sum = 0;
    let n = 0;
    for (const r of runs) {
      const c = costs[r.id];
      if (c !== undefined) {
        sum += c;
        n += 1;
      }
    }
    return { sum, n };
  }, [runs, costs]);

  const selectedRun = useMemo(
    () => runs.find((r) => r.id === selectedRunId) ?? runs[0] ?? null,
    [runs, selectedRunId]
  );

  const visibleStages = useMemo(() => {
    if (!selectedRun || selectedRun.usePlotting) return PIPELINE_STAGES;
    return PIPELINE_STAGES.filter((s) => s.id !== "plotting");
  }, [selectedRun]);

  const filteredLogs = useMemo(() => {
    const q = textFilter.trim().toLowerCase();
    return logs.filter((l) => {
      if (stageFilter !== "all" && l.stage !== stageFilter) return false;
      if (q && !l.line.toLowerCase().includes(q)) return false;
      return true;
    });
  }, [logs, stageFilter, textFilter]);

  useEffect(() => {
    if (!autoScroll || !consoleRef.current) return;
    consoleRef.current.scrollTop = consoleRef.current.scrollHeight;
  }, [filteredLogs, autoScroll]);

  const stageCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const l of logs) counts.set(l.stage, (counts.get(l.stage) ?? 0) + 1);
    return counts;
  }, [logs]);

  // Display rows: each visible line keeps its number in the full log, and a
  // stage marker is inserted wherever the stage changes.
  const logRows = useMemo(() => {
    const lineNo = new Map<RunLogEntry, number>();
    logs.forEach((l, i) => lineNo.set(l, i + 1));
    const rows: LogRow[] = [];
    let prevStage: string | null = null;
    filteredLogs.forEach((entry, idx) => {
      const id = entry.id || `i${idx}`;
      if (entry.stage !== prevStage) {
        rows.push({ kind: "break", key: `b-${id}`, stage: entry.stage });
        prevStage = entry.stage;
      }
      const info = lineInfo(entry);
      rows.push({
        kind: "line",
        key: `l-${id}`,
        n: lineNo.get(entry) ?? idx + 1,
        time: info.time,
        tone: info.tone,
        entry,
      });
    });
    return rows;
  }, [logs, filteredLogs]);

  function getStageState(stageId: string): "done" | "active" | "pending" {
    if (!selectedRun) return "pending";
    if (selectedRun.status === "succeeded" || selectedRun.currentStage === "completed") {
      return "done";
    }
    const ids = visibleStages.map((s) => s.id as string);
    const activeIdx = ids.indexOf(selectedRun.currentStage);
    const curIdx = ids.indexOf(stageId);
    if (activeIdx === -1 || curIdx === -1) return "pending";
    if (curIdx < activeIdx) return "done";
    if (curIdx === activeIdx) {
      return selectedRun.status === "running" ? "active" : "pending";
    }
    return "pending";
  }

  const query = textFilter.trim().toLowerCase();
  const selectedIndex = selectedRun ? runs.indexOf(selectedRun) : -1;
  const selectedNo = selectedIndex >= 0 ? runs.length - selectedIndex : 0;
  const isRunning = selectedRun?.status === "running";

  return (
    <div className="rv-layout">
      {/* ---- Run index ---- */}
      <aside className="card rv-index" aria-label="运行记录">
        <header className="rv-index-head">
          <div>
            <h2 className="rv-index-title">运行记录</h2>
            <div className="rv-index-sub">按开始时间，最新在前</div>
            {costTotal.n > 0 && (
              <div
                className="rv-index-sub rv-index-cost"
                title={`${costTotal.n} 次运行的估算费用之和（按各平台标价换算为加元，免费额度和折扣不计；更早版本的运行没有费用记录）`}
              >
                累计约 <strong>{formatCad(costTotal.sum)}</strong>
              </div>
            )}
          </div>
          <span className="rv-index-count" aria-label={`共 ${runs.length} 次运行`}>
            {runs.length}
          </span>
        </header>

        {runs.length === 0 ? (
          <div className="rv-index-empty">
            当前项目尚未启动过运行。在工作台点击右上角「启动生成」即可开始。
          </div>
        ) : (
          <ul className="rv-run-list">
            {runs.map((r, i) => {
              const active = selectedRun?.id === r.id;
              return (
                <li key={r.id} className={`rv-run${active ? " is-active" : ""}`}>
                  <button
                    type="button"
                    className="rv-run-main"
                    aria-current={active ? "true" : undefined}
                    onClick={() => onSelectRun(r.id)}
                  >
                    <span className="rv-run-top">
                      <span className="rv-run-no">No. {pad2(runs.length - i)}</span>
                      <StatusBadge status={r.status} />
                    </span>
                    <span className="rv-run-title" title={`${r.templateName} · ${r.writerModel}`}>
                      {r.templateName} · {r.writerModel}
                    </span>
                    <span className="rv-run-time">
                      <span>{formatIsoShort(r.startedAt)}</span>
                      <span className="rv-run-time-sep" aria-hidden="true">
                        ·
                      </span>
                      <span>
                        <RunDuration run={r} />
                      </span>
                      {costs[r.id] !== undefined && (
                        <>
                          <span className="rv-run-time-sep" aria-hidden="true">
                            ·
                          </span>
                          <span title="估算费用（加元，按标价）">{formatCad(costs[r.id] ?? 0)}</span>
                        </>
                      )}
                    </span>
                  </button>
                  {r.status !== "running" && (
                    <button
                      type="button"
                      className="rv-run-delete"
                      title="删除此运行记录（仅删除数据库记录）"
                      aria-label="删除此运行记录"
                      onClick={(e) => {
                        e.stopPropagation();
                        onDeleteRun(r.id);
                      }}
                    >
                      <Trash2 size={13} aria-hidden="true" />
                    </button>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </aside>

      {/* ---- Run detail ---- */}
      <section className="card rv-detail" aria-label="运行详情">
        {!selectedRun ? (
          <div className="empty-state rv-detail-empty">
            <span className="rv-empty-icon" aria-hidden="true">
              <ScrollText size={20} strokeWidth={1.6} />
            </span>
            <h3 className="empty-state-title">还没有可查看的运行</h3>
            <p>在工作台启动一次生成后，这里会显示阶段进度与持久化日志。</p>
          </div>
        ) : (
          <>
            <header className="rv-head">
              <div className="rv-head-main">
                <div className="rv-eyebrow">
                  运行编号
                  <span className="rv-mono" title={selectedRun.id}>
                    {selectedRun.id.slice(0, 8)}
                  </span>
                </div>
                <div className="rv-title-row">
                  <h2 className="rv-title">第 {selectedNo} 次运行</h2>
                  <StatusBadge status={selectedRun.status} />
                </div>
              </div>

              <div className="rv-actions">
                {isRunning && (
                  <button
                    type="button"
                    className="btn btn-danger btn-sm"
                    onClick={() => onStopRun(selectedRun.id)}
                  >
                    <Square size={12} aria-hidden="true" /> 停止运行
                  </button>
                )}
                <button
                  type="button"
                  className="btn btn-sm"
                  onClick={() => api.openPathInSystem(selectedRun.runDir)}
                >
                  <FolderOpen size={13} aria-hidden="true" /> 打开输出目录
                </button>
                {onExportLatex && selectedRun.status === "succeeded" && (
                  <button
                    type="button"
                    className="btn btn-sm"
                    disabled={exportingRunId !== null}
                    title="把最终 LaTeX 源码（.tex / .bib / 样式文件 / 图表）打包为 .zip，可直接上传到 Overleaf"
                    onClick={async () => {
                      const run = selectedRun;
                      setExportingRunId(run.id);
                      try {
                        await onExportLatex(run);
                      } finally {
                        setExportingRunId(null);
                      }
                    }}
                  >
                    {exportingRunId === selectedRun.id ? (
                      <Loader2 size={13} className="spin" aria-hidden="true" />
                    ) : (
                      <FileArchive size={13} aria-hidden="true" />
                    )}
                    {exportingRunId === selectedRun.id ? "导出中…" : "导出 Overleaf 包"}
                  </button>
                )}
                <button
                  type="button"
                  className={`btn btn-sm${selectedRun.pdfPath ? " btn-primary" : ""}`}
                  onClick={() => onOpenArtifacts(selectedRun.id)}
                >
                  <FileText size={13} aria-hidden="true" /> 查看成果与 PDF
                </button>
              </div>
            </header>

            <dl className="rv-ledger">
              <div className="rv-ledger-cell">
                <dt>模板</dt>
                <dd title={selectedRun.templateName}>{selectedRun.templateName}</dd>
              </div>
              <div className="rv-ledger-cell">
                <dt>写作模型</dt>
                <dd className="rv-mono" title={selectedRun.writerModel}>
                  {selectedRun.writerModel}
                </dd>
              </div>
              <div className="rv-ledger-cell">
                <dt>评审模型</dt>
                <dd className="rv-mono" title={selectedRun.reflectionModel}>
                  {selectedRun.reflectionModel || "—"}
                </dd>
              </div>
              <div className="rv-ledger-cell">
                <dt>绘图</dt>
                <dd className={selectedRun.usePlotting ? undefined : "is-muted"}>
                  {selectedRun.usePlotting ? "已启用" : "未启用"}
                </dd>
              </div>
              <div className="rv-ledger-cell">
                <dt>开始</dt>
                <dd>{formatIsoShort(selectedRun.startedAt)}</dd>
              </div>
              <div className="rv-ledger-cell">
                <dt>结束</dt>
                <dd className={selectedRun.finishedAt ? undefined : "is-muted"}>
                  {selectedRun.finishedAt
                    ? formatIsoShort(selectedRun.finishedAt)
                    : isRunning
                      ? "进行中"
                      : "—"}
                </dd>
              </div>
              <div className="rv-ledger-cell">
                <dt>{isRunning ? "已用时" : "用时"}</dt>
                <dd>
                  <RunDuration run={selectedRun} />
                </dd>
              </div>
              <div className="rv-ledger-cell">
                <dt>{isRunning ? "本次已花约" : "估算费用"}</dt>
                <dd
                  className={costs[selectedRun.id] === undefined ? "is-muted" : "rv-cost"}
                  title={
                    costs[selectedRun.id] === undefined
                      ? "没有费用记录（还没有模型调用，或为更早版本的运行）"
                      : `按各平台标价估算，换算为加元；免费额度、折扣和税费不计。${
                          isRunning ? "运行中每 10 秒更新。" : ""
                        }明细见「查看成果」的用量页。`
                  }
                >
                  {costs[selectedRun.id] === undefined ? "—" : formatCad(costs[selectedRun.id] ?? 0)}
                </dd>
              </div>
              <div className="rv-ledger-cell">
                <dt>退出码</dt>
                <dd className={selectedRun.exitCode === null ? "is-muted" : "rv-mono"}>
                  {selectedRun.exitCode ?? "—"}
                </dd>
              </div>
            </dl>

            <ol className="rv-stages" aria-label="流水线阶段">
              {visibleStages.map((st, i) => {
                const state = getStageState(st.id);
                const halted =
                  state === "pending" &&
                  selectedRun.currentStage === st.id &&
                  (selectedRun.status === "failed" || selectedRun.status === "cancelled");
                const look = halted
                  ? selectedRun.status === "failed"
                    ? "failed"
                    : "stopped"
                  : state;
                const meta = stageMeta(st.id);
                return (
                  <li
                    key={st.id}
                    className={`rv-stage is-${look}`}
                    aria-current={state === "active" ? "step" : undefined}
                  >
                    <span className="rv-stage-node" aria-hidden="true">
                      {look === "done" ? (
                        <Check size={12} strokeWidth={2.75} />
                      ) : look === "failed" ? (
                        <X size={12} strokeWidth={2.75} />
                      ) : look === "stopped" ? (
                        <Square size={8} strokeWidth={0} fill="currentColor" />
                      ) : look === "active" ? (
                        <span className="rv-stage-pulse" />
                      ) : (
                        i + 1
                      )}
                    </span>
                    <span className="rv-stage-text">
                      <span className="rv-stage-en">{meta.en}</span>
                      <span className="rv-stage-zh">{meta.zh}</span>
                    </span>
                    <span className="rv-sr">{STAGE_STATE_LABEL[look]}</span>
                  </li>
                );
              })}
            </ol>

            {selectedRun.errorMessage && (
              <div
                className={`rv-alert${selectedRun.status === "cancelled" ? " is-stopped" : ""}`}
              >
                {selectedRun.status === "cancelled" ? (
                  <Ban size={15} aria-hidden="true" />
                ) : (
                  <XCircle size={15} aria-hidden="true" />
                )}
                <div className="rv-alert-body">
                  <div className="rv-alert-title">
                    {selectedRun.status === "cancelled" ? "运行已停止" : "运行未成功"}
                  </div>
                  <div className="rv-alert-msg">{selectedRun.errorMessage}</div>
                </div>
              </div>
            )}

            {/* ---- Log viewer ---- */}
            <div className="rv-log">
              <div className="rv-log-toolbar">
                <div className="rv-log-filters" role="group" aria-label="按阶段筛选日志">
                  <button
                    type="button"
                    className={`chip${stageFilter === "all" ? " on" : ""}`}
                    aria-pressed={stageFilter === "all"}
                    onClick={() => setStageFilter("all")}
                  >
                    全部 <span className="rv-chip-count">{logs.length}</span>
                  </button>
                  {visibleStages.map((s) => {
                    const meta = stageMeta(s.id);
                    return (
                      <button
                        key={s.id}
                        type="button"
                        className={`chip${stageFilter === s.id ? " on" : ""}`}
                        aria-pressed={stageFilter === s.id}
                        title={meta.en}
                        onClick={() => setStageFilter(s.id)}
                      >
                        {meta.zh}{" "}
                        <span className="rv-chip-count">{stageCounts.get(s.id) ?? 0}</span>
                      </button>
                    );
                  })}
                </div>

                <div className="rv-search">
                  <Search size={13} className="rv-search-icon" aria-hidden="true" />
                  <input
                    className="input"
                    placeholder="筛选日志关键词"
                    aria-label="筛选日志关键词"
                    value={textFilter}
                    onChange={(e) => setTextFilter(e.target.value)}
                  />
                  {textFilter && (
                    <button
                      type="button"
                      className="rv-search-clear"
                      aria-label="清除关键词"
                      title="清除关键词"
                      onClick={() => setTextFilter("")}
                    >
                      <X size={12} aria-hidden="true" />
                    </button>
                  )}
                </div>
              </div>

              <div
                className="rv-console"
                ref={consoleRef}
                role="log"
                aria-label="运行日志"
                tabIndex={0}
              >
                {filteredLogs.length === 0 ? (
                  <div className="rv-console-empty">
                    <span>
                      {logs.length === 0
                        ? isRunning
                          ? "等待日志输出…"
                          : "这次运行没有记录日志。"
                        : "没有匹配当前筛选条件的日志。"}
                    </span>
                  </div>
                ) : (
                  logRows.map((row) => {
                    if (row.kind === "break") {
                      const meta = stageMeta(row.stage);
                      return (
                        <div key={row.key} className="rv-break">
                          <span className="rv-ln" />
                          <span className="rv-time" />
                          <span className="rv-break-label">
                            {meta.zh}
                            {meta.en !== meta.zh && <span className="rv-break-en">{meta.en}</span>}
                          </span>
                        </div>
                      );
                    }
                    return (
                      <div key={row.key} className={`rv-line is-${row.tone}`}>
                        <span className="rv-ln">{row.n}</span>
                        <span className="rv-time">{row.time}</span>
                        <span className="rv-text">{highlight(row.entry.line, query)}</span>
                      </div>
                    );
                  })
                )}
              </div>

              <div className="rv-log-foot">
                <span>
                  {filteredLogs.length < logs.length
                    ? `显示 ${filteredLogs.length} / ${logs.length} 行`
                    : `共 ${logs.length} 行`}
                </span>
                {isRunning && (
                  <span className="rv-live">
                    <span className="rv-live-dot" aria-hidden="true" />
                    实时输出中
                  </span>
                )}
                <span className="rv-spacer" />
                <label className="rv-switch">
                  <input
                    type="checkbox"
                    checked={autoScroll}
                    onChange={(e) => setAutoScroll(e.target.checked)}
                  />
                  <span className="rv-switch-track" aria-hidden="true" />
                  自动滚动
                </label>
              </div>
            </div>
          </>
        )}
      </section>
    </div>
  );
}
