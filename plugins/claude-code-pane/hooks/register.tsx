import { atom, read, update } from 'claude-code'
import type { Register } from 'claude-code'

import type {
  PoEnv, PoJob, PoLibItem, PoProject, PoRun, PoRunDetail, PoSnapshot, PoView,
} from '../types'

const PANE = 'paper-orchestra'
const TITLE = 'PaperOrchestra'
const SERVER = 'paper-orchestra-studio'
const REFRESH_MS = 60_000

const VIEW0: PoView = { tab: 'home', projectId: '', runId: '', projQuery: '', libQuery: '', confirmStop: '' }
const snapshot = atom({ plugin: 'paper-orchestra-pane', key: 'snapshot' } as const, null, { shape: 'snap-2' })
const busy = atom({ plugin: 'paper-orchestra-pane', key: 'busy' } as const, false, { shape: 'busy-1' })
const view = atom({ plugin: 'paper-orchestra-pane', key: 'view' } as const, VIEW0, { shape: 'view-1' })
const details = atom({ plugin: 'paper-orchestra-pane', key: 'details' } as const, {}, { shape: 'details-1' })
const env = atom({ plugin: 'paper-orchestra-pane', key: 'env' } as const, null, { shape: 'env-1' })

type Any = Record<string, any>
const S = (v: unknown): string => (v === null || v === undefined ? '' : String(v))
const N = (v: unknown): number => (Number.isFinite(Number(v)) ? Number(v) : 0)
const msg = (err: any): string => S(err?.message ?? err).slice(0, 160)

async function callJson($: any, tool: string, args: Any): Promise<Any> {
  const res = await $.mcp.call(SERVER, tool, args)
  const text = (res.content ?? []).map((b: Any) => (b.type === 'text' ? S(b.text) : '')).join('')
  if (res.isError) throw new Error(text.slice(0, 200) || `${tool} failed`)
  if (res.structuredContent && typeof res.structuredContent === 'object') return res.structuredContent as Any
  try {
    return JSON.parse(text)
  } catch {
    throw new Error(`${tool}: unreadable reply`)
  }
}

// ---------- data ----------

async function refresh($: any): Promise<void> {
  if (await read($, busy)) return
  await update($, busy, () => true)
  try {
    const prev = await read($, snapshot)
    const snap: PoSnapshot = {
      bridgeOnline: false, bridgeError: '', projects: [], runs: [], jobs: [],
      library: prev?.library ?? [], libraryTotal: prev?.libraryTotal ?? 0,
      fetchedAt: await $.clock.now(), error: '',
    }
    const errs: string[] = []
    const [bridge, projects, runs, jobs, lib] = await Promise.allSettled([
      callJson($, 'po_studio_bridge', { action: 'status' }),
      callJson($, 'po_projects', { action: 'list' }),
      callJson($, 'po_projects', { action: 'list_runs', limit: 20 }),
      callJson($, 'po_job_manage', { action: 'list' }),
      callJson($, 'po_literature_library', { action: 'list', limit: 60 }),
    ])
    if (bridge.status === 'fulfilled') {
      snap.bridgeOnline = !!bridge.value.bridge?.online
      snap.bridgeError = S(bridge.value.bridge?.error)
    } else errs.push(msg(bridge.reason))
    if (projects.status === 'fulfilled') {
      snap.projects = (projects.value.projects ?? []).map((p: Any): PoProject => ({
        id: S(p.id), title: S(p.title) || '(untitled)', template: S(p.template_name),
        writer: S(p.writer_model) || 'auto', reflection: S(p.reflection_model) || 'auto',
        plotting: !!p.use_plotting, runCount: N(p.run_count), figureCount: N(p.figure_count),
        createdAt: S(p.created_at), updatedAt: S(p.updated_at),
      }))
    } else errs.push(msg(projects.reason))
    if (runs.status === 'fulfilled') {
      snap.runs = (runs.value.runs ?? []).map((r: Any): PoRun => ({
        id: S(r.id), projectId: S(r.project_id), status: S(r.status), stage: S(r.current_stage),
        template: S(r.template_name), runDir: S(r.run_dir), pdfPath: S(r.pdf_path),
        startedAt: S(r.started_at), finishedAt: S(r.finished_at), error: S(r.error_message),
      }))
    } else errs.push(msg(runs.reason))
    if (jobs.status === 'fulfilled') {
      snap.jobs = (jobs.value.jobs ?? []).map((j: Any): PoJob => ({
        id: S(j.job_id ?? j.id), label: S(j.label ?? j.kind ?? j.command ?? j.job_id) || 'job',
        state: S(j.state ?? j.status),
      }))
    }
    if (lib.status === 'fulfilled') {
      snap.libraryTotal = N(lib.value.total)
      snap.library = (lib.value.items ?? []).map((i: Any): PoLibItem => ({
        id: S(i.id), title: S(i.title), authors: S(i.authors).slice(0, 160), year: S(i.year),
        venue: S(i.venue), doi: S(i.doi), url: S(i.url), folder: S(i.folder), readStatus: S(i.read_status),
      }))
    }
    snap.error = errs.join(' · ')

    // Tell the person when a run changes state while they look elsewhere.
    if (prev) {
      const before = new Map(prev.runs.map(r => [r.id, r.status]))
      for (const r of snap.runs) {
        const was = before.get(r.id)
        if (was !== undefined && was !== r.status) {
          const title = snap.projects.find(p => p.id === r.projectId)?.title ?? r.id
          $.ui.toast(`PaperOrchestra：${title.slice(0, 40)} → ${r.status}`)
        }
      }
    }
    await update($, snapshot, () => snap)
    const active = snap.jobs.length + snap.runs.filter(r => r.status === 'running').length
    $.ui.status(`PO ${snap.bridgeOnline ? '●' : '○'}${active > 0 ? ` · ${active} 个任务运行中` : ''}`)
  } finally {
    await update($, busy, () => false)
  }
}

const EMPTY_DETAIL: PoRunDetail = {
  loading: true, error: '', pdf: '', texCount: 0, cost: 0, currency: '', calls: 0, errors: 0,
  inTok: 0, outTok: 0, models: [], stages: [], totalSec: 0, cited: 0, verified: 0, missing: 0,
  uncited: 0, hasAudit: false,
}

async function loadDetail($: any, run: PoRun): Promise<void> {
  if (run.runDir === '') return
  await update($, details, all => ({ ...all, [run.id]: { ...(all[run.id] ?? EMPTY_DETAIL), loading: true } }))
  let d: PoRunDetail
  try {
    const s = await callJson($, 'po_inspect_run', { action: 'summary', run_dir: run.runDir })
    const u = s.usageReport ?? {}
    const tb = s.timeBreakdown ?? {}
    const audit = s.citationAudit?.summary
    d = {
      loading: false, error: '',
      pdf: S(s.finalPdfPath), texCount: (s.latexFiles ?? []).filter((f: string) => S(f).endsWith('.tex')).length,
      cost: N(u.cost?.total), currency: S(u.cost?.currency), calls: N(u.totals?.calls), errors: N(u.totals?.errors),
      inTok: N(u.totals?.input_tokens), outTok: N(u.totals?.output_tokens),
      models: (u.by_model ?? []).map((m: Any) => ({
        model: S(m.model), calls: N(m.calls), tokens: N(m.input_tokens) + N(m.output_tokens),
        cost: N(m.cost?.cad ?? m.cost?.amount),
      })).sort((a: Any, b: Any) => b.cost - a.cost).slice(0, 8),
      stages: Object.entries(tb)
        .filter(([k]) => k !== 'Start_Time_Absolute' && k !== 'Total_Time_Span')
        .map(([name, sec]) => ({ name, sec: N(sec) })),
      totalSec: N(tb.Total_Time_Span),
      cited: N(audit?.cited), verified: N(audit?.verified), missing: N(audit?.missing_in_bib),
      uncited: N(audit?.uncited), hasAudit: !!audit,
    }
  } catch (err) {
    d = { ...EMPTY_DETAIL, loading: false, error: msg(err) }
  }
  await update($, details, all => {
    const keep = Object.fromEntries(Object.entries(all).slice(-5))
    return { ...keep, [run.id]: d }
  })
}

async function loadEnv($: any): Promise<void> {
  const cur = await read($, env)
  const base: PoEnv = cur ?? {
    loading: true, error: '', testing: false, tools: [], repoDir: '', outputDir: '', pdfDir: '',
    envFileOk: false, dbOk: false, providers: [], scholar: [],
  }
  await update($, env, () => ({ ...base, loading: true }))
  const [e, p] = await Promise.allSettled([
    callJson($, 'po_check_environment', {}),
    callJson($, 'po_check_providers', {}),
  ])
  const next: PoEnv = { ...base, loading: false, error: '' }
  if (e.status === 'fulfilled') {
    const v = e.value.env ?? e.value
    next.tools = Object.entries(v.tools ?? {}).map(([name, path]) => ({ name, found: S(path) !== '' }))
    next.repoDir = S(v.repoDir); next.outputDir = S(v.outputRootDir); next.pdfDir = S(v.defaultPdfDir)
    next.envFileOk = !!v.envExists; next.dbOk = !!v.dbExists
  } else next.error = msg(e.reason)
  if (p.status === 'fulfilled') {
    next.providers = (p.value.providers ?? []).map((x: Any) => ({
      label: S(x.label ?? x.name), model: S(x.model), configured: !!x.configured,
    }))
    if (next.scholar.length === 0) {
      next.scholar = (p.value.scholar ?? []).map((x: Any) => ({ name: S(x.name), ok: null, latencyMs: null, error: '' }))
    }
  } else next.error = [next.error, msg(p.reason)].filter(Boolean).join(' · ')
  await update($, env, () => next)
}

async function testScholar($: any): Promise<void> {
  await update($, env, v => (v ? { ...v, testing: true } : v))
  try {
    const p = await callJson($, 'po_check_providers', { scholar: true, timeout: 10 })
    const scholar = (p.scholar ?? []).map((x: Any) => ({
      name: S(x.name), ok: x.ok === null || x.ok === undefined ? null : !!x.ok,
      latencyMs: x.latency_ms === null || x.latency_ms === undefined ? null : N(x.latency_ms), error: S(x.error).slice(0, 80),
    }))
    await update($, env, v => (v ? { ...v, testing: false, scholar } : v))
  } catch (err) {
    $.ui.toast(`检索源测试失败：${msg(err)}`)
    await update($, env, v => (v ? { ...v, testing: false } : v))
  }
}

// ---------- actions (all return at once: a press has a 10 s budget) ----------

function openStudio($: any, tab: string): void {
  $.ui.toast(`正在打开 Studio · ${tab}…`)
  void (async () => {
    try {
      await callJson($, 'po_studio_bridge', { action: 'open_studio', auto_launch: true, tab, launch_timeout: 30 })
    } catch (err) {
      $.ui.toast(`Studio：${msg(err)}`)
    }
    await refresh($)
  })()
}

// One list of commands per OS; the first that exits cleanly wins.
function openCommands(platform: string, path: string, reveal: boolean): { cmds: string[][]; trustExit: boolean } {
  if (platform === 'macos') return { cmds: [reveal ? ['open', '-R', path] : ['open', path]], trustExit: true }
  if (platform === 'windows') {
    // explorer.exe returns 1 even when it opens the window, so its exit code says nothing.
    const win = path.replace(/\//g, '\\')
    return { cmds: [reveal ? ['explorer.exe', `/select,${win}`] : ['explorer.exe', win]], trustExit: false }
  }
  // Linux and WSL have no "select in file manager" verb: revealing a folder opens it.
  return { cmds: [['xdg-open', path], ['wslview', path], ['gio', 'open', path]], trustExit: true }
}

function openPath($: any, path: string, reveal = false): void {
  if (path === '') return
  void (async () => {
    let platform = 'macos'
    try {
      platform = S((await $.session.info()).platform) || 'macos'
    } catch { /* older builds: keep the macOS behaviour this pane always had */ }
    const { cmds, trustExit } = openCommands(platform, path, reveal)
    let last = ''
    for (const cmd of cmds) {
      try {
        const r = await $.process.run(cmd, { timeoutMs: 8000 })
        if (!trustExit || r.exitCode === 0) return
        last = `${cmd[0]} exit ${r.exitCode}`
      } catch (err) {
        last = msg(err)
      }
    }
    $.ui.toast(`打不开（${last}）：${path}`)
  })()
}

function ask($: any, text: string): void {
  $.ui.toast('已发给 Claude')
  void $.prompt.submit({ text })
}

function copyPdf($: any, run: PoRun): void {
  void (async () => {
    try {
      const r = await callJson($, 'po_inspect_run', { action: 'copy_pdf', run_dir: run.runDir })
      $.ui.toast(`PDF 已复制到 ${S(r.dest_path ?? r.destPath ?? r.path ?? '默认 PDF 目录')}`)
    } catch (err) {
      $.ui.toast(`复制失败：${msg(err)}`)
    }
  })()
}

function stopJob($: any, id: string): void {
  void (async () => {
    try {
      await callJson($, 'po_job_manage', { action: 'stop', job_id: id })
      $.ui.toast('任务已停止')
    } catch (err) {
      $.ui.toast(`停止失败：${msg(err)}`)
    }
    await update($, view, v => ({ ...v, confirmStop: '' }))
    await refresh($)
  })()
}

function importDoi($: any, doi: string): void {
  const clean = doi.trim().replace(/^https?:\/\/(dx\.)?doi\.org\//i, '')
  if (clean === '') return
  $.ui.toast(`正在导入 ${clean}…`)
  void (async () => {
    try {
      await callJson($, 'po_literature_library', { action: 'import_doi', doi: clean })
      $.ui.toast(`已导入 ${clean}`)
    } catch (err) {
      $.ui.toast(`导入失败：${msg(err)}`)
    }
    await refresh($)
  })()
}

// ---------- formatting ----------

function statusColor(status: string): string {
  if (status === 'succeeded') return 'success'
  if (status === 'running' || status === 'queued') return 'warning'
  if (status === 'failed') return 'error'
  return 'inactive'
}
const STATUS_ZH: Record<string, string> = {
  succeeded: '成功', running: '运行中', queued: '排队中', failed: '失败', cancelled: '已取消',
}
const zh = (s: string): string => STATUS_ZH[s] ?? s
const p2 = (n: number): string => String(n).padStart(2, '0')
function when(iso: string): string {
  if (iso === '') return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return `${p2(d.getMonth() + 1)}-${p2(d.getDate())} ${p2(d.getHours())}:${p2(d.getMinutes())}`
}
function dur(sec: number): string {
  if (sec <= 0) return ''
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = Math.floor(sec % 60)
  return h > 0 ? `${h}h ${p2(m)}m` : m > 0 ? `${m}m ${p2(s)}s` : `${s}s`
}
function span(a: string, b: string): string {
  if (a === '' || b === '') return ''
  return dur((new Date(b).getTime() - new Date(a).getTime()) / 1000)
}
function kTok(n: number): string {
  return n >= 1e6 ? `${(n / 1e6).toFixed(2)}M` : n >= 1e3 ? `${(n / 1e3).toFixed(0)}k` : String(n)
}
const STAGE_ZH: Record<string, string> = {
  OutlineAgent: '大纲', Parallel_Lit_and_Plot: '文献 + 绘图', SectionWritingAgent: '分节写作',
  ContentRefinementAgent: '内容精修',
}

// ---------- hooks ----------

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    await $.command.register({ name: 'po-pane', description: '在侧边栏打开 PaperOrchestra 面板' })
    void $.ui.open({ id: PANE, title: TITLE })
    void refresh($)
    $.clock.every(REFRESH_MS, () => { void refresh($) })
    return next(e)
  })

  on('command.run', { command: 'po-pane' }, async $ => {
    await $.ui.open({ id: PANE, title: TITLE })
    void refresh($)
    return { text: 'PaperOrchestra 面板已打开。' }
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const { Box, Text, Button, Input, Link, Progress } = $.ui.resolve(e)
    const snap = await read($, snapshot)
    const isBusy = await read($, busy)
    const v = await read($, view)
    const allDetails = await read($, details)
    const envInfo = await read($, env)

    const projects = snap?.projects ?? []
    const runs = snap?.runs ?? []
    const jobs = snap?.jobs ?? []
    const titleOf = (id: string): string => projects.find(p => p.id === id)?.title ?? id
    const go = (patch: Partial<PoView>) => () => update($, view, cur => ({ ...cur, confirmStop: '', ...patch }))
    const showRun = (r: PoRun) => () => {
      void loadDetail($, r)
      return update($, view, (cur): PoView => ({ ...cur, tab: 'runs', runId: r.id, confirmStop: '' }))
    }

    const Card = (key: string, children: any) => (
      <Box key={key} flexDirection="column" borderStyle="round" borderColor="subtle" paddingX={1} gap={0}>
        {children}
      </Box>
    )
    const Stat = (key: string, value: string, label: string, color?: string) => (
      <Box key={key} flexDirection="column" flexGrow={1} borderStyle="round" borderColor="subtle" paddingX={1} alignItems="center">
        <Text size="title" weight="bold" color={color}>{value}</Text>
        <Text dimColor size="caption">{label}</Text>
      </Box>
    )
    const RunRow = (r: PoRun) => (
      <Box key={`run-${r.id}`} flexDirection="column" borderStyle="round" borderColor="subtle" paddingX={1}>
        <Box flexDirection="row" justifyContent="space-between" alignItems="center">
          <Text wrap="truncate-end">
            <Text color={statusColor(r.status)} weight="semibold">● {zh(r.status)}</Text>
            <Text dimColor> · {r.stage} · {when(r.startedAt)}{span(r.startedAt, r.finishedAt) ? ` · ${span(r.startedAt, r.finishedAt)}` : ''}</Text>
          </Text>
          <Button key={`run-open-${r.id}`} label="详情" onPress={showRun(r)} />
        </Box>
        <Text size="caption" wrap="truncate-end">{titleOf(r.projectId)}</Text>
        {r.error !== '' && <Text color="error" size="caption" wrap="truncate-end">{r.error}</Text>}
      </Box>
    )

    // ----- tabs -----
    const TABS: { id: PoView['tab']; label: string }[] = [
      { id: 'home', label: '总览' },
      { id: 'projects', label: `项目 ${projects.length}` },
      { id: 'runs', label: `运行 ${runs.length}` },
      { id: 'library', label: `文献 ${snap?.libraryTotal ?? 0}` },
      { id: 'env', label: '环境' },
    ]

    let body: any

    if (v.tab === 'home') {
      const latest = runs[0]
      const running = runs.filter(r => r.status === 'running')
      body = (
        <Box flexDirection="column" gap={1}>
          <Box flexDirection="row" gap={1}>
            {Stat('s-proj', String(projects.length), '项目')}
            {Stat('s-runs', String(runs.length), '运行')}
            {Stat('s-lib', String(snap?.libraryTotal ?? 0), '文献')}
            {Stat('s-jobs', String(jobs.length + running.length), '进行中', jobs.length + running.length > 0 ? 'warning' : undefined)}
          </Box>

          <Text weight="semibold">进行中的任务</Text>
          {jobs.length === 0 && running.length === 0 && <Text dimColor>现在没有任务在跑。</Text>}
          {running.map(r => (
            <Box key={`live-${r.id}`} flexDirection="column">
              <Progress label={`${titleOf(r.projectId).slice(0, 40)} · ${r.stage}`} valueText={`已运行 ${span(r.startedAt, new Date(snap?.fetchedAt ?? 0).toISOString())}`} />
            </Box>
          ))}
          {jobs.map(j => (
            <Box key={`job-${j.id}`} flexDirection="row" justifyContent="space-between" alignItems="center">
              <Text wrap="truncate-end"><Text color="warning">● {j.state}</Text> · {j.label}</Text>
              {v.confirmStop === j.id
                ? <Box flexDirection="row" gap={1}>
                    <Button key={`stop-yes-${j.id}`} variant="primary" label="确认停止" onPress={() => stopJob($, j.id)} />
                    <Button key={`stop-no-${j.id}`} label="取消" onPress={go({})} />
                  </Box>
                : <Button key={`stop-${j.id}`} label="停止" onPress={go({ confirmStop: j.id })} />}
            </Box>
          ))}

          {latest && <Text weight="semibold">最近一次运行</Text>}
          {latest && RunRow(latest)}
          {latest && latest.pdfPath !== '' && (
            <Box flexDirection="row" gap={1} flexWrap="wrap">
              <Button key="home-pdf" variant="primary" label="打开 PDF" onPress={() => openPath($, latest.pdfPath)} />
              <Button key="home-dir" label="在文件夹中显示" onPress={() => openPath($, latest.runDir, true)} />
            </Box>
          )}

          <Text weight="semibold">在 Studio 中打开</Text>
          <Box flexDirection="row" flexWrap="wrap" gap={1}>
            {[['workspace', '工作区'], ['studio', 'Studio'], ['library', '文献库'], ['runs', '运行'], ['artifacts', '产物'], ['settings', '设置']].map(([tab, label]) => (
              <Button key={`tab-${tab}`} label={label} onPress={() => openStudio($, tab as string)} />
            ))}
          </Box>

          <Text weight="semibold">让 Claude 来做</Text>
          <Box flexDirection="row" flexWrap="wrap" gap={1}>
            <Button key="ask-new" label="新建论文项目" onPress={() => ask($, '帮我在 PaperOrchestra 里新建一个论文项目。先问我题目、想法和目标期刊模板。')} />
            <Button key="ask-lit" label="检索文献" onPress={() => ask($, '用 PaperOrchestra 的文献库帮我在线检索文献。先问我检索主题。')} />
            <Button key="ask-wb" label="打开完整工作台" onPress={() => ask($, '帮我打开 PaperOrchestra 工作台')} />
          </Box>
        </Box>
      )
    } else if (v.tab === 'projects') {
      const proj = projects.find(p => p.id === v.projectId)
      if (proj) {
        const mine = runs.filter(r => r.projectId === proj.id)
        const ref = `项目「${proj.title}」(project_id=${proj.id})`
        body = (
          <Box flexDirection="column" gap={1}>
            <Button key="proj-back" label="← 全部项目" onPress={go({ projectId: '' })} />
            <Text size="heading" weight="semibold">{proj.title}</Text>
            {Card('proj-meta', [
              <Text key="m1"><Text dimColor>模板　</Text>{proj.template}</Text>,
              <Text key="m2"><Text dimColor>写作模型　</Text>{proj.writer}<Text dimColor>　审阅模型　</Text>{proj.reflection}</Text>,
              <Text key="m3"><Text dimColor>自动绘图　</Text>{proj.plotting ? '开' : '关'}<Text dimColor>　图片　</Text>{proj.figureCount}<Text dimColor>　运行　</Text>{proj.runCount}</Text>,
              <Text key="m4" dimColor size="caption">创建 {when(proj.createdAt)} · 更新 {when(proj.updatedAt)}</Text>,
            ])}
            <Text weight="semibold">操作</Text>
            <Box flexDirection="row" flexWrap="wrap" gap={1}>
              <Button key="p-run" variant="primary" label="开始生成论文" onPress={() => ask($, `用 PaperOrchestra 为${ref}启动一次论文生成运行。开始前先跟我确认模板和模型，并告诉我大概的花费。`)} />
              <Button key="p-edit" label="修改想法/设置" onPress={() => ask($, `我想修改 PaperOrchestra ${ref}的想法或设置。先把现在的内容给我看，再问我要改什么。`)} />
              <Button key="p-fig" label="添加图片" onPress={() => ask($, `我想给 PaperOrchestra ${ref}添加图片。问我图片路径和图注。`)} />
              <Button key="p-studio" label="在 Studio 中打开" onPress={() => openStudio($, 'workspace')} />
            </Box>
            <Text weight="semibold">这个项目的运行（{mine.length}）</Text>
            {mine.length === 0 && <Text dimColor>还没有运行过。</Text>}
            {mine.map(RunRow)}
          </Box>
        )
      } else {
        const q = v.projQuery.trim().toLowerCase()
        const shown = q === '' ? projects : projects.filter(p => p.title.toLowerCase().includes(q) || p.template.toLowerCase().includes(q))
        body = (
          <Box flexDirection="column" gap={1}>
            <Input key="proj-filter" placeholder="按标题或模板筛选，回车确认" value={v.projQuery} submitLabel="筛选"
              onSubmit={(value: string) => { void update($, view, cur => ({ ...cur, projQuery: value })) }} />
            {q !== '' && <Button key="proj-clear" label={`清除筛选「${v.projQuery}」`} onPress={go({ projQuery: '' })} />}
            {shown.length === 0 && <Text dimColor>没有匹配的项目。</Text>}
            {shown.map(p => (
              <Box key={`proj-${p.id}`} flexDirection="column" borderStyle="round" borderColor="subtle" paddingX={1}>
                <Box flexDirection="row" justifyContent="space-between" alignItems="center">
                  <Text weight="medium" wrap="truncate-end">{p.title}</Text>
                  <Button key={`proj-open-${p.id}`} label="打开" onPress={go({ projectId: p.id })} />
                </Box>
                <Text dimColor size="caption" wrap="truncate-end">{p.template} · {p.runCount} 次运行 · {p.figureCount} 张图 · 更新 {when(p.updatedAt)}</Text>
              </Box>
            ))}
          </Box>
        )
      }
    } else if (v.tab === 'runs') {
      const run = runs.find(r => r.id === v.runId)
      if (run) {
        const d = allDetails[run.id]
        const ref = `运行目录 ${run.runDir}`
        const maxStage = Math.max(1, ...(d?.stages ?? []).map(s => s.sec))
        body = (
          <Box flexDirection="column" gap={1}>
            <Button key="run-back" label="← 全部运行" onPress={go({ runId: '' })} />
            <Text size="heading" weight="semibold" maxLines={3}>{titleOf(run.projectId)}</Text>
            <Text>
              <Text color={statusColor(run.status)} weight="semibold">● {zh(run.status)}</Text>
              <Text dimColor> · {run.stage} · {run.template}</Text>
            </Text>
            <Text dimColor size="caption">开始 {when(run.startedAt)}{run.finishedAt ? ` · 结束 ${when(run.finishedAt)} · 用时 ${span(run.startedAt, run.finishedAt)}` : ''}</Text>
            {run.error !== '' && <Text color="error">{run.error}</Text>}

            <Box flexDirection="row" flexWrap="wrap" gap={1}>
              {run.pdfPath !== '' && <Button key="r-pdf" variant="primary" label="打开 PDF" onPress={() => openPath($, run.pdfPath)} />}
              {run.pdfPath !== '' && <Button key="r-copy" label="复制 PDF 到默认目录" onPress={() => copyPdf($, run)} />}
              <Button key="r-dir" label="在文件夹中显示" onPress={() => openPath($, run.runDir, true)} />
              <Button key="r-reload" label="重新读取" onPress={() => { void loadDetail($, run) }} />
            </Box>

            {(!d || d.loading) && <Progress label="正在读取运行结果…" />}
            {d && !d.loading && d.error !== '' && <Text color="error" size="caption">{d.error}</Text>}

            {d && !d.loading && d.error === '' && (
              <Box flexDirection="column" gap={1}>
                <Box flexDirection="row" gap={1}>
                  {Stat('d-cost', d.cost > 0 ? `${d.cost.toFixed(2)}` : '—', `花费 ${d.currency}`)}
                  {Stat('d-calls', String(d.calls), `模型调用${d.errors > 0 ? ` · ${d.errors} 错` : ''}`, d.errors > 0 ? 'error' : undefined)}
                  {Stat('d-tok', kTok(d.inTok + d.outTok), 'tokens')}
                  {Stat('d-time', dur(d.totalSec) || '—', '流程用时')}
                </Box>

                {d.hasAudit && <Text weight="semibold">引用核查</Text>}
                {d.hasAudit && <Progress label="已在线核实的引用" value={d.verified} max={Math.max(1, d.cited)} valueText={`${d.verified} / ${d.cited}`} color={d.verified === d.cited ? 'success' : 'warning'} />}
                {d.hasAudit && <Text dimColor size="caption">bib 中缺失 {d.missing} · 未被引用 {d.uncited}</Text>}

                {d.stages.length > 0 && <Text weight="semibold">各阶段用时</Text>}
                {d.stages.map(s => (
                  <Progress key={`st-${s.name}`} label={STAGE_ZH[s.name] ?? s.name} value={s.sec} max={maxStage} valueText={dur(s.sec)} />
                ))}

                {d.models.length > 0 && <Text weight="semibold">各模型花费（{d.currency}）</Text>}
                {d.models.map(m => (
                  <Box key={`m-${m.model}`} flexDirection="row" justifyContent="space-between">
                    <Text mono wrap="truncate-end">{m.model}</Text>
                    <Text tabular dimColor>{m.calls} 次 · {kTok(m.tokens)} · <Text weight="semibold">{m.cost.toFixed(2)}</Text></Text>
                  </Box>
                ))}
              </Box>
            )}

            <Text weight="semibold">让 Claude 来做</Text>
            <Box flexDirection="row" flexWrap="wrap" gap={1}>
              <Button key="a-audit" label="投稿前审计" onPress={() => ask($, `用 PaperOrchestra 对${ref}做一次投稿前审计（稿件质量、引用在线核查、LaTeX 日志），把问题按严重程度列给我。`)} />
              <Button key="a-blind" label="双盲泄露检查" onPress={() => ask($, `用 PaperOrchestra 检查${ref}里的稿件有没有双盲信息泄露。`)} />
              <Button key="a-arxiv" label="打包 arXiv / Overleaf" onPress={() => ask($, `用 PaperOrchestra 把${ref}打包成可以上传 arXiv / Overleaf 的压缩包，告诉我文件在哪。`)} />
              <Button key="a-report" label="作者报告" onPress={() => ask($, `用 PaperOrchestra 为${ref}生成作者报告（report.md），并把要点总结给我。`)} />
              {run.status !== 'succeeded' && run.status !== 'running' && (
                <Button key="a-resume" label="续跑这次运行" onPress={() => ask($, `用 PaperOrchestra 续跑 run_id=${run.id}（${ref}）。开始前先跟我确认。`)} />
              )}
            </Box>
            <Text dimColor size="caption" wrap="truncate-middle">{run.runDir}</Text>
          </Box>
        )
      } else {
        body = (
          <Box flexDirection="column" gap={1}>
            {runs.length === 0 && <Text dimColor>还没有运行记录。</Text>}
            {runs.map(RunRow)}
          </Box>
        )
      }
    } else if (v.tab === 'library') {
      const lib = snap?.library ?? []
      const q = v.libQuery.trim().toLowerCase()
      const shown = q === '' ? lib : lib.filter(i => `${i.title} ${i.authors} ${i.venue} ${i.year} ${i.folder}`.toLowerCase().includes(q))
      body = (
        <Box flexDirection="column" gap={1}>
          <Input key="lib-filter" placeholder="筛选标题 / 作者 / 期刊，回车确认" value={v.libQuery} submitLabel="筛选"
            onSubmit={(value: string) => { void update($, view, cur => ({ ...cur, libQuery: value })) }} />
          <Input key="lib-doi" placeholder="粘贴 DOI 导入文献库，例如 10.1038/…" submitLabel="导入"
            onSubmit={(value: string) => importDoi($, value)} />
          <Box flexDirection="row" flexWrap="wrap" gap={1}>
            {q !== '' && <Button key="lib-clear" label={`清除筛选「${v.libQuery}」`} onPress={go({ libQuery: '' })} />}
            <Button key="lib-search" label="在线检索" onPress={() => ask($, '用 PaperOrchestra 的文献库帮我在线检索文献并存入文献库。先问我检索主题。')} />
            <Button key="lib-bib" label="导出 BibTeX" onPress={() => ask($, '用 PaperOrchestra 把文献库导出成 BibTeX，告诉我文件在哪。')} />
            <Button key="lib-graph" label="生成引用图谱" onPress={() => ask($, '用 PaperOrchestra 为文献库生成引用关系图谱。')} />
          </Box>
          <Text dimColor size="caption">显示 {shown.length} 篇，文献库共 {snap?.libraryTotal ?? 0} 篇</Text>
          {shown.length === 0 && <Text dimColor>没有匹配的文献。</Text>}
          {shown.map(i => (
            <Box key={`lib-${i.id}`} flexDirection="column" borderStyle="round" borderColor="subtle" paddingX={1}>
              <Text weight="medium" maxLines={2}>{i.title}</Text>
              <Text dimColor size="caption" wrap="truncate-end">{i.authors}</Text>
              <Text size="caption" wrap="truncate-end">
                <Text italic>{i.venue}</Text>{i.year ? ` · ${i.year}` : ''}{i.folder ? ` · ${i.folder}` : ''}
                {i.readStatus ? <Text dimColor> · {i.readStatus}</Text> : null}
              </Text>
              {i.doi !== ''
                ? <Link href={`https://doi.org/${i.doi}`} label={`doi:${i.doi}`} />
                : i.url !== '' ? <Link href={i.url} label="打开链接" /> : null}
            </Box>
          ))}
        </Box>
      )
    } else {
      const en = envInfo
      const okCount = en?.providers.filter(p => p.configured).length ?? 0
      body = (
        <Box flexDirection="column" gap={1}>
          <Box flexDirection="row" gap={1} flexWrap="wrap">
            <Button key="env-reload" label={en?.loading ? '检查中…' : '重新检查'} onPress={() => { void loadEnv($) }} />
            <Button key="env-scholar" label={en?.testing ? '测试中…' : '测试文献检索源'} onPress={() => { void testScholar($) }} />
            <Button key="env-settings" label="Studio 设置" onPress={() => openStudio($, 'settings')} />
          </Box>
          {(!en || (en.loading && en.tools.length === 0)) && <Progress label="正在检查环境…" />}
          {en && en.error !== '' && <Text color="error" size="caption">{en.error}</Text>}
          {en && en.tools.length > 0 && (
            <Box flexDirection="column" gap={1}>
              <Text weight="semibold">本机工具</Text>
              <Box flexDirection="row" flexWrap="wrap" columnGap={2}>
                {en.tools.map(t => (
                  <Text key={`tool-${t.name}`} color={t.found ? 'success' : 'error'}>{t.found ? '✓' : '✗'} <Text mono>{t.name}</Text></Text>
                ))}
              </Box>
              {Card('env-paths', [
                <Text key="e1" size="caption"><Text color={en.dbOk ? 'success' : 'error'}>{en.dbOk ? '✓' : '✗'}</Text> 数据库　<Text color={en.envFileOk ? 'success' : 'error'}>{en.envFileOk ? '✓' : '✗'}</Text> .env 配置</Text>,
                <Text key="e2" dimColor size="caption" wrap="truncate-middle">代码　{en.repoDir}</Text>,
                <Text key="e3" dimColor size="caption" wrap="truncate-middle">输出　{en.outputDir}</Text>,
                <Text key="e4" dimColor size="caption" wrap="truncate-middle">PDF　{en.pdfDir}</Text>,
              ])}
              <Text weight="semibold">模型服务商（已配置 {okCount} / {en.providers.length}）</Text>
              {en.providers.filter(p => p.configured).map(p => (
                <Box key={`prov-${p.label}`} flexDirection="row" justifyContent="space-between">
                  <Text wrap="truncate-end"><Text color="success">●</Text> {p.label}</Text>
                  <Text mono dimColor size="caption">{p.model}</Text>
                </Box>
              ))}
              <Text dimColor size="caption" maxLines={3}>未配置：{en.providers.filter(p => !p.configured).map(p => p.label).join('、') || '无'}</Text>
              <Text weight="semibold">文献检索源</Text>
              <Box flexDirection="row" flexWrap="wrap" columnGap={2}>
                {en.scholar.map(s => (
                  <Text key={`sch-${s.name}`} color={s.ok === null ? 'inactive' : s.ok ? 'success' : 'error'}>
                    {s.ok === null ? '○' : s.ok ? '✓' : '✗'} {s.name}{s.latencyMs !== null ? <Text dimColor> {s.latencyMs}ms</Text> : null}
                  </Text>
                ))}
              </Box>
            </Box>
          )}
        </Box>
      )
    }

    return (
      <Box flexDirection="column" gap={1}>
        <Box flexDirection="row" justifyContent="space-between" alignItems="center">
          <Text color={snap ? (snap.bridgeOnline ? 'success' : 'error') : 'inactive'} weight="semibold">
            {snap ? (snap.bridgeOnline ? '● Studio 已连接' : '● Studio 未连接') : '加载中…'}
          </Text>
          <Box flexDirection="row" gap={1}>
            {snap && !snap.bridgeOnline && <Button key="launch" variant="primary" label="启动 Studio" onPress={() => openStudio($, 'workspace')} />}
            <Button key="refresh" label={isBusy ? '刷新中…' : '刷新'} onPress={() => { void refresh($) }} />
          </Box>
        </Box>
        {snap && !snap.bridgeOnline && snap.bridgeError !== '' && <Text dimColor size="caption" maxLines={2}>{snap.bridgeError}</Text>}
        {snap?.error ? <Text color="error" size="caption" maxLines={3}>{snap.error}</Text> : null}

        <Box flexDirection="row" flexWrap="wrap" gap={1}>
          {TABS.map(t => (
            <Button key={`nav-${t.id}`} label={t.label} variant={v.tab === t.id ? 'primary' : 'secondary'}
              onPress={() => {
                if (t.id === 'env' && !envInfo) void loadEnv($)
                return update($, view, cur => ({ ...cur, tab: t.id, projectId: '', runId: '', confirmStop: '' }))
              }} />
          ))}
        </Box>

        {body}

        {snap && <Text dimColor size="caption">更新于 {new Date(snap.fetchedAt).toLocaleTimeString()} · 每 60 秒自动刷新</Text>}
      </Box>
    )
  })
}
