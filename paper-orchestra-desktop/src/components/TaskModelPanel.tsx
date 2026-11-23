// "按任务指定模型" on the workbench: every PO_TASK_MODEL_<TASK> value (all the
// steps that call a model, the image model and the literature web search), the
// same settings as the environment page, edited in place and saved straight to
// the repo's .env. Each field shows what that step really runs with.

import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import { PIPELINE_TASKS, taskModelEnv } from "../providerCatalog";
import {
  GEMINI_SEARCH_MODEL,
  ROLE_LABEL,
  SCHOLAR_SEARCH,
  searchPlatform,
  ROUTE_SOURCE_LABEL,
  isAutoModelRef,
  resolveRouting,
  taskRole,
  providerKeys,
  type RouteStep,
} from "../autoRouting";
import type { EnvironmentStatus, Project } from "../types";

interface TaskModelPanelProps {
  project: Project;
  envStatus: EnvironmentStatus | null;
  /** Datalist id with model suggestions (shared with the workbench model fields). */
  listId: string;
  /** Datalist id with image model suggestions. */
  imageListId: string;
  /** Called after .env was written, so the app re-reads the environment. */
  onSaved: () => void | Promise<void>;
  onToast: (msg: string, isError?: boolean) => void;
}

/** Tasks the paper pipeline never runs (only the evaluation scripts use them). */
const HELPER_ONLY: Record<string, string> = { citation_parse: "写论文用不到，仅评测脚本使用" };

const SEARCH_LIST_ID = "ws-search-refs";
const SEARCH_SUGGESTIONS = [GEMINI_SEARCH_MODEL, "gemini-3.8-flash", "deepseek-flash", "off"];

const LABEL: Record<string, string> = Object.fromEntries(PIPELINE_TASKS.map((t) => [t.id, t.label]));

export function TaskModelPanel({ project, envStatus, listId, imageListId, onSaved, onToast }: TaskModelPanelProps) {
  /** PO_TASK_MODEL_* exactly as written in .env (null until loaded). */
  const [saved, setSaved] = useState<Record<string, string> | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [savingTask, setSavingTask] = useState<string | null>(null);
  const [loadError, setLoadError] = useState("");
  const savingRef = useRef(false);
  const savedRef = useRef<Record<string, string> | null>(null);

  async function load() {
    try {
      const cfg = await api.getEnvConfig();
      const out: Record<string, string> = {};
      for (const t of PIPELINE_TASKS) out[t.id] = (cfg.extraEnv[taskModelEnv(t.id)] ?? "").trim();
      const before = savedRef.current;
      savedRef.current = out;
      setSaved(out);
      // Keep a field the user is still typing in; refresh every untouched one.
      setDrafts((prev) => {
        const next: Record<string, string> = {};
        for (const t of PIPELINE_TASKS) {
          const dirty = before !== null && prev[t.id] !== undefined && prev[t.id] !== before[t.id];
          next[t.id] = dirty ? prev[t.id] : out[t.id];
        }
        return next;
      });
      setLoadError("");
    } catch (e) {
      setLoadError(String(e));
    }
  }

  // Re-read whenever the environment check changes (e.g. after saving on the environment page).
  const envKey = JSON.stringify(envStatus?.taskModels ?? {});
  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [envKey]);

  async function commit(task: string) {
    if (!saved || savingRef.current) return;
    const value = (drafts[task] ?? "").trim();
    if (value === (saved[task] ?? "")) return;
    savingRef.current = true;
    setSavingTask(task);
    const key = taskModelEnv(task);
    try {
      // Fresh read so nothing else in .env (keys, other settings) is overwritten.
      const cfg = await api.getEnvConfig();
      const extraEnv = { ...cfg.extraEnv };
      if (value) extraEnv[key] = value;
      else if (key in extraEnv) extraEnv[key] = ""; // stays in .env as `KEY=`, like the environment page
      await api.saveEnvConfig({ ...cfg, extraEnv });
      savedRef.current = { ...(savedRef.current ?? {}), [task]: value };
      setSaved(savedRef.current);
      setDrafts((prev) => ({ ...prev, [task]: value }));
      await onSaved();
    } catch (e) {
      onToast(`保存「${PIPELINE_TASKS.find((t) => t.id === task)?.label ?? task}」失败：${e}`, true);
      setDrafts((prev) => ({ ...prev, [task]: saved[task] ?? "" }));
    } finally {
      savingRef.current = false;
      setSavingTask(null);
    }
  }

  const routingInput = {
    writerModel: project.writerModel,
    reflectionModel: project.reflectionModel,
    plottingModel: project.plottingModel,
    imageModel: project.imageModel,
    usePlotting: true, // show the plotting tasks even while the agent is off
    autoModel: (envStatus?.autoModel ?? "").trim(),
    keys: providerKeys(envStatus),
    autoFallback: (envStatus?.autoFallbackModel ?? "").trim(),
    anyProviderConfigured: (envStatus?.configuredProviders ?? []).length > 0,
  };

  /** What each step runs with, using the drafts as typed. */
  function mergedTaskModels(): Record<string, string> {
    const merged: Record<string, string> = { ...(envStatus?.taskModels ?? {}) };
    for (const t of PIPELINE_TASKS) {
      const v = (drafts[t.id] ?? "").trim();
      if (v) merged[t.id] = v;
      else if (saved) delete merged[t.id];
    }
    return merged;
  }

  const effective = useMemo(() => {
    const merged = mergedTaskModels();
    const map: Record<string, RouteStep> = {};
    for (const s of resolveRouting({ ...routingInput, taskModels: merged })) map[s.task] = s;
    return map;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(drafts), JSON.stringify(routingInput), envKey, saved]);

  /** What each step falls back to when its own field is left empty (the others as typed). */
  const fallback = useMemo(() => {
    const merged = mergedTaskModels();
    const map: Record<string, RouteStep> = {};
    for (const t of PIPELINE_TASKS) {
      const others = { ...merged };
      delete others[t.id];
      const step = resolveRouting({ ...routingInput, taskModels: others }).find((s) => s.task === t.id);
      if (step) map[t.id] = step;
    }
    return map;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(drafts), JSON.stringify(routingInput), envKey, saved]);

  const filled = PIPELINE_TASKS.filter((t) => (drafts[t.id] ?? "").trim()).length;
  const autoModel = routingInput.autoModel;
  // With web search on, the search-query model is not called, and Gemini results skip the relevance screen.
  const search = effective["literature_search"];
  const webSearch = !!search && !search.model.startsWith(SCHOLAR_SEARCH);
  const geminiSearch = webSearch && searchPlatform(search.model) === "gemini";

  return (
    <div className="ws-route" role="group" aria-labelledby="ws-task-models-title">
      <div className="ws-route-head">
        <span className="ws-route-title" id="ws-task-models-title">
          按任务指定模型
        </span>
        <span className="ws-route-note">
          共 {PIPELINE_TASKS.length} 步 · {filled > 0 ? `已指定 ${filled} 步` : "都没指定"}
          {autoModel ? ` · 留空的用统一 auto 模型 ${autoModel}` : " · 留空的按 Key 自动分工"}
        </span>
      </div>

      {loadError ? (
        <div className="ws-route-foot">读不到 .env：{loadError}</div>
      ) : (
        <div className="ws-task-grid">
          {PIPELINE_TASKS.map((t) => {
            const role = taskRole(t.id);
            const eff = effective[t.id];
            const fb = fallback[t.id];
            const notCalled =
              HELPER_ONLY[t.id] ??
              (t.id === "literature_query" && webSearch ? "联网检索时用不到" : undefined) ??
              (t.id === "relevance_screen" && geminiSearch ? "Gemini 联网检索时跳过" : undefined);
            // Same condition as the workbench's 「清除」 notice (plotting models only count while plotting is on).
            const legacy =
              t.id === "image"
                ? project.usePlotting && !isAutoModelRef(project.imageModel)
                : !!role &&
                  (role !== "plotting" || project.usePlotting) &&
                  !isAutoModelRef(routingInputRoleModel(project, role));
            const legacyName = t.id === "image" ? "图像模型" : role ? `${ROLE_LABEL[role]} 模型` : "";
            // Empty field: show what the step falls back to (its tag says where from).
            const placeholder = fb
              ? fb.source === "inherit" && fb.parent
                ? `同「${LABEL[fb.parent] ?? fb.parent}」：${fb.model}`
                : fb.model
              : "auto";
            const list = t.id === "literature_search" ? SEARCH_LIST_ID : t.id === "image" ? imageListId : listId;
            return (
              <div key={t.id} className="field ws-task-field">
                <label htmlFor={`ws-task-${t.id}`} className="ws-task-label" title={t.hint}>
                  <span>{t.label}</span>
                  {notCalled ? (
                    <span className="ws-route-src is-pending" title={t.id === "literature_query" || t.id === "relevance_screen" ? "联网检索时直接由搜索模型找论文" : t.hint}>
                      {HELPER_ONLY[t.id] ? "不参与写论文" : notCalled}
                    </span>
                  ) : legacy ? (
                    <span className="ws-route-src is-workbench" title={`这个项目还留着旧版的${legacyName}设置，这一项暂时不生效；点上方的「清除」即可`}>
                      被旧版{legacyName}覆盖
                    </span>
                  ) : eff ? (
                    <span className={`ws-route-src is-${eff.source}`} title={eff.source === "fallback" ? eff.model : undefined}>
                      {eff.source === "inherit" && eff.parent
                        ? `跟随${LABEL[eff.parent] ?? eff.parent}`
                        : ROUTE_SOURCE_LABEL[eff.source]}
                    </span>
                  ) : null}
                </label>
                <input
                  id={`ws-task-${t.id}`}
                  className="input ws-model-input"
                  list={list}
                  value={drafts[t.id] ?? ""}
                  placeholder={HELPER_ONLY[t.id] ?? placeholder}
                  title={eff ? `${t.hint}\n实际使用：${eff.model}` : t.hint}
                  spellCheck={false}
                  autoComplete="off"
                  disabled={saved === null || savingTask === t.id}
                  onChange={(e) => setDrafts((prev) => ({ ...prev, [t.id]: e.target.value }))}
                  onBlur={() => commit(t.id)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") (e.target as HTMLInputElement).blur();
                    if (e.key === "Escape") {
                      setDrafts((prev) => ({ ...prev, [t.id]: saved?.[t.id] ?? "" }));
                    }
                  }}
                />
              </div>
            );
          })}
        </div>
      )}

      <datalist id={SEARCH_LIST_ID}>
        {SEARCH_SUGGESTIONS.map((m) => (
          <option key={m} value={m} />
        ))}
      </datalist>

      <div className="ws-route-foot">
        和「环境与密钥」页的「按任务指定模型」是同一份设置，改完离开输入框就保存到 .env，对所有项目生效。留空的步骤：标「跟随」的用它所属那一步的模型，其余用统一 auto 模型，统一模型也没填才按已配置的 Key 自动分工。鼠标停在名字上能看到每一步做什么。
      </div>
    </div>
  );
}

function routingInputRoleModel(project: Project, role: "writer" | "reflection" | "plotting"): string {
  if (role === "writer") return project.writerModel;
  if (role === "reflection") return project.reflectionModel;
  return project.plottingModel;
}
