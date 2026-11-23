import {
  Activity,
  Bookmark,
  Check,
  Cpu,
  Download,
  Loader2,
  Plus,
  RefreshCw,
  Terminal,
  Trash2,
  Upload,
} from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../api";
import type {
  ResearchMemory,
  ResearchPreferences,
  SshExecResult,
  SshHost,
  SshTestResult,
} from "../types";
import "./studio.css";

interface ComputeAndMemoryProps {
  onToast: (msg: string, isError?: boolean) => void;
}

export function ResearchPreferencesSection({ onToast }: ComputeAndMemoryProps) {
  const [prefs, setPrefs] = useState<ResearchPreferences>({
    outputLanguage: "zh",
    citationStyle: "GB/T 7714",
    deliveryFormat: "latex",
    customRules: "",
  });
  const [memories, setMemories] = useState<ResearchMemory[]>([]);
  const [savingPrefs, setSavingPrefs] = useState(false);

  const [memCat, setMemCat] = useState("direction");
  const [memTitle, setMemTitle] = useState("");
  const [memContent, setMemContent] = useState("");
  const [savingMem, setSavingMem] = useState(false);

  async function refresh() {
    try {
      const [p, m] = await Promise.all([api.getResearchPreferences(), api.listResearchMemories()]);
      setPrefs(p);
      setMemories(m);
    } catch (e) {
      onToast(`加载研究记忆失败：${e}`, true);
    }
  }

  useEffect(() => {
    void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleSavePrefs() {
    setSavingPrefs(true);
    try {
      const saved = await api.saveResearchPreferences(prefs);
      setPrefs(saved);
      onToast("全局研究偏好已保存，将自动注入后续论文流水线与 SOP");
    } catch (e) {
      onToast(`保存偏好失败：${e}`, true);
    } finally {
      setSavingPrefs(false);
    }
  }

  async function handleAddMemory() {
    if (!memTitle.trim() || !memContent.trim()) {
      onToast("请填写记忆标题和详细内容。", true);
      return;
    }
    setSavingMem(true);
    try {
      await api.saveResearchMemory({
        category: memCat,
        title: memTitle.trim(),
        content: memContent.trim(),
        enabled: true,
      });
      setMemTitle("");
      setMemContent("");
      await refresh();
      onToast("已添加长期研究记忆");
    } catch (e) {
      onToast(`添加失败：${e}`, true);
    } finally {
      setSavingMem(false);
    }
  }

  async function toggleMemory(m: ResearchMemory) {
    try {
      await api.saveResearchMemory({
        id: m.id,
        category: m.category,
        title: m.title,
        content: m.content,
        enabled: !m.enabled,
      });
      await refresh();
    } catch (e) {
      onToast(`更新失败：${e}`, true);
    }
  }

  return (
    <div className="stack">
      <div className="card st-card">
        <div className="row" style={{ justifyContent: "space-between", marginBottom: 10 }}>
          <strong>全局交付偏好与学术规范（ClawsGO Persistent Rules）</strong>
          <button type="button" className="btn btn-primary btn-sm" onClick={handleSavePrefs} disabled={savingPrefs}>
            {savingPrefs ? <Loader2 size={13} className="spin" /> : <Check size={13} />} 保存偏好
          </button>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: 10 }}>
          <div className="field">
            <label>默认输出语言</label>
            <select
              className="select"
              value={prefs.outputLanguage}
              onChange={(e) => setPrefs((p) => ({ ...p, outputLanguage: e.target.value }))}
            >
              <option value="zh">中文为主 (学术表达)</option>
              <option value="en">英文为主 (English Academic)</option>
              <option value="bilingual">中英双语（如计算机学报双摘要）</option>
            </select>
          </div>
          <div className="field">
            <label>默认引文格式</label>
            <select
              className="select"
              value={prefs.citationStyle}
              onChange={(e) => setPrefs((p) => ({ ...p, citationStyle: e.target.value }))}
            >
              <option value="GB/T 7714">国标 GB/T 7714-2015 顺序编码</option>
              <option value="Nature">Nature 上标顺序编码</option>
              <option value="IEEE">IEEE 编号</option>
              <option value="APA">APA 作者-年份</option>
            </select>
          </div>
          <div className="field">
            <label>默认交付载体</label>
            <select
              className="select"
              value={prefs.deliveryFormat}
              onChange={(e) => setPrefs((p) => ({ ...p, deliveryFormat: e.target.value }))}
            >
              <option value="latex">LaTeX 源码 + 可编译 PDF</option>
              <option value="markdown">结构化 Markdown 报告</option>
            </select>
          </div>
        </div>
        <div className="field" style={{ marginTop: 10 }}>
          <label>长期自定义科研规范（自动注入每次论文写作与 SOP 任务）</label>
          <textarea
            className="textarea"
            rows={3}
            value={prefs.customRules}
            onChange={(e) => setPrefs((p) => ({ ...p, customRules: e.target.value }))}
            placeholder="例如：必须对所有表格单元格居中；所有结论标注出处位置；计量单位统一 SI；不使用生成式 AI 伪造实验图。"
          />
        </div>
      </div>

      <div className="card st-card">
        <div className="row" style={{ marginBottom: 8 }}>
          <Bookmark size={15} />
          <strong>长期研究记忆库（方向 · 实验约束 · 审稿教训）</strong>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "140px 180px minmax(0, 1fr) auto", gap: 8, alignItems: "end" }}>
          <div className="field">
            <label>分类</label>
            <select className="select" value={memCat} onChange={(e) => setMemCat(e.target.value)}>
              <option value="direction">研究方向</option>
              <option value="equipment">设备与数据集</option>
              <option value="style">写作与审稿偏好</option>
              <option value="project">进行中课题</option>
            </select>
          </div>
          <div className="field">
            <label>标题</label>
            <input
              className="input"
              value={memTitle}
              onChange={(e) => setMemTitle(e.target.value)}
              placeholder="例如：课题组 DFT 口径"
            />
          </div>
          <div className="field">
            <label>记忆正文</label>
            <input
              className="input"
              value={memContent}
              onChange={(e) => setMemContent(e.target.value)}
              placeholder="例如：统一使用 B3LYP/6-31G(d) 与 SMD 溶剂模型，不编造基线结果"
            />
          </div>
          <button type="button" className="btn btn-primary btn-sm" onClick={handleAddMemory} disabled={savingMem}>
            <Plus size={13} /> 添加记忆
          </button>
        </div>

        <div className="stack" style={{ marginTop: 12 }}>
          {memories.length === 0 ? (
            <p className="hint">还没有保存的研究记忆。</p>
          ) : (
            memories.map((m) => (
              <div
                key={m.id}
                className="rs-file-pill"
                style={{ padding: "8px 10px", opacity: m.enabled ? 1 : 0.6 }}
              >
                <label className="row" style={{ flex: 1, cursor: "pointer" }}>
                  <input type="checkbox" checked={m.enabled} onChange={() => void toggleMemory(m)} />
                  <span className="badge badge-neutral">{m.category}</span>
                  <strong>{m.title}：</strong>
                  <span>{m.content}</span>
                </label>
                <button
                  type="button"
                  className="btn btn-ghost btn-sm"
                  title="删除"
                  onClick={async () => {
                    await api.deleteResearchMemory(m.id);
                    await refresh();
                  }}
                >
                  <Trash2 size={13} />
                </button>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}

export function SshComputeHostsSection({ onToast }: ComputeAndMemoryProps) {
  const [hosts, setHosts] = useState<SshHost[]>([]);
  const [selectedId, setSelectedId] = useState<string>("");
  const [alias, setAlias] = useState("");
  const [hostAddr, setHostAddr] = useState("");
  const [port, setPort] = useState("22");
  const [username, setUsername] = useState("root");
  const [keyPath, setKeyPath] = useState("~/.ssh/id_ed25519");
  const [privateKeyPem, setPrivateKeyPem] = useState("");
  const [remoteDir, setRemoteDir] = useState("/root");
  const [saving, setSaving] = useState(false);

  const [testingId, setTestingId] = useState("");
  const [testResult, setTestResult] = useState<Record<string, SshTestResult>>({});
  const [cmdText, setCmdText] = useState("nvidia-smi");
  const [cmdBusy, setCmdBusy] = useState(false);
  const [cmdOutput, setCmdOutput] = useState<SshExecResult | null>(null);
  const [scpRemote, setScpRemote] = useState("/root/results.csv");

  async function refreshHosts() {
    try {
      const list = await api.listSshHosts();
      setHosts(list);
      if (!selectedId && list.length > 0) setSelectedId(list[0].id);
    } catch (e) {
      onToast(`加载算力主机失败：${e}`, true);
    }
  }

  useEffect(() => {
    void refreshHosts();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleSaveHost() {
    if (!alias.trim() || !hostAddr.trim()) {
      onToast("请填写主机别名与 SSH 地址。", true);
      return;
    }
    setSaving(true);
    try {
      const saved = await api.saveSshHost({
        alias: alias.trim(),
        host: hostAddr.trim(),
        port: Number(port) || 22,
        username: username.trim() || "root",
        authType: privateKeyPem.trim() ? "pasted_key" : "key_path",
        keyPath: keyPath.trim(),
        privateKeyPem: privateKeyPem.trim() || null,
        remoteWorkDir: remoteDir.trim() || "~",
        notes: "",
      });
      setAlias("");
      setHostAddr("");
      setPrivateKeyPem("");
      setSelectedId(saved.id);
      await refreshHosts();
      onToast(`已保存算力主机「${saved.alias}」`);
    } catch (e) {
      onToast(`保存算力主机失败：${e}`, true);
    } finally {
      setSaving(false);
    }
  }

  async function handleTestHost(id: string) {
    setTestingId(id);
    try {
      const res = await api.testSshHost(id);
      setTestResult((prev) => ({ ...prev, [id]: res }));
      await refreshHosts();
      if (res.ok) onToast("SSH 连接成功，已刷新 GPU 状态");
      else onToast(res.error || "SSH 测试连接失败", true);
    } catch (e) {
      onToast(`测试失败：${e}`, true);
    } finally {
      setTestingId("");
    }
  }

  async function handleExec() {
    if (!selectedId || !cmdText.trim()) return;
    setCmdBusy(true);
    try {
      const res = await api.execSshCommand(selectedId, cmdText);
      setCmdOutput(res);
    } catch (e) {
      onToast(`远程执行失败：${e}`, true);
    } finally {
      setCmdBusy(false);
    }
  }

  return (
    <div className="stack">
      <div className="card st-card">
        <div className="row" style={{ justifyContent: "space-between", marginBottom: 8 }}>
          <strong>添加实验室 GPU 服务器 / AutoDL 算力实例 (SSH)</strong>
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => void refreshHosts()}>
            <RefreshCw size={12} /> 刷新
          </button>
        </div>

        <details style={{ marginBottom: 10 }}>
          <summary style={{ cursor: "pointer", fontSize: "var(--fs-sm)", color: "var(--accent)" }}>
            AutoDL / Linux GPU 服务器三步生成并粘贴 SSH 私钥向导
          </summary>
          <div className="callout" style={{ marginTop: 6, fontSize: "var(--fs-xs)" }}>
            <div>1. 在服务器终端生成密钥（连续三次回车）：<code>ssh-keygen -t ed25519 -f ~/.ssh/claws_key</code></div>
            <div>2. 写入公钥授权：<code>cat ~/.ssh/claws_key.pub &gt;&gt; ~/.ssh/authorized_keys &amp;&amp; chmod 600 ~/.ssh/authorized_keys</code></div>
            <div>3. 复制私钥全文（含 BEGIN / END）：<code>cat ~/.ssh/claws_key</code> 并粘贴到下方「直接粘贴私钥」框即可（本机自动 0600 加密存放）。</div>
          </div>
        </details>

        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))", gap: 8 }}>
          <div className="field">
            <label>别名 (如 555 / A100)</label>
            <input className="input" value={alias} onChange={(e) => setAlias(e.target.value)} placeholder="555" />
          </div>
          <div className="field">
            <label>主机地址 (at 右侧)</label>
            <input className="input st-mono" value={hostAddr} onChange={(e) => setHostAddr(e.target.value)} placeholder="region-1.autodl.pro" />
          </div>
          <div className="field">
            <label>端口</label>
            <input className="input st-mono" value={port} onChange={(e) => setPort(e.target.value)} placeholder="31245" />
          </div>
          <div className="field">
            <label>用户名 (at 左侧)</label>
            <input className="input st-mono" value={username} onChange={(e) => setUsername(e.target.value)} placeholder="root" />
          </div>
          <div className="field">
            <label>默认远程工作目录</label>
            <input className="input st-mono" value={remoteDir} onChange={(e) => setRemoteDir(e.target.value)} placeholder="/root/autodl-tmp" />
          </div>
          <div className="field">
            <label>本机私钥路径（或下方粘贴）</label>
            <input className="input st-mono" value={keyPath} onChange={(e) => setKeyPath(e.target.value)} placeholder="~/.ssh/id_ed25519" />
          </div>
        </div>
        <div className="field" style={{ marginTop: 8 }}>
          <label>直接粘贴私钥全文（可选，保存为本地 0600 密钥文件）</label>
          <textarea
            className="textarea st-mono"
            rows={2}
            value={privateKeyPem}
            onChange={(e) => setPrivateKeyPem(e.target.value)}
            placeholder="-----BEGIN OPENSSH PRIVATE KEY----- ..."
          />
        </div>
        <div className="row" style={{ justifyContent: "flex-end", marginTop: 8 }}>
          <button type="button" className="btn btn-primary btn-sm" onClick={handleSaveHost} disabled={saving}>
            {saving ? <Loader2 size={13} className="spin" /> : <Plus size={13} />} 保存计算主机
          </button>
        </div>
      </div>

      <div className="card st-card">
        <strong>已登记计算主机 ({hosts.length})</strong>
        {hosts.length === 0 ? (
          <p className="hint" style={{ marginTop: 6 }}>还没有登记远程算力主机。</p>
        ) : (
          <div className="stack" style={{ marginTop: 8 }}>
            {hosts.map((h) => {
              const res = testResult[h.id];
              const isSel = selectedId === h.id;
              return (
                <div
                  key={h.id}
                  style={{
                    border: `1px solid ${isSel ? "var(--accent)" : "var(--border)"}`,
                    borderRadius: 8,
                    padding: 10,
                    background: isSel ? "var(--accent-soft)" : "var(--bg-card)",
                  }}
                >
                  <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 8 }}>
                    <button
                      type="button"
                      className="row"
                      style={{ background: "none", border: "none", padding: 0, cursor: "pointer" }}
                      onClick={() => setSelectedId(h.id)}
                    >
                      <Cpu size={15} />
                      <strong>{h.alias}</strong>
                      <span className="st-mono hint">
                        {h.username}@{h.host}:{h.port}
                      </span>
                      {h.lastStatus === "online" ? (
                        <span className="badge badge-succeeded">在线</span>
                      ) : h.lastStatus === "error" ? (
                        <span className="badge badge-failed">连接失败</span>
                      ) : (
                        <span className="badge badge-neutral">未检测</span>
                      )}
                    </button>
                    <div className="row">
                      <button
                        type="button"
                        className="btn btn-sm"
                        disabled={testingId === h.id}
                        onClick={() => void handleTestHost(h.id)}
                      >
                        {testingId === h.id ? <Loader2 size={12} className="spin" /> : <Activity size={12} />}
                        测试连接 & nvidia-smi
                      </button>
                      <button
                        type="button"
                        className="btn btn-ghost btn-sm"
                        onClick={async () => {
                          await api.deleteSshHost(h.id);
                          await refreshHosts();
                        }}
                      >
                        <Trash2 size={12} />
                      </button>
                    </div>
                  </div>
                  {(h.lastGpuInfo || res?.systemInfo) && (
                    <div className="hint st-mono" style={{ marginTop: 6 }}>
                      {res?.systemInfo ? `${res.systemInfo} · ` : ""}
                      {h.lastGpuInfo}
                    </div>
                  )}
                  {res?.gpus?.map((g, gi) => {
                    const pct = g.memTotalMb > 0 ? Math.min(100, (g.memUsedMb / g.memTotalMb) * 100) : 0;
                    return (
                      <div key={gi} style={{ marginTop: 6 }}>
                        <div className="row" style={{ justifyContent: "space-between", fontSize: "var(--fs-xs)" }}>
                          <span>GPU {gi}: {g.name}</span>
                          <span>算力 {g.utilizationPct.toFixed(0)}% · 显存 {g.memUsedMb.toFixed(0)}/{g.memTotalMb.toFixed(0)} MiB</span>
                        </div>
                        <div className="gpu-meter">
                          <div className="gpu-meter-fill" style={{ width: `${pct}%` }} />
                        </div>
                      </div>
                    );
                  })}
                  {res?.error && <div className="hint" style={{ color: "var(--danger)", marginTop: 6 }}>{res.error}</div>}
                </div>
              );
            })}
          </div>
        )}

        {selectedId && (
          <div style={{ marginTop: 14, borderTop: "1px solid var(--border)", paddingTop: 10 }} className="stack">
            <div className="row" style={{ justifyContent: "space-between" }}>
              <span className="row">
                <Terminal size={14} /> <strong>远程实验控制台（对当前选中主机执行命令与文件互传）</strong>
              </span>
            </div>
            <div className="row" style={{ gap: 8 }}>
              <input
                className="input st-mono"
                style={{ flex: 1 }}
                value={cmdText}
                onChange={(e) => setCmdText(e.target.value)}
                placeholder="输入命令，例如：python train.py --epochs 50 或 tail -n 30 train.log"
              />
              <button type="button" className="btn btn-primary btn-sm" onClick={handleExec} disabled={cmdBusy}>
                {cmdBusy ? <Loader2 size={13} className="spin" /> : "执行命令"}
              </button>
            </div>
            <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
              <input
                className="input st-mono"
                style={{ flex: 1, minWidth: 220 }}
                value={scpRemote}
                onChange={(e) => setScpRemote(e.target.value)}
                placeholder="远程路径，如 /root/results/metrics.csv"
              />
              <button
                type="button"
                className="btn btn-sm"
                onClick={async () => {
                  const local = await api.pickSaveFile("remote_result");
                  if (!local) return;
                  try {
                    const msg = await api.scpTransfer(selectedId, "download", local, scpRemote);
                    onToast(msg);
                  } catch (e) {
                    onToast(`${e}`, true);
                  }
                }}
              >
                <Download size={12} /> 拉取回本机…
              </button>
              <button
                type="button"
                className="btn btn-sm"
                onClick={async () => {
                  const local = await api.pickFile();
                  if (!local) return;
                  try {
                    const msg = await api.scpTransfer(selectedId, "upload", local, scpRemote);
                    onToast(msg);
                  } catch (e) {
                    onToast(`${e}`, true);
                  }
                }}
              >
                <Upload size={12} /> 上传文件到远程…
              </button>
            </div>
            {cmdOutput && (
              <pre className="le-diff-pre" style={{ maxHeight: 200 }}>
                {`[exit=${cmdOutput.exitCode ?? "?"} · ${cmdOutput.durationMs} ms]\n${cmdOutput.stdout}${cmdOutput.stderr ? `\n--- stderr ---\n${cmdOutput.stderr}` : ""}`}
              </pre>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
