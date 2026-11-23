import {
  BookOpen,
  ChevronRight,
  PenLine,
  Plus,
  ScrollText,
  Search,
  Settings2,
  Trash2,
  X,
} from "lucide-react";
import { useState, type ReactNode } from "react";
import type { EnvironmentStatus, NavTab, ProjectSummary } from "../types";
import { BrandMark } from "./BrandMark";

interface SidebarProps {
  activeTab: NavTab;
  onSelectTab: (tab: NavTab) => void;
  projects: ProjectSummary[];
  selectedProjectId: string | null;
  onSelectProject: (id: string) => void;
  onCreateProject: () => void;
  onDeleteProject: (id: string, title: string) => void;
  envStatus: EnvironmentStatus | null;
  runningProjectIds: Set<string>;
}

const NAV_ITEMS: Array<{ id: NavTab; label: string; icon: ReactNode }> = [
  { id: "workspace", label: "工作台", icon: <PenLine size={16} strokeWidth={1.75} /> },
  { id: "runs", label: "运行与日志", icon: <ScrollText size={16} strokeWidth={1.75} /> },
  { id: "artifacts", label: "成果与 PDF", icon: <BookOpen size={16} strokeWidth={1.75} /> },
  { id: "settings", label: "环境与密钥", icon: <Settings2 size={16} strokeWidth={1.75} /> },
];

export function Sidebar({
  activeTab,
  onSelectTab,
  projects,
  selectedProjectId,
  onSelectProject,
  onCreateProject,
  onDeleteProject,
  envStatus,
  runningProjectIds,
}: SidebarProps) {
  const [query, setQuery] = useState("");

  const filtered = projects.filter((p) =>
    p.title.toLowerCase().includes(query.trim().toLowerCase())
  );

  const providerCount = envStatus?.configuredProviders?.length ?? 0;
  /** A broken providers.json makes every run fail, whatever keys are set. */
  const providersJsonBroken = Boolean(envStatus?.providersJsonError);
  const hasModelKey =
    !providersJsonBroken &&
    Boolean(
      envStatus?.hasAnthropicKey ||
        envStatus?.hasDeepseekKey ||
        envStatus?.hasGeminiKey ||
        envStatus?.hasVertexAi ||
        envStatus?.hasOpenaiKey ||
        providerCount > 0
    );
  const scholarCount = envStatus?.scholarSources?.length;

  const selectedIsRunning = Boolean(
    selectedProjectId && runningProjectIds.has(selectedProjectId)
  );

  const checking = envStatus === null;
  const cliReady = Boolean(envStatus?.cliScriptExists && envStatus?.pythonExists);
  const latexReady = Boolean(envStatus?.pdflatexAvailable && envStatus?.bibtexAvailable);

  return (
    <aside className="sidebar" aria-label="导航与项目">
      <div className="sidebar-brand">
        <div className="brand-title-wrap">
          <div className="brand-badge" aria-hidden="true">
            <BrandMark size={22} />
          </div>
          <div className="brand-text">
            <div className="brand-title">PaperOrchestra</div>
            <div className="brand-subtitle">Studio · 论文写作工作台</div>
          </div>
        </div>
      </div>

      <nav className="sidebar-nav" aria-label="主导航">
        {NAV_ITEMS.map((item) => {
          const isActive = activeTab === item.id;
          return (
            <button
              key={item.id}
              type="button"
              className={`nav-btn ${isActive ? "active" : ""}`}
              aria-current={isActive ? "page" : undefined}
              onClick={() => onSelectTab(item.id)}
            >
              <span className="nav-btn-left">
                {item.icon}
                <span>{item.label}</span>
              </span>
              {item.id === "runs" && selectedIsRunning && (
                <span className="nav-live" title="当前项目正在生成" />
              )}
            </button>
          );
        })}
      </nav>

      <div className="sidebar-projects-header">
        <span className="sidebar-section-label">
          项目
          <span className="sidebar-count">{projects.length}</span>
        </span>
        <div className="sidebar-project-actions">
          <button
            type="button"
            className="icon-btn-dark is-primary"
            title="新建项目"
            onClick={onCreateProject}
          >
            <Plus size={13} />
            新建
          </button>
        </div>
      </div>

      <div className="sidebar-search">
        <div className="sidebar-search-box">
          <Search size={13} className="sidebar-search-icon" aria-hidden="true" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="搜索项目"
            aria-label="搜索项目"
          />
          {query && (
            <button
              type="button"
              className="sidebar-search-clear"
              aria-label="清除搜索"
              onClick={() => setQuery("")}
            >
              <X size={12} />
            </button>
          )}
        </div>
      </div>

      <div className="project-list" role="list">
        {filtered.length === 0 ? (
          <div className="project-list-empty">
            {projects.length === 0 ? "还没有项目，点击「新建」开始" : "没有匹配的项目"}
          </div>
        ) : (
          filtered.map((p) => {
            const isActive = p.id === selectedProjectId;
            const isRunning = runningProjectIds.has(p.id) || p.lastRunStatus === "running";
            return (
              <div
                key={p.id}
                role="listitem"
                className={`project-item ${isActive ? "active" : ""}`}
              >
                <button
                  type="button"
                  className="project-item-main"
                  aria-current={isActive ? "true" : undefined}
                  onClick={() => onSelectProject(p.id)}
                >
                  <span className="project-item-title">{p.title || "未命名项目"}</span>
                  <span className="project-item-meta">
                    <span className="project-meta-text">
                      {p.templateName} · {p.runCount} 次运行
                    </span>
                    {isRunning ? (
                      <span className="project-status is-running">
                        <span className="dot dot-live" />
                        运行中
                      </span>
                    ) : p.lastRunStatus === "succeeded" ? (
                      <span className="project-status is-succeeded">已完成</span>
                    ) : p.lastRunStatus === "failed" ? (
                      <span className="project-status is-failed">失败</span>
                    ) : null}
                  </span>
                </button>
                <button
                  type="button"
                  className="project-delete-btn"
                  title="删除项目"
                  aria-label={`删除项目「${p.title}」`}
                  onClick={(e) => {
                    e.stopPropagation();
                    onDeleteProject(p.id, p.title);
                  }}
                >
                  <Trash2 size={13} strokeWidth={1.75} />
                </button>
              </div>
            );
          })
        )}
      </div>

      <div className="sidebar-footer">
        <div className="sidebar-footer-head">
          <span className="sidebar-section-label">运行环境</span>
          <button
            type="button"
            className="sidebar-footer-link"
            onClick={() => onSelectTab("settings")}
          >
            详情
            <ChevronRight size={12} />
          </button>
        </div>
        <div className="health-row">
          <span className="health-label">
            <span className={`dot ${checking ? "dot-idle" : cliReady ? "dot-ok" : "dot-err"}`} />
            Python 与 CLI
          </span>
          <span className="health-value">
            {checking ? "检测中…" : envStatus?.pythonExists ? "已就绪" : "未找到"}
          </span>
        </div>
        <div className="health-row">
          <span className="health-label">
            <span className={`dot ${checking ? "dot-idle" : latexReady ? "dot-ok" : "dot-warn"}`} />
            LaTeX 编译
          </span>
          <span className="health-value">
            {checking
              ? "检测中…"
              : !envStatus?.pdflatexAvailable
              ? "缺失"
              : !envStatus?.bibtexAvailable
              ? "缺 bibtex"
              : "可用"}
          </span>
        </div>
        <div className="health-row">
          <span className="health-label">
            <span
              className={`dot ${
                checking ? "dot-idle" : providersJsonBroken ? "dot-err" : hasModelKey ? "dot-ok" : "dot-warn"
              }`}
            />
            模型密钥
          </span>
          <span className="health-value">
            {checking
              ? "检测中…"
              : providersJsonBroken
              ? "providers.json 有误"
              : !hasModelKey
              ? "待填写"
              : providerCount > 0
              ? `${providerCount} 个服务商`
              : "已配置"}
          </span>
        </div>
        {scholarCount !== undefined && (
          <div className="health-row">
            <span className="health-label">
              <span className={`dot ${scholarCount > 0 ? "dot-ok" : "dot-warn"}`} />
              文献检索
            </span>
            <span className="health-value">
              {scholarCount > 0 ? `${scholarCount} 个来源` : "未启用"}
            </span>
          </div>
        )}
      </div>
    </aside>
  );
}
