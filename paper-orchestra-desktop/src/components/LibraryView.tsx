import {
  BookMarked,
  Check,
  Copy,
  ExternalLink,
  FileText,
  FolderOpen,
  Globe,
  Loader2,
  Network,
  Plus,
  Search,
  Trash2,
  Upload,
} from "lucide-react";
import { openUrl } from "@tauri-apps/plugin-opener";
import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import type { KnowledgeGraph, KnowledgeGraphNode, LibraryItem } from "../types";
import { DocumentArtifactView } from "./DocumentArtifactView";
import "./studio.css";

interface LibraryViewProps {
  activeProjectId: string | null;
  onToast: (msg: string, isError?: boolean) => void;
}

export function LibraryView({ activeProjectId, onToast }: LibraryViewProps) {
  const [items, setItems] = useState<LibraryItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [folderFilter, setFolderFilter] = useState<string>("all");
  const [searchText, setSearchText] = useState("");
  const [selectedId, setSelectedId] = useState<string>("");

  const [onlineQuery, setOnlineQuery] = useState("");
  const [onlineHits, setOnlineHits] = useState<Array<Partial<LibraryItem>>>([]);
  const [doiInput, setDoiInput] = useState("");
  const [busy, setBusy] = useState<"" | "search" | "pdf" | "doi" | "graph" | "export">("");

  const [graph, setGraph] = useState<KnowledgeGraph | null>(null);
  const [selectedNode, setSelectedNode] = useState<KnowledgeGraphNode | null>(null);
  const [editingNotes, setEditingNotes] = useState<boolean>(false);
  const [exportText, setExportText] = useState<{ format: string; text: string } | null>(null);

  const [notesDraft, setNotesDraft] = useState("");
  const [folderDraft, setFolderDraft] = useState("默认文献夹");

  async function refresh() {
    setLoading(true);
    try {
      const list = await api.listLibraryItems();
      setItems(list);
      if (!selectedId && list.length > 0) setSelectedId(list[0].id);
    } catch (e) {
      onToast(`加载文献库失败：${e}`, true);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const selectedItem = useMemo(
    () => items.find((i) => i.id === selectedId) ?? items[0] ?? null,
    [items, selectedId]
  );

  useEffect(() => {
    setNotesDraft(selectedItem?.aiNotes || "");
    setFolderDraft(selectedItem?.folder || "默认文献夹");
  }, [selectedItem?.id]);

  const folders = useMemo(() => {
    const s = new Set<string>();
    items.forEach((i) => {
      if (i.folder) s.add(i.folder);
    });
    return [...s].sort();
  }, [items]);

  const filteredItems = useMemo(() => {
    const q = searchText.trim().toLowerCase();
    return items.filter((i) => {
      if (folderFilter === "__project__" && i.projectId !== activeProjectId) return false;
      if (folderFilter !== "all" && folderFilter !== "__project__" && i.folder !== folderFilter) return false;
      if (q) {
        const hay = `${i.title} ${i.authors} ${i.venue} ${i.doi} ${i.tags.join(" ")}`.toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
  }, [items, folderFilter, activeProjectId, searchText]);

  async function savePartialItem(raw: Partial<LibraryItem>, customFolder?: string) {
    const saved = await api.saveLibraryItem({
      projectId: activeProjectId,
      folder: customFolder || (folderFilter !== "all" && folderFilter !== "__project__" ? folderFilter : "默认文献夹"),
      title: raw.title || "Untitled",
      authors: raw.authors || "",
      year: raw.year ?? null,
      venue: raw.venue || "",
      doi: raw.doi || "",
      arxivId: raw.arxivId || "",
      url: raw.url || "",
      pdfPath: raw.pdfPath || "",
      bibtex: raw.bibtex || "",
      gb7714: raw.gb7714 || "",
      abstractText: raw.abstractText || "",
      aiNotes: raw.aiNotes || "",
      keyClaims: raw.keyClaims || [],
      tags: raw.tags || [],
      readStatus: raw.readStatus || "abstract_only",
    });
    await refresh();
    setSelectedId(saved.id);
    return saved;
  }

  async function handleSearchOnline() {
    if (!onlineQuery.trim()) return;
    setBusy("search");
    try {
      const res = await api.searchLibraryOnline(onlineQuery.trim(), 12, true);
      setOnlineHits(res.items || []);
      onToast(`已从 8 大学术检索源获取 ${res.items?.length ?? 0} 条真实文献`);
    } catch (e) {
      onToast(`在线检索失败：${e}`, true);
    } finally {
      setBusy("");
    }
  }

  async function handleImportPdf() {
    const pdf = await api.pickFile([{ name: "PDF 文献", extensions: ["pdf"] }]);
    if (!pdf) return;
    setBusy("pdf");
    try {
      const res = await api.importLibraryPdf(pdf);
      await savePartialItem(res.item);
      onToast(`已完成 PDF 精读并归档：${res.item.title || "文献"}`);
    } catch (e) {
      onToast(`导入 PDF 失败：${e}`, true);
    } finally {
      setBusy("");
    }
  }

  async function handleImportDoi() {
    if (!doiInput.trim()) return;
    setBusy("doi");
    try {
      const res = await api.importLibraryDoi(doiInput.trim());
      await savePartialItem(res.item);
      setDoiInput("");
      onToast(`已按 DOI 入库：${res.item.title}`);
    } catch (e) {
      onToast(`DOI 查询失败：${e}`, true);
    } finally {
      setBusy("");
    }
  }

  async function handleBuildGraph() {
    const scope = filteredItems.length > 0 ? filteredItems : items;
    if (scope.length === 0) {
      onToast("请先入库至少 1 篇文献。", true);
      return;
    }
    setBusy("graph");
    try {
      const res = await api.buildLibraryGraph(scope);
      setGraph(res.graph);
      setSelectedNode(null);
      onToast("知识图谱、阅读路线与文献对比矩阵已生成");
    } catch (e) {
      onToast(`构建知识图谱失败：${e}`, true);
    } finally {
      setBusy("");
    }
  }

  async function handleExport(fmt: "bibtex" | "gb7714") {
    const scope = filteredItems.length > 0 ? filteredItems : items;
    if (scope.length === 0) return;
    setBusy("export");
    try {
      const res = await api.exportLibraryBib(scope, fmt);
      setExportText({ format: fmt, text: res.text });
    } catch (e) {
      onToast(`导出失败：${e}`, true);
    } finally {
      setBusy("");
    }
  }

  async function handleSaveEdits() {
    if (!selectedItem) return;
    await api.saveLibraryItem({
      id: selectedItem.id,
      projectId: selectedItem.projectId,
      folder: folderDraft.trim() || "默认文献夹",
      title: selectedItem.title,
      authors: selectedItem.authors,
      year: selectedItem.year,
      venue: selectedItem.venue,
      doi: selectedItem.doi,
      arxivId: selectedItem.arxivId,
      url: selectedItem.url,
      pdfPath: selectedItem.pdfPath,
      bibtex: selectedItem.bibtex,
      gb7714: selectedItem.gb7714,
      abstractText: selectedItem.abstractText,
      aiNotes: notesDraft,
      keyClaims: selectedItem.keyClaims,
      tags: selectedItem.tags,
      readStatus: selectedItem.readStatus,
    });
    await refresh();
    onToast("已保存文献笔记与分组");
  }

  const graphPositions = useMemo(() => {
    if (!graph) return new Map<string, { x: number; y: number }>();
    const map = new Map<string, { x: number; y: number }>();
    const concepts = graph.nodes.filter((n) => n.kind === "concept" || n.kind === "method");
    const papers = graph.nodes.filter((n) => !concepts.includes(n));
    const width = 760;
    const cx = width / 2;
    const cy = 185;
    papers.forEach((n, idx) => {
      const angle = (idx / Math.max(1, papers.length)) * Math.PI * 2 - Math.PI / 2;
      map.set(n.id, { x: cx + Math.cos(angle) * 260, y: cy + Math.sin(angle) * 140 });
    });
    concepts.forEach((n, idx) => {
      const angle = (idx / Math.max(1, concepts.length)) * Math.PI * 2;
      map.set(n.id, { x: cx + Math.cos(angle) * 105, y: cy + Math.sin(angle) * 65 });
    });
    return map;
  }, [graph]);

  return (
    <div className="stack">
      <div className="card stack">
        <div className="row" style={{ flexWrap: "wrap", gap: 8 }}>
          <div className="row" style={{ flex: 1, minWidth: 260 }}>
            <input
              className="input"
              style={{ flex: 1 }}
              value={onlineQuery}
              onChange={(e) => setOnlineQuery(e.target.value)}
              placeholder="输入关键词在线检索（自动双语扩词并查询 8 大学术源）…"
              onKeyDown={(e) => e.key === "Enter" && void handleSearchOnline()}
            />
            <button type="button" className="btn btn-primary btn-sm" disabled={busy !== ""} onClick={() => void handleSearchOnline()}>
              {busy === "search" ? <Loader2 size={13} className="spin" /> : <Globe size={13} />} 在线检索入库
            </button>
          </div>
          <div className="row">
            <input
              className="input st-mono"
              style={{ width: 170 }}
              value={doiInput}
              onChange={(e) => setDoiInput(e.target.value)}
              placeholder="粘贴 DOI (10.xxxx/...)"
            />
            <button type="button" className="btn btn-sm" disabled={busy !== "" || !doiInput.trim()} onClick={() => void handleImportDoi()}>
              {busy === "doi" ? <Loader2 size={13} className="spin" /> : <Plus size={13} />} DOI 入库
            </button>
          </div>
          <button type="button" className="btn btn-sm" disabled={busy !== ""} onClick={() => void handleImportPdf()}>
            {busy === "pdf" ? <Loader2 size={13} className="spin" /> : <Upload size={13} />} 导入 PDF 自动精读
          </button>
          <button type="button" className="btn btn-primary btn-sm" disabled={busy !== "" || items.length === 0} onClick={() => void handleBuildGraph()}>
            {busy === "graph" ? <Loader2 size={13} className="spin" /> : <Network size={13} />} 生成知识图谱与对比矩阵
          </button>
          <button type="button" className="btn btn-sm" disabled={busy !== "" || items.length === 0} onClick={() => void handleExport("gb7714")}>
            导出 GB/T 7714
          </button>
          <button type="button" className="btn btn-sm" disabled={busy !== "" || items.length === 0} onClick={() => void handleExport("bibtex")}>
            导出 .bib
          </button>
        </div>

        {onlineHits.length > 0 && (
          <div className="callout stack" style={{ maxHeight: 220, overflowY: "auto" }}>
            <div className="row" style={{ justifyContent: "space-between" }}>
              <strong>检索结果 ({onlineHits.length}) — 点击「加入文献库」</strong>
              <button type="button" className="btn btn-ghost btn-sm" onClick={() => setOnlineHits([])}>
                收起
              </button>
            </div>
            {onlineHits.map((h, i) => (
              <div key={i} className="rs-file-pill" style={{ gap: 10 }}>
                <span style={{ flex: 1 }}>
                  <strong>{h.title}</strong> · {h.authors} ({h.year || "n.d."}) {h.doi ? `DOI:${h.doi}` : ""}
                </span>
                <button
                  type="button"
                  className="btn btn-primary btn-sm"
                  onClick={async () => {
                    await savePartialItem(h);
                    onToast(`已入库：${h.title}`);
                  }}
                >
                  加入文献库
                </button>
              </div>
            ))}
          </div>
        )}

        {exportText && (
          <div className="callout stack">
            <div className="row" style={{ justifyContent: "space-between" }}>
              <strong>导出结果 ({exportText.format.toUpperCase()})</strong>
              <div className="row">
                <button
                  type="button"
                  className="btn btn-sm"
                  onClick={() => {
                    void navigator.clipboard.writeText(exportText.text);
                    onToast("已复制到剪贴板");
                  }}
                >
                  <Copy size={12} /> 复制
                </button>
                <button type="button" className="btn btn-ghost btn-sm" onClick={() => setExportText(null)}>
                  关闭
                </button>
              </div>
            </div>
            <pre className="le-diff-pre">{exportText.text}</pre>
          </div>
        )}
      </div>

      {graph && (
        <div className="card stack">
          <div className="row" style={{ justifyContent: "space-between" }}>
            <strong>文献知识图谱 · 阅读路线 · 证据对比矩阵</strong>
            <button type="button" className="btn btn-ghost btn-sm" onClick={() => setGraph(null)}>
              收起图谱
            </button>
          </div>

          <div className="kg-svg-wrap">
            <svg viewBox="0 0 760 370" style={{ width: "100%", height: 340, display: "block" }}>
              {graph.edges.map((e, idx) => {
                const a = graphPositions.get(e.source);
                const b = graphPositions.get(e.target);
                if (!a || !b) return null;
                return (
                  <g key={idx}>
                    <line x1={a.x} y1={a.y} x2={b.x} y2={b.y} stroke="var(--border-strong)" strokeWidth={1.3} opacity={0.8} />
                  </g>
                );
              })}
              {graph.nodes.map((n) => {
                const pos = graphPositions.get(n.id);
                if (!pos) return null;
                const fill =
                  n.role === "survey"
                    ? "#0284c7"
                    : n.role === "seminal"
                      ? "#059669"
                      : n.kind === "concept"
                        ? "#d97706"
                        : "#2b4a70";
                const isSel = selectedNode?.id === n.id;
                return (
                  <g
                    key={n.id}
                    style={{ cursor: "pointer" }}
                    onClick={() => {
                      setSelectedNode(n);
                      if (n.itemId) setSelectedId(n.itemId);
                    }}
                  >
                    <circle cx={pos.x} cy={pos.y} r={n.kind === "concept" ? 13 : 16} fill={fill} stroke={isSel ? "#dc2626" : "#ffffff"} strokeWidth={isSel ? 3 : 2} />
                    <text
                      x={pos.x}
                      y={pos.y + (n.kind === "concept" ? 26 : 30)}
                      textAnchor="middle"
                      fontSize="11"
                      fill="var(--text-primary)"
                    >
                      {n.label}
                    </text>
                  </g>
                );
              })}
            </svg>
          </div>
          {selectedNode && (
            <div className="hint">
              已选中节点：<strong>{selectedNode.fullTitle || selectedNode.label}</strong>（{selectedNode.role || selectedNode.kind}
              {selectedNode.year ? ` · ${selectedNode.year}` : ""}）
            </div>
          )}

          {graph.readingOrder?.length > 0 && (
            <div>
              <strong style={{ fontSize: "var(--fs-sm)" }}>推荐阅读顺序（综述立框 → 经典奠基 → 近期前沿）</strong>
              <div className="stack" style={{ marginTop: 6 }}>
                {graph.readingOrder.map((r) => (
                  <div key={r.step} className="rs-file-pill">
                    <span>
                      <span className="badge badge-neutral" style={{ marginRight: 6 }}>
                        第 {r.step} 篇 · {r.stage}
                      </span>
                      <strong>{r.title}</strong> {r.year ? `(${r.year})` : ""} — {r.purpose}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {graph.comparisonMatrix?.rows?.length > 0 && (
            <div className="data-table-wrap">
              <table className="data-table">
                <thead>
                  <tr>
                    {graph.comparisonMatrix.columns.map((c) => (
                      <th key={c}>{c}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {graph.comparisonMatrix.rows.map((row, ri) => (
                    <tr key={ri}>
                      {Object.values(row).map((val, vi) => (
                        <td key={vi}>{val}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      <div className="lb-layout">
        <aside className="card stack">
          <div className="row" style={{ gap: 6 }}>
            <Search size={13} />
            <input
              className="input"
              value={searchText}
              onChange={(e) => setSearchText(e.target.value)}
              placeholder="筛选库内文献…"
            />
          </div>
          <div className="stack">
            <button
              type="button"
              className={`cs-tab ${folderFilter === "all" ? "active" : ""}`}
              onClick={() => setFolderFilter("all")}
            >
              <BookMarked size={13} /> 全部文献 ({items.length})
            </button>
            {activeProjectId && (
              <button
                type="button"
                className={`cs-tab ${folderFilter === "__project__" ? "active" : ""}`}
                onClick={() => setFolderFilter("__project__")}
              >
                <FolderOpen size={13} /> 关联当前项目 ({items.filter((i) => i.projectId === activeProjectId).length})
              </button>
            )}
            {folders.map((f) => (
              <button
                key={f}
                type="button"
                className={`cs-tab ${folderFilter === f ? "active" : ""}`}
                onClick={() => setFolderFilter(f)}
              >
                <FolderOpen size={13} /> {f} ({items.filter((i) => i.folder === f).length})
              </button>
            ))}
          </div>
        </aside>

        <div className="lb-main-split">
          <div className="card stack" style={{ maxHeight: 620, overflowY: "auto" }}>
            {loading ? (
              <div className="hint">
                <Loader2 size={14} className="spin" /> 加载中…
              </div>
            ) : filteredItems.length === 0 ? (
              <div className="empty-state">
                <FileText size={20} />
                <p>当前文献夹暂无文献。请使用上方「在线检索」「DOI 入库」或「导入 PDF 自动精读」。</p>
              </div>
            ) : (
              filteredItems.map((it) => (
                <button
                  key={it.id}
                  type="button"
                  className={`lb-item ${it.id === selectedItem?.id ? "active" : ""}`}
                  onClick={() => setSelectedId(it.id)}
                >
                  <div className="row" style={{ justifyContent: "space-between", width: "100%" }}>
                    <span className="badge badge-neutral">
                      {it.readStatus === "full_read" ? "全文精读" : it.readStatus === "abstract_only" ? "仅摘要" : "待读"}
                    </span>
                    <span className="hint">{it.year || "n.d."}</span>
                  </div>
                  <strong>{it.title}</strong>
                  <span className="hint">{it.authors || "未知作者"}</span>
                </button>
              ))
            )}
          </div>

          <div className="card stack">
            {!selectedItem ? (
              <div className="empty-state">
                <p>选择左侧一篇文献查看精读笔记、引用格式与证据溯源列表。</p>
              </div>
            ) : (
              <>
                <div className="row" style={{ justifyContent: "space-between", alignItems: "flex-start" }}>
                  <h3 style={{ margin: 0, fontSize: "var(--fs-lg)" }}>{selectedItem.title}</h3>
                  <button
                    type="button"
                    className="btn btn-ghost btn-sm"
                    onClick={async () => {
                      await api.deleteLibraryItem(selectedItem.id);
                      await refresh();
                      onToast("已从文献库移除");
                    }}
                  >
                    <Trash2 size={13} />
                  </button>
                </div>
                <div className="hint">
                  {selectedItem.authors} · {selectedItem.venue} {selectedItem.year ? `(${selectedItem.year})` : ""}
                </div>
                <div className="row" style={{ flexWrap: "wrap", gap: 6 }}>
                  {selectedItem.url && (
                    <button type="button" className="btn btn-sm" onClick={() => void openUrl(selectedItem.url)}>
                      <ExternalLink size={12} /> 打开来源页面
                    </button>
                  )}
                  {selectedItem.pdfPath && (
                    <button type="button" className="btn btn-sm" onClick={() => void api.openPathInSystem(selectedItem.pdfPath)}>
                      <FileText size={12} /> 打开本地 PDF
                    </button>
                  )}
                  {selectedItem.gb7714 && (
                    <button
                      type="button"
                      className="btn btn-sm"
                      onClick={() => {
                        void navigator.clipboard.writeText(selectedItem.gb7714);
                        onToast("已复制 GB/T 7714 引文");
                      }}
                    >
                      <Copy size={12} /> 复制 GB/T 7714
                    </button>
                  )}
                </div>

                {selectedItem.abstractText && (
                  <div className="callout" style={{ fontSize: "var(--fs-xs)" }}>
                    <strong>摘要：</strong>
                    {selectedItem.abstractText}
                  </div>
                )}

                {selectedItem.keyClaims.length > 0 && (
                  <div className="stack">
                    <strong style={{ fontSize: "var(--fs-sm)" }}>结论溯源条目 ({selectedItem.keyClaims.length})</strong>
                    {selectedItem.keyClaims.map((c, idx) => (
                      <div key={idx} className="rs-file-pill" style={{ flexDirection: "column", alignItems: "flex-start" }}>
                        <strong>{c.claim}</strong>
                        <span className="hint">
                          {c.location ? `[${c.location}] ` : ""}
                          {c.evidence}
                        </span>
                      </div>
                    ))}
                  </div>
                )}

                <div style={{ display: "grid", gridTemplateColumns: "180px auto", gap: 8, alignItems: "end" }}>
                  <div className="field">
                    <label>所属文献夹</label>
                    <input className="input" value={folderDraft} onChange={(e) => setFolderDraft(e.target.value)} />
                  </div>
                  <button type="button" className="btn btn-primary btn-sm" onClick={() => void handleSaveEdits()}>
                    <Check size={12} /> 保存笔记与分组
                  </button>
                </div>
                <div className="stack">
                  <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
                    <label style={{ fontSize: "var(--fs-sm)", fontWeight: 500 }}>精读笔记与结构化报告</label>
                    <button
                      type="button"
                      className="btn btn-sm"
                      onClick={() => setEditingNotes((v) => !v)}
                    >
                      {editingNotes || !notesDraft.trim() ? "切换 Artifacts 排版阅读" : "编辑笔记源码"}
                    </button>
                  </div>
                  {!editingNotes && notesDraft.trim() ? (
                    <DocumentArtifactView
                      content={notesDraft}
                      title={selectedItem.title}
                      defaultFilename={`${selectedItem.title}_精读笔记`}
                      maxHeight={420}
                      onToast={onToast}
                    />
                  ) : (
                    <textarea
                      className="textarea"
                      rows={6}
                      value={notesDraft}
                      onChange={(e) => setNotesDraft(e.target.value)}
                    />
                  )}
                </div>
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
