export type PoProject = {
  id: string; title: string; template: string; writer: string; reflection: string
  plotting: boolean; runCount: number; figureCount: number; createdAt: string; updatedAt: string
}
export type PoRun = {
  id: string; projectId: string; status: string; stage: string; template: string
  runDir: string; pdfPath: string; startedAt: string; finishedAt: string; error: string
}
export type PoJob = { id: string; label: string; state: string }
export type PoLibItem = {
  id: string; title: string; authors: string; year: string; venue: string
  doi: string; url: string; folder: string; readStatus: string
}
export type PoSnapshot = {
  bridgeOnline: boolean
  bridgeError: string
  projects: PoProject[]
  runs: PoRun[]
  jobs: PoJob[]
  library: PoLibItem[]
  libraryTotal: number
  fetchedAt: number
  error: string
}
export type PoModelUse = { model: string; calls: number; tokens: number; cost: number }
export type PoStage = { name: string; sec: number }
export type PoRunDetail = {
  loading: boolean; error: string
  pdf: string; texCount: number
  cost: number; currency: string; calls: number; errors: number; inTok: number; outTok: number
  models: PoModelUse[]; stages: PoStage[]; totalSec: number
  cited: number; verified: number; missing: number; uncited: number; hasAudit: boolean
}
export type PoProvider = { label: string; model: string; configured: boolean }
export type PoScholar = { name: string; ok: boolean | null; latencyMs: number | null; error: string }
export type PoEnv = {
  loading: boolean; error: string; testing: boolean
  tools: { name: string; found: boolean }[]
  repoDir: string; outputDir: string; pdfDir: string; envFileOk: boolean; dbOk: boolean
  providers: PoProvider[]; scholar: PoScholar[]
}
export type PoView = {
  tab: 'home' | 'projects' | 'runs' | 'library' | 'env'
  projectId: string; runId: string
  projQuery: string; libQuery: string
  confirmStop: string
}

declare module 'claude-code' {
  interface PluginState {
    'paper-orchestra-pane': {
      snapshot: Shaped<PoSnapshot | null>
      busy: Shaped<boolean>
      view: Shaped<PoView>
      details: Shaped<Record<string, PoRunDetail>>
      env: Shaped<PoEnv | null>
    }
  }
}
