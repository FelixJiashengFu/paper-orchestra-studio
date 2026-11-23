import {
  AlertCircle,
  Check,
  CheckCircle2,
  Loader2,
  Play,
  Plus,
  Save,
  Square,
  X,
} from "lucide-react";
import { downloadDir, join } from "@tauri-apps/api/path";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api";
import { ArtifactsView } from "./components/ArtifactsView";
import { BrandMark } from "./components/BrandMark";
import { LibraryView } from "./components/LibraryView";
import { ResearchStudioView } from "./components/ResearchStudioView";
import { RunsView } from "./components/RunsView";
import { SettingsView } from "./components/SettingsView";
import { Sidebar } from "./components/Sidebar";
import { WorkspaceView } from "./components/WorkspaceView";
import type {
  EnvironmentStatus,
  NavTab,
  Project,
  ProjectSummary,
  RunLogEntry,
  RunRecord,
  WorkspaceHandoff,
} from "./types";

const VIEW_LABELS: Record<NavTab, string> = {
  workspace: "工作台",
  studio: "科研工坊",
  library: "文献与图谱",
  runs: "运行与日志",
  artifacts: "成果与 PDF",
  settings: "环境与算力",
};

const STAGE_LABELS: Record<string, string> = {
  outline: "大纲",
  literature: "文献",
  plotting: "绘图",
  writing: "正文",
  refinement: "润色",
  completed: "收尾",
};

export function App() {
  const [activeTab, setActiveTab] = useState<NavTab>("workspace");
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(null);
  const [project, setProject] = useState<Project | null>(null);
  const [saveState, setSaveState] = useState<"saved" | "dirty" | "saving">("saved");
  const [runs, setRuns] = useState<RunRecord[]>([]);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [logs, setLogs] = useState<RunLogEntry[]>([]);
  const [envStatus, setEnvStatus] = useState<EnvironmentStatus | null>(null);
  const [toast, setToast] = useState<{ msg: string; isError: boolean } | null>(null);

  const selectedProjectIdRef = useRef<string | null>(null);
  const selectedRunIdRef = useRef<string | null>(null);
  const saveTimerRef = useRef<number | null>(null);
  const latestProjectRef = useRef<Project | null>(null);

  selectedProjectIdRef.current = selectedProjectId;
  selectedRunIdRef.current = selectedRunId;
  latestProjectRef.current = project;

  const showToast = useCallback((msg: string, isError = false) => {
    setToast({ msg, isError });
    window.setTimeout(() => {
      setToast((prev) => (prev?.msg === msg ? null : prev));
    }, 3500);
  }, []);

  /**
   * "导出 Overleaf 包": asks where to save, zips the run's final LaTeX sources
   * there and resolves to the written path (null when cancelled or failed;
   * the outcome is reported with a toast either way).
   */
  const handleExportLatex = useCallback(
    async (run: RunRecord, opts?: { quietSuccess?: boolean }): Promise<string | null> => {
      const folder = run.runDir.split(/[\\/]/).filter(Boolean).pop() || "paper";
      const fileName = `${folder}_overleaf.zip`;
      let defaultPath = fileName;
      try {
        defaultPath = await join(await downloadDir(), fileName);
      } catch {
        // no Downloads folder: the dialog opens in its default location
      }
      try {
        const dest = await api.pickSaveFile(defaultPath, [
          { name: "ZIP 压缩包", extensions: ["zip"] },
        ]);
        if (!dest) return null;
        const written = await api.exportRunLatex(run.runDir, dest);
        if (!opts?.quietSuccess) showToast(`已导出 Overleaf 包：${written}`);
        return written;
      } catch (e) {
        showToast(`导出 Overleaf 包失败：${e}`, true);
        return null;
      }
    },
    [showToast]
  );

  const refreshEnv = useCallback(async () => {
    try {
      const st = await api.checkEnvironment();
      setEnvStatus(st);
    } catch {
      // ignore initial check errors
    }
  }, []);

  const refreshProjects = useCallback(async (preferId?: string) => {
    const list = await api.listProjects();
    setProjects(list);
    setSelectedProjectId((curr) => {
      if (preferId && list.some((p) => p.id === preferId)) return preferId;
      if (curr && list.some((p) => p.id === curr)) return curr;
      return list[0]?.id ?? null;
    });
  }, []);

  const refreshRunsForProject = useCallback(async (projectId: string, preferRunId?: string) => {
    const list = await api.listRuns(projectId);
    setRuns(list);
    setSelectedRunId((curr) => {
      if (preferRunId && list.some((r) => r.id === preferRunId)) return preferRunId;
      if (curr && list.some((r) => r.id === curr)) return curr;
      return list[0]?.id ?? null;
    });
  }, []);

  // Initial bootstrap
  useEffect(() => {
    refreshEnv();
    refreshProjects();
  }, [refreshEnv, refreshProjects]);

  // Load selected project details & runs
  useEffect(() => {
    if (!selectedProjectId) {
      setProject(null);
      setRuns([]);
      setSelectedRunId(null);
      setLogs([]);
      return;
    }

    let cancelled = false;
    async function load() {
      if (!selectedProjectId) return;
      try {
        const [proj, runList] = await Promise.all([
          api.getProject(selectedProjectId),
          api.listRuns(selectedProjectId),
        ]);
        if (cancelled) return;
        setProject(proj);
        setSaveState("saved");
        setRuns(runList);
        setSelectedRunId(runList[0]?.id ?? null);
      } catch (e) {
        showToast(`加载项目失败: ${e}`, true);
      }
    }
    load();
    return () => {
      cancelled = true;
    };
  }, [selectedProjectId, showToast]);

  // Load persisted logs when selected run changes
  useEffect(() => {
    if (!selectedRunId) {
      setLogs([]);
      return;
    }
    let cancelled = false;
    api
      .getRunLogs(selectedRunId, 0, 10000)
      .then((rows) => {
        if (!cancelled) setLogs(rows);
      })
      .catch(() => {
        if (!cancelled) setLogs([]);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedRunId]);

  // Live Tauri events for streaming logs & status updates
  useEffect(() => {
    let unlistenLog: (() => void) | null = null;
    let unlistenStatus: (() => void) | null = null;

    api
      .onRunLog((payload) => {
        if (payload.runId === selectedRunIdRef.current) {
          setLogs((prev) => [
            ...prev,
            {
              id: Date.now() + Math.random(),
              runId: payload.runId,
              stage: payload.stage,
              line: payload.line,
              createdAt: payload.createdAt,
            },
          ]);
        }
      })
      .then((fn) => {
        unlistenLog = fn;
      });

    api
      .onRunStatus((payload) => {
        const updated = payload.run;
        if (updated.projectId === selectedProjectIdRef.current) {
          setRuns((prev) => {
            const exists = prev.some((r) => r.id === updated.id);
            if (exists) {
              return prev.map((r) => (r.id === updated.id ? updated : r));
            }
            return [updated, ...prev];
          });
        }
        refreshProjects();
        if (updated.status === "succeeded") {
          showToast("论文生成完成！可在「成果与 PDF」中预览或导出。");
        } else if (updated.status === "failed") {
          showToast(updated.errorMessage || "运行失败，请查看运行日志。", true);
        }
      })
      .then((fn) => {
        unlistenStatus = fn;
      });

    return () => {
      unlistenLog?.();
      unlistenStatus?.();
    };
  }, [refreshProjects, showToast]);

  const persistProject = useCallback(
    async (target: Project) => {
      setSaveState("saving");
      try {
        const saved = await api.saveProject({
          id: target.id,
          title: target.title,
          templateName: target.templateName,
          writerModel: target.writerModel,
          reflectionModel: target.reflectionModel,
          plottingModel: target.plottingModel,
          imageModel: target.imageModel,
          researchCutoff: target.researchCutoff,
          usePlotting: target.usePlotting,
          plottingMaxCriticRounds: target.plottingMaxCriticRounds,
          ideaMarkdown: target.ideaMarkdown,
          experimentalLogMarkdown: target.experimentalLogMarkdown,
        });
        setProjects((prev) =>
          prev.map((p) =>
            p.id === saved.id
              ? {
                  ...p,
                  title: saved.title,
                  templateName: saved.templateName,
                  writerModel: saved.writerModel,
                  usePlotting: saved.usePlotting,
                  updatedAt: saved.updatedAt,
                }
              : p
          )
        );
        setSaveState("saved");
        return saved;
      } catch (e) {
        setSaveState("dirty");
        showToast(`保存失败: ${e}`, true);
        throw e;
      }
    },
    [showToast]
  );

  function handleProjectChange(patch: Partial<Project>) {
    setProject((prev) => {
      if (!prev) return prev;
      const next = { ...prev, ...patch };
      latestProjectRef.current = next;
      setSaveState("dirty");

      if (saveTimerRef.current !== null) {
        window.clearTimeout(saveTimerRef.current);
      }
      saveTimerRef.current = window.setTimeout(() => {
        if (latestProjectRef.current?.id === next.id) {
          persistProject(latestProjectRef.current);
        }
      }, 500);

      return next;
    });
  }

  async function handleFlushSave() {
    if (saveTimerRef.current !== null) {
      window.clearTimeout(saveTimerRef.current);
      saveTimerRef.current = null;
    }
    if (latestProjectRef.current) {
      await persistProject(latestProjectRef.current);
    }
  }

  async function handleCreateProject() {
    await handleFlushSave();
    const defaultTpl = envStatus?.templates[0] ?? "cvpr2025";
    const created = await api.createProject("新研究论文项目", defaultTpl);
    await refreshProjects(created.id);
    setActiveTab("workspace");
    showToast("已创建新项目");
  }

  async function handleDeleteProject(id: string, title: string) {
    if (!window.confirm(`确定删除项目「${title}」及其 SQLite 运行日志吗？（不会删除已生成目录中的 PDF 文件）`)) {
      return;
    }
    await api.deleteProject(id);
    await refreshProjects();
    showToast("已删除项目");
  }

  async function handleAddFigure(sourcePath: string, caption: string) {
    if (!project) return;
    const fig = await api.addProjectFigure(project.id, sourcePath, caption);
    setProject((prev) => (prev ? { ...prev, figures: [...prev.figures, fig] } : prev));
    showToast(`已添加附件: ${fig.fileName}`);
  }

  async function handleUpdateFigureCaption(figureId: string, caption: string) {
    setProject((prev) =>
      prev
        ? {
            ...prev,
            figures: prev.figures.map((f) => (f.id === figureId ? { ...f, caption } : f)),
          }
        : prev
    );
    await api.updateProjectFigureCaption(figureId, caption);
  }

  async function handleDeleteFigure(figureId: string) {
    await api.deleteProjectFigure(figureId);
    setProject((prev) =>
      prev
        ? { ...prev, figures: prev.figures.filter((f) => f.id !== figureId) }
        : prev
    );
  }

  const activeRunForProject = useMemo(
    () => runs.find((r) => r.status === "running") ?? null,
    [runs]
  );

  const selectedRun = useMemo(
    () => runs.find((r) => r.id === selectedRunId) ?? runs[0] ?? null,
    [runs, selectedRunId]
  );

  const runningProjectIds = useMemo(() => {
    const s = new Set<string>();
    for (const r of runs) {
      if (r.status === "running") s.add(r.projectId);
    }
    return s;
  }, [runs]);

  async function handleStartRun() {
    if (!project) return;
    if (envStatus?.providersJsonError) {
      // Python cannot resolve any model with a broken providers.json.
      showToast(`providers.json 有误，运行会失败：${envStatus.providersJsonError}`, true);
      return;
    }
    await handleFlushSave();
    try {
      const started = await api.startRun(project.id);
      await refreshRunsForProject(project.id, started.id);
      setSelectedRunId(started.id);
      setLogs([]);
      setActiveTab("runs");
      showToast("PaperOrchestra 流水线已启动");
    } catch (e) {
      showToast(`${e}`, true);
    }
  }

  async function handleStopRun(runId: string) {
    try {
      await api.stopRun(runId);
      showToast("已发送停止信号");
    } catch (e) {
      showToast(`停止失败: ${e}`, true);
    }
  }

  async function handleResumeRun(runId: string) {
    await handleFlushSave();
    try {
      const resumed = await api.resumeRun(runId);
      if (project) await refreshRunsForProject(project.id, resumed.id);
      setSelectedRunId(resumed.id);
      setLogs([]);
      setActiveTab("runs");
      showToast("已从最近断点恢复继续执行 (Loop Resume)");
    } catch (e) {
      showToast(`断点续跑失败: ${e}`, true);
    }
  }

  async function handleApplyHandoff(handoff: WorkspaceHandoff) {
    await handleFlushSave();
    const templates = envStatus?.templates ?? [];
    const targetTpl = templates.includes(handoff.recommendedTemplate)
      ? handoff.recommendedTemplate
      : project?.templateName || templates[0] || "cvpr2025";
    const targetProject =
      project ??
      (await api.createProject(handoff.title || "科研工坊导入课题", targetTpl));
    const updated: Project = {
      ...targetProject,
      title: handoff.title || targetProject.title,
      templateName: targetTpl,
      ideaMarkdown: handoff.ideaMarkdown || targetProject.ideaMarkdown,
      experimentalLogMarkdown: handoff.experimentalLogMarkdown || targetProject.experimentalLogMarkdown,
    };
    const saved = await persistProject(updated);
    for (const figPath of handoff.figurePaths || []) {
      try {
        const fig = await api.addProjectFigure(saved.id, figPath, "Generated in Research Studio");
        saved.figures.push(fig);
      } catch {
        // Ignore duplicate or missing optional figures
      }
    }
    setProject(saved);
    setSelectedProjectId(saved.id);
    await refreshProjects(saved.id);
    setActiveTab("workspace");
    showToast("已一键导入课题思路、实验记录与图表至论文工作台！");
  }

  async function handleDeleteRun(runId: string) {
    if (!project) return;
    await api.deleteRun(runId);
    await refreshRunsForProject(project.id);
    await refreshProjects(project.id);
  }

  /** Settings, Studio and Library are cross-project pages (no project title box needed). */
  const projectHeader =
    project !== null && activeTab !== "settings" && activeTab !== "studio" && activeTab !== "library";

  const runningStageLabel = activeRunForProject
    ? STAGE_LABELS[activeRunForProject.currentStage] || activeRunForProject.currentStage || "准备中"
    : "";

  return (
    <div className="app-shell">
      <Sidebar
        activeTab={activeTab}
        onSelectTab={setActiveTab}
        projects={projects}
        selectedProjectId={selectedProjectId}
        onSelectProject={async (id) => {
          await handleFlushSave();
          setSelectedProjectId(id);
        }}
        onCreateProject={handleCreateProject}
        onDeleteProject={handleDeleteProject}
        envStatus={envStatus}
        runningProjectIds={runningProjectIds}
      />

      <main className="main-area">
        <header className="topbar">
          <div className="topbar-title-group">
            <div className="topbar-heading">
              <div className="topbar-eyebrow">
                {projectHeader && project ? (
                  <>
                    <span>{VIEW_LABELS[activeTab]}</span>
                    <span className="topbar-eyebrow-sep" aria-hidden="true">
                      /
                    </span>
                    <span className="mono">{project.templateName}</span>
                  </>
                ) : (
                  <span>PaperOrchestra Studio</span>
                )}
              </div>
              {projectHeader && project ? (
                <input
                  className="project-title-input"
                  value={project.title}
                  onChange={(e) => handleProjectChange({ title: e.target.value })}
                  placeholder="输入项目名称…"
                  aria-label="项目名称"
                />
              ) : (
                <div className="topbar-title">
                  {activeTab === "settings" || activeTab === "studio" || activeTab === "library"
                    ? VIEW_LABELS[activeTab]
                    : "论文写作工作台"}
                </div>
              )}
            </div>
          </div>

          <div className="topbar-actions">
            {!projectHeader && activeRunForProject && (
              <button
                type="button"
                className="run-indicator"
                onClick={() => setActiveTab("runs")}
                title="查看运行日志"
              >
                <Loader2 size={13} className="spin" />
                生成中 · {runningStageLabel}
              </button>
            )}
            {projectHeader && project && (
              <>
                <span
                  className={`save-state save-state-${saveState}`}
                  role="status"
                  aria-live="polite"
                >
                  {saveState === "saving" ? (
                    <Loader2 size={13} className="spin" />
                  ) : saveState === "dirty" ? (
                    <span className="dot dot-warn" />
                  ) : (
                    <Check size={13} />
                  )}
                  {saveState === "saving" ? "保存中…" : saveState === "dirty" ? "未保存" : "已保存"}
                </span>
                <button
                  type="button"
                  className="btn btn-ghost"
                  onClick={handleFlushSave}
                  disabled={saveState === "saving"}
                  title="立即保存到本地数据库"
                >
                  <Save size={14} strokeWidth={1.75} /> 保存
                </button>
                <span className="topbar-sep" aria-hidden="true" />
                {activeRunForProject ? (
                  <>
                    <button
                      type="button"
                      className="run-indicator"
                      onClick={() => setActiveTab("runs")}
                      title="查看运行日志"
                    >
                      <Loader2 size={13} className="spin" />
                      生成中 · {runningStageLabel}
                    </button>
                    {/* The run header on the runs page has its own stop button. */}
                    {!(activeTab === "runs" && selectedRun?.id === activeRunForProject.id) && (
                      <button
                        type="button"
                        className="btn btn-danger"
                        onClick={() => handleStopRun(activeRunForProject.id)}
                      >
                        <Square size={12} fill="currentColor" /> 停止生成
                      </button>
                    )}
                  </>
                ) : (
                  <button type="button" className="btn btn-primary" onClick={handleStartRun}>
                    <Play size={13} fill="currentColor" /> 启动生成
                  </button>
                )}
              </>
            )}
          </div>
        </header>

        <section className="view-scroll">
          <div style={{ display: activeTab === "studio" ? undefined : "none" }}>
            <ResearchStudioView onApplyHandoff={handleApplyHandoff} onToast={showToast} />
          </div>
          {activeTab === "studio" ? null : activeTab === "settings" ? (
            <SettingsView onEnvUpdated={refreshEnv} onToast={showToast} />
          ) : activeTab === "library" ? (
            <LibraryView activeProjectId={selectedProjectId} onToast={showToast} />
          ) : !project ? (
            <div className="card empty-state empty-state-hero">
              <div className="empty-state-mark" aria-hidden="true">
                <BrandMark size={44} />
              </div>
              <h2 className="empty-state-title">还没有论文项目</h2>
              <p>新建一个项目，填好研究思路和实验记录，就能从研究思路一路生成到 PDF。</p>
              <div className="empty-state-actions">
                <button type="button" className="btn btn-primary" onClick={handleCreateProject}>
                  <Plus size={15} /> 新建项目
                </button>
              </div>
            </div>
          ) : activeTab === "workspace" ? (
            <WorkspaceView
              project={project}
              templates={envStatus?.templates ?? []}
              envStatus={envStatus}
              onChange={handleProjectChange}
              onAddFigure={handleAddFigure}
              onUpdateFigureCaption={handleUpdateFigureCaption}
              onDeleteFigure={handleDeleteFigure}
              onEnvUpdated={refreshEnv}
              onToast={showToast}
            />
          ) : activeTab === "runs" ? (
            <RunsView
              runs={runs}
              selectedRunId={selectedRunId}
              onSelectRun={setSelectedRunId}
              logs={logs}
              onStopRun={handleStopRun}
              onResumeRun={handleResumeRun}
              onDeleteRun={handleDeleteRun}
              onOpenArtifacts={(runId) => {
                setSelectedRunId(runId);
                setActiveTab("artifacts");
              }}
              onExportLatex={handleExportLatex}
            />
          ) : (
            <ArtifactsView run={selectedRun} onExportLatex={handleExportLatex} onToast={showToast} />
          )}
        </section>
      </main>

      {toast && (
        <div
          key={toast.msg}
          className={`toast ${toast.isError ? "toast-error" : "toast-info"}`}
          role={toast.isError ? "alert" : "status"}
          aria-live={toast.isError ? "assertive" : "polite"}
        >
          <span className="toast-icon">
            {toast.isError ? <AlertCircle size={16} /> : <CheckCircle2 size={16} />}
          </span>
          <span className="toast-msg">{toast.msg}</span>
          <button
            type="button"
            className="toast-close"
            aria-label="关闭提示"
            onClick={() => setToast(null)}
          >
            <X size={13} />
          </button>
        </div>
      )}
    </div>
  );
}

export default App;
