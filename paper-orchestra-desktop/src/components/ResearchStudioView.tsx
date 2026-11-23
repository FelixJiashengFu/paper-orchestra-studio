import {
  ArrowRight,
  BarChart3,
  BookOpen,
  Compass,
  FileSpreadsheet,
  FlaskConical,
  FolderOpen,
  GitBranch,
  Link2,
  Loader2,
  Palette,
  Play,
  Plus,
  RefreshCw,
  Sigma,
  Sparkles,
  Trash2,
  Upload,
  Wrench,
} from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { api } from "../api";
import type {
  DataAnalysisResult,
  DataProfile,
  SaveSkillInput,
  SkillEntry,
  SopKind,
  SopWorkflowResult,
  WorkspaceHandoff,
} from "../types";
import { DocumentArtifactView } from "./DocumentArtifactView";
import { FigureReviewBench } from "./FigureReviewBench";
import { StudioMonitorPanel, useStudioTelemetry } from "./StudioMonitorPanel";
import "./studio.css";

interface ResearchStudioViewProps {
  onApplyHandoff: (handoff: WorkspaceHandoff) => Promise<void>;
  onToast: (msg: string, isError?: boolean) => void;
}

type StudioSubTab = "sops" | "figures" | "skills";
type SelectedSop = SopKind | "data_analysis";

interface SopDef {
  id: SelectedSop;
  title: string;
  badge: string;
  desc: string;
  icon: ReactNode;
  placeholder: string;
}

const SOP_DEFS: SopDef[] = [
  {
    id: "paper_reading",
    title: "论文精读 SOP (6步)",
    badge: "6步精读",
    desc: "上传 1+ 篇 PDF：材料完整性检查 → 全文概览 → 章节任务链 → 关键段落/公式 → 图文一致性 → Mermaid 框图与审稿批注表。",
    icon: <BookOpen size={16} />,
    placeholder: "输入你的精读重点或具体问题（可选），例如：着重拆解能量函数离散化细节与边界条件。",
  },
  {
    id: "lit_review",
    title: "文献综述 SOP (4步防编造)",
    badge: "GB/T 7714",
    desc: "拆解核心问题 → 统一证据提取矩阵 → 先写文献梳理再定背景 → 逐句标明来源与阅读深度，杜绝假文献。",
    icon: <Sparkles size={16} />,
    placeholder: "输入综述研究主题，例如：世界模型在自动驾驶规划与预测中的发展现状与开放挑战",
  },
  {
    id: "topic_ideation",
    title: "科研选题 SOP (从零到一 5步)",
    badge: "研一自救",
    desc: "梳理个人/课题组条件 → 文献领域地图与 5 篇精读排序 → 对比 3 个候选选题 → 设计 2-3 周最小验证方案 → 导师简报。",
    icon: <Compass size={16} />,
    placeholder: "输入导师给的初步方向或研究关键词，例如：钙钛矿电极材料的微纳掺杂与超级电容器循环稳定性",
  },
  {
    id: "data_analysis",
    title: "数据分析 7 步实操 (Python 沙盒)",
    badge: "代码自修复",
    desc: "上传 CSV/XLSX：实时生成数据字典 → AI 写 Python 运行统计检验(n/效应量/95%CI)+出图(报错自动重修) → 四层结论报告。",
    icon: <BarChart3 size={16} />,
    placeholder: "输入分析目标与统计问题，例如：比较各处理组的均值差异、相关性与效应量，绘制出版级对比图。",
  },
  {
    id: "reproduction",
    title: "文献复现七步法 SOP",
    badge: "工程闭环",
    desc: "明确复现目标边界 → 模块化规格 → SHA-256 资源哈希清单 → 最小闭环代码脚手架 → 指标对齐与三层消融检验。",
    icon: <GitBranch size={16} />,
    placeholder: "输入目标论文与希望达到的复现层级，例如：复现 GrabCut 矩形初始化+GMM+最大流最小割核心流程",
  },
  {
    id: "math_modeling",
    title: "数学建模全流程 SOP",
    badge: "国赛/美赛",
    desc: "赛题解析 → 自动化清洗与周期分析 → 多模型定量对比(MAE/RMSE/MAPE/R²)与特征重要性排序 → 一键转入数模 LaTeX 模板。",
    icon: <Sigma size={16} />,
    placeholder: "粘贴赛题核心需求，并附加赛题 CSV/XLSX 数据文件以自动运行多模型基准评测与画图。",
  },
];

export function ResearchStudioView({ onApplyHandoff, onToast }: ResearchStudioViewProps) {
  const [subTab, setSubTab] = useState<StudioSubTab>("sops");
  const [selectedSop, setSelectedSop] = useState<SelectedSop>("paper_reading");
  const [topic, setTopic] = useState("");
  const [notes, setNotes] = useState("");
  const [files, setFiles] = useState<string[]>([]);
  const [queryOnline, setQueryOnline] = useState(true);
  const [targetCount, setTargetCount] = useState("20");
  const [litLang, setLitLang] = useState<"zh" | "en">("zh");
  const [discipline, setDiscipline] = useState("");
  const [equipment, setEquipment] = useState("");
  const [timeframe, setTimeframe] = useState("2-3 周");
  const [reproLevel, setReproLevel] = useState("重新实现核心算法并用可控/替代基准定量验证");
  const [contestKind, setContestKind] = useState<"cumcm" | "mcm">("cumcm");

  const [dataProfile, setDataProfile] = useState<DataProfile | null>(null);
  const [profiling, setProfiling] = useState(false);

  const [runningSop, setRunningSop] = useState(false);
  const [sopResult, setSopResult] = useState<SopWorkflowResult | null>(null);
  const [dataResult, setDataResult] = useState<DataAnalysisResult | null>(null);
  const [handoffBusy, setHandoffBusy] = useState(false);
  const sopTelemetry = useStudioTelemetry();
  const skillTelemetry = useStudioTelemetry();

  // Skills Manager state
  const [skills, setSkills] = useState<SkillEntry[]>([]);
  const [selectedSkillId, setSelectedSkillId] = useState<string>("");
  const [skillPrompt, setSkillPrompt] = useState("");
  const [skillFiles, setSkillFiles] = useState<string[]>([]);
  const [skillOutput, setSkillOutput] = useState("");
  const [skillOutDir, setSkillOutDir] = useState("");
  const [skillBusy, setSkillBusy] = useState(false);
  const [newSkillOpen, setNewSkillOpen] = useState(false);
  const [skillForm, setSkillForm] = useState<SaveSkillInput>({
    name: "",
    description: "",
    category: "自定义技能",
    content: "",
  });
  const [githubSkillUrl, setGithubSkillUrl] = useState("");

  async function refreshSkills() {
    try {
      const list = await api.listSkills();
      setSkills(list);
      if (!selectedSkillId && list.length > 0) setSelectedSkillId(list[0].id);
    } catch (e) {
      onToast(`读取技能列表失败：${e}`, true);
    }
  }

  useEffect(() => {
    void refreshSkills();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handlePickSopFiles() {
    const p = await api.pickFile([
      { name: "科研材料", extensions: ["pdf", "csv", "tsv", "xlsx", "json", "md", "txt", "py", "tex"] },
    ]);
    if (!p) return;
    if (!files.includes(p)) {
      const next = [...files, p];
      setFiles(next);
      if (/\.(csv|tsv|xlsx|xls|json)$/i.test(p)) {
        setProfiling(true);
        try {
          const prof = await api.runDataProfile(p);
          setDataProfile(prof);
        } catch (e) {
          onToast(`读取数据字典失败：${e}`, true);
        } finally {
          setProfiling(false);
        }
      }
    }
  }

  async function handleRunSop() {
    if (selectedSop === "data_analysis") {
      const dataFile = files.find((f) => /\.(csv|tsv|xlsx|xls|json)$/i.test(f));
      if (!dataFile) {
        onToast("请先附带 1 份 CSV / TSV / XLSX / JSON 数据文件。", true);
        return;
      }
      const jobId = sopTelemetry.beginJob(activeSopDef.title);
      setRunningSop(true);
      setSopResult(null);
      setDataResult(null);
      try {
        const res = await api.runDataAnalysis(
          dataFile,
          topic.trim() || "请执行七步完整数据分析、统计检验并绘制出版级图表",
          undefined,
          jobId
        );
        setDataResult(res);
        sopTelemetry.finishJob(res.usageReport, res.outDir);
        onToast(`数据分析完成（代码运行 ${res.attempts} 轮自检通过，产出 ${res.figurePaths.length} 张图）`);
      } catch (e) {
        sopTelemetry.failJob(e);
        onToast(`执行失败：${e}`, true);
      } finally {
        setRunningSop(false);
      }
      return;
    }

    const jobId = sopTelemetry.beginJob(activeSopDef.title);
    setRunningSop(true);
    setSopResult(null);
    setDataResult(null);
    try {
      const payload = {
        topic: topic.trim(),
        question: topic.trim(),
        notes: notes.trim(),
        files,
        queryOnline,
        extra: {
          targetCount: Number(targetCount) || 20,
          language: litLang,
          discipline,
          equipment,
          timeframe,
          targetLevel: reproLevel,
          contest: contestKind,
        },
      };
      const res = await api.runSopWorkflow(selectedSop, payload, undefined, jobId);
      setSopResult(res);
      sopTelemetry.finishJob(res.usageReport, res.outDir);
      onToast("SOP 科研流程执行完成！");
    } catch (e) {
      sopTelemetry.failJob(e);
      onToast(`执行失败：${e}`, true);
    } finally {
      setRunningSop(false);
    }
  }

  async function handleImportHandoff(h: WorkspaceHandoff) {
    setHandoffBusy(true);
    try {
      await onApplyHandoff(h);
    } finally {
      setHandoffBusy(false);
    }
  }

  const activeSopDef = SOP_DEFS.find((s) => s.id === selectedSop) ?? SOP_DEFS[0];
  const activeHandoff = dataResult?.workspaceHandoff ?? sopResult?.workspaceHandoff ?? null;
  const activeReportMd = dataResult?.reportMarkdown ?? sopResult?.reportMarkdown ?? "";
  const activeOutDir = dataResult?.outDir ?? sopResult?.outDir ?? "";
  const selectedSkill = skills.find((s) => s.id === selectedSkillId) ?? null;

  return (
    <div className="rs-root">
      <div className="cs-tabs" role="tablist">
        <button
          type="button"
          className={`cs-tab ${subTab === "sops" ? "active" : ""}`}
          onClick={() => setSubTab("sops")}
        >
          <FlaskConical size={14} /> 科研 SOP 工作流 (6 种)
        </button>
        <button
          type="button"
          className={`cs-tab ${subTab === "figures" ? "active" : ""}`}
          onClick={() => setSubTab("figures")}
        >
          <Palette size={14} /> 科研绘图与局部修改工坊
        </button>
        <button
          type="button"
          className={`cs-tab ${subTab === "skills" ? "active" : ""}`}
          onClick={() => setSubTab("skills")}
        >
          <Wrench size={14} /> 技能库与 GitHub 导入 ({skills.length})
        </button>
      </div>

      {subTab === "sops" && (
        <div className="stack">
          <div className="rs-sop-grid">
            {SOP_DEFS.map((sop) => (
              <button
                key={sop.id}
                type="button"
                className={`rs-sop-card ${selectedSop === sop.id ? "active" : ""}`}
                onClick={() => setSelectedSop(sop.id)}
              >
                <div className="rs-sop-head">
                  <span className="row">
                    {sop.icon}
                    <span>{sop.title}</span>
                  </span>
                  <span className="badge badge-neutral">{sop.badge}</span>
                </div>
                <span className="rs-sop-desc">{sop.desc}</span>
              </button>
            ))}
          </div>

          <div className="rs-split">
            <div className="card stack">
              <strong>{activeSopDef.title} · 任务参数</strong>

              <div className="field">
                <label>核心主题 / 研究问题</label>
                <textarea
                  className="textarea"
                  rows={3}
                  value={topic}
                  onChange={(e) => setTopic(e.target.value)}
                  placeholder={activeSopDef.placeholder}
                />
              </div>

              {selectedSop === "lit_review" && (
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 8 }}>
                  <div className="field">
                    <label>检索目标篇数</label>
                    <input className="input" value={targetCount} onChange={(e) => setTargetCount(e.target.value)} />
                  </div>
                  <div className="field">
                    <label>综述语言</label>
                    <select className="select" value={litLang} onChange={(e) => setLitLang(e.target.value as "zh" | "en")}>
                      <option value="zh">中文 (GB/T 7714)</option>
                      <option value="en">英文</option>
                    </select>
                  </div>
                  <div className="field">
                    <label>在线联网检索</label>
                    <label className="row" style={{ marginTop: 6 }}>
                      <input type="checkbox" checked={queryOnline} onChange={(e) => setQueryOnline(e.target.checked)} />
                      <span>8 学术源补全</span>
                    </label>
                  </div>
                </div>
              )}

              {selectedSop === "topic_ideation" && (
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
                  <div className="field">
                    <label>所在学科与知识背景</label>
                    <input
                      className="input"
                      value={discipline}
                      onChange={(e) => setDiscipline(e.target.value)}
                      placeholder="材料工程 / 计算机视觉"
                    />
                  </div>
                  <div className="field">
                    <label>定题周期</label>
                    <input className="input" value={timeframe} onChange={(e) => setTimeframe(e.target.value)} />
                  </div>
                  <div className="field" style={{ gridColumn: "1 / -1" }}>
                    <label>可用仪器 / 数据集 / 算力条件</label>
                    <input
                      className="input"
                      value={equipment}
                      onChange={(e) => setEquipment(e.target.value)}
                      placeholder="例如：XRD/电化学工作站、2×RTX 4090、世界银行公开数据"
                    />
                  </div>
                </div>
              )}

              {selectedSop === "reproduction" && (
                <div className="field">
                  <label>复现目标层级</label>
                  <input className="input" value={reproLevel} onChange={(e) => setReproLevel(e.target.value)} />
                </div>
              )}

              {selectedSop === "math_modeling" && (
                <div className="field">
                  <label>目标竞赛模板</label>
                  <select className="select" value={contestKind} onChange={(e) => setContestKind(e.target.value as "cumcm" | "mcm")}>
                    <option value="cumcm">国赛 CUMCM / 电工杯</option>
                    <option value="mcm">美赛 COMAP MCM/ICM</option>
                  </select>
                </div>
              )}

              <div className="field">
                <label>附加背景资料或限制要求（可选）</label>
                <textarea
                  className="textarea"
                  rows={2}
                  value={notes}
                  onChange={(e) => setNotes(e.target.value)}
                  placeholder="例如：不可编造无实验支持的数据；优先对比线性回归、岭回归与梯度提升树等基线。"
                />
              </div>

              <div className="stack">
                <div className="row" style={{ justifyContent: "space-between" }}>
                  <label style={{ fontSize: "var(--fs-sm)", fontWeight: 500 }}>
                    附带材料（PDF 论文 / CSV / Excel 数据 / 代码）
                  </label>
                  <button type="button" className="btn btn-sm" onClick={() => void handlePickSopFiles()}>
                    <Upload size={12} /> 添加文件…
                  </button>
                </div>
                {files.length > 0 && (
                  <div className="rs-file-list">
                    {files.map((fp) => (
                      <div key={fp} className="rs-file-pill">
                        <span className="st-mono" title={fp}>
                          {fp.split(/[\\/]/).pop()}
                        </span>
                        <button
                          type="button"
                          className="btn btn-ghost btn-sm"
                          onClick={() => setFiles((prev) => prev.filter((x) => x !== fp))}
                        >
                          <Trash2 size={12} />
                        </button>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              {profiling && (
                <div className="hint">
                  <Loader2 size={13} className="spin" /> 正在本地离线扫描数据字典与质量警告…
                </div>
              )}

              {dataProfile && (
                <div className="callout" style={{ fontSize: "var(--fs-xs)" }}>
                  <div className="row" style={{ justifyContent: "space-between", marginBottom: 4 }}>
                    <strong>
                      <FileSpreadsheet size={13} /> 数据字典：{dataProfile.fileName}（{dataProfile.rows} 行 × {dataProfile.cols} 列）
                    </strong>
                    {dataProfile.duplicateRows > 0 && (
                      <span className="badge badge-warn">重复行 {dataProfile.duplicateRows}</span>
                    )}
                  </div>
                  {dataProfile.qualityWarnings.map((w, i) => (
                    <div key={i} style={{ color: "var(--warn)" }}>
                      · {w}
                    </div>
                  ))}
                  <div className="st-mono" style={{ marginTop: 4 }}>
                    列：{dataProfile.columns.map((c) => `${c.name}(${c.dtype})`).join(", ")}
                  </div>
                </div>
              )}

              <button type="button" className="btn btn-primary" onClick={() => void handleRunSop()} disabled={runningSop}>
                {runningSop ? <Loader2 size={14} className="spin" /> : <Play size={14} />}
                {runningSop ? "正在执行科研工作流（含检索 / 代码运行 / 核验）…" : `运行 ${activeSopDef.title}`}
              </button>
            </div>

            <div className="card stack">
              <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 8 }}>
                <strong>交付结果与可复现产物</strong>
                <div className="row">
                  {activeOutDir && (
                    <button type="button" className="btn btn-sm" onClick={() => api.openPathInSystem(activeOutDir)}>
                      <FolderOpen size={13} /> 打开结果文件夹
                    </button>
                  )}
                  {activeHandoff && (
                    <button
                      type="button"
                      className="btn btn-primary btn-sm"
                      disabled={handoffBusy}
                      onClick={() => void handleImportHandoff(activeHandoff)}
                    >
                      {handoffBusy ? <Loader2 size={13} className="spin" /> : <ArrowRight size={13} />}
                      一键导入论文工作台（生成完整 LaTeX）
                    </button>
                  )}
                </div>
              </div>

              <StudioMonitorPanel job={sopTelemetry.job} onCancel={() => void sopTelemetry.cancelJob()} />

              {!activeReportMd && !sopTelemetry.job ? (
                <div className="empty-state" style={{ minHeight: 260 }}>
                  <FlaskConical size={22} />
                  <p>在左侧选择 SOP 模式并填入材料，点击运行后将展示实时进度、Token 消耗、完整报告和生成图表。</p>
                </div>
              ) : activeReportMd ? (
                <div className="stack">
                  {dataResult && (
                    <div className="callout callout-success" style={{ fontSize: "var(--fs-xs)" }}>
                      Python 脚本 <code>analysis.py</code> 已实际运行完成（第 {dataResult.attempts} 轮自修复通过），产出{" "}
                      <strong>{dataResult.figurePaths.length}</strong> 张图表与 <code>stats_results.json</code>、
                      <code>cleaning_log.json</code>。
                    </div>
                  )}
                  {sopResult && sopResult.artifacts?.sources ? (
                    <div className="callout" style={{ fontSize: "var(--fs-xs)" }}>
                      已核验来源池共 <strong>{(sopResult.artifacts.sources as unknown[]).length}</strong> 篇（全文精读{" "}
                      {String(sopResult.artifacts.fullReadCount ?? 0)} 篇 · 仅摘要{" "}
                      {String(sopResult.artifacts.abstractOnlyCount ?? 0)} 篇），已导出 <code>references.bib</code>。
                    </div>
                  ) : null}
                  {sopResult && sopResult.artifacts?.mathBenchmark ? (
                    <div className="callout callout-success" style={{ fontSize: "var(--fs-xs)" }}>
                      已完成实数 Python 建模基准测试（OLS / Ridge / 加权近邻 MAE, RMSE, MAPE, R² 与特征敏感性排序），图表与{" "}
                      <code>math_benchmark.json</code> 已生成。
                    </div>
                  ) : null}

                  <DocumentArtifactView
                    content={activeReportMd}
                    title={activeSopDef.title}
                    defaultFilename={topic.trim() ? `${activeSopDef.title}_${topic.trim().slice(0, 30)}` : activeSopDef.title}
                    maxHeight={620}
                    onToast={onToast}
                  />

                  {(dataResult?.bundles?.length || sopResult?.bundles?.length) ? (
                    <div style={{ marginTop: 8 }}>
                      <strong style={{ display: "block", marginBottom: 8 }}>本次生成图表（支持直接圈选局部修改）</strong>
                      <FigureReviewBench baseDir={activeOutDir} onToast={onToast} />
                    </div>
                  ) : null}
                </div>
              ) : null}
            </div>
          </div>
        </div>
      )}

      {subTab === "figures" && (
        <div className="card">
          <FigureReviewBench baseDir="" onToast={onToast} />
        </div>
      )}

      {subTab === "skills" && (
        <div className="rs-split">
          <div className="card stack">
            <div className="row" style={{ justifyContent: "space-between" }}>
              <strong>技能列表 ({skills.length})</strong>
              <div className="row">
                <button
                  type="button"
                  className="btn btn-sm"
                  onClick={async () => {
                    const p = await api.pickFile([{ name: "Markdown Skill", extensions: ["md"] }]);
                    if (!p) return;
                    try {
                      const saved = await api.importSkillFromFile(p);
                      await refreshSkills();
                      setSelectedSkillId(saved.id);
                      onToast(`已导入本地技能：${saved.name}`);
                    } catch (e) {
                      onToast(`${e}`, true);
                    }
                  }}
                >
                  <Upload size={12} /> 本地 .md 导入
                </button>
                <button type="button" className="btn btn-primary btn-sm" onClick={() => setNewSkillOpen((v) => !v)}>
                  <Plus size={12} /> 新建技能
                </button>
              </div>
            </div>

            <div className="row" style={{ gap: 6 }}>
              <input
                className="input st-mono"
                style={{ flex: 1 }}
                value={githubSkillUrl}
                onChange={(e) => setGithubSkillUrl(e.target.value)}
                placeholder="从 GitHub Skill URL 导入 (https://github.com/owner/repo/.../SKILL.md)"
              />
              <button
                type="button"
                className="btn btn-sm"
                onClick={async () => {
                  if (!githubSkillUrl.trim()) return;
                  try {
                    const saved = await api.importSkillFromUrl(githubSkillUrl.trim());
                    setGithubSkillUrl("");
                    await refreshSkills();
                    setSelectedSkillId(saved.id);
                    onToast(`已从 GitHub 导入技能：${saved.name}`);
                  } catch (e) {
                    onToast(`${e}`, true);
                  }
                }}
              >
                <Link2 size={12} /> 拉取
              </button>
            </div>

            {newSkillOpen && (
              <div className="callout stack">
                <input
                  className="input"
                  placeholder="技能名称"
                  value={skillForm.name}
                  onChange={(e) => setSkillForm((s) => ({ ...s, name: e.target.value }))}
                />
                <input
                  className="input"
                  placeholder="一句话说明"
                  value={skillForm.description}
                  onChange={(e) => setSkillForm((s) => ({ ...s, description: e.target.value }))}
                />
                <textarea
                  className="textarea"
                  rows={5}
                  placeholder="Markdown / Prompt 指令正文…"
                  value={skillForm.content}
                  onChange={(e) => setSkillForm((s) => ({ ...s, content: e.target.value }))}
                />
                <div className="row" style={{ justifyContent: "flex-end" }}>
                  <button
                    type="button"
                    className="btn btn-primary btn-sm"
                    onClick={async () => {
                      try {
                        const saved = await api.saveSkill(skillForm);
                        setNewSkillOpen(false);
                        setSkillForm({ name: "", description: "", category: "自定义技能", content: "" });
                        await refreshSkills();
                        setSelectedSkillId(saved.id);
                        onToast("已保存技能");
                      } catch (e) {
                        onToast(`${e}`, true);
                      }
                    }}
                  >
                    保存技能
                  </button>
                </div>
              </div>
            )}

            <div className="stack" style={{ maxHeight: 460, overflowY: "auto" }}>
              {skills.map((s) => (
                <div
                  key={s.id}
                  className={`lb-item ${s.id === selectedSkillId ? "active" : ""}`}
                  onClick={() => setSelectedSkillId(s.id)}
                >
                  <div className="row" style={{ justifyContent: "space-between" }}>
                    <strong>{s.name}</strong>
                    <div className="row">
                      <span className="badge badge-neutral">{s.builtin ? "内置" : s.category}</span>
                      {!s.builtin && (
                        <button
                          type="button"
                          className="btn btn-ghost btn-sm"
                          onClick={async (e) => {
                            e.stopPropagation();
                            await api.deleteSkill(s.id);
                            await refreshSkills();
                          }}
                        >
                          <Trash2 size={12} />
                        </button>
                      )}
                    </div>
                  </div>
                  <span className="hint">{s.description}</span>
                </div>
              ))}
            </div>
          </div>

          <div className="card stack">
            <div className="row" style={{ justifyContent: "space-between" }}>
              <strong>{selectedSkill ? `执行技能：${selectedSkill.name}` : "选择左侧技能"}</strong>
              <button type="button" className="btn btn-ghost btn-sm" onClick={() => void refreshSkills()}>
                <RefreshCw size={12} /> 刷新
              </button>
            </div>
            {selectedSkill && (
              <>
                <details>
                  <summary style={{ cursor: "pointer", fontSize: "var(--fs-xs)", color: "var(--accent)" }}>
                    查看技能源码 (Markdown)
                  </summary>
                  <pre className="le-diff-pre" style={{ marginTop: 6, maxHeight: 160 }}>
                    {selectedSkill.content}
                  </pre>
                </details>
                <div className="field">
                  <label>任务指令（自动融合全局研究偏好与长期记忆）</label>
                  <textarea
                    className="textarea"
                    rows={3}
                    value={skillPrompt}
                    onChange={(e) => setSkillPrompt(e.target.value)}
                    placeholder="例如：/运行该技能 并针对附带的文献和实验数据给出详细输出…"
                  />
                </div>
                <div className="row" style={{ justifyContent: "space-between" }}>
                  <button
                    type="button"
                    className="btn btn-sm"
                    onClick={async () => {
                      const p = await api.pickFile();
                      if (p && !skillFiles.includes(p)) setSkillFiles((prev) => [...prev, p]);
                    }}
                  >
                    <Upload size={12} /> 添加参考文件 ({skillFiles.length})
                  </button>
                  <button
                    type="button"
                    className="btn btn-primary btn-sm"
                    disabled={skillBusy || !skillPrompt.trim()}
                    onClick={async () => {
                      const jobId = skillTelemetry.beginJob(`技能：${selectedSkill.name}`);
                      setSkillBusy(true);
                      setSkillOutput("");
                      setSkillOutDir("");
                      try {
                        const res = await api.runCustomSkill(selectedSkill.id, skillPrompt, skillFiles, undefined, jobId);
                        setSkillOutput(res.outputMarkdown);
                        setSkillOutDir(res.outDir);
                        skillTelemetry.finishJob(res.usageReport, res.outDir);
                        onToast("技能执行完毕");
                      } catch (e) {
                        skillTelemetry.failJob(e);
                        onToast(`技能执行失败：${e}`, true);
                      } finally {
                        setSkillBusy(false);
                      }
                    }}
                  >
                    {skillBusy ? <Loader2 size={13} className="spin" /> : <Play size={13} />} 执行技能
                  </button>
                </div>
                <StudioMonitorPanel job={skillTelemetry.job} onCancel={() => void skillTelemetry.cancelJob()} />
                {skillOutput && (
                  <>
                    <div className="row" style={{ justifyContent: "space-between" }}>
                      <strong>技能交付结果</strong>
                      {skillOutDir && (
                        <button type="button" className="btn btn-sm" onClick={() => api.openPathInSystem(skillOutDir)}>
                          <FolderOpen size={12} /> 打开输出目录
                        </button>
                      )}
                    </div>
                    <DocumentArtifactView
                      content={skillOutput}
                      title={selectedSkill.name}
                      defaultFilename={selectedSkill.id}
                      maxHeight={460}
                      onToast={onToast}
                    />
                  </>
                )}
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
