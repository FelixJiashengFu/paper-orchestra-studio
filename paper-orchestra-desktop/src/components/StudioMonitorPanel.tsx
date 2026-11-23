import {
  Ban,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Clock,
  Cpu,
  FolderOpen,
  Loader2,
  ScrollText,
  Square,
  XCircle,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";
import { providerLabel } from "../providerCatalog";
import { formatCad, formatInt, parseUsageReport, totalTokens } from "../runReports";
import type {
  StudioEventPayload,
  StudioJobTelemetry,
  StudioStepItem,
  UsageReport,
} from "../types";

const STAGE_LABELS: Record<string, string> = {
  init: "启动任务",
  profile: "数据剖析",
  codegen: "生成脚本",
  execute: "沙盒执行",
  figures: "图表归档",
  report: "撰写报告",
  materials: "读取材料",
  search: "文献检索",
  benchmark: "基准建模",
  synthesize: "模型推理",
  finalize: "归档交付",
  prepare: "版本准备",
  llm: "智能改图",
  render: "渲染校验",
  sync: "同步写回",
};

function stageTitle(stage: string): string {
  return STAGE_LABELS[stage] ?? stage;
}

function formatClockTime(isoOrNow?: string): string {
  const d = isoOrNow ? new Date(isoOrNow) : new Date();
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleTimeString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  });
}

function formatElapsedMs(ms: number): string {
  const totalSec = Math.max(0, Math.floor(ms / 1000));
  const min = Math.floor(totalSec / 60);
  const sec = totalSec % 60;
  return `${String(min).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
}

function makeJobId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `studio-${Date.now()}-${Math.random().toString(16).slice(2, 10)}`;
}

function normalizeUsage(raw: unknown): UsageReport | null {
  if (!raw || typeof raw !== "object") return null;
  try {
    return parseUsageReport(JSON.stringify(raw));
  } catch {
    return null;
  }
}

export function useStudioTelemetry() {
  const [job, setJob] = useState<StudioJobTelemetry | null>(null);
  const activeJobIdRef = useRef<string | null>(null);
  const logSeqRef = useRef(0);

  useEffect(() => {
    let unlisten: (() => void) | null = null;
    let disposed = false;

    api
      .onStudioEvent((ev: StudioEventPayload) => {
        if (disposed || !ev.jobId || ev.jobId !== activeJobIdRef.current) return;
        setJob((prev) => {
          if (!prev || prev.jobId !== ev.jobId) return prev;
          const ts = formatClockTime(ev.timestamp);

          if (ev.kind === "started") {
            return {
              ...prev,
              outDir: ev.outDir || prev.outDir,
              currentMessage: ev.message || prev.currentMessage,
              logs: ev.message
                ? [
                    ...prev.logs.slice(-299),
                    { id: ++logSeqRef.current, time: ts, level: "info", text: ev.message },
                  ]
                : prev.logs,
            };
          }

          if (ev.kind === "usage") {
            const parsed = normalizeUsage(ev.usage);
            if (!parsed) return prev;
            return { ...prev, usage: parsed };
          }

          if (ev.kind === "progress") {
            const stage = (ev.stage || "work").trim();
            const status = ev.status || "running";
            const msg = ev.message || stageTitle(stage);
            const model = ev.model || prev.activeModel;
            const estAdd =
              status === "running" && ev.estInputTokens ? Number(ev.estInputTokens) : 0;

            const steps: StudioStepItem[] = [...prev.steps];
            const idx = steps.findIndex((s) => s.stage === stage);
            const item: StudioStepItem = {
              stage,
              status,
              message: msg,
              model: ev.model || steps[idx]?.model,
              detail: ev.detail || undefined,
              timestamp: ev.timestamp,
            };
            if (idx >= 0) {
              steps[idx] = item;
            } else {
              steps.push(item);
            }

            const nextLogs = [
              ...prev.logs.slice(-299),
              {
                id: ++logSeqRef.current,
                time: ts,
                level: status === "error" ? "error" : "stage",
                text: `[${stageTitle(stage)}] ${msg}${ev.detail ? ` — ${ev.detail}` : ""}`,
              },
            ];

            return {
              ...prev,
              activeModel: model,
              estInputTokens: prev.estInputTokens + estAdd,
              currentMessage: msg,
              steps,
              logs: nextLogs,
            };
          }

          if (ev.kind === "log") {
            const text = (ev.line || "").trim();
            if (!text) return prev;
            return {
              ...prev,
              logs: [
                ...prev.logs.slice(-299),
                { id: ++logSeqRef.current, time: ts, level: ev.level || "info", text },
              ],
            };
          }

          if (ev.kind === "done") {
            return {
              ...prev,
              status: "completed",
              finishedAtMs: prev.finishedAtMs ?? Date.now(),
              currentMessage: ev.message || "执行完成",
            };
          }

          if (ev.kind === "cancelled") {
            return {
              ...prev,
              status: "cancelled",
              finishedAtMs: prev.finishedAtMs ?? Date.now(),
              currentMessage: ev.message || "已手动停止",
              logs: [
                ...prev.logs.slice(-299),
                { id: ++logSeqRef.current, time: ts, level: "warn", text: ev.message || "已手动停止任务" },
              ],
            };
          }

          if (ev.kind === "error") {
            const err = ev.message || "执行失败";
            return {
              ...prev,
              status: "error",
              finishedAtMs: prev.finishedAtMs ?? Date.now(),
              currentMessage: err,
              errorMessage: err,
              logs: [
                ...prev.logs.slice(-299),
                { id: ++logSeqRef.current, time: ts, level: "error", text: err },
              ],
            };
          }

          return prev;
        });
      })
      .then((fn) => {
        if (disposed) {
          fn();
        } else {
          unlisten = fn;
        }
      });

    return () => {
      disposed = true;
      if (unlisten) unlisten();
    };
  }, []);

  const beginJob = useCallback((title: string): string => {
    const jobId = makeJobId();
    activeJobIdRef.current = jobId;
    setJob({
      jobId,
      title,
      status: "running",
      startedAtMs: Date.now(),
      estInputTokens: 0,
      currentMessage: "正在初始化工作流环境…",
      steps: [],
      logs: [
        {
          id: ++logSeqRef.current,
          time: formatClockTime(),
          level: "stage",
          text: `开始执行：${title}`,
        },
      ],
      usage: null,
    });
    return jobId;
  }, []);

  const finishJob = useCallback((usageFallback?: UsageReport | null, outDir?: string) => {
    setJob((prev) => {
      if (!prev) return prev;
      const fallbackHasData =
        !!usageFallback &&
        (totalTokens(usageFallback.totals) > 0 || usageFallback.totals.calls > 0);
      return {
        ...prev,
        status: prev.status === "cancelled" ? "cancelled" : "completed",
        finishedAtMs: prev.finishedAtMs ?? Date.now(),
        outDir: outDir || prev.outDir,
        currentMessage: prev.status === "cancelled" ? "已手动停止" : "执行完成",
        usage: fallbackHasData ? usageFallback : (prev.usage ?? usageFallback ?? null),
      };
    });
  }, []);

  const failJob = useCallback((err: unknown) => {
    const msg = String(err ?? "执行失败").replace(/^Error:\s*/i, "");
    setJob((prev) => {
      if (!prev) return prev;
      if (prev.status === "cancelled") return prev;
      const isCancelled = msg.includes("手动停止");
      return {
        ...prev,
        status: isCancelled ? "cancelled" : "error",
        finishedAtMs: prev.finishedAtMs ?? Date.now(),
        currentMessage: msg,
        errorMessage: isCancelled ? undefined : msg,
      };
    });
  }, []);

  const cancelJob = useCallback(async () => {
    const jid = activeJobIdRef.current;
    if (!jid) return;
    try {
      await api.cancelStudioJob(jid);
    } catch {
      // process might already have exited
    }
  }, []);

  const clearJob = useCallback(() => {
    activeJobIdRef.current = null;
    setJob(null);
  }, []);

  return { job, beginJob, finishJob, failJob, cancelJob, clearJob };
}

interface StudioMonitorPanelProps {
  job: StudioJobTelemetry | null;
  onCancel?: () => void;
  compact?: boolean;
}

export function StudioMonitorPanel({ job, onCancel, compact = false }: StudioMonitorPanelProps) {
  const [nowMs, setNowMs] = useState(() => Date.now());
  const [showLogs, setShowLogs] = useState(true);
  const [showModels, setShowModels] = useState(false);
  const logRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!job || job.status !== "running") return;
    setNowMs(Date.now());
    setShowLogs(true);
    const timer = window.setInterval(() => setNowMs(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [job?.jobId, job?.status]);

  useEffect(() => {
    if (compact && job && job.status === "completed") {
      setShowLogs(false);
    }
  }, [compact, job?.status]);

  useEffect(() => {
    if (!showLogs || !logRef.current) return;
    logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [job?.logs.length, showLogs]);

  if (!job) return null;

  const isRunning = job.status === "running";
  const elapsedMs = (job.finishedAtMs ?? nowMs) - job.startedAtMs;
  const t = job.usage?.totals ?? { calls: 0, errors: 0, input_tokens: 0, output_tokens: 0 };
  const grandTokens = totalTokens(t);
  const billedIn = t.input_tokens;
  const displayInTokens =
    billedIn > 0
      ? formatInt(billedIn)
      : job.estInputTokens > 0
        ? `~${formatInt(job.estInputTokens)}`
        : "0";
  const inSubLabel =
    billedIn > 0 ? "已计费输入" : job.estInputTokens > 0 && isRunning ? "预计在途输入" : "输入 Tokens";
  const cost = job.usage?.cost ?? null;
  const byModel = job.usage?.by_model ?? [];

  return (
    <div className={`sm-panel ${compact ? "is-compact" : ""}`}>
      <div className="sm-head">
        <div className="row" style={{ flexWrap: "wrap", gap: 8 }}>
          {isRunning ? (
            <span className="badge badge-running">
              <Loader2 size={11} className="spin" /> 运行中
            </span>
          ) : job.status === "completed" ? (
            <span className="badge badge-succeeded">
              <CheckCircle2 size={11} /> 已完成
            </span>
          ) : job.status === "cancelled" ? (
            <span className="badge badge-cancelled">
              <Ban size={11} /> 已停止
            </span>
          ) : (
            <span className="badge badge-failed">
              <XCircle size={11} /> 执行失败
            </span>
          )}
          <strong style={{ fontSize: "var(--fs-sm)" }}>{job.title}</strong>
          <span className="sm-timer">
            <Clock size={11} /> {formatElapsedMs(elapsedMs)}
          </span>
          {job.activeModel && (
            <span className="badge badge-neutral st-mono" title="当前调用模型">
              <Cpu size={11} /> {job.activeModel}
            </span>
          )}
        </div>

        <div className="row" style={{ gap: 6 }}>
          {job.outDir && (
            <button
              type="button"
              className="btn btn-sm"
              onClick={() => api.openPathInSystem(job.outDir!)}
              title={job.outDir}
            >
              <FolderOpen size={12} /> 结果目录
            </button>
          )}
          {isRunning && onCancel && (
            <button type="button" className="btn btn-danger btn-sm" onClick={onCancel}>
              <Square size={11} fill="currentColor" /> 停止运行
            </button>
          )}
        </div>
      </div>

      <div className="sm-status-line">{job.currentMessage}</div>

      <div className="sm-metrics">
        <div className="sm-metric">
          <span className="sm-metric-label">LLM 调用</span>
          <span className="sm-metric-value">
            {formatInt(t.calls)} 次{t.errors > 0 ? <em className="sm-err-count"> ({t.errors} 错)</em> : null}
          </span>
        </div>
        <div className="sm-metric">
          <span className="sm-metric-label">{inSubLabel}</span>
          <span className="sm-metric-value st-mono">{displayInTokens}</span>
        </div>
        <div className="sm-metric">
          <span className="sm-metric-label">输出 Tokens</span>
          <span className="sm-metric-value st-mono">{formatInt(t.output_tokens)}</span>
        </div>
        <div className="sm-metric">
          <span className="sm-metric-label">实时合计 Tokens</span>
          <span className="sm-metric-value st-mono is-accent">{formatInt(grandTokens)}</span>
        </div>
        <div className="sm-metric">
          <span className="sm-metric-label">估算费用 (CAD)</span>
          <span className="sm-metric-value st-mono">{cost ? formatCad(cost.total) : "—"}</span>
        </div>
      </div>

      {job.steps.length > 0 && (
        <div className="sm-steps" aria-label="阶段进度">
          {job.steps.map((st) => (
            <span
              key={st.stage}
              className={`sm-step is-${st.status}`}
              title={st.detail ? `${st.message}\n${st.detail}` : st.message}
            >
              {st.status === "running" ? (
                <Loader2 size={11} className="spin" />
              ) : st.status === "completed" ? (
                <CheckCircle2 size={11} />
              ) : (
                <XCircle size={11} />
              )}
              <span>{stageTitle(st.stage)}</span>
            </span>
          ))}
        </div>
      )}

      <div className="sm-toggles">
        <button type="button" className="btn btn-ghost btn-sm" onClick={() => setShowLogs((v) => !v)}>
          {showLogs ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
          <ScrollText size={12} /> 实时运行日志 ({job.logs.length} 条)
        </button>
        {byModel.length > 0 && (
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => setShowModels((v) => !v)}>
            {showModels ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
            按模型 Token 明细 ({byModel.length})
          </button>
        )}
      </div>

      {showModels && byModel.length > 0 && (
        <div className="data-table-wrap" style={{ marginTop: 4 }}>
          <table className="data-table">
            <thead>
              <tr>
                <th>平台</th>
                <th>模型</th>
                <th className="is-num">调用</th>
                <th className="is-num">输入</th>
                <th className="is-num">输出</th>
                <th className="is-num">合计</th>
                <th className="is-num">估算费用</th>
              </tr>
            </thead>
            <tbody>
              {byModel.map((r) => (
                <tr key={`${r.provider}:${r.model}`}>
                  <td>{r.provider ? providerLabel(r.provider) : "—"}</td>
                  <td className="is-mono">{r.model}</td>
                  <td className="is-num">{formatInt(r.calls)}</td>
                  <td className="is-num">{formatInt(r.input_tokens)}</td>
                  <td className="is-num">{formatInt(r.output_tokens)}</td>
                  <td className="is-num is-strong">{formatInt(totalTokens(r))}</td>
                  <td className="is-num">{r.cost ? formatCad(r.cost.cad) : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {showLogs && (
        <div
          ref={logRef}
          className={`sm-console ${compact ? "is-compact" : ""}`}
          role="log"
          aria-label="科研工坊实时日志"
        >
          {job.logs.map((entry) => (
            <div key={entry.id} className={`sm-log-line is-${entry.level}`}>
              <span className="sm-log-time">{entry.time}</span>
              <span className="sm-log-text">{entry.text}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
