// Models fetched from the providers' own "list models" endpoints
// ("从各家 API 拉取最新模型" in Settings). The list is kept in localStorage so
// every model field (Settings, Workspace) can suggest it after a restart, and a
// window event tells open views to refresh. It only adds suggestions: any ref
// can still be typed, and nothing here changes which model a run uses.

import { useEffect, useState } from "react";
import type { ModelListResult } from "./types";

const STORAGE_KEY = "po.fetchedModels.v1";
const EVENT = "po-fetched-models";

export interface FetchedModels {
  /** ISO time of the fetch. */
  fetchedAt: string;
  rows: ModelListResult[];
}

function readStore(): FetchedModels | null {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const data = JSON.parse(raw) as FetchedModels;
    if (!data || !Array.isArray(data.rows)) return null;
    return data;
  } catch {
    return null;
  }
}

/** Saves a fetch (rows that failed keep the models of the previous fetch). */
export function saveFetchedModels(rows: ModelListResult[]): FetchedModels {
  const previous = readStore();
  const merged = rows.map((row) => {
    if (row.ok || !previous) return row;
    const old = previous.rows.find((r) => r.name === row.name && r.ok);
    return old ? { ...row, models: old.models } : row;
  });
  const data: FetchedModels = { fetchedAt: new Date().toISOString(), rows: merged };
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(data));
  } catch {
    // Storage unavailable: the list still shows until the app restarts.
  }
  window.dispatchEvent(new CustomEvent<FetchedModels>(EVENT, { detail: data }));
  return data;
}

/** Every fetched ref, provider by provider, without duplicates. */
export function fetchedModelRefs(data: FetchedModels | null): string[] {
  const out: string[] = [];
  for (const row of data?.rows ?? []) {
    for (const ref of row.models ?? []) {
      if (ref && !out.includes(ref)) out.push(ref);
    }
  }
  return out;
}

/** The last fetch, updated live when any view fetches again. */
export function useFetchedModels(): FetchedModels | null {
  const [data, setData] = useState<FetchedModels | null>(() => readStore());
  useEffect(() => {
    const onFetched = (e: Event) => setData((e as CustomEvent<FetchedModels>).detail ?? readStore());
    const onStorage = (e: StorageEvent) => {
      if (e.key === STORAGE_KEY) setData(readStore());
    };
    window.addEventListener(EVENT, onFetched);
    window.addEventListener("storage", onStorage);
    return () => {
      window.removeEventListener(EVENT, onFetched);
      window.removeEventListener("storage", onStorage);
    };
  }, []);
  return data;
}
