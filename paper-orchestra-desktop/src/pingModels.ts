// "测试模型" on the diagnostics page: the model each provider is pinged with by
// 「测试连接」, when the platform default is not the one to test (for example a
// model the free tier has no quota for). Kept per computer in localStorage; it
// only affects the connection test, never which model a run uses.

const STORAGE_KEY = "po.pingModels.v1";

export type PingModels = Record<string, string>;

export function loadPingModels(): PingModels {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    const data = raw ? (JSON.parse(raw) as unknown) : null;
    if (!data || typeof data !== "object" || Array.isArray(data)) return {};
    const out: PingModels = {};
    for (const [name, model] of Object.entries(data as Record<string, unknown>)) {
      if (typeof model === "string" && model.trim()) out[name] = model.trim();
    }
    return out;
  } catch {
    return {};
  }
}

export function savePingModels(models: PingModels): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(models));
  } catch {
    // Storage unavailable: the choice lasts until the app restarts.
  }
}

/** Sets (or, when blank, clears) one provider's test model; returns the new map. */
export function withPingModel(models: PingModels, name: string, model: string): PingModels {
  const next = { ...models };
  const value = model.trim();
  if (value) next[name] = value;
  else delete next[name];
  return next;
}

/** The platform's own model name: a leading "name:" (as in fetched refs) is dropped. */
export function bareModel(name: string, ref: string): string {
  const prefix = `${name}:`;
  return ref.toLowerCase().startsWith(prefix.toLowerCase()) ? ref.slice(prefix.length) : ref;
}
