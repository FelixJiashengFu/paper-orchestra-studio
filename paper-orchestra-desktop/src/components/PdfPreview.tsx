/// <reference types="vite/client" />
/**
 * PDF preview for the live editor (pdf.js).
 *
 * Every page gets a placeholder of the right size; only pages near the
 * viewport are drawn (IntersectionObserver) and pages far away give their
 * canvas back, so a 30-page paper does not hold 30 retina bitmaps. A reload
 * (after each compile) keeps the old bitmaps on screen until the new ones are
 * drawn, and keeps the scroll position (same page, same offset in it).
 *
 * The legacy build is used on purpose: the modern one needs very new JS
 * (Map.prototype.getOrInsertComputed, Math.sumPrecise ...) that the system
 * WebView on older macOS versions does not have.
 */
import { Minus, Plus, ScanLine } from "lucide-react";
import { useEffect, useImperativeHandle, useRef, useState, type ReactNode, type Ref } from "react";
import {
  GlobalWorkerOptions,
  PDFWorker,
  getDocument,
  type PDFDocumentLoadingTask,
  type PDFPageProxy,
  type RenderTask,
} from "pdfjs-dist/legacy/build/pdf.mjs";
import workerSrc from "pdfjs-dist/legacy/build/pdf.worker.min.mjs?url";
import type { SyncBox } from "../types";

GlobalWorkerOptions.workerSrc = workerSrc;

export interface PdfPreviewHandle {
  /** Scrolls a SyncTeX box into view and flashes it; false when that page does not exist. */
  showBox(box: SyncBox): boolean;
  hasDocument(): boolean;
}

interface PdfPreviewProps {
  /** PDF bytes; a new array (even with the same bytes) reloads the document. */
  data: Uint8Array | null;
  /** Shown over the pages: the PDF is from an earlier compile. */
  staleNote?: string;
  /** Shown while there is no document. */
  placeholder?: ReactNode;
  /** Double-click on a page: page is 1-based, x/y in PDF points from the page's top-left. */
  onPageDoubleClick?: (page: number, x: number, y: number) => void;
  ref?: Ref<PdfPreviewHandle>;
}

interface ViewerState {
  pages: number;
  currentPage: number;
  /** 1 = fit to the pane width. */
  zoom: number;
  loading: boolean;
  error: string;
}

const ZOOM_STEP = 1.2;
const ZOOM_MIN = 0.4;
const ZOOM_MAX = 3;

export function PdfPreview({ data, staleNote, placeholder, onPageDoubleClick, ref }: PdfPreviewProps) {
  const hostRef = useRef<HTMLDivElement>(null);
  const stackRef = useRef<HTMLDivElement>(null);
  const viewerRef = useRef<PdfViewer | null>(null);
  const dblRef = useRef(onPageDoubleClick);
  const [state, setState] = useState<ViewerState>({
    pages: 0,
    currentPage: 0,
    zoom: 1,
    loading: false,
    error: "",
  });

  useEffect(() => {
    dblRef.current = onPageDoubleClick;
  }, [onPageDoubleClick]);

  useEffect(() => {
    const host = hostRef.current;
    const stack = stackRef.current;
    if (!host || !stack) return;
    const viewer = new PdfViewer(host, stack, {
      onState: (patch) => setState((prev) => ({ ...prev, ...patch })),
      onDoubleClick: (page, x, y) => dblRef.current?.(page, x, y),
    });
    viewerRef.current = viewer;
    return () => {
      viewer.destroy();
      viewerRef.current = null;
    };
  }, []);

  useEffect(() => {
    if (data) void viewerRef.current?.load(data);
  }, [data]);

  useImperativeHandle(
    ref,
    () => ({
      showBox: (box) => viewerRef.current?.showBox(box) ?? false,
      hasDocument: () => viewerRef.current?.hasDocument() ?? false,
    }),
    []
  );

  const hasPages = state.pages > 0;
  const zoomPct = Math.round(state.zoom * 100);

  return (
    <div className="le-pdf">
      <div className="le-pdf-bar">
        <span className="le-pdf-pages" aria-live="polite">
          {hasPages ? `第 ${state.currentPage || 1} / ${state.pages} 页` : state.loading ? "正在载入 PDF…" : "PDF 预览"}
        </span>
        <span className="le-pdf-hint">双击 PDF 可跳到对应源码</span>
        <div className="le-pdf-zoom" role="group" aria-label="缩放">
          <button
            type="button"
            className="btn btn-ghost btn-sm btn-icon"
            title="缩小"
            aria-label="缩小"
            disabled={!hasPages || state.zoom <= ZOOM_MIN + 1e-6}
            onClick={() => viewerRef.current?.setZoom(state.zoom / ZOOM_STEP)}
          >
            <Minus size={14} aria-hidden="true" />
          </button>
          <button
            type="button"
            className="btn btn-ghost btn-sm le-pdf-fit"
            title="适合宽度"
            disabled={!hasPages}
            aria-pressed={Math.abs(state.zoom - 1) < 1e-6}
            onClick={() => viewerRef.current?.setZoom(1)}
          >
            <ScanLine size={13} aria-hidden="true" />
            {Math.abs(state.zoom - 1) < 1e-6 ? "适合宽度" : `${zoomPct}%`}
          </button>
          <button
            type="button"
            className="btn btn-ghost btn-sm btn-icon"
            title="放大"
            aria-label="放大"
            disabled={!hasPages || state.zoom >= ZOOM_MAX - 1e-6}
            onClick={() => viewerRef.current?.setZoom(state.zoom * ZOOM_STEP)}
          >
            <Plus size={14} aria-hidden="true" />
          </button>
        </div>
      </div>
      <div className="le-pdf-frame">
        <div className="le-pdf-host" ref={hostRef}>
          {/* Pages are managed by PdfViewer, not by React. */}
          <div className="le-pdf-stack" ref={stackRef} />
        </div>
        {!hasPages && (
          <div className="le-pdf-empty">
            {state.error ? <span className="le-pdf-error">{state.error}</span> : placeholder}
          </div>
        )}
        {hasPages && staleNote && <div className="le-pdf-stale">{staleNote}</div>}
        {hasPages && state.error && <div className="le-pdf-stale is-error">{state.error}</div>}
      </div>
    </div>
  );
}

/* --------------------------------------------------------------------------
   Imperative viewer
   -------------------------------------------------------------------------- */

interface PageInfo {
  proxy: PDFPageProxy;
  /** Size at scale 1, i.e. in PDF points. */
  w: number;
  h: number;
}

interface Slot {
  el: HTMLDivElement;
  /** The bitmap on screen (may be from an older document or scale until redrawn). */
  canvas: HTMLCanvasElement | null;
  /** What `canvas` shows: docSeq:scale:outputScale. */
  key: string;
  task: RenderTask | null;
  near: boolean;
}

interface Anchor {
  index: number;
  /** How far down that page the top of the viewport is (0..1). */
  ratio: number;
  left: number;
}

interface ViewerCallbacks {
  onState: (patch: Partial<ViewerState>) => void;
  onDoubleClick: (page: number, x: number, y: number) => void;
}

/** Side padding of the page column (keep in sync with .le-pdf-stack). */
const STACK_PAD_X = 16;
/** Above this many device pixels a page canvas is drawn at a lower resolution (WebKit canvas limits). */
const MAX_CANVAS_PIXELS = 16_777_216;

class PdfViewer {
  private worker: PDFWorker | null = null;
  private docTask: PDFDocumentLoadingTask | null = null;
  private pendingTask: PDFDocumentLoadingTask | null = null;
  private docSeq = 0;
  private loadSeq = 0;
  private pages: PageInfo[] = [];
  private slots: Slot[] = [];
  private zoom = 1;
  private scale = 1;
  private queue = new Set<number>();
  private rendering = false;
  private destroyed = false;
  private lastWidth = 0;
  private renderTimer = 0;
  private scrollFrame = 0;
  private io: IntersectionObserver;
  private ro: ResizeObserver;
  private dprQuery: MediaQueryList | null = null;

  constructor(
    private host: HTMLDivElement,
    private stack: HTMLDivElement,
    private cb: ViewerCallbacks
  ) {
    this.io = new IntersectionObserver((entries) => this.onIntersect(entries), {
      root: host,
      // Draw a screenful ahead in both directions; give back canvases beyond that.
      rootMargin: "100% 0px",
    });
    this.ro = new ResizeObserver(() => this.onResize());
    this.ro.observe(host);
    this.host.addEventListener("scroll", this.onScroll, { passive: true });
    this.stack.addEventListener("dblclick", this.onDblClick);
    this.watchDpr();
  }

  hasDocument(): boolean {
    return this.pages.length > 0;
  }

  async load(data: Uint8Array): Promise<void> {
    if (this.destroyed) return;
    const seq = ++this.loadSeq;
    this.cb.onState({ loading: true });
    if (!this.worker) this.worker = new PDFWorker();
    // A newer compile finished before this one loaded: drop the older load.
    if (this.pendingTask) void this.pendingTask.destroy();
    // pdf.js takes ownership of the buffer it is given, so hand it a copy.
    const task = getDocument({ data: data.slice(), worker: this.worker });
    this.pendingTask = task;
    let pages: PageInfo[];
    try {
      const doc = await task.promise;
      pages = [];
      for (let i = 1; i <= doc.numPages; i++) {
        const proxy = await doc.getPage(i);
        const vp = proxy.getViewport({ scale: 1 });
        pages.push({ proxy, w: vp.width, h: vp.height });
      }
    } catch (e) {
      if (seq === this.loadSeq && !this.destroyed) {
        this.pendingTask = null;
        void task.destroy();
        this.cb.onState({ loading: false, error: `PDF 打不开：${errorText(e)}` });
      }
      return;
    }
    if (seq !== this.loadSeq || this.destroyed) {
      void task.destroy();
      return;
    }
    this.pendingTask = null;

    const anchor = this.captureAnchor();
    // Stop drawing the old document; its bitmaps stay up until replaced.
    this.cancelAll();
    const oldTask = this.docTask;
    this.docTask = task;
    this.docSeq++;
    this.pages = pages;
    this.syncSlots();
    this.layout(anchor, true);
    if (oldTask) void oldTask.destroy();
    this.cb.onState({ pages: pages.length, loading: false, error: "" });
    this.updateCurrentPage();
  }

  setZoom(zoom: number) {
    const z = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, zoom));
    if (Math.abs(z - this.zoom) < 1e-6 || !this.pages.length) return;
    const anchor = this.captureAnchor();
    this.zoom = z;
    this.layout(anchor, true);
    this.cb.onState({ zoom: z });
  }

  showBox(box: SyncBox): boolean {
    const index = Math.round(box.page) - 1;
    const slot = this.slots[index];
    const page = this.pages[index];
    if (!slot || !page) return false;
    const s = slot.el.offsetWidth / page.w;
    // SyncTeX boxes can be empty (width 0 for a whole paragraph, tiny heights):
    // keep the highlight visible.
    const width = box.width >= 4 ? box.width : Math.max(40, page.w - box.h - 36);
    const height = box.height >= 4 ? box.height : 10;
    const top = box.v - height;
    const pad = 2;
    const hl = document.createElement("div");
    hl.className = "le-pdf-hl";
    hl.style.left = `${(box.h - pad) * s}px`;
    hl.style.top = `${(top - pad) * s}px`;
    hl.style.width = `${(width + pad * 2) * s}px`;
    hl.style.height = `${(height + pad * 2) * s}px`;
    slot.el.appendChild(hl);
    window.setTimeout(() => hl.remove(), 1700);

    const behavior: ScrollBehavior = prefersReducedMotion() ? "auto" : "smooth";
    const y = slot.el.offsetTop + top * s - this.host.clientHeight * 0.35;
    const boxLeft = slot.el.offsetLeft + box.h * s;
    const boxRight = boxLeft + width * s;
    let left = this.host.scrollLeft;
    if (boxLeft < left || boxRight > left + this.host.clientWidth) {
      left = Math.max(0, boxLeft - 24);
    }
    this.host.scrollTo({ top: Math.max(0, y), left, behavior });
    return true;
  }

  destroy() {
    this.destroyed = true;
    this.loadSeq++;
    window.clearTimeout(this.renderTimer);
    cancelAnimationFrame(this.scrollFrame);
    this.io.disconnect();
    this.ro.disconnect();
    this.host.removeEventListener("scroll", this.onScroll);
    this.stack.removeEventListener("dblclick", this.onDblClick);
    this.dprQuery?.removeEventListener("change", this.onDprChange);
    this.cancelAll();
    for (const slot of this.slots) releaseCanvas(slot.canvas);
    this.slots = [];
    this.stack.replaceChildren();
    this.pages = [];
    if (this.pendingTask) void this.pendingTask.destroy();
    if (this.docTask) void this.docTask.destroy();
    this.pendingTask = null;
    this.docTask = null;
    this.worker?.destroy();
    this.worker = null;
  }

  /* ---- layout ---------------------------------------------------------- */

  /** One slot per page; existing slots (and their bitmaps) are reused. */
  private syncSlots() {
    while (this.slots.length > this.pages.length) {
      const slot = this.slots.pop()!;
      this.io.unobserve(slot.el);
      releaseCanvas(slot.canvas);
      slot.el.remove();
    }
    while (this.slots.length < this.pages.length) {
      const el = document.createElement("div");
      el.className = "le-pdf-page";
      el.dataset.index = String(this.slots.length);
      this.stack.appendChild(el);
      this.slots.push({ el, canvas: null, key: "", task: null, near: false });
      this.io.observe(el);
    }
    this.slots.forEach((slot, i) => {
      slot.el.setAttribute("aria-label", `第 ${i + 1} 页`);
    });
  }

  private computeScale(): number {
    const avail = Math.max(120, this.host.clientWidth - STACK_PAD_X * 2);
    const widest = Math.max(1, ...this.pages.map((p) => p.w));
    return (avail / widest) * this.zoom;
  }

  /** Sizes every placeholder for the current width and zoom, then (re)draws what is near. */
  private layout(anchor: Anchor | null, renderNow: boolean) {
    if (!this.pages.length) return;
    this.lastWidth = this.host.clientWidth;
    this.scale = this.computeScale();
    this.pages.forEach((p, i) => {
      const el = this.slots[i].el;
      el.style.width = `${Math.floor(p.w * this.scale)}px`;
      el.style.height = `${Math.floor(p.h * this.scale)}px`;
    });
    if (anchor) this.restoreAnchor(anchor);
    if (renderNow) {
      window.clearTimeout(this.renderTimer);
      this.enqueueNear();
    } else {
      // While the pane is being resized the old bitmaps are stretched; redraw once it settles.
      window.clearTimeout(this.renderTimer);
      this.renderTimer = window.setTimeout(() => this.enqueueNear(), 160);
    }
  }

  private captureAnchor(): Anchor | null {
    const top = this.host.scrollTop;
    for (let i = 0; i < this.slots.length; i++) {
      const el = this.slots[i].el;
      if (el.offsetTop + el.offsetHeight > top) {
        return {
          index: i,
          ratio: el.offsetHeight > 0 ? (top - el.offsetTop) / el.offsetHeight : 0,
          left: this.host.scrollWidth > 0 ? this.host.scrollLeft / this.host.scrollWidth : 0,
        };
      }
    }
    return null;
  }

  private restoreAnchor(a: Anchor) {
    const slot = this.slots[Math.min(a.index, this.slots.length - 1)];
    if (!slot) return;
    this.host.scrollTop = slot.el.offsetTop + Math.max(0, a.ratio) * slot.el.offsetHeight;
    this.host.scrollLeft = a.left * this.host.scrollWidth;
  }

  private onResize() {
    if (this.destroyed || !this.pages.length) return;
    if (Math.abs(this.host.clientWidth - this.lastWidth) < 1) return;
    this.layout(this.captureAnchor(), false);
  }

  private watchDpr() {
    this.dprQuery?.removeEventListener("change", this.onDprChange);
    this.dprQuery = window.matchMedia(`(resolution: ${window.devicePixelRatio || 1}dppx)`);
    this.dprQuery.addEventListener("change", this.onDprChange);
  }

  /** Window moved to a screen with another pixel density: redraw sharp. */
  private onDprChange = () => {
    if (this.destroyed) return;
    this.watchDpr();
    this.enqueueNear();
  };

  private onScroll = () => {
    if (this.scrollFrame) return;
    this.scrollFrame = requestAnimationFrame(() => {
      this.scrollFrame = 0;
      this.updateCurrentPage();
    });
  };

  private updateCurrentPage() {
    if (!this.slots.length) return;
    const mid = this.host.scrollTop + this.host.clientHeight / 2;
    let current = this.slots.length;
    for (let i = 0; i < this.slots.length; i++) {
      const el = this.slots[i].el;
      if (el.offsetTop + el.offsetHeight >= mid) {
        current = i + 1;
        break;
      }
    }
    this.cb.onState({ currentPage: current });
  }

  private onDblClick = (e: MouseEvent) => {
    const el = (e.target as Element | null)?.closest<HTMLDivElement>(".le-pdf-page");
    if (!el || !this.stack.contains(el)) return;
    const index = Number(el.dataset.index);
    const page = this.pages[index];
    if (!page) return;
    e.preventDefault();
    const rect = el.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return;
    const x = ((e.clientX - rect.left) / rect.width) * page.w;
    const y = ((e.clientY - rect.top) / rect.height) * page.h;
    this.cb.onDoubleClick(index + 1, x, y);
  };

  /* ---- drawing --------------------------------------------------------- */

  private renderKey(): string {
    return `${this.docSeq}:${this.scale.toFixed(4)}:${window.devicePixelRatio || 1}`;
  }

  private onIntersect(entries: IntersectionObserverEntry[]) {
    for (const entry of entries) {
      const index = Number((entry.target as HTMLElement).dataset.index);
      const slot = this.slots[index];
      if (!slot) continue;
      slot.near = entry.isIntersecting;
      if (slot.near) {
        if (slot.key !== this.renderKey()) this.queue.add(index);
      } else {
        // Far away: give the memory back (it is drawn again when it comes near).
        this.queue.delete(index);
        slot.task?.cancel();
        slot.task = null;
        releaseCanvas(slot.canvas);
        slot.canvas = null;
        slot.key = "";
      }
    }
    this.pump();
  }

  private enqueueNear() {
    const key = this.renderKey();
    this.slots.forEach((slot, i) => {
      if (slot.near && slot.key !== key) this.queue.add(i);
    });
    this.pump();
  }

  private cancelAll() {
    this.queue.clear();
    for (const slot of this.slots) {
      slot.task?.cancel();
      slot.task = null;
    }
  }

  /** Draws one page at a time, the one closest to the middle of the view first. */
  private pump() {
    if (this.rendering || this.destroyed || !this.queue.size) return;
    const mid = this.host.scrollTop + this.host.clientHeight / 2;
    let best = -1;
    let bestDist = Infinity;
    for (const i of this.queue) {
      const slot = this.slots[i];
      if (!slot || !slot.near) {
        this.queue.delete(i);
        continue;
      }
      const center = slot.el.offsetTop + slot.el.offsetHeight / 2;
      const dist = Math.abs(center - mid);
      if (dist < bestDist) {
        bestDist = dist;
        best = i;
      }
    }
    if (best < 0) return;
    this.queue.delete(best);
    this.rendering = true;
    void this.renderSlot(best).finally(() => {
      this.rendering = false;
      this.pump();
    });
  }

  private async renderSlot(index: number) {
    const slot = this.slots[index];
    const page = this.pages[index];
    if (!slot || !page) return;
    const key = this.renderKey();
    if (slot.key === key) return;
    const cssW = page.w * this.scale;
    const cssH = page.h * this.scale;
    let out = window.devicePixelRatio || 1;
    if (cssW * cssH * out * out > MAX_CANVAS_PIXELS) out = Math.sqrt(MAX_CANVAS_PIXELS / (cssW * cssH));
    const viewport = page.proxy.getViewport({ scale: this.scale * out });
    const canvas = document.createElement("canvas");
    canvas.className = "le-pdf-canvas";
    canvas.width = Math.max(1, Math.floor(viewport.width));
    canvas.height = Math.max(1, Math.floor(viewport.height));
    const docSeq = this.docSeq;
    let task: RenderTask;
    try {
      task = page.proxy.render({ canvas, viewport });
    } catch {
      releaseCanvas(canvas);
      return;
    }
    slot.task = task;
    try {
      await task.promise;
    } catch {
      // Cancelled (scrolled away, reloaded) or a broken page: keep what is shown.
      releaseCanvas(canvas);
      return;
    } finally {
      if (slot.task === task) slot.task = null;
    }
    if (this.destroyed || docSeq !== this.docSeq || this.slots[index] !== slot || !slot.near) {
      releaseCanvas(canvas);
      return;
    }
    // Swap in the new bitmap only now, so a reload never flashes an empty page.
    const old = slot.canvas;
    slot.el.prepend(canvas);
    if (old) {
      old.remove();
      releaseCanvas(old);
    }
    slot.canvas = canvas;
    slot.key = key;
    // The scale may have changed while drawing: draw again at the new one.
    if (key !== this.renderKey()) this.queue.add(index);
  }
}

function releaseCanvas(canvas: HTMLCanvasElement | null) {
  if (!canvas) return;
  // Zero-size canvases free their backing store right away (WebKit keeps it otherwise).
  canvas.width = 0;
  canvas.height = 0;
  canvas.remove();
}

function prefersReducedMotion(): boolean {
  try {
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  } catch {
    return false;
  }
}

function errorText(e: unknown): string {
  if (e instanceof Error) return e.message;
  return String(e);
}
