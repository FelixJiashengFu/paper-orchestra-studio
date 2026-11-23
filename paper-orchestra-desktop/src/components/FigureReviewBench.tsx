import {
  Code2,
  Crop,
  History,
  Image as ImageIcon,
  Loader2,
  Plus,
  RotateCcw,
  Sparkles,
  Upload,
  Wand2,
  X,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { api, base64ToBlobUrl, formatIsoShort, guessImageMime } from "../api";
import type { FigureBbox, FigureBundle } from "../types";
import { StudioMonitorPanel, useStudioTelemetry } from "./StudioMonitorPanel";
import "./studio.css";

interface FigureReviewBenchProps {
  /** Run folder OR standalone figure studio dir; empty string = default <output_root>/figure_studio */
  baseDir: string;
  readOnly?: boolean;
  onToast?: (msg: string, isError?: boolean) => void;
  onFigureUpdated?: () => void;
}

const EDIT_CHIPS = [
  "移开遮挡柱子的图例",
  "把局部或该子图改成独立纵轴标度",
  "放大坐标轴刻度和图例字号至出版级",
  "切换为色盲友好的 Okabe-Ito 配色并加白色背景",
  "只保留核心趋势线并补充关键标注箭头",
];

export function FigureReviewBench({
  baseDir,
  readOnly = false,
  onToast,
  onFigureUpdated,
}: FigureReviewBenchProps) {
  const [bundles, setBundles] = useState<FigureBundle[]>([]);
  const [standalone, setStandalone] = useState<Array<{ name: string; path: string; relDir: string }>>([]);
  const [loading, setLoading] = useState(false);
  const [selectedIdx, setSelectedIdx] = useState(0);
  const [viewVersion, setViewVersion] = useState<string>("");
  const [imgUrl, setImgUrl] = useState<string | null>(null);
  const [bbox, setBbox] = useState<FigureBbox | null>(null);
  const [dragStart, setDragStart] = useState<{ x: number; y: number } | null>(null);
  const [instruction, setInstruction] = useState("");
  const [busy, setBusy] = useState<"" | "revise" | "restore" | "create" | "promote">("");
  const [showCode, setShowCode] = useState(false);

  // Create new figure drawer
  const [createOpen, setCreateOpen] = useState(false);
  const [createMode, setCreateMode] = useState<"data_code" | "schematic_svg" | "concept_image">("data_code");
  const [createTitle, setCreateTitle] = useState("");
  const [createPrompt, setCreatePrompt] = useState("");
  const [createDataFile, setCreateDataFile] = useState("");
  const [createRefImage, setCreateRefImage] = useState("");

  const canvasRef = useRef<HTMLDivElement | null>(null);
  const figTelemetry = useStudioTelemetry();

  async function loadList(preferBundleDir?: string) {
    setLoading(true);
    try {
      const res = await api.runFigureStudioList(baseDir);
      setBundles(res.bundles || []);
      setStandalone(res.standalone || []);
      if (preferBundleDir) {
        const idx = (res.bundles || []).findIndex((b) => b.bundleDir === preferBundleDir);
        if (idx >= 0) setSelectedIdx(idx);
      } else {
        setSelectedIdx((prev) => Math.min(prev, Math.max(0, (res.bundles?.length ?? 1) - 1)));
      }
    } catch (e) {
      onToast?.(`读取图表工作台失败：${e}`, true);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadList();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [baseDir]);

  const activeBundle = bundles[selectedIdx] ?? null;

  useEffect(() => {
    setViewVersion(activeBundle?.activeVersion || "");
    setBbox(null);
  }, [activeBundle?.bundleDir, activeBundle?.activeVersion]);

  const viewedVersionObj = useMemo(() => {
    if (!activeBundle) return null;
    return (
      activeBundle.versions.find((v) => v.version === viewVersion) ??
      activeBundle.versions[activeBundle.versions.length - 1] ??
      null
    );
  }, [activeBundle, viewVersion]);

  useEffect(() => {
    let revokeUrl: string | null = null;
    let cancelled = false;
    async function loadPreview() {
      const p = viewedVersionObj?.abs_image_path;
      if (!p) {
        setImgUrl(null);
        return;
      }
      try {
        const b64 = await api.readFileBase64(p);
        if (cancelled) return;
        revokeUrl = base64ToBlobUrl(b64, guessImageMime(p));
        setImgUrl(revokeUrl);
      } catch {
        setImgUrl(null);
      }
    }
    void loadPreview();
    return () => {
      cancelled = true;
      if (revokeUrl) URL.revokeObjectURL(revokeUrl);
    };
  }, [viewedVersionObj?.abs_image_path, viewedVersionObj?.version]);

  function clientToNorm(clientX: number, clientY: number): { x: number; y: number } | null {
    const el = canvasRef.current;
    if (!el) return null;
    const rect = el.getBoundingClientRect();
    if (rect.width < 10 || rect.height < 10) return null;
    return {
      x: Math.max(0, Math.min(1, (clientX - rect.left) / rect.width)),
      y: Math.max(0, Math.min(1, (clientY - rect.top) / rect.height)),
    };
  }

  function onPointerDown(e: React.PointerEvent<HTMLDivElement>) {
    if (readOnly || !imgUrl) return;
    const pt = clientToNorm(e.clientX, e.clientY);
    if (!pt) return;
    e.currentTarget.setPointerCapture(e.pointerId);
    setDragStart(pt);
    setBbox({ x: pt.x, y: pt.y, w: 0.01, h: 0.01 });
  }

  function onPointerMove(e: React.PointerEvent<HTMLDivElement>) {
    if (!dragStart) return;
    const pt = clientToNorm(e.clientX, e.clientY);
    if (!pt) return;
    const x = Math.min(dragStart.x, pt.x);
    const y = Math.min(dragStart.y, pt.y);
    const w = Math.max(0.015, Math.abs(pt.x - dragStart.x));
    const h = Math.max(0.015, Math.abs(pt.y - dragStart.y));
    setBbox({
      x: Number(x.toFixed(4)),
      y: Number(y.toFixed(4)),
      w: Number(Math.min(1 - x, w).toFixed(4)),
      h: Number(Math.min(1 - y, h).toFixed(4)),
    });
  }

  function onPointerUp() {
    if (!dragStart) return;
    setDragStart(null);
    if (bbox && bbox.w < 0.02 && bbox.h < 0.02) {
      setBbox(null);
    }
  }

  async function handlePromote(path: string, name: string) {
    setBusy("promote");
    try {
      const targetDir = baseDir || "";
      const res = await api.runFigureStudioEnsureBundle(targetDir, path, name.replace(/\.[^.]+$/, ""));
      await loadList(res.bundle?.bundleDir);
      onToast?.(`已纳入版本审阅台：${name}`);
    } catch (e) {
      onToast?.(`归档图表失败：${e}`, true);
    } finally {
      setBusy("");
    }
  }

  async function handleRevise() {
    if (!activeBundle || !instruction.trim()) return;
    const jobId = figTelemetry.beginJob(`局部改图：${activeBundle.title || activeBundle.figureId}`);
    setBusy("revise");
    try {
      const res = await api.runFigureStudioRevise({
        bundleDir: activeBundle.bundleDir,
        instruction: instruction.trim(),
        bbox,
        baseDir,
        jobId,
      });
      setBundles((prev) => prev.map((b) => (b.bundleDir === res.bundle.bundleDir ? res.bundle : b)));
      setViewVersion(res.bundle.activeVersion);
      setInstruction("");
      setBbox(null);
      figTelemetry.finishJob(res.usageReport, res.bundle.bundleDir);
      onToast?.(`已生成新版本 ${res.bundle.activeVersion}，代码与图片已同步存档`);
      onFigureUpdated?.();
    } catch (e) {
      figTelemetry.failJob(e);
      onToast?.(`局部改图失败：${e}`, true);
    } finally {
      setBusy("");
    }
  }

  async function handleRestore(ver: string) {
    if (!activeBundle) return;
    setBusy("restore");
    try {
      const res = await api.runFigureStudioRestore(activeBundle.bundleDir, ver, baseDir);
      setBundles((prev) => prev.map((b) => (b.bundleDir === res.bundle.bundleDir ? res.bundle : b)));
      setViewVersion(ver);
      onToast?.(`已将当前激活图表回滚至 ${ver}`);
      onFigureUpdated?.();
    } catch (e) {
      onToast?.(`恢复版本失败：${e}`, true);
    } finally {
      setBusy("");
    }
  }

  async function handleCreate() {
    if (!createPrompt.trim()) {
      onToast?.("请先输入绘图描述或要求。", true);
      return;
    }
    const jobId = figTelemetry.beginJob(`新建图表：${createTitle.trim() || createMode}`);
    setBusy("create");
    try {
      const res = await api.runFigureStudioCreate({
        outDir: baseDir,
        mode: createMode,
        prompt: createPrompt.trim(),
        title: createTitle.trim() || undefined,
        dataFile: createDataFile || undefined,
        refImage: createRefImage || undefined,
        jobId,
      });
      setCreateOpen(false);
      setCreateTitle("");
      setCreatePrompt("");
      setCreateDataFile("");
      setCreateRefImage("");
      await loadList(res.bundle.bundleDir);
      figTelemetry.finishJob(res.usageReport, res.bundle.bundleDir);
      onToast?.("已按选定路线生成新图表并建立 v1 版本存档");
      onFigureUpdated?.();
    } catch (e) {
      figTelemetry.failJob(e);
      onToast?.(`生成图表失败：${e}`, true);
    } finally {
      setBusy("");
    }
  }

  return (
    <div className="fb-wrap">
      <div className="fb-toolbar">
        <div className="row">
          <span className="hint">
            拖拽图片可直接<strong>圈选局部区域</strong>，输入一句要求让 AI 仅改局部；所有历史版本（含脚本/数据）自动存档可随时找回。
          </span>
        </div>
        {!readOnly && (
          <div className="row">
            <button
              type="button"
              className="btn btn-sm"
              onClick={async () => {
                const picked = await api.pickFile([{ name: "图片", extensions: ["png", "jpg", "jpeg", "svg"] }]);
                if (picked) void handlePromote(picked, picked.split(/[\\/]/).pop() || "figure");
              }}
              disabled={busy !== ""}
            >
              <Upload size={13} /> 导入外部图入库
            </button>
            <button
              type="button"
              className="btn btn-primary btn-sm"
              onClick={() => setCreateOpen((v) => !v)}
              disabled={busy !== ""}
            >
              <Plus size={13} /> 三路线新建 / 参考图复刻
            </button>
          </div>
        )}
      </div>

      <StudioMonitorPanel compact job={figTelemetry.job} onCancel={() => void figTelemetry.cancelJob()} />

      {createOpen && (
        <div className="card stack">
          <div className="row" style={{ justifyContent: "space-between" }}>
            <strong>三路线科研绘图 & 参考图风格复刻（Nature/Science 工作流）</strong>
            <button type="button" className="btn btn-ghost btn-sm" onClick={() => setCreateOpen(false)}>
              <X size={13} />
            </button>
          </div>
          <div className="cs-tabs" style={{ marginBottom: 4 }}>
            <button
              type="button"
              className={`cs-tab ${createMode === "data_code" ? "active" : ""}`}
              onClick={() => setCreateMode("data_code")}
            >
              1. 数据→代码绘图（Python 可复现 + 参考图复刻）
            </button>
            <button
              type="button"
              className={`cs-tab ${createMode === "schematic_svg" ? "active" : ""}`}
              onClick={() => setCreateMode("schematic_svg")}
            >
              2. 结构→矢量示意图（可编辑 SVG）
            </button>
            <button
              type="button"
              className={`cs-tab ${createMode === "concept_image" ? "active" : ""}`}
              onClick={() => setCreateMode("concept_image")}
            >
              3. 概念→视觉总览图（Overview / 封面）
            </button>
          </div>
          <div className="field">
            <label>图表标题（可选，用于命名版本库）</label>
            <input
              className="input"
              value={createTitle}
              onChange={(e) => setCreateTitle(e.target.value)}
              placeholder="例如：fig_sensitivity_heatmap"
            />
          </div>
          <div className="field">
            <label>绘图任务说明（说明给谁看、展示什么变量/结构、保持哪些风格要素）</label>
            <textarea
              className="textarea"
              rows={3}
              value={createPrompt}
              onChange={(e) => setCreatePrompt(e.target.value)}
              placeholder={
                createMode === "data_code"
                  ? "例如：根据附带的 CSV 数据绘制四组模型的 MAE 与 R2 对比分组柱状图+散点叠加，严格采用 Nature 配色。"
                  : createMode === "schematic_svg"
                    ? "例如：绘制分四阶段（Stage 1~4）的模型架构流程图，纯白底色，箭头指向清晰，不使用英文以外的乱码。"
                    : "例如：基于全文方法构建 4-Stage Research Overview 概念总览图。"
              }
            />
          </div>
          <div className="row" style={{ flexWrap: "wrap", gap: 10 }}>
            {createMode === "data_code" && (
              <div className="row">
                <button
                  type="button"
                  className="btn btn-sm"
                  onClick={async () => {
                    const p = await api.pickFile([{ name: "数据文件", extensions: ["csv", "tsv", "xlsx", "json"] }]);
                    if (p) setCreateDataFile(p);
                  }}
                >
                  选择真实数据文件 (CSV/XLSX)…
                </button>
                {createDataFile && (
                  <span className="badge badge-succeeded">
                    {createDataFile.split(/[\\/]/).pop()}
                    <button type="button" onClick={() => setCreateDataFile("")} style={{ marginLeft: 4 }}>
                      ×
                    </button>
                  </span>
                )}
              </div>
            )}
            <div className="row">
              <button
                type="button"
                className="btn btn-sm"
                onClick={async () => {
                  const p = await api.pickFile([{ name: "参考图", extensions: ["png", "jpg", "jpeg", "webp"] }]);
                  if (p) setCreateRefImage(p);
                }}
              >
                <ImageIcon size={13} /> 添加参考风格图（可选复刻样式）…
              </button>
              {createRefImage && (
                <span className="badge badge-neutral">
                  {createRefImage.split(/[\\/]/).pop()}
                  <button type="button" onClick={() => setCreateRefImage("")} style={{ marginLeft: 4 }}>
                    ×
                  </button>
                </span>
              )}
            </div>
          </div>
          <div className="row" style={{ justifyContent: "flex-end" }}>
            <button type="button" className="btn btn-primary btn-sm" onClick={handleCreate} disabled={busy !== ""}>
              {busy === "create" ? <Loader2 size={13} className="spin" /> : <Sparkles size={13} />}
              {busy === "create" ? "生成中（含代码自检修复）…" : "生成并建立版本档案"}
            </button>
          </div>
        </div>
      )}

      {loading && bundles.length === 0 ? (
        <div className="hint">
          <Loader2 size={14} className="spin" /> 正在扫描图表版本库…
        </div>
      ) : bundles.length === 0 && standalone.length === 0 ? (
        <div className="empty-state card">
          <ImageIcon size={22} />
          <p>暂无已归档图表。可点击右上角「三路线新建 / 参考图复刻」，或在生成论文后于此处审阅每张图。</p>
        </div>
      ) : (
        <div className="fb-grid">
          <aside className="fb-sidebar">
            {bundles.map((b, i) => (
              <button
                key={b.bundleDir}
                type="button"
                className={`fb-item ${i === selectedIdx ? "active" : ""}`}
                onClick={() => setSelectedIdx(i)}
              >
                <span className="fb-item-title">{b.title || b.figureId}</span>
                <span className="fb-item-meta">
                  激活 {b.activeVersion} · 共 {b.versions.length} 版 · {b.taskName === "diagram" ? "示意图" : "数据图"}
                </span>
              </button>
            ))}
            {standalone.length > 0 && (
              <>
                <div className="sidebar-section-label" style={{ marginTop: 8 }}>
                  未纳入版本库的单图
                </div>
                {standalone.map((s) => (
                  <div key={s.path} className="fb-item" style={{ cursor: "default" }}>
                    <span className="fb-item-title">{s.name}</span>
                    <span className="fb-item-meta">{s.relDir}</span>
                    {!readOnly && (
                      <button
                        type="button"
                        className="btn btn-sm"
                        style={{ marginTop: 4 }}
                        disabled={busy !== ""}
                        onClick={() => void handlePromote(s.path, s.name)}
                      >
                        纳入版本审阅台
                      </button>
                    )}
                  </div>
                ))}
              </>
            )}
          </aside>

          <div className="fb-stage">
            {activeBundle && (
              <>
                <div className="fb-versions">
                  <History size={14} />
                  <span className="hint">历史版本档案：</span>
                  {activeBundle.versions.map((v) => {
                    const isActive = v.version === activeBundle.activeVersion;
                    const isViewing = v.version === (viewedVersionObj?.version ?? "");
                    return (
                      <button
                        key={v.version}
                        type="button"
                        className={`fb-ver-pill ${isViewing ? "viewing" : ""}`}
                        title={`${v.instruction} (${formatIsoShort(v.created_at)})`}
                        onClick={() => setViewVersion(v.version)}
                      >
                        {isActive && <span className="fb-ver-active-dot" title="当前论文引用版本" />}
                        <span>{v.version}</span>
                        {v.bbox && <Crop size={10} />}
                      </button>
                    );
                  })}
                  {viewedVersionObj && viewedVersionObj.version !== activeBundle.activeVersion && !readOnly && (
                    <button
                      type="button"
                      className="btn btn-sm"
                      disabled={busy !== ""}
                      onClick={() => void handleRestore(viewedVersionObj.version)}
                    >
                      {busy === "restore" ? <Loader2 size={12} className="spin" /> : <RotateCcw size={12} />}
                      恢复此版本 ({viewedVersionObj.version}) 为正文图
                    </button>
                  )}
                  {viewedVersionObj?.code && (
                    <button
                      type="button"
                      className="btn btn-ghost btn-sm"
                      onClick={() => setShowCode((v) => !v)}
                    >
                      <Code2 size={13} /> {showCode ? "隐藏生成源码" : "查看生成代码"}
                    </button>
                  )}
                  {viewedVersionObj?.abs_svg_path && (
                    <button
                      type="button"
                      className="btn btn-ghost btn-sm"
                      onClick={() => api.revealPathInExplorer(viewedVersionObj.abs_svg_path!)}
                    >
                      显示矢量 SVG 文件
                    </button>
                  )}
                </div>

                {viewedVersionObj?.instruction && (
                  <div className="hint">
                    <strong>{viewedVersionObj.version} 修改说明：</strong>
                    {viewedVersionObj.instruction}
                  </div>
                )}

                <div
                  ref={canvasRef}
                  className="fb-canvas-box"
                  onPointerDown={onPointerDown}
                  onPointerMove={onPointerMove}
                  onPointerUp={onPointerUp}
                  title={readOnly ? "" : "在图上拖拽框选要修改的局部区域"}
                >
                  {imgUrl ? <img src={imgUrl} alt={activeBundle.title} draggable={false} /> : <div className="hint" style={{ padding: 24 }}>图片载入中…</div>}
                  {bbox && (
                    <div
                      className="fb-bbox"
                      style={{
                        left: `${bbox.x * 100}%`,
                        top: `${bbox.y * 100}%`,
                        width: `${bbox.w * 100}%`,
                        height: `${bbox.h * 100}%`,
                      }}
                    >
                      <span className="fb-bbox-tag">局部修改区</span>
                    </div>
                  )}
                </div>

                {showCode && viewedVersionObj?.code && (
                  <pre className="le-diff-pre" style={{ maxHeight: 240 }}>
                    {viewedVersionObj.code}
                  </pre>
                )}

                {!readOnly && (
                  <div className="fb-controls">
                    <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap" }}>
                      <div className="row">
                        <Crop size={14} />
                        <strong>审阅台局部改图</strong>
                        {bbox ? (
                          <span className="badge badge-warn">
                            已圈选 ({Math.round(bbox.x * 100)}%, {Math.round(bbox.y * 100)}% · {Math.round(bbox.w * 100)}%×
                            {Math.round(bbox.h * 100)}%)
                          </span>
                        ) : (
                          <span className="hint">未框选（将基于当前激活版全局微调）</span>
                        )}
                      </div>
                      {bbox && (
                        <button type="button" className="btn btn-ghost btn-sm" onClick={() => setBbox(null)}>
                          <X size={12} /> 清除框选
                        </button>
                      )}
                    </div>

                    <div className="fb-chips">
                      {EDIT_CHIPS.map((c) => (
                        <button key={c} type="button" className="fb-chip" onClick={() => setInstruction(c)}>
                          {c}
                        </button>
                      ))}
                    </div>

                    <div className="row" style={{ gap: 8, alignItems: "flex-end" }}>
                      <textarea
                        className="textarea"
                        rows={2}
                        style={{ flex: 1 }}
                        value={instruction}
                        onChange={(e) => setInstruction(e.target.value)}
                        placeholder="写一句具体的调整要求，例如：把图例移到左下角空白处、仅保留前三条曲线、将此处柱状图改为独立纵轴标度…"
                      />
                      <button
                        type="button"
                        className="btn btn-primary"
                        disabled={busy !== "" || !instruction.trim()}
                        onClick={() => void handleRevise()}
                      >
                        {busy === "revise" ? <Loader2 size={14} className="spin" /> : <Wand2 size={14} />}
                        {busy === "revise" ? "生成修订中…" : "仅改此处并存档"}
                      </button>
                    </div>
                  </div>
                )}

                {activeBundle.caption && (
                  <div className="callout" style={{ fontSize: "var(--fs-sm)" }}>
                    <strong>当前图注：</strong>
                    {activeBundle.caption}
                  </div>
                )}
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
