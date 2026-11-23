// 「导入填写单」: pick a PDF / Markdown / Word form, see what each field would
// become, and fill the chosen ones into the project. Nothing changes until the
// user confirms; fields that already hold the same value are left out.

import { useState } from "react";
import { FileUp, X } from "lucide-react";
import { api } from "../api";
import { matchTemplate, templateLabel, type IntakeFormResult, type Project } from "../types";

interface IntakeImportProps {
  project: Project;
  templates: string[];
  onApply: (patch: Partial<Project>) => void;
  onToast: (msg: string, isError?: boolean) => void;
}

interface Row {
  key: string;
  label: string;
  current: string;
  next: string;
  patch: Partial<Project>;
  /** Long text: shown as a preview with its length. */
  long?: boolean;
  /** Cannot be applied (e.g. an unknown template); the reason. */
  problem?: string;
}

const FILTERS = [{ name: "填写单", extensions: ["pdf", "md", "markdown", "txt", "docx"] }];

function preview(text: string, max = 280): string {
  const t = text.trim();
  return t.length > max ? `${t.slice(0, max)}…` : t;
}

function buildRows(result: IntakeFormResult, project: Project, templates: string[]): Row[] {
  const f = result.fields;
  const rows: Row[] = [];
  if (f.title !== undefined)
    rows.push({ key: "title", label: "项目标题", current: project.title, next: f.title, patch: { title: f.title } });
  if (f.template !== undefined) {
    const id = matchTemplate(f.template, templates);
    rows.push({
      key: "template",
      label: "LaTeX 模板",
      current: templateLabel(project.templateName),
      next: id ? templateLabel(id) : f.template,
      patch: id ? { templateName: id } : {},
      problem: id ? undefined : "没有对应的模板，请在下拉框里手动选",
    });
  }
  // 文献截止日期 is no longer imported: every run uses the month it starts in.
  if (f.usePlotting !== undefined)
    rows.push({
      key: "plotting",
      label: "自动绘图 Agent",
      current: project.usePlotting ? "启用" : "关闭",
      next: f.usePlotting ? "启用" : "关闭",
      patch: { usePlotting: f.usePlotting },
    });
  if (f.plottingMaxCriticRounds !== undefined)
    rows.push({
      key: "rounds",
      label: "绘图最大自修轮次",
      current: String(project.plottingMaxCriticRounds),
      next: String(f.plottingMaxCriticRounds),
      patch: { plottingMaxCriticRounds: f.plottingMaxCriticRounds },
    });
  if (f.ideaMarkdown !== undefined)
    rows.push({
      key: "idea",
      label: "研究思路",
      current: project.ideaMarkdown,
      next: f.ideaMarkdown,
      patch: { ideaMarkdown: f.ideaMarkdown },
      long: true,
    });
  if (f.experimentalLogMarkdown !== undefined)
    rows.push({
      key: "log",
      label: "实验记录",
      current: project.experimentalLogMarkdown,
      next: f.experimentalLogMarkdown,
      patch: { experimentalLogMarkdown: f.experimentalLogMarkdown },
      long: true,
    });
  return rows;
}

export function IntakeImport({ project, templates, onApply, onToast }: IntakeImportProps) {
  const [busy, setBusy] = useState(false);
  const [fileName, setFileName] = useState("");
  const [result, setResult] = useState<IntakeFormResult | null>(null);
  const [chosen, setChosen] = useState<Record<string, boolean>>({});

  const rows = result ? buildRows(result, project, templates) : [];
  const same = (r: Row) => r.current.trim() === r.next.trim();

  async function pick() {
    const path = await api.pickFile(FILTERS);
    if (!path) return;
    setBusy(true);
    try {
      const res = await api.parseIntakeForm(path);
      const built = buildRows(res, project, templates);
      setFileName(path.split(/[\\/]/).pop() ?? path);
      setChosen(Object.fromEntries(built.map((r) => [r.key, !r.problem && !same(r)])));
      setResult(res);
    } catch (e) {
      onToast(`导入失败：${e}`, true);
    } finally {
      setBusy(false);
    }
  }

  function apply() {
    const patch: Partial<Project> = {};
    for (const r of rows) if (chosen[r.key] && !r.problem) Object.assign(patch, r.patch);
    const n = rows.filter((r) => chosen[r.key] && !r.problem).length;
    if (n > 0) {
      onApply(patch);
      onToast(`已从「${fileName}」填入 ${n} 项`);
    }
    setResult(null);
  }

  const selectable = rows.filter((r) => !r.problem);
  const count = selectable.filter((r) => chosen[r.key]).length;

  return (
    <>
      <button type="button" className="btn btn-sm" onClick={pick} disabled={busy} title="从 PDF、Markdown 或 Word 填写单里读出各栏，先预览再填入">
        <FileUp size={13} /> {busy ? "正在读取…" : "导入填写单"}
      </button>

      {result && (
        <div className="intake-backdrop" role="presentation" onClick={() => setResult(null)}>
          <div
            className="intake-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="intake-title"
            onClick={(e) => e.stopPropagation()}
            onKeyDown={(e) => e.key === "Escape" && setResult(null)}
          >
            <div className="intake-head">
              <div>
                <h3 id="intake-title">导入填写单</h3>
                <p className="intake-file">{fileName}</p>
              </div>
              <button type="button" className="btn btn-sm btn-icon" aria-label="关闭" onClick={() => setResult(null)}>
                <X size={14} />
              </button>
            </div>

            <p className="hint">勾选的栏位会被覆盖成右边的新内容；内容一样的默认不勾。填入后仍可在工作台里修改。</p>

            {result.warnings.length > 0 && (
              <ul className="intake-warnings">
                {result.warnings.map((w) => (
                  <li key={w}>{w}</li>
                ))}
              </ul>
            )}

            <div className="intake-rows">
              {rows.map((r) => (
                <label key={r.key} className={`intake-row${r.problem ? " is-disabled" : ""}`}>
                  <input
                    type="checkbox"
                    checked={!!chosen[r.key] && !r.problem}
                    disabled={!!r.problem}
                    onChange={(e) => setChosen((prev) => ({ ...prev, [r.key]: e.target.checked }))}
                  />
                  <span className="intake-label">
                    {r.label}
                    {same(r) && <span className="badge badge-neutral">相同</span>}
                  </span>
                  <span className="intake-values">
                    {r.long ? (
                      <>
                        <span className="intake-meta">
                          现在 {r.current.trim().length.toLocaleString()} 字符 → 导入 {r.next.trim().length.toLocaleString()} 字符
                        </span>
                        <pre className="intake-preview">{preview(r.next)}</pre>
                      </>
                    ) : (
                      <span className="intake-short">
                        <span className="intake-old">{r.current || "（空）"}</span>
                        <span aria-hidden="true"> → </span>
                        <span className="intake-new">{r.next}</span>
                      </span>
                    )}
                    {r.problem && <span className="intake-problem">{r.problem}</span>}
                  </span>
                </label>
              ))}
            </div>

            <div className="intake-actions">
              <button type="button" className="btn btn-sm" onClick={() => setResult(null)}>
                取消
              </button>
              <button type="button" className="btn btn-sm btn-primary" onClick={apply} disabled={count === 0}>
                填入所选（{count} 项）
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
