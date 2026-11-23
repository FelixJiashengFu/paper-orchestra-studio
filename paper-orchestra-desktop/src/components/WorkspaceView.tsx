import { FileImage, ImagePlus, Trash2, TriangleAlert } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { api, base64ToBlobUrl, guessImageMime } from "../api";
import {
  OFFICIAL_PROVIDERS,
  PRESET_PROVIDERS,
  canonicalProviderId,
  isKnownTextOnlyRef,
} from "../providerCatalog";
import { fetchedModelRefs, useFetchedModels } from "../modelCatalog";
import { isAutoModelRef, providerKeys, resolveRouting } from "../autoRouting";
import {
  IMAGE_MODEL_PRESETS,
  MODEL_PRESETS,
  isJournalTemplate,
  templateLabel,
  type EnvironmentStatus,
  type Project,
} from "../types";
import { TaskModelPanel } from "./TaskModelPanel";
import { IntakeImport } from "./IntakeImport";
import "./settings.css";

interface WorkspaceViewProps {
  project: Project;
  templates: string[];
  envStatus: EnvironmentStatus | null;
  onChange: (patch: Partial<Project>) => void;
  onAddFigure: (sourcePath: string, caption: string) => Promise<void>;
  onUpdateFigureCaption: (figureId: string, caption: string) => Promise<void>;
  onDeleteFigure: (figureId: string) => Promise<void>;
  /** Re-run the environment check (after the task models were saved to .env). */
  onEnvUpdated: () => void | Promise<void>;
  onToast: (msg: string, isError?: boolean) => void;
}

const lower = (s: string) => s.trim().toLowerCase();

/** The current month (YYYY-MM): a run started now takes literature up to and including it. */
function thisMonth(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

export function WorkspaceView({
  project,
  templates,
  envStatus,
  onChange,
  onAddFigure,
  onUpdateFigureCaption,
  onDeleteFigure,
  onEnvUpdated,
  onToast,
}: WorkspaceViewProps) {
  const [previews, setPreviews] = useState<Record<string, string>>({});
  const [newCaption, setNewCaption] = useState("");
  const [addingFig, setAddingFig] = useState(false);

  const templateList = templates.length > 0 ? templates : ["cvpr2025", "iclr2025"];

  useEffect(() => {
    let cancelled = false;
    const createdUrls: string[] = [];

    async function loadThumbs() {
      const next: Record<string, string> = {};
      for (const fig of project.figures) {
        const lowerName = fig.fileName.toLowerCase();
        if (lowerName.endsWith(".pdf")) continue;
        try {
          const b64 = await api.readFileBase64(fig.sourcePath);
          if (cancelled) return;
          const url = base64ToBlobUrl(b64, guessImageMime(fig.fileName));
          createdUrls.push(url);
          next[fig.id] = url;
        } catch {
          // ignore missing local file preview
        }
      }
      if (!cancelled) {
        setPreviews(next);
      }
    }

    loadThumbs();
    return () => {
      cancelled = true;
      createdUrls.forEach((u) => URL.revokeObjectURL(u));
    };
  }, [project.figures]);

  async function handlePickFigure() {
    const file = await api.pickFile([
      { name: "Figures", extensions: ["png", "jpg", "jpeg", "pdf"] },
    ]);
    if (!file) return;
    setAddingFig(true);
    try {
      const caption = newCaption.trim() || "Figure attached by user.";
      await onAddFigure(file, caption);
      setNewCaption("");
    } finally {
      setAddingFig(false);
    }
  }

  // ---- provider data from the last environment check ----------------------

  const configuredProviders = envStatus?.configuredProviders ?? [];
  const customProviders = envStatus?.customProviders ?? [];
  const autoModel = (envStatus?.autoModel ?? "").trim();

  const isCustom = (name: string) => customProviders.some((c) => lower(c) === lower(name));

  const fetchedModels = useFetchedModels();

  /** Datalist: auto, refs for configured providers, the static presets, then what the APIs listed. */
  const modelOptions = useMemo(() => {
    const out: string[] = [];
    const add = (v: string) => {
      if (v && !out.includes(v)) out.push(v);
    };
    add("auto");
    for (const name of configuredProviders) {
      if (isCustom(name)) {
        add(`${name}:`);
        continue;
      }
      const id = canonicalProviderId(name);
      const preset = PRESET_PROVIDERS.find((p) => p.id === id);
      if (preset) {
        add(preset.examplePlaceholder ? `${preset.id}:` : preset.exampleRef);
        continue;
      }
      const official = OFFICIAL_PROVIDERS.find((p) => p.id === id);
      if (official) add(`${official.id}:${official.exampleRef}`);
    }
    for (const name of customProviders) add(`${name}:`);
    MODEL_PRESETS.forEach(add);
    fetchedModelRefs(fetchedModels).forEach(add);
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [configuredProviders.join("|"), customProviders.join("|"), fetchedModels]);

  /** Image models: the presets, then fetched Gemini image models. */
  const imageModelOptions = useMemo(() => {
    const out: string[] = [...IMAGE_MODEL_PRESETS];
    for (const ref of fetchedModelRefs(fetchedModels)) {
      if (/image|imagen|banana/i.test(ref) && !out.includes(ref)) out.push(ref);
    }
    return out;
  }, [fetchedModels]);

  /** What auto uses without a legacy key (`_non_legacy_default`), computed by the backend. */
  const autoFallback = (envStatus?.autoFallbackModel ?? "").trim();
  const taskModels = envStatus?.taskModels ?? {};

  /** Every step the next run executes, with the model it really uses (same rules as the backend). */
  const routeSteps = resolveRouting({
    writerModel: project.writerModel,
    reflectionModel: project.reflectionModel,
    plottingModel: project.plottingModel,
    imageModel: project.imageModel,
    usePlotting: project.usePlotting,
    autoModel,
    taskModels,
    keys: providerKeys(envStatus),
    autoFallback,
    anyProviderConfigured: configuredProviders.length > 0,
  });
  /**
   * Writer / Reflection / Plotting / image models saved by earlier versions. The
   * workbench no longer shows these fields (the per-step models replace them),
   * but an explicit value still wins at run time, so it is shown with a way to clear it.
   */
  const legacyRoleModels = (
    [
      { role: "writer", label: "Writer（大纲、文献、正文）", model: project.writerModel },
      { role: "reflection", label: "Reflection（评审、润色、版式）", model: project.reflectionModel },
      { role: "plotting", label: "Plotting（绘图）", model: project.usePlotting ? project.plottingModel : "" },
      { role: "image", label: "图像模型", model: project.usePlotting ? project.imageModel : "" },
    ] as const
  ).filter((r) => !isAutoModelRef(r.model));
  /** A platform is configured but none has a default model: auto cannot start. */
  const needsDefaultModel =
    routeSteps.some((s) => s.source === "pending") && configuredProviders.length > 0;
  const providersJsonError = envStatus?.providersJsonError ?? "";

  // ---- text-only warning ----------------------------------------------------

  const textOnlyModels = useMemo(() => {
    const refs: string[] = [project.writerModel, project.reflectionModel];
    if (project.usePlotting) refs.push(project.plottingModel);
    const out: string[] = [];
    for (const ref of refs) {
      const r = ref.trim();
      if (r && isKnownTextOnlyRef(r, customProviders) && !out.includes(r)) out.push(r);
    }
    for (const step of routeSteps) {
      if (step.needsVision && step.source !== "pending" && isKnownTextOnlyRef(step.model, customProviders)) {
        if (!out.includes(step.model)) out.push(step.model);
      }
    }
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    project.writerModel,
    project.reflectionModel,
    project.plottingModel,
    project.usePlotting,
    routeSteps.map((i) => `${i.task}=${i.model}`).join("|"),
    customProviders.join("|"),
  ]);

  return (
    <div className="ws-root">
      <div className="card">
        <div className="card-title">
          <span>流水线参数</span>
          <div className="ws-card-actions">
            <IntakeImport project={project} templates={templateList} onApply={onChange} onToast={onToast} />
            <span className="ws-meta">自动保存至本地 SQLite</span>
          </div>
        </div>

        <div className="ws-group">
          <h4 className="ws-group-title">模板与范围</h4>
          <div className="config-grid">
            <div className="field">
              <label htmlFor="ws-template">LaTeX 模板（会议 / 期刊）</label>
              <select
                id="ws-template"
                className="select"
                value={project.templateName}
                onChange={(e) => onChange({ templateName: e.target.value })}
              >
                {[
                  { label: "会议模板", items: templateList.filter((t) => !isJournalTemplate(t)) },
                  { label: "期刊模板", items: templateList.filter(isJournalTemplate) },
                ]
                  .filter((g) => g.items.length > 0)
                  .map((g) => (
                    <optgroup key={g.label} label={g.label}>
                      {g.items.map((t) => (
                        <option key={t} value={t} title={t}>
                          {templateLabel(t)}
                        </option>
                      ))}
                    </optgroup>
                  ))}
              </select>
              {isJournalTemplate(project.templateName) && (
                <p className="hint">
                  期刊模式：没有单独的 Related Work 节（文献综述写进引言），按期刊的章节、摘要字数和
                  TOC 图要求写作，审稿按该期刊审稿人的标准。
                </p>
              )}
            </div>

            <div className="field">
              <label htmlFor="ws-cutoff">文献截止日期（自动）</label>
              <input
                id="ws-cutoff"
                className="input st-tnum"
                value={thisMonth()}
                disabled
                readOnly
              />
              <p className="hint">每次开始运行时自动更新，收录到当月为止发表的文献（含本月），不用手动填。</p>
            </div>
          </div>
        </div>

        <div className="ws-group">
          <h4 className="ws-group-title">模型</h4>
          {legacyRoleModels.length > 0 && (
            <div className="ws-notice" role="note">
              <TriangleAlert size={14} />
              <span>
                这个项目还留着旧版的整体模型设置：
                {legacyRoleModels.map((r, i) => (
                  <span key={r.role}>
                    {i > 0 && "、"}
                    {r.label} = <code>{r.model}</code>
                  </span>
                ))}
                。运行时它们会盖过下面对应的任务。{" "}
                <button
                  type="button"
                  className="btn btn-sm"
                  onClick={() =>
                    onChange({ writerModel: "auto", reflectionModel: "auto", plottingModel: "auto", imageModel: "auto" })
                  }
                >
                  清除，只用下面的分步设置
                </button>
              </span>
            </div>
          )}
          <TaskModelPanel
            project={project}
            envStatus={envStatus}
            listId="ws-model-refs"
            imageListId="ws-image-model-refs"
            onSaved={onEnvUpdated}
            onToast={onToast}
          />
          <p className="hint ws-syntax">
            写法：<code>平台:模型名</code> 指定平台，如 <code>qwen:qwen-max</code>、
            <code>ollama:qwen2.5:14b</code>；不带前缀时按名称自动识别（claude-* → Anthropic，gpt-* → OpenAI，deepseek-* → DeepSeek，gemini-* → Gemini）。
          </p>
        </div>

        <div className="ws-group">
          <h4 className="ws-group-title">绘图</h4>
          <div className="config-grid">
            <div className="field">
              <label htmlFor="ws-plotting-toggle">自动绘图 Agent</label>
              <select
                id="ws-plotting-toggle"
                className="select"
                value={project.usePlotting ? "yes" : "no"}
                onChange={(e) => onChange({ usePlotting: e.target.value === "yes" })}
              >
                <option value="yes">启用 Plotting Agent</option>
                <option value="no">关闭（仅使用下方附件图片）</option>
              </select>
            </div>

            {project.usePlotting && (
              <>
                <div className="field">
                  <label htmlFor="ws-rounds">绘图最大自修轮次</label>
                  <input
                    id="ws-rounds"
                    className="input st-tnum"
                    type="number"
                    min={1}
                    max={10}
                    value={project.plottingMaxCriticRounds}
                    onChange={(e) =>
                      onChange({
                        plottingMaxCriticRounds: Math.max(
                          1,
                          Math.min(10, Number(e.target.value) || 3)
                        ),
                      })
                    }
                  />
                </div>
              </>
            )}
          </div>
        </div>

        <datalist id="ws-model-refs">
          {modelOptions.map((m) => (
            <option key={m} value={m} />
          ))}
        </datalist>
        <datalist id="ws-image-model-refs">
          {imageModelOptions.map((m) => (
            <option key={m} value={m} />
          ))}
        </datalist>

        {needsDefaultModel && (
          <div className="ws-notice" role="note">
            <TriangleAlert size={14} />
            <span>已配置的平台没有内置默认模型：请在「环境与密钥」中为它填写默认模型，或设置统一 auto 模型。</span>
          </div>
        )}

        {providersJsonError && (
          <div className="ws-notice is-danger" role="alert">
            <TriangleAlert size={14} />
            <span>
              providers.json 有误，运行会失败：{providersJsonError}。请到「环境与密钥」修正。
            </span>
          </div>
        )}

        {textOnlyModels.length > 0 && (
          <div className="ws-notice" role="note">
            <TriangleAlert size={14} />
            <span>
              {textOnlyModels.map((m, i) => (
                <span key={m}>
                  {i > 0 && "、"}
                  <code>{m}</code>
                </span>
              ))}{" "}
              是纯文本模型，运行时会自动省略图片附件并跳过 VLM 版式截图评审。需要完整的视觉评审时，可为相应任务改用支持图片的模型，或在设置中配置 Claude、OpenAI、Gemini 等密钥后使用 <code>auto</code>。
            </span>
          </div>
        )}
      </div>

      <div className="editors-split">
        <div className="card ws-editor-card">
          <div className="card-title">
            <span className="ws-editor-title">
              <span>研究思路</span>
              <span className="ws-editor-file">idea_sparse.md</span>
            </span>
            <span className="ws-meta">{project.ideaMarkdown.length.toLocaleString()} 字符</span>
          </div>
          <textarea
            className="markdown-editor"
            aria-label="研究思路"
            placeholder="输入研究问题、核心假设、方法设计与预期贡献（支持 Markdown）…"
            value={project.ideaMarkdown}
            onChange={(e) => onChange({ ideaMarkdown: e.target.value })}
          />
        </div>

        <div className="card ws-editor-card">
          <div className="card-title">
            <span className="ws-editor-title">
              <span>实验记录</span>
              <span className="ws-editor-file">experimental_log.md</span>
            </span>
            <span className="ws-meta">
              {project.experimentalLogMarkdown.length.toLocaleString()} 字符
            </span>
          </div>
          <textarea
            className="markdown-editor"
            aria-label="实验记录"
            placeholder="粘贴实验设置、数据集划分、消融与主实验表格数据、定性观察等…"
            value={project.experimentalLogMarkdown}
            onChange={(e) => onChange({ experimentalLogMarkdown: e.target.value })}
          />
        </div>
      </div>

      <div className="card">
        <div className="card-title">
          <span>
            图表附件 <span className="ws-meta">{project.figures.length}</span>
          </span>
          <div className="ws-fig-actions">
            <input
              className="input"
              placeholder="可选：先填写图注 Caption"
              aria-label="新图片的图注"
              value={newCaption}
              onChange={(e) => setNewCaption(e.target.value)}
            />
            <button
              type="button"
              className="btn btn-sm"
              disabled={addingFig}
              onClick={handlePickFigure}
            >
              <ImagePlus size={14} />
              {addingFig ? "添加中…" : "添加图片 / PDF"}
            </button>
          </div>
        </div>

        {project.figures.length === 0 ? (
          <div className="ws-fig-empty">
            <p className="hint">
              暂无附件图片。若关闭 Plotting Agent，建议至少添加一张架构图或结果图。
            </p>
          </div>
        ) : (
          <div className="figures-grid">
            {project.figures.map((fig) => (
              <div key={fig.id} className="figure-card">
                <div className="figure-preview">
                  {previews[fig.id] ? (
                    <img src={previews[fig.id]} alt={fig.caption || fig.fileName} />
                  ) : (
                    <div className="ws-fig-placeholder">
                      <FileImage size={24} strokeWidth={1.5} />
                      <span>{fig.fileName}</span>
                    </div>
                  )}
                </div>
                <div className="ws-fig-head">
                  <span className="ws-fig-name" title={fig.sourcePath}>
                    {fig.fileName}
                  </span>
                  <button
                    type="button"
                    className="btn btn-sm ws-fig-delete"
                    aria-label={`删除 ${fig.fileName}`}
                    title="删除"
                    onClick={() => onDeleteFigure(fig.id)}
                  >
                    <Trash2 size={13} />
                  </button>
                </div>
                <textarea
                  className="input ws-fig-caption"
                  rows={2}
                  value={fig.caption}
                  placeholder="输入图注 Caption…"
                  aria-label={`${fig.fileName} 的图注`}
                  onChange={(e) => onUpdateFigureCaption(fig.id, e.target.value)}
                />
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
