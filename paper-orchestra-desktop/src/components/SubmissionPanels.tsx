/**
 * 「投稿工具」: arXiv package, double-blind check, LaTeX diagnostics, a
 * changes PDF between two versions, and the online citation check. Each panel
 * runs one subcommand of paper-orchestra/tools/submission_tools.py.
 *
 * The checks are adapted from Lattice (https://github.com/leo1oel/lattice,
 * Apache-2.0); see paper-orchestra/THIRD_PARTY_NOTICES.md.
 */
import {
  AlertTriangle,
  CheckCircle2,
  ChevronRight,
  ExternalLink,
  FolderOpen,
  Loader2,
  Play,
  XCircle,
} from "lucide-react";
import { openUrl } from "@tauri-apps/plugin-opener";
import { downloadDir, join } from "@tauri-apps/api/path";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { api } from "../api";
import type { LeanAuditReport, RunRecord } from "../types";

type CheckState = "ok" | "warn" | "fail";

export interface CheckRow {
  id: string;
  state: CheckState;
  label: string;
  detail: string;
  items: string[];
}

interface ArxivReport {
  kind: "arxiv";
  rootDocument: string;
  files: { path: string; bytes: number }[];
  totalBytes: number;
  checks: CheckRow[];
  blocked: boolean;
  zip: string | null;
}

interface ChecksReport {
  kind: "blind" | "diagnose" | "manuscript-audit";
  rootDocument: string;
  summary?: string;
  checks: CheckRow[];
  compiledNow?: boolean;
}

interface VersionInfo {
  path: string;
  name: string;
  folder: string;
  label: string;
  hasPdf: boolean;
  mtime: number;
}

interface DiffReport {
  pdf: string;
  simplified: boolean;
  pages: number | null;
}

type CiteStatus = "verified" | "fix" | "mismatch" | "not_found" | "error" | "skipped";

interface CiteEntry {
  key: string;
  title: string;
  year: number | null;
  doi: string | null;
  status: CiteStatus;
  found: { source: string; title: string; doi: string | null; year: number | null; venue: string; url: string | null } | null;
  score: number;
  notes: string[];
  suggestions?: string[];
}

interface CiteReport {
  checkedAt: string;
  total: number;
  counts: Partial<Record<CiteStatus, number>>;
  entries: CiteEntry[];
}

const ONLINE_FILE = "citation_online_check.json";

function runTool<T>(args: string[]): Promise<T> {
  return api.runSubmissionTool(args) as Promise<T>;
}

function errText(e: unknown): string {
  return typeof e === "string" ? e : e instanceof Error ? e.message : String(e);
}

async function defaultSavePath(fileName: string): Promise<string> {
  try {
    return await join(await downloadDir(), fileName);
  } catch {
    return fileName;
  }
}

function safeName(s: string): string {
  return s.replace(/[\\/:*?"<>|]+/g, "-").slice(0, 60) || "paper";
}

function runBaseName(run: RunRecord): string {
  const parts = run.runDir.split(/[\\/]/).filter(Boolean);
  return safeName(parts[parts.length - 1] ?? "paper");
}

/* -------------------------------------------------------------------------- shared */

function StateIcon({ state }: { state: CheckState }) {
  if (state === "ok") return <CheckCircle2 size={15} className="sp-ok" aria-label="通过" />;
  if (state === "warn") return <AlertTriangle size={15} className="sp-warn" aria-label="注意" />;
  return <XCircle size={15} className="sp-fail" aria-label="有问题" />;
}

export function CheckList({ checks }: { checks: CheckRow[] }) {
  return (
    <ul className="sp-checks">
      {checks.map((c) => (
        <li key={c.id} className={`sp-check is-${c.state}`}>
          {c.items.length > 0 ? (
            <details>
              <summary>
                <StateIcon state={c.state} />
                <span className="sp-check-label">{c.label}</span>
                <span className="sp-check-detail">{c.detail}</span>
                <ChevronRight size={13} className="sp-chev" aria-hidden="true" />
              </summary>
              <ul className="sp-items">
                {c.items.map((it, i) => (
                  <li key={i}>{it}</li>
                ))}
              </ul>
            </details>
          ) : (
            <div className="sp-check-row">
              <StateIcon state={c.state} />
              <span className="sp-check-label">{c.label}</span>
              <span className="sp-check-detail">{c.detail}</span>
            </div>
          )}
        </li>
      ))}
    </ul>
  );
}

function Intro({ children }: { children: ReactNode }) {
  return <p className="sp-intro">{children}</p>;
}

function ErrorBox({ msg }: { msg: string }) {
  return (
    <div className="rv-alert" role="alert">
      <XCircle size={15} aria-hidden="true" />
      <div className="rv-alert-body">
        <div className="rv-alert-msg">{msg}</div>
      </div>
    </div>
  );
}

function RunButton({
  busy,
  disabled,
  onClick,
  children,
  primary,
  busyText = "运行中…",
}: {
  busy: boolean;
  disabled?: boolean;
  onClick: () => void;
  children: ReactNode;
  primary?: boolean;
  busyText?: string;
}) {
  return (
    <button
      type="button"
      className={`btn btn-sm${primary ? " btn-primary" : ""}`}
      onClick={onClick}
      disabled={busy || disabled}
    >
      {busy ? <Loader2 size={13} className="spin" aria-hidden="true" /> : <Play size={13} aria-hidden="true" />}
      {busy ? busyText : children}
    </button>
  );
}

function SavedFile({ path, label }: { path: string; label: string }) {
  return (
    <div className="callout callout-success sp-saved" role="status">
      <CheckCircle2 size={15} aria-hidden="true" />
      <div className="sp-saved-body">
        <div>{label}</div>
        <div className="sp-mono" title={path}>
          {path}
        </div>
      </div>
      <button type="button" className="btn btn-sm" onClick={() => api.openPathInSystem(path)}>
        <ExternalLink size={13} aria-hidden="true" /> 打开
      </button>
      <button type="button" className="btn btn-sm" onClick={() => api.revealPathInExplorer(path).catch(() => undefined)}>
        <FolderOpen size={13} aria-hidden="true" /> 在文件夹中显示
      </button>
    </div>
  );
}

function useRunReset(run: RunRecord, reset: () => void) {
  useEffect(() => {
    reset();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [run.runDir]);
}

/* -------------------------------------------------------------------------- arXiv */

export function ArxivPanel({ run, running }: { run: RunRecord; running: boolean }) {
  const [stripComments, setStripComments] = useState(true);
  const [mergeInputs, setMergeInputs] = useState(false);
  const [busy, setBusy] = useState<"check" | "export" | null>(null);
  const [report, setReport] = useState<ArxivReport | null>(null);
  const [error, setError] = useState("");
  useRunReset(run, () => {
    setReport(null);
    setError("");
  });

  async function go(exporting: boolean) {
    setError("");
    let out: string | null = null;
    if (exporting) {
      out = await api.pickSaveFile(await defaultSavePath(`${runBaseName(run)}-arxiv.zip`), [
        { name: "ZIP 压缩包", extensions: ["zip"] },
      ]);
      if (!out) return;
    }
    setBusy(exporting ? "export" : "check");
    try {
      const args = ["arxiv", run.runDir];
      if (out) args.push("--out", out);
      if (!stripComments) args.push("--keep-comments");
      if (mergeInputs) args.push("--merge-inputs");
      setReport(await runTool<ArxivReport>(args));
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="sp-panel">
      <Intro>
        按论文编译时真正读到的文件打包，带上 <code>.bbl</code>（arXiv 不跑 BibTeX），再在空文件夹里单独编译一遍确认能过。
        先「检查」看结果，没有红色问题再导出。
      </Intro>
      <div className="sp-options">
        <label>
          <input type="checkbox" checked={stripComments} onChange={(e) => setStripComments(e.target.checked)} />
          删掉 % 注释（arXiv 会公开源码，注释里的话别人都能看到）
        </label>
        <label>
          <input type="checkbox" checked={mergeInputs} onChange={(e) => setMergeInputs(e.target.checked)} />
          把 \input 的文件并进主文档
        </label>
      </div>
      <div className="sp-actions">
        <RunButton busy={busy === "check"} disabled={!!busy || running} onClick={() => go(false)} busyText="编译检查中…">
          检查
        </RunButton>
        <RunButton busy={busy === "export"} disabled={!!busy || running} onClick={() => go(true)} primary busyText="打包中…">
          检查并导出 arXiv 包…
        </RunButton>
      </div>
      {error && <ErrorBox msg={error} />}
      {report && (
        <>
          {report.zip && <SavedFile path={report.zip} label="已导出 arXiv 投稿包，到 arXiv 投稿页上传这个 .zip。" />}
          {!report.zip && report.blocked && (
            <div className="callout callout-danger">有红色问题，先修好再导出。</div>
          )}
          <div className="sp-meta">
            主文档 <code>{report.rootDocument}</code> · {report.files.length} 个文件 · {(report.totalBytes / 1e6).toFixed(2)} MB
          </div>
          <CheckList checks={report.checks} />
          <details className="sp-files">
            <summary>包里的文件</summary>
            <ul>
              {report.files.map((f) => (
                <li key={f.path}>
                  <code>{f.path}</code> <span className="sp-muted">{(f.bytes / 1024).toFixed(0)} KB</span>
                </li>
              ))}
            </ul>
          </details>
        </>
      )}
    </div>
  );
}

/* -------------------------------------------------------------------------- blind + diagnose */

function ChecksPanel({
  run,
  running,
  command,
  intro,
  button,
  extraFlag,
}: {
  run: RunRecord;
  running: boolean;
  command: "blind" | "diagnose" | "manuscript-audit";
  intro: ReactNode;
  button: string;
  extraFlag?: { flag: string; label: string };
}) {
  const [busy, setBusy] = useState(false);
  const [flagOn, setFlagOn] = useState(false);
  const [report, setReport] = useState<ChecksReport | null>(null);
  const [error, setError] = useState("");
  useRunReset(run, () => {
    setReport(null);
    setError("");
  });

  async function go() {
    setBusy(true);
    setError("");
    try {
      const args = [command, run.runDir];
      if (extraFlag && flagOn) args.push(extraFlag.flag);
      setReport(await runTool<ChecksReport>(args));
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="sp-panel">
      <Intro>{intro}</Intro>
      {extraFlag && (
        <div className="sp-options">
          <label>
            <input type="checkbox" checked={flagOn} onChange={(e) => setFlagOn(e.target.checked)} />
            {extraFlag.label}
          </label>
        </div>
      )}
      <div className="sp-actions">
        <RunButton busy={busy} disabled={running} onClick={go} primary>
          {button}
        </RunButton>
      </div>
      {error && <ErrorBox msg={error} />}
      {report && (
        <>
          <div className="sp-meta">
            主文档 <code>{report.rootDocument}</code>
            {report.summary ? ` · ${report.summary}` : ""}
            {report.compiledNow ? " · 运行目录里没有编译日志，刚在临时文件夹里编译了一遍" : ""}
          </div>
          <CheckList checks={report.checks} />
        </>
      )}
    </div>
  );
}

export function BlindPanel({ run, running }: { run: RunRecord; running: boolean }) {
  return (
    <ChecksPanel
      run={run}
      running={running}
      command="blind"
      button="开始检查"
      intro={
        <>
          按论文用的会议模板判断要不要匿名（NeurIPS、ICML、ICLR、CVPR 审稿版、AAAI、ACL 等），再查会暴露作者的地方：
          \author、\thanks、致谢、个人仓库链接、「我们之前的工作」、PDF 元数据；最后查字体是否嵌入、有没有 Type 3。
        </>
      }
    />
  );
}

export function DiagnosePanel({ run, running }: { run: RunRecord; running: boolean }) {
  return (
    <ChecksPanel
      run={run}
      running={running}
      command="diagnose"
      button="读取编译日志"
      extraFlag={{ flag: "--find-packages", label: "联网查缺的文件属于哪个 TeX 宏包（用 tlmgr，需要联网，稍慢）" }}
      intro={
        <>
          读最终论文的 LaTeX 编译日志，把错误、缺的宏包、未定义的引用和交叉引用、超出页边的行整理出来，常见错误附中文说明。
          流水线遇到错误也会继续编译，所以 PDF 能出来不代表没有错误。
        </>
      }
    />
  );
}

export function ManuscriptAuditPanel({ run, running }: { run: RunRecord; running: boolean }) {
  return (
    <ChecksPanel
      run={run}
      running={running}
      command="manuscript-audit"
      button="开始体检"
      intro={
        <>
          按 <code>Nature-Paper-Skills</code> 投稿审计标准离线检查成稿（不改写原文件）：统计正文与摘要字数（剥离注释与浮动体）、
          扫描 AI 写作痕迹与破折号/分号节奏、核对图编号与图注开头、排查残留草稿占位符、检查统计检验与误差棒要素，以及期刊必备声明。
        </>
      }
    />
  );
}

/* -------------------------------------------------------------------------- latexdiff */

export function DiffPanel({ run, running }: { run: RunRecord; running: boolean }) {
  const [versions, setVersions] = useState<VersionInfo[]>([]);
  const [oldPath, setOldPath] = useState("");
  const [newPath, setNewPath] = useState("");
  const [onlyChanged, setOnlyChanged] = useState(false);
  const [busy, setBusy] = useState(false);
  const [loadingVersions, setLoadingVersions] = useState(false);
  const [report, setReport] = useState<DiffReport | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    setReport(null);
    setError("");
    setLoadingVersions(true);
    runTool<{ versions: VersionInfo[] }>(["versions", run.runDir])
      .then((r) => {
        if (!alive) return;
        setVersions(r.versions);
        const compiled = r.versions.filter((v) => v.hasPdf);
        const pool = compiled.length >= 2 ? compiled : r.versions;
        setOldPath(pool[0]?.path ?? "");
        setNewPath(pool[pool.length - 1]?.path ?? "");
      })
      .catch((e) => alive && setError(errText(e)))
      .finally(() => alive && setLoadingVersions(false));
    return () => {
      alive = false;
    };
  }, [run.runDir, running]);

  const options = useMemo(() => {
    const extra = [oldPath, newPath].filter((p) => p && !versions.some((v) => v.path === p));
    return [
      ...versions.map((v) => ({ path: v.path, label: `${v.label}（${v.name}）` })),
      ...extra.map((p) => ({ path: p, label: `其他：${p}` })),
    ];
  }, [versions, oldPath, newPath]);

  async function pickOther(setter: (p: string) => void) {
    const p = await api.pickFile([{ name: "LaTeX 文档", extensions: ["tex"] }], run.runDir);
    if (p) setter(p);
  }

  async function go() {
    setError("");
    const out = await api.pickSaveFile(await defaultSavePath(`${runBaseName(run)}-changes.pdf`), [
      { name: "PDF", extensions: ["pdf"] },
    ]);
    if (!out) return;
    setBusy(true);
    try {
      const args = ["diff", oldPath, newPath, "--out", out];
      if (onlyChanged) args.push("--only-changed-pages");
      setReport(await runTool<DiffReport>(args));
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(false);
    }
  }

  const select = (value: string, set: (p: string) => void, label: string) => (
    <div className="sp-pick">
      <span className="sp-pick-label">{label}</span>
      <select value={value} onChange={(e) => set(e.target.value)} disabled={loadingVersions}>
        {options.map((o) => (
          <option key={o.path} value={o.path}>
            {o.label}
          </option>
        ))}
      </select>
      <button type="button" className="btn btn-sm btn-ghost" onClick={() => pickOther(set)}>
        选别的 .tex…
      </button>
    </div>
  );

  return (
    <div className="sp-panel">
      <Intro>
        用 latexdiff 生成一份标出改动的 PDF：新增的字是蓝色波浪线，删掉的是红色删除线。可以比较这次运行里的初稿和各轮润色，
        也可以选另一次运行的 <code>.tex</code>。适合写 rebuttal 或给合作者看改了什么。
      </Intro>
      {versions.length === 0 && !loadingVersions && !error && (
        <div className="av-sub-empty">这次运行还没有可比较的版本。</div>
      )}
      {options.length > 0 && (
        <>
          {select(oldPath, setOldPath, "旧版本")}
          {select(newPath, setNewPath, "新版本")}
          <div className="sp-options">
            <label>
              <input type="checkbox" checked={onlyChanged} onChange={(e) => setOnlyChanged(e.target.checked)} />
              只保留有改动的页
            </label>
          </div>
          <div className="sp-actions">
            <RunButton
              busy={busy}
              disabled={running || !oldPath || !newPath || oldPath === newPath}
              onClick={go}
              primary
              busyText="生成中（要编译两遍，约半分钟）…"
            >
              生成对比 PDF…
            </RunButton>
          </div>
        </>
      )}
      {error && <ErrorBox msg={error} />}
      {report && (
        <>
          <SavedFile path={report.pdf} label={`已生成对比 PDF${report.pages ? `（${report.pages} 页）` : ""}。`} />
          {report.simplified && (
            <div className="callout callout-warn">
              带完整标记的版本编译不过，这份用了简化标记：公式和引用的改动没有单独标出。
            </div>
          )}
        </>
      )}
    </div>
  );
}

/* -------------------------------------------------------------------------- online citation check */

const CITE_LABEL: Record<CiteStatus, string> = {
  not_found: "查不到",
  mismatch: "标题对不上",
  fix: "要改",
  error: "没查成",
  skipped: "跳过",
  verified: "已核实",
};

const CITE_STATE: Record<CiteStatus, CheckState> = {
  not_found: "fail",
  mismatch: "warn",
  fix: "warn",
  error: "warn",
  skipped: "warn",
  verified: "ok",
};

export function CiteOnlinePanel({ run, running }: { run: RunRecord; running: boolean }) {
  const [report, setReport] = useState<CiteReport | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState<"problems" | "all">("problems");

  useEffect(() => {
    let alive = true;
    setReport(null);
    setError("");
    api
      .readRunTextArtifact(run.runDir, ONLINE_FILE)
      .then((text) => {
        if (!alive) return;
        try {
          setReport(JSON.parse(text) as CiteReport);
        } catch {
          /* broken file: run the check again */
        }
      })
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [run.runDir]);

  async function go() {
    setBusy(true);
    setError("");
    try {
      setReport(await runTool<CiteReport>(["cite-online", run.runDir]));
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(false);
    }
  }

  const shown = (report?.entries ?? []).filter((e) => filter === "all" || e.status !== "verified");
  const c = report?.counts ?? {};
  const problems = (c.not_found ?? 0) + (c.mismatch ?? 0) + (c.fix ?? 0) + (c.error ?? 0);

  return (
    <div className="sp-panel">
      <Intro>
        把正文引用的每条文献拿到 Crossref、arXiv 和 Semantic Scholar 上核对标题、作者和 DOI，专门揪出 AI
        编出来的假文献。会把文献标题发给这三个公开数据库，不发送你的论文内容或密钥。几十条文献大约要一两分钟。
      </Intro>
      <div className="sp-actions">
        <RunButton busy={busy} disabled={running} onClick={go} primary busyText="联网核查中…">
          {report ? "重新核查" : "开始联网核查"}
        </RunButton>
        {report && (
          <span className="sp-muted">
            上次核查：{report.checkedAt.replace("T", " ").slice(0, 16)}
          </span>
        )}
      </div>
      {error && <ErrorBox msg={error} />}
      {report && (
        <>
          <div className="sp-stats">
            <span className="sp-stat is-ok">已核实 {c.verified ?? 0}</span>
            <span className="sp-stat is-warn">要改 {c.fix ?? 0}</span>
            <span className="sp-stat is-warn">标题对不上 {c.mismatch ?? 0}</span>
            <span className="sp-stat is-fail">查不到 {c.not_found ?? 0}</span>
            {(c.error ?? 0) > 0 && <span className="sp-stat is-warn">没查成 {c.error}</span>}
            <span className="sp-muted">共 {report.total} 条</span>
          </div>
          <div className="sp-options">
            <label>
              <input type="radio" checked={filter === "problems"} onChange={() => setFilter("problems")} />
              只看有问题的（{problems}）
            </label>
            <label>
              <input type="radio" checked={filter === "all"} onChange={() => setFilter("all")} />
              全部
            </label>
          </div>
          {shown.length === 0 ? (
            <div className="av-sub-empty">没有问题，所有文献都在数据库里找到了。</div>
          ) : (
            <ul className="sp-checks">
              {shown.map((e) => (
                <li key={e.key} className={`sp-check is-${CITE_STATE[e.status]}`}>
                  <div className="sp-cite">
                    <div className="sp-check-row">
                      <StateIcon state={CITE_STATE[e.status]} />
                      <span className="sp-check-label">{CITE_LABEL[e.status]}</span>
                      <code className="sp-cite-key">{e.key}</code>
                    </div>
                    <div className="sp-cite-title">{e.title || "（无标题）"}</div>
                    {e.found && e.status !== "verified" && (
                      <div className="sp-muted">
                        {e.found.source} 最接近的是：「{e.found.title}」{e.found.year ? `（${e.found.year}）` : ""}
                      </div>
                    )}
                    {[...e.notes, ...(e.suggestions ?? [])].map((n, i) => (
                      <div key={i} className="sp-cite-note">
                        {n}
                      </div>
                    ))}
                    {e.found?.url && (
                      <button type="button" className="btn btn-ghost btn-sm" onClick={() => openUrl(e.found!.url!)}>
                        <ExternalLink size={12} aria-hidden="true" /> 打开数据库页面
                      </button>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  );
}

/* -------------------------------------------------------------------------- Lean 4 audit */

export function LeanAuditPanel({ running }: { running: boolean }) {
  const [projectDir, setProjectDir] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [report, setReport] = useState<LeanAuditReport | null>(null);

  async function pickAndAudit() {
    const dir = await api.pickDirectory(projectDir || undefined);
    if (!dir) return;
    setProjectDir(dir);
    setBusy(true);
    setError("");
    try {
      const res = (await api.runSubmissionTool(["lean-audit", dir])) as LeanAuditReport;
      setReport(res);
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(false);
    }
  }

  async function rerun() {
    if (!projectDir) return;
    setBusy(true);
    setError("");
    try {
      const res = (await api.runSubmissionTool(["lean-audit", projectDir])) as LeanAuditReport;
      setReport(res);
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="sp-panel">
      <Intro>
        针对形式化数学/化学定理证明项目（如 Lean 4）：扫描全项目 <code>.lean</code> 源文件并运行 <code>lake build</code>
        ，严格统计 <code>sorry</code> / <code>admit</code> / 新增 <code>axiom</code> / <code>unsafe</code> 零容忍核验结果，防止“仅靠注释或公理凑过”。
      </Intro>
      <div className="sp-actions">
        <RunButton busy={busy} disabled={running} onClick={pickAndAudit} primary busyText="正在核验 Lean 4 工程…">
          <FolderOpen size={13} aria-hidden="true" /> 选择 Lean 4 项目目录并核验…
        </RunButton>
        {projectDir && (
          <RunButton busy={busy} disabled={running} onClick={rerun} busyText="重新核验中…">
            重新检查
          </RunButton>
        )}
      </div>
      {projectDir && <div className="sp-muted st-mono">目标目录：{projectDir}</div>}
      {error && <ErrorBox msg={error} />}
      {report && (
        <>
          <div className="sp-stats">
            <span className="sp-stat is-ok">Lean 文件 {report.leanFiles} 个 / {report.totalLines} 行</span>
            <span className={`sp-stat ${report.zeroHoles ? "is-ok" : "is-fail"}`}>
              sorry={report.sorryCount} · admit={report.admitCount} · axiom={report.axiomCount}
            </span>
            <span className={`sp-stat ${report.buildOk ? "is-ok" : report.buildRan ? "is-fail" : "is-warn"}`}>
              {report.buildRan ? (report.buildOk ? "lake build 编译通过" : "lake build 失败") : "未运行 lake build"}
            </span>
          </div>
          {report.issues.length > 0 && (
            <ul className="sp-checks">
              {report.issues.map((iss, idx) => (
                <li key={idx} className="sp-check is-fail">
                  <div className="sp-check-row">
                    <StateIcon state="fail" />
                    <span className="sp-check-label">
                      [{iss.kind}] {iss.file}:{iss.line}
                    </span>
                  </div>
                  <div className="sp-check-detail st-mono">{iss.excerpt}</div>
                </li>
              ))}
            </ul>
          )}
          {report.buildTail && (
            <details className="le-log" open={!report.buildOk}>
              <summary>lake build 输出尾部</summary>
              <pre>{report.buildTail}</pre>
            </details>
          )}
        </>
      )}
    </div>
  );
}
