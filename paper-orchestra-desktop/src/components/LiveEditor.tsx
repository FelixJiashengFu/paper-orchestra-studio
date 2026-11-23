/**
 * 「编辑并实时预览」: an Overleaf-like editor for one run's final LaTeX.
 *
 * Left: CodeMirror on the run's export source folder (the files the Overleaf
 * export and the submission tools use). Right: the PDF latexmk builds from it
 * into <run>/.live_preview/. Typing saves 800 ms after the last keystroke and
 * every save that writes something recompiles; one compile runs at a time and
 * a change made meanwhile gets its own compile right after, so the last edit
 * is never dropped. A save that fails for a passing reason is retried (the
 * editor stays editable); only a conflict or a running pipeline locks it.
 */
import { getCurrentWindow, type Window as TauriWindow } from "@tauri-apps/api/window";
import { ask } from "@tauri-apps/plugin-dialog";
import {
  ChevronRight,
  ExternalLink,
  FileCode2,
  Loader2,
  Lock,
  LocateFixed,
  Play,
  RotateCcw,
  TriangleAlert,
  X,
  XCircle,
} from "lucide-react";
import {
  useEffect,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
} from "react";
import { createPortal } from "react-dom";
import { defaultKeymap, history, historyKeymap, indentWithTab } from "@codemirror/commands";
import {
  HighlightStyle,
  StreamLanguage,
  bracketMatching,
  indentOnInput,
  syntaxHighlighting,
} from "@codemirror/language";
import { stex } from "@codemirror/legacy-modes/mode/stex";
import { highlightSelectionMatches, search, searchKeymap } from "@codemirror/search";
import {
  Compartment,
  EditorState,
  RangeSet,
  RangeSetBuilder,
  StateEffect,
  StateField,
  type Extension,
} from "@codemirror/state";
import {
  Decoration,
  EditorView,
  GutterMarker,
  drawSelection,
  dropCursor,
  gutterLineClass,
  highlightActiveLine,
  highlightActiveLineGutter,
  highlightSpecialChars,
  keymap,
  lineNumbers,
  type DecorationSet,
} from "@codemirror/view";
import { tags as t } from "@lezer/highlight";
import { api, base64ToBytes } from "../api";
import type { LiveCompileIssue, LiveCompileResult, LiveEditSession } from "../types";
import { LIVE_EDIT_ERR_CONFLICT, LIVE_EDIT_ERR_RUN_ACTIVE, compileErrors } from "../types";
import { PdfPreview, type PdfPreviewHandle } from "./PdfPreview";
import "./live-editor.css";

interface LiveEditorProps {
  runDir: string;
  /** Called after any pending save has been written (or the user chose to drop it). */
  onClose: () => void;
}

type SaveState = "clean" | "dirty" | "saving" | "saved";

/** What one save did: nothing to write (or same as on disk), wrote the file, or failed. */
type SaveOutcome = "clean" | "wrote" | "failed";

/** A save failed for a reason that may pass; gaveUp: the automatic retries are used up. */
interface SaveTrouble {
  message: string;
  gaveUp: boolean;
}

type CompileView =
  | { kind: "done"; result: LiveCompileResult; at: Date }
  | { kind: "error"; message: string; at: Date };

type Tone = "muted" | "busy" | "ok" | "warn" | "error";

const AUTOSAVE_MS = 800;
/** Waits before the automatic retries of a failed save; after the last one the user retries by hand. */
const RETRY_DELAYS_MS = [1000, 2000, 4000, 8000];
const NOTICE_MS = 5000;
const SPLIT_KEY = "po.liveEditor.split";
const IS_MAC = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);
const MOD = IS_MAC ? "⌘" : "Ctrl+";

export function LiveEditor({ runDir, onClose }: LiveEditorProps) {
  const [session, setSession] = useState<LiveEditSession | null>(null);
  const [openError, setOpenError] = useState("");
  const [file, setFile] = useState("");
  const [saveState, setSaveState] = useState<SaveState>("clean");
  /** A save error retrying cannot fix (conflict, run started): the editor is read-only. */
  const [saveLock, setSaveLock] = useState("");
  const [saveTrouble, setSaveTrouble] = useState<SaveTrouble | null>(null);
  const [compiling, setCompiling] = useState(false);
  const [compile, setCompile] = useState<CompileView | null>(null);
  const [notice, setNotice] = useState<{ text: string; tone: Tone } | null>(null);
  const [pdfData, setPdfData] = useState<Uint8Array | null>(null);
  /** The latest PDF the preview has (for 用系统阅读器打开). */
  const [pdfPath, setPdfPath] = useState<string | null>(null);
  const [pdfStale, setPdfStale] = useState(false);
  /** Files that have a backup copy (session.backedUp, then hasBackup of every read / save / restore). */
  const [backups, setBackups] = useState<Set<string>>(() => new Set());
  const [issuesOpen, setIssuesOpen] = useState(false);
  const [split, setSplit] = useState(loadSplit);
  const [busy, setBusy] = useState<"" | "switch" | "restore" | "close">("");

  const editorHostRef = useRef<HTMLDivElement>(null);
  const bodyRef = useRef<HTMLDivElement>(null);
  const pdfRef = useRef<PdfPreviewHandle>(null);
  const viewRef = useRef<EditorView | null>(null);
  const mountedRef = useRef(false);
  const sessionRef = useRef<LiveEditSession | null>(null);
  const fileRef = useRef("");
  const mtimes = useRef(new Map<string, number>());
  /** The text each file has on disk as far as this editor knows (read, saved or restored). */
  const diskText = useRef(new Map<string, string>());
  /** Editor state (undo history) and scroll position of files switched away from. */
  const stateCache = useRef(new Map<string, { state: EditorState; scroll: StateEffect<unknown> }>());
  const dirtyRef = useRef(false);
  const editGen = useRef(0);
  const saveTimer = useRef(0);
  const saveInFlight = useRef<Promise<SaveOutcome> | null>(null);
  /** Failed saves in a row (picks the next retry delay). */
  const saveFailures = useRef(0);
  const retryTimer = useRef(0);
  /** Set after a conflict / run-active save error: no more writes until the editor is reopened. */
  const autosaveOff = useRef(false);
  const runActiveRef = useRef(false);
  const compilingRef = useRef(false);
  const compileQueued = useRef(false);
  /** > 0 while closing: a save then does not start a compile (heldWrite remembers it did write). */
  const holdCompile = useRef(0);
  const heldWrite = useRef(false);
  const hasPdfRef = useRef(false);
  const pdfStaleRef = useRef(false);
  const hadProblems = useRef(false);
  const busyRef = useRef(false);
  const closingRef = useRef(false);
  const windowClosing = useRef(false);
  const flashTimer = useRef(0);
  const noticeTimer = useRef(0);
  /** Ends a divider drag that is still going (also on unmount). */
  const dragStop = useRef<(() => void) | null>(null);
  const compartments = useRef({
    lang: new Compartment(),
    readOnly: new Compartment(),
    dark: new Compartment(),
  }).current;

  /* ---- small helpers ---------------------------------------------------- */

  function locked(): boolean {
    return runActiveRef.current || autosaveOff.current;
  }

  function showNotice(text: string, tone: Tone = "muted") {
    window.clearTimeout(noticeTimer.current);
    setNotice({ text, tone });
    noticeTimer.current = window.setTimeout(() => setNotice(null), NOTICE_MS);
  }

  function setBackup(f: string, has: boolean) {
    setBackups((prev) => {
      if (prev.has(f) === has) return prev;
      const next = new Set(prev);
      if (has) next.add(f);
      else next.delete(f);
      return next;
    });
  }

  function setStale(stale: boolean) {
    pdfStaleRef.current = stale;
    setPdfStale(stale);
  }

  /** Edits not on disk yet: typed but not saved, or a save still on its way. */
  function hasUnsaved(): boolean {
    return dirtyRef.current || saveInFlight.current != null;
  }

  function makeState(doc: string, forFile: string): EditorState {
    return EditorState.create({
      doc,
      extensions: [
        baseExtensions(),
        compartments.lang.of(languageFor(forFile)),
        compartments.readOnly.of(EditorState.readOnly.of(locked())),
        compartments.dark.of(EditorView.darkTheme.of(prefersDark())),
        EditorView.updateListener.of((u) => {
          if (u.docChanged) actions.current.onDocChanged();
        }),
      ],
    });
  }

  /** Makes the open editor match the current read-only flag and colour scheme. */
  function syncCompartments(view: EditorView) {
    view.dispatch({
      effects: [
        compartments.readOnly.reconfigure(EditorState.readOnly.of(locked())),
        compartments.dark.reconfigure(EditorView.darkTheme.of(prefersDark())),
      ],
    });
  }

  /* ---- saving ----------------------------------------------------------- */

  function onDocChanged() {
    dirtyRef.current = true;
    editGen.current++;
    setSaveState("dirty");
    if (locked()) return;
    window.clearTimeout(saveTimer.current);
    // A retry is already planned: it writes the newest text.
    if (retryTimer.current) return;
    saveTimer.current = window.setTimeout(() => void saveNow(), AUTOSAVE_MS);
  }

  function clearSaveTimers() {
    window.clearTimeout(saveTimer.current);
    window.clearTimeout(retryTimer.current);
    retryTimer.current = 0;
  }

  /**
   * Writes the open file if it has unsaved changes. Every path that saves goes
   * through here, and a save that wrote something starts a compile itself.
   */
  async function saveNow(): Promise<SaveOutcome> {
    clearSaveTimers();
    while (saveInFlight.current) await saveInFlight.current;
    // The save waited for may have failed and planned a retry: this call is that retry.
    clearSaveTimers();
    if (!dirtyRef.current) return "clean";
    const view = viewRef.current;
    if (!view || locked()) return "failed";
    const p = writeFile(view);
    saveInFlight.current = p;
    try {
      return await p;
    } finally {
      if (saveInFlight.current === p) saveInFlight.current = null;
    }
  }

  async function writeFile(view: EditorView): Promise<SaveOutcome> {
    const target = fileRef.current;
    const gen = editGen.current;
    const content = view.state.doc.toString();
    // The backend writes nothing when the text equals the file on disk.
    const wrote = content !== diskText.current.get(target);
    setSaveState("saving");
    try {
      const res = await api.liveEditSave(runDir, target, content, mtimes.current.get(target) ?? null);
      mtimes.current.set(target, res.mtimeMs);
      diskText.current.set(target, content);
      saveFailures.current = 0;
      if (!mountedRef.current) return wrote ? "wrote" : "clean";
      setBackup(target, res.hasBackup);
      setSaveTrouble(null);
      if (editGen.current === gen && fileRef.current === target) {
        dirtyRef.current = false;
        setSaveState("saved");
      } else {
        // More typing arrived while writing: its own timer saves it.
        setSaveState("dirty");
      }
      if (wrote) compileAfterWrite();
      return wrote ? "wrote" : "clean";
    } catch (e) {
      const message = errText(e);
      if (!mountedRef.current) return "failed";
      setSaveState("dirty");
      // The retry (or the user) writes the newest text; no separate autosave.
      clearSaveTimers();
      if (message === LIVE_EDIT_ERR_CONFLICT || message === LIVE_EDIT_ERR_RUN_ACTIVE) {
        autosaveOff.current = true;
        setSaveTrouble(null);
        setSaveLock(message);
        if (viewRef.current) syncCompartments(viewRef.current);
        return "failed";
      }
      // Anything else may pass (disk busy, file briefly in use...): keep editing, retry later.
      const delay = RETRY_DELAYS_MS[saveFailures.current++];
      setSaveTrouble({ message, gaveUp: delay == null });
      if (delay != null) {
        retryTimer.current = window.setTimeout(() => {
          retryTimer.current = 0;
          void saveNow();
        }, delay);
      }
      return "failed";
    }
  }

  function compileAfterWrite() {
    if (holdCompile.current > 0) heldWrite.current = true;
    else void requestCompile();
  }

  /** 重试保存 / ⌘S: try right away, and start the automatic retries over. */
  async function retrySave(): Promise<SaveOutcome> {
    saveFailures.current = 0;
    return saveNow();
  }

  /** Saves until nothing is pending (waits for a save on its way; typing during a save is saved too). */
  async function flushSave(): Promise<boolean> {
    for (let i = 0; i < 5; i++) {
      if ((await saveNow()) === "failed") return false;
      if (!dirtyRef.current) return true;
    }
    return !dirtyRef.current;
  }

  /** Closing: saves made now do not start a compile; if the close is called off, compile what they wrote. */
  async function withCompileHeld<T>(fn: (closed: () => void) => Promise<T>): Promise<T> {
    let didClose = false;
    holdCompile.current++;
    try {
      return await fn(() => {
        didClose = true;
      });
    } finally {
      holdCompile.current--;
      if (holdCompile.current === 0 && heldWrite.current) {
        heldWrite.current = false;
        if (!didClose && mountedRef.current) void requestCompile();
      }
    }
  }

  /* ---- compiling -------------------------------------------------------- */

  async function requestCompile() {
    if (compilingRef.current) {
      compileQueued.current = true;
      return;
    }
    compilingRef.current = true;
    setCompiling(true);
    try {
      do {
        compileQueued.current = false;
        await compileOnce();
      } while (compileQueued.current && mountedRef.current);
    } finally {
      compilingRef.current = false;
      if (mountedRef.current) setCompiling(false);
    }
  }

  async function compileOnce() {
    const s = sessionRef.current;
    if (!s) return;
    try {
      const result = await api.liveEditCompile(runDir, s.mainFile);
      if (!mountedRef.current) return;
      let bytes: Uint8Array | null = null;
      let readError = "";
      // A failed compile leaves the last good PDF in place: only load it if nothing is shown yet.
      if (result.pdfPath && (result.ok || !hasPdfRef.current)) {
        try {
          bytes = base64ToBytes(await api.readFileBase64(result.pdfPath));
        } catch (e) {
          readError = errText(e);
        }
      }
      if (!mountedRef.current) return;
      if (bytes) {
        hasPdfRef.current = true;
        setPdfData(bytes);
      }
      if (result.pdfPath) setPdfPath(result.pdfPath);
      // What is on screen is not this compile's PDF (it failed, or the new one could not be read).
      setStale(hasPdfRef.current && !(result.ok && bytes));
      // Open the problem list when problems appear (not again while they stay, the user may have folded it).
      const hasProblems = !result.ok || compileErrors(result.issues).length > 0;
      if (hasProblems && !hadProblems.current) setIssuesOpen(true);
      hadProblems.current = hasProblems;
      setCompile({ kind: "done", result, at: new Date() });
      if (readError) showNotice(`PDF 读不出来：${readError}`, "error");
    } catch (e) {
      if (!mountedRef.current) return;
      if (!hadProblems.current) setIssuesOpen(true);
      hadProblems.current = true;
      setStale(hasPdfRef.current);
      setCompile({ kind: "error", message: errText(e), at: new Date() });
    }
  }

  /** ⌘S / 立即编译: save now (a failed save is retried right away) and compile. */
  async function saveAndCompile() {
    if (!sessionRef.current || busyRef.current) return;
    // A save that wrote has started its compile already.
    if ((await retrySave()) === "clean") void requestCompile();
  }

  /* ---- navigation ------------------------------------------------------- */

  async function switchFile(next: string): Promise<boolean> {
    const s = sessionRef.current;
    const view = viewRef.current;
    if (!s || !view) return false;
    if (next === fileRef.current) return true;
    if (busyRef.current) return false;
    busyRef.current = true;
    setBusy("switch");
    try {
      // A save here starts its compile like any other (the switch must not swallow it).
      if (!(await flushSave())) {
        showNotice("当前文件还没保存好，先不切换。", "error");
        return false;
      }
      const res = await api.liveEditRead(runDir, next);
      if (!mountedRef.current || viewRef.current !== view) return false;
      stateCache.current.set(fileRef.current, { state: view.state, scroll: view.scrollSnapshot() });
      mtimes.current.set(next, res.mtimeMs);
      diskText.current.set(next, res.content);
      setBackup(next, res.hasBackup);
      const cached = stateCache.current.get(next);
      // Reuse the earlier editor state (keeps undo history) only if the file is unchanged on disk.
      const reuse = !!cached && cached.state.doc.toString() === res.content;
      fileRef.current = next;
      view.setState(reuse ? cached.state : makeState(res.content, next));
      syncCompartments(view);
      if (reuse) view.dispatch({ effects: cached.scroll });
      dirtyRef.current = false;
      setSaveState("clean");
      setFile(next);
      return true;
    } catch (e) {
      showNotice(`打不开 ${next}：${errText(e)}`, "error");
      return false;
    } finally {
      busyRef.current = false;
      if (mountedRef.current) setBusy("");
    }
  }

  function revealLine(lineNo: number) {
    const view = viewRef.current;
    if (!view) return;
    const doc = view.state.doc;
    const line = doc.line(Math.min(Math.max(1, Math.round(lineNo)), doc.lines));
    view.dispatch({
      selection: { anchor: line.from },
      effects: [EditorView.scrollIntoView(line.from, { y: "center" }), setFlashLine.of(line.from)],
    });
    view.focus();
    window.clearTimeout(flashTimer.current);
    flashTimer.current = window.setTimeout(() => {
      viewRef.current?.dispatch({ effects: setFlashLine.of(null) });
    }, 1600);
  }

  async function jumpTo(target: string, line: number | null) {
    const s = sessionRef.current;
    if (!s) return;
    const rel = normalizeRel(target);
    if (!s.files.includes(rel)) {
      showNotice(`这段内容来自 ${rel}，这里只能编辑 .tex 和 .bib 文件。`, "warn");
      return;
    }
    if (rel !== fileRef.current && !(await switchFile(rel))) return;
    if (line) revealLine(line);
    else viewRef.current?.focus();
  }

  /**
   * Why SyncTeX would not match the PDF on screen right now ("" when it would):
   * a compile rewrites the .synctex.gz, and a failed one leaves an older PDF up.
   */
  function syncBlocker(): string {
    if (compilingRef.current) return "正在编译，编完再定位。";
    if (!pdfRef.current?.hasDocument()) return "先编译一次才能定位。";
    if (pdfStaleRef.current) return "PDF 不是最新的，修好错误重新编译后才能定位。";
    return "";
  }

  async function syncToPdf() {
    const s = sessionRef.current;
    const view = viewRef.current;
    if (!s || !view) return;
    const blocked = syncBlocker();
    if (blocked) {
      showNotice(blocked, "warn");
      return;
    }
    const head = view.state.selection.main.head;
    const line = view.state.doc.lineAt(head);
    try {
      const box = await api.liveEditSyncView(runDir, s.mainFile, fileRef.current, line.number, head - line.from + 1);
      if (!box) {
        showNotice(
          dirtyRef.current
            ? "先编译一次才能定位（刚改的内容还没编译）。"
            : "这一行在 PDF 里没有对应位置（比如导言区、注释或空行）。",
          "warn"
        );
        return;
      }
      if (!pdfRef.current?.showBox(box)) showNotice("PDF 里没有这一页，先重新编译一次。", "warn");
    } catch (e) {
      // The backend's message says what to do (e.g. no new PDF from the last compile).
      showNotice(errText(e), "error");
    }
  }

  async function syncFromPdf(page: number, x: number, y: number) {
    const s = sessionRef.current;
    if (!s) return;
    const blocked = syncBlocker();
    if (blocked) {
      showNotice(blocked, "warn");
      return;
    }
    try {
      const src = await api.liveEditSyncEdit(runDir, s.mainFile, page, x, y);
      if (!src) {
        showNotice("这里找不到对应的源码（可能来自模板或样式文件）。", "warn");
        return;
      }
      await jumpTo(src.file, src.line);
    } catch (e) {
      showNotice(errText(e), "error");
    }
  }

  async function openPdfInSystem() {
    if (!pdfPath) return;
    try {
      await api.openPathInSystem(pdfPath);
    } catch (e) {
      showNotice(`打不开 PDF：${errText(e)}`, "error");
    }
  }

  /* ---- restore & close -------------------------------------------------- */

  async function restoreOriginal() {
    const view = viewRef.current;
    const target = fileRef.current;
    if (!view || !target || busyRef.current || runActiveRef.current) return;
    const yes = await ask(
      `把「${target}」恢复成流水线写出的原稿吗？\n在这里对它做过的修改会全部丢掉，不能撤销。`,
      { title: "恢复原稿", kind: "warning", okLabel: "恢复原稿", cancelLabel: "取消" }
    ).catch(() => false);
    if (!yes || !mountedRef.current || fileRef.current !== target) return;
    busyRef.current = true;
    setBusy("restore");
    try {
      // The unsaved edits are dropped on purpose; the compile below covers the restored text.
      clearSaveTimers();
      // Let a save that is already on its way land first, then overwrite it.
      while (saveInFlight.current) await saveInFlight.current;
      clearSaveTimers();
      const res = await api.liveEditRestore(runDir, target);
      if (!mountedRef.current || viewRef.current !== view || fileRef.current !== target) return;
      clearSaveTimers();
      mtimes.current.set(target, res.mtimeMs);
      diskText.current.set(target, res.content);
      setBackup(target, res.hasBackup);
      stateCache.current.delete(target);
      view.setState(makeState(res.content, target));
      syncCompartments(view);
      dirtyRef.current = false;
      saveFailures.current = 0;
      setSaveTrouble(null);
      setSaveState("clean");
      showNotice("已恢复原稿。", "ok");
      void requestCompile();
    } catch (e) {
      showNotice(`恢复失败：${errText(e)}`, "error");
      // Nothing was restored: the edits whose autosave was cancelled above still need saving.
      if (mountedRef.current && dirtyRef.current) void saveNow();
    } finally {
      busyRef.current = false;
      if (mountedRef.current) setBusy("");
    }
  }

  function askDropUnsaved(title: string): Promise<boolean> {
    return ask("还有修改没保存成功，关掉后这些修改会丢失。\n确定要关掉吗？", {
      title,
      kind: "warning",
      okLabel: "仍然关闭",
      cancelLabel: "留在这里",
    }).catch(() => false);
  }

  async function requestClose() {
    if (closingRef.current) return;
    closingRef.current = true;
    setBusy("close");
    try {
      await withCompileHeld(async (closed) => {
        if (hasUnsaved()) {
          const saved = !locked() && (await flushSave());
          if (!saved) {
            if (!(await askDropUnsaved("关闭编辑页"))) return;
            dirtyRef.current = false;
            clearSaveTimers();
          }
        }
        closed();
        onClose();
      });
    } finally {
      closingRef.current = false;
      if (mountedRef.current) setBusy("");
    }
  }

  /** The window (app) is being closed while edits are unsaved: save first, close only if that worked or the user agrees. */
  async function closeWindowAfterSave() {
    if (windowClosing.current) return;
    windowClosing.current = true;
    try {
      await withCompileHeld(async (closed) => {
        const saved = !locked() && (await flushSave());
        if (!saved && !(await askDropUnsaved("关闭窗口"))) return;
        try {
          await currentWindow()?.destroy();
          closed();
        } catch (e) {
          if (mountedRef.current) showNotice(`关不掉窗口：${errText(e)}`, "error");
        }
      });
    } finally {
      windowClosing.current = false;
    }
  }

  // Event listeners registered once call the latest versions of these.
  const actions = useRef({
    onDocChanged,
    saveAndCompile,
    syncToPdf,
    syncFromPdf,
    requestClose,
    closeWindowAfterSave,
    hasUnsaved,
    saveNow,
  });
  actions.current = {
    onDocChanged,
    saveAndCompile,
    syncToPdf,
    syncFromPdf,
    requestClose,
    closeWindowAfterSave,
    hasUnsaved,
    saveNow,
  };

  /* ---- lifecycle -------------------------------------------------------- */

  useEffect(() => {
    mountedRef.current = true;
    let cancelled = false;
    (async () => {
      try {
        const s = await api.liveEditOpen(runDir);
        if (cancelled) return;
        const first = await api.liveEditRead(runDir, s.mainFile);
        if (cancelled || !editorHostRef.current) return;
        sessionRef.current = s;
        runActiveRef.current = s.runActive;
        fileRef.current = s.mainFile;
        mtimes.current.set(s.mainFile, first.mtimeMs);
        diskText.current.set(s.mainFile, first.content);
        const view = new EditorView({ state: makeState(first.content, s.mainFile), parent: editorHostRef.current });
        viewRef.current = view;
        setSession(s);
        setFile(s.mainFile);
        const backedUp = new Set(s.backedUp.map(normalizeRel));
        if (first.hasBackup) backedUp.add(s.mainFile);
        else backedUp.delete(s.mainFile);
        setBackups(backedUp);
        view.focus();
        void requestCompile();
      } catch (e) {
        if (!cancelled) setOpenError(errText(e));
      }
    })();
    return () => {
      cancelled = true;
      mountedRef.current = false;
      clearSaveTimers();
      window.clearTimeout(flashTimer.current);
      window.clearTimeout(noticeTimer.current);
      dragStop.current?.();
      document.body.classList.remove("le-resizing");
      const view = viewRef.current;
      // Closed some other way (not through requestClose): still write the last edit.
      if (view && dirtyRef.current && !locked() && !saveInFlight.current) {
        void api
          .liveEditSave(runDir, fileRef.current, view.state.doc.toString(), mtimes.current.get(fileRef.current) ?? null)
          .catch(() => undefined);
      }
      view?.destroy();
      viewRef.current = null;
      sessionRef.current = null;
      stateCache.current.clear();
      diskText.current.clear();
    };
  }, [runDir]);

  // Closing the window (or reloading the page) must not lose unsaved edits.
  // Tauri's onCloseRequested destroys the window itself unless preventDefault
  // is called before the handler's first await.
  useEffect(() => {
    let gone = false;
    let unlisten: (() => void) | null = null;
    currentWindow()
      ?.onCloseRequested((event) => {
        if (!actions.current.hasUnsaved()) return;
        event.preventDefault();
        void actions.current.closeWindowAfterSave();
      })
      .then(
        (fn) => {
          if (gone) fn();
          else unlisten = fn;
        },
        () => undefined
      );
    function onBeforeUnload(e: BeforeUnloadEvent) {
      if (!actions.current.hasUnsaved()) return;
      // Best effort: the save request leaves right away (the page may still go).
      void actions.current.saveNow();
      e.preventDefault();
      e.returnValue = "";
    }
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => {
      gone = true;
      unlisten?.();
      window.removeEventListener("beforeunload", onBeforeUnload);
    };
  }, []);

  // Shortcuts work wherever the focus is (editor, PDF, toolbar). Capture phase,
  // so ⌘↵ does not also insert a line in the editor.
  useEffect(() => {
    function onKeyCapture(e: KeyboardEvent) {
      if (e.isComposing || e.keyCode === 229) return;
      const mod = IS_MAC ? e.metaKey && !e.ctrlKey : e.ctrlKey && !e.metaKey;
      if (!mod || e.altKey) return;
      if (!e.shiftKey && e.key.toLowerCase() === "s") {
        e.preventDefault();
        e.stopPropagation();
        void actions.current.saveAndCompile();
      } else if (e.key === "Enter") {
        e.preventDefault();
        e.stopPropagation();
        void actions.current.syncToPdf();
      }
    }
    // Bubble phase: the search panel closes itself on Esc first (and marks the event handled).
    function onKey(e: KeyboardEvent) {
      if (e.key !== "Escape" || e.defaultPrevented || e.isComposing || e.keyCode === 229) return;
      e.preventDefault();
      void actions.current.requestClose();
    }
    window.addEventListener("keydown", onKeyCapture, true);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKeyCapture, true);
      window.removeEventListener("keydown", onKey);
    };
  }, []);

  // Follow the system's light / dark switch (CodeMirror's own panels use the flag).
  useEffect(() => {
    let mq: MediaQueryList;
    try {
      mq = window.matchMedia("(prefers-color-scheme: dark)");
    } catch {
      return;
    }
    const onChange = () => {
      if (viewRef.current) syncCompartments(viewRef.current);
    };
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  // Mark the error lines of the open file.
  useEffect(() => {
    const view = viewRef.current;
    if (!view) return;
    const lines =
      compile?.kind === "done"
        ? compile.result.issues
            .filter((i) => i.line != null && i.file != null && normalizeRel(i.file) === file)
            .map((i) => i.line as number)
        : [];
    view.dispatch({ effects: setErrorLines.of(lines) });
  }, [compile, file]);

  // Keep the page from scrolling under the overlay.
  useEffect(() => {
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, []);

  /* ---- divider ---------------------------------------------------------- */

  function onDividerDown(e: ReactPointerEvent<HTMLDivElement>) {
    const body = bodyRef.current;
    if (!body || e.button !== 0) return;
    e.preventDefault();
    dragStop.current?.();
    const pointerId = e.pointerId;
    try {
      e.currentTarget.setPointerCapture(pointerId);
    } catch {
      // No capture: the window listeners below still see the drag.
    }
    const rect = body.getBoundingClientRect();
    let last = split;
    const move = (ev: PointerEvent) => {
      if (ev.pointerId !== pointerId) return;
      last = clampSplit((ev.clientX - rect.left) / rect.width);
      setSplit(last);
    };
    const end = (ev: PointerEvent) => {
      if (ev.pointerId === pointerId) stop();
    };
    // On the window, not the handle: the release can land anywhere (or capture can be lost).
    const stop = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", end);
      window.removeEventListener("pointercancel", end);
      window.removeEventListener("lostpointercapture", end);
      document.body.classList.remove("le-resizing");
      if (dragStop.current === stop) dragStop.current = null;
      saveSplit(last);
    };
    dragStop.current = stop;
    document.body.classList.add("le-resizing");
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", end);
    window.addEventListener("pointercancel", end);
    window.addEventListener("lostpointercapture", end);
  }

  function onDividerKey(e: ReactKeyboardEvent<HTMLDivElement>) {
    const step = e.shiftKey ? 0.1 : 0.02;
    let next: number | null = null;
    if (e.key === "ArrowLeft") next = split - step;
    else if (e.key === "ArrowRight") next = split + step;
    else if (e.key === "Home") next = 0.5;
    if (next == null) return;
    e.preventDefault();
    const v = clampSplit(next);
    setSplit(v);
    saveSplit(v);
  }

  /* ---- render ----------------------------------------------------------- */

  const runActive = !!session?.runActive;
  const issues = compile?.kind === "done" ? compile.result.issues : [];
  const status = statusLine({ saveState, compiling, compile, notice, saveLock, saveTrouble, ready: !!session });
  const hasBackup = backups.has(file);
  const canRestore = !!session && !runActive && hasBackup && !busy;
  const title = session ? `${session.sourceRel}/${file}` : "";

  const overlay = (
    <div className="le-overlay" role="dialog" aria-modal="true" aria-label="编辑论文">
      <header className="le-head">
        <div className="le-head-main">
          <h2 className="le-title">编辑论文</h2>
          {session && (
            <span className="le-path" title={`${session.sourceDir}/${file}`}>
              <FileCode2 size={13} aria-hidden="true" />
              <span className="le-path-text">{title}</span>
            </span>
          )}
          <span className="le-hint">这是本次运行的最终稿，导出 Overleaf 包和投稿工具用的就是它。</span>
        </div>
        <div className="le-head-actions">
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={() => void openPdfInSystem()}
            disabled={!pdfPath}
            title={pdfPath ? "用系统的 PDF 阅读器打开右边的预览 PDF" : "编译出 PDF 后才能打开"}
          >
            <ExternalLink size={13} aria-hidden="true" />
            用系统阅读器打开 PDF
          </button>
          <button
            type="button"
            className="btn btn-ghost btn-sm le-close"
            onClick={() => void requestClose()}
            disabled={busy === "close"}
            title="关闭（Esc）"
          >
            {busy === "close" ? <Loader2 size={14} className="spin" aria-hidden="true" /> : <X size={14} aria-hidden="true" />}
            关闭
          </button>
        </div>
      </header>

      <div className="le-toolbar">
        <label className="le-file">
          <span className="le-sr">正在编辑的文件</span>
          <select
            className="select le-file-select"
            value={file}
            disabled={!session || busy !== ""}
            onChange={(e) => void switchFile(e.target.value)}
          >
            {(session?.files ?? []).map((f) => (
              <option key={f} value={f}>
                {f === session?.mainFile ? `${f}（主文件）` : f}
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          className="btn btn-primary btn-sm"
          onClick={() => void saveAndCompile()}
          disabled={!session || busy !== ""}
          title={`保存并马上编译（${MOD}S）`}
        >
          {compiling ? <Loader2 size={13} className="spin" aria-hidden="true" /> : <Play size={13} aria-hidden="true" />}
          立即编译
          <kbd className="le-kbd">{MOD}S</kbd>
        </button>
        <button
          type="button"
          className="btn btn-sm"
          onClick={() => void syncToPdf()}
          disabled={!session || busy !== ""}
          title={`在右边的 PDF 里找到光标所在的这一行（${MOD}↵）`}
        >
          <LocateFixed size={13} aria-hidden="true" />
          在 PDF 中定位
          <kbd className="le-kbd">{MOD}↵</kbd>
        </button>
        <button
          type="button"
          className="btn btn-sm"
          onClick={() => void restoreOriginal()}
          disabled={!canRestore}
          title={
            runActive
              ? "流水线还在跑，跑完才能恢复"
              : hasBackup
                ? "把这个文件恢复成流水线写出的原稿"
                : "这个文件还没在这里改过，不需要恢复"
          }
        >
          {busy === "restore" ? (
            <Loader2 size={13} className="spin" aria-hidden="true" />
          ) : (
            <RotateCcw size={13} aria-hidden="true" />
          )}
          恢复原稿
        </button>
        <div className={`le-status is-${status.tone}`} role="status" aria-live="polite">
          {status.tone === "busy" ? (
            <Loader2 size={13} className="spin" aria-hidden="true" />
          ) : (
            <span className="le-status-dot" aria-hidden="true" />
          )}
          <span className="le-status-text">{status.text}</span>
        </div>
      </div>

      {runActive && (
        <div className="le-banner is-warn" role="note">
          <Lock size={14} aria-hidden="true" />
          流水线还在跑，现在只能看不能改。跑完后重新打开就能编辑。
        </div>
      )}
      {saveLock ? (
        <div className="le-banner is-error" role="alert">
          <XCircle size={14} aria-hidden="true" />
          <span>
            <strong>保存没成功：</strong>
            {saveLock.replace(/^保存失败：/, "")} 自动保存已停止，编辑区暂时只读（没保存的内容可以先复制出来）。
          </span>
        </div>
      ) : (
        saveTrouble?.gaveUp && (
          <div className="le-banner is-error" role="alert">
            <XCircle size={14} aria-hidden="true" />
            <span className="le-banner-text">
              <strong>保存没成功：</strong>
              {saveTrouble.message.replace(/^保存失败：/, "")} 修改还在编辑区里，可以继续改。
            </span>
            <button
              type="button"
              className="btn btn-sm le-banner-action"
              onClick={() => void retrySave()}
              disabled={saveState === "saving"}
            >
              {saveState === "saving" ? (
                <Loader2 size={13} className="spin" aria-hidden="true" />
              ) : (
                <RotateCcw size={13} aria-hidden="true" />
              )}
              重试保存
            </button>
          </div>
        )
      )}

      <div className="le-body" ref={bodyRef} style={{ gridTemplateColumns: `minmax(0, ${split}fr) 6px minmax(0, ${1 - split}fr)` }}>
        <section className="le-pane le-pane-code" aria-label="LaTeX 源码">
          <div className="le-editor" ref={editorHostRef} />
          {!session && (
            <div className="le-pane-cover">
              {openError ? (
                <div className="le-open-error" role="alert">
                  <XCircle size={18} aria-hidden="true" />
                  <div>
                    <div className="le-open-error-title">打不开编辑页</div>
                    <div>{openError}</div>
                  </div>
                </div>
              ) : (
                <span className="le-loading">
                  <Loader2 size={16} className="spin" aria-hidden="true" /> 正在打开…
                </span>
              )}
            </div>
          )}
          <IssuesPanel
            open={issuesOpen}
            onToggle={() => setIssuesOpen((v) => !v)}
            compile={compile}
            compiling={compiling}
            issues={issues}
            currentFile={file}
            files={session?.files ?? []}
            onPick={(i) => void jumpTo(i.file ?? "", i.line)}
          />
        </section>

        <div
          className="le-divider"
          role="separator"
          aria-orientation="vertical"
          aria-label="拖动调整左右宽度"
          aria-valuemin={20}
          aria-valuemax={80}
          aria-valuenow={Math.round(split * 100)}
          tabIndex={0}
          title="拖动调整左右宽度，双击恢复一半一半"
          onPointerDown={onDividerDown}
          onDoubleClick={() => {
            setSplit(0.5);
            saveSplit(0.5);
          }}
          onKeyDown={onDividerKey}
        />

        <section className="le-pane le-pane-pdf" aria-label="PDF 预览">
          <PdfPreview
            ref={pdfRef}
            data={pdfData}
            staleNote={pdfStale ? "这次没编译出新 PDF，显示的是上一次的结果" : undefined}
            onPageDoubleClick={(p, x, y) => void actions.current.syncFromPdf(p, x, y)}
            placeholder={
              openError ? null : compile?.kind === "error" || (compile?.kind === "done" && !compile.result.pdfPath) ? (
                <span className="le-pdf-msg is-error">还没有 PDF。看看左下方的编译问题。</span>
              ) : (
                <span className="le-pdf-msg">
                  <Loader2 size={16} className="spin" aria-hidden="true" />
                  正在编译，第一次大约要几秒到半分钟…
                </span>
              )
            }
          />
        </section>
      </div>
    </div>
  );

  return createPortal(overlay, document.body);
}

/* --------------------------------------------------------------------------
   Issues panel (under the editor)
   -------------------------------------------------------------------------- */

function IssuesPanel({
  open,
  onToggle,
  compile,
  compiling,
  issues,
  currentFile,
  files,
  onPick,
}: {
  open: boolean;
  onToggle: () => void;
  compile: CompileView | null;
  compiling: boolean;
  issues: LiveCompileIssue[];
  currentFile: string;
  files: string[];
  onPick: (issue: LiveCompileIssue) => void;
}) {
  const result = compile?.kind === "done" ? compile.result : null;
  const failed = compile?.kind === "error" || (result != null && !result.ok);
  const errorCount = compileErrors(issues).length;
  const summary = !compile
    ? compiling
      ? "正在编译…"
      : "还没编译"
    : compile.kind === "error"
      ? "编译没能运行"
      : [
          errorCount ? `${errorCount} 处错误` : "没有错误",
          result && result.warnings > 0 ? `${result.warnings} 个警告` : "",
          result && !result.ok ? "没有生成 PDF" : "",
        ]
          .filter(Boolean)
          .join(" · ");
  const tone = failed || errorCount > 0 ? "is-error" : result ? "is-ok" : "";

  return (
    <div className={`le-issues${open ? " is-open" : ""}`}>
      <button type="button" className="le-issues-head" onClick={onToggle} aria-expanded={open}>
        <ChevronRight size={13} className="le-issues-chev" aria-hidden="true" />
        <span className="le-issues-title">编译问题</span>
        <span className={`le-issues-summary ${tone}`}>{summary}</span>
      </button>
      {open && (
        <div className="le-issues-body">
          {compile?.kind === "error" && <div className="le-issue-plain is-error">{compile.message}</div>}
          {issues.length > 0 && (
            <ul className="le-issue-list">
              {issues.map((issue, i) => {
                const rel = issue.file ? normalizeRel(issue.file) : null;
                const editable = rel != null && files.includes(rel);
                const where = [
                  rel && rel !== currentFile ? rel : "",
                  issue.line != null ? `第 ${issue.line} 行` : "",
                ]
                  .filter(Boolean)
                  .join(" ");
                return (
                  <li key={`${i}:${issue.file}:${issue.line}`}>
                    <button
                      type="button"
                      className={`le-issue${issue.severity === "warning" ? " is-warning" : ""}`}
                      disabled={!editable}
                      title={
                        editable
                          ? "跳到这一行"
                          : rel
                            ? `${rel} 不能在这里编辑`
                            : "LaTeX 没说是哪一行出的错，看看下面的日志"
                      }
                      onClick={() => onPick(issue)}
                    >
                      <TriangleAlert size={13} className="le-issue-icon" aria-hidden="true" />
                      <span className="le-issue-text">
                        {where && <span className="le-issue-where">{where}：</span>}
                        {issue.message}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
          {result && errorCount === 0 && result.ok && (
            <div className="le-issue-plain">
              没有 LaTeX 错误{result.warnings > 0 ? `，有 ${result.warnings} 个警告（一般不影响 PDF）` : ""}。
            </div>
          )}
          {result && (!result.ok || (issues.length > 0 && issues.every((i) => i.line == null))) && result.logTail && (
            <details className="le-log" open={!result.ok}>
              <summary>编译日志（最后几行）</summary>
              <pre>{result.logTail}</pre>
            </details>
          )}
        </div>
      )}
    </div>
  );
}

/* --------------------------------------------------------------------------
   Status line
   -------------------------------------------------------------------------- */

function statusLine({
  saveState,
  compiling,
  compile,
  notice,
  saveLock,
  saveTrouble,
  ready,
}: {
  saveState: SaveState;
  compiling: boolean;
  compile: CompileView | null;
  notice: { text: string; tone: Tone } | null;
  saveLock: string;
  saveTrouble: SaveTrouble | null;
  ready: boolean;
}): { text: string; tone: Tone } {
  if (notice) return notice;
  if (!ready) return { text: "正在打开…", tone: "busy" };
  if (saveLock) return { text: "保存失败，自动保存已停止", tone: "error" };
  if (saveTrouble) {
    return saveTrouble.gaveUp
      ? { text: "保存没成功，修改还没写进文件", tone: "error" }
      : { text: "保存没成功，正在重试…", tone: "warn" };
  }
  if (saveState === "saving") return { text: "正在保存…", tone: "busy" };
  if (compiling) return { text: "正在编译…", tone: "busy" };
  if (saveState === "dirty") return { text: "有修改，马上自动保存", tone: "muted" };
  if (compile?.kind === "error") return { text: `编译没能运行：${compile.message}`, tone: "error" };
  if (compile?.kind === "done") {
    const r = compile.result;
    if (!r.ok) return { text: "编译失败，没有生成 PDF", tone: "error" };
    const errors = compileErrors(r.issues).length;
    if (errors > 0) return { text: `有 ${errors} 处 LaTeX 错误，PDF 可能不完整`, tone: "error" };
    return {
      text: `PDF 已更新 ${formatClock(compile.at)}（用时 ${(r.durationMs / 1000).toFixed(1)} 秒）`,
      tone: "ok",
    };
  }
  if (saveState === "saved") return { text: "已保存", tone: "ok" };
  return { text: "就绪", tone: "muted" };
}

/* --------------------------------------------------------------------------
   CodeMirror setup
   -------------------------------------------------------------------------- */

const setErrorLines = StateEffect.define<number[]>();
const setFlashLine = StateEffect.define<number | null>();

const errorLineDeco = Decoration.line({ class: "le-cm-error" });
const flashLineDeco = Decoration.line({ class: "le-cm-flash" });

class ErrorGutterMarker extends GutterMarker {
  elementClass = "le-cm-error-gutter";
}
const errorGutterMarker = new ErrorGutterMarker();

interface ErrorMarks {
  lines: DecorationSet;
  gutter: RangeSet<GutterMarker>;
}

/** Red background + red line number on the lines LaTeX reported errors for. */
const errorField = StateField.define<ErrorMarks>({
  create: () => ({ lines: Decoration.none, gutter: RangeSet.empty }),
  update(marks, tr) {
    let { lines, gutter } = marks;
    if (tr.docChanged) {
      lines = lines.map(tr.changes);
      gutter = gutter.map(tr.changes);
    }
    for (const e of tr.effects) {
      if (!e.is(setErrorLines)) continue;
      const doc = tr.state.doc;
      const nums = [...new Set(e.value)].filter((n) => n >= 1 && n <= doc.lines).sort((a, b) => a - b);
      const lb = new RangeSetBuilder<Decoration>();
      const gb = new RangeSetBuilder<GutterMarker>();
      for (const n of nums) {
        const from = doc.line(n).from;
        lb.add(from, from, errorLineDeco);
        gb.add(from, from, errorGutterMarker);
      }
      lines = lb.finish();
      gutter = gb.finish();
    }
    return lines === marks.lines && gutter === marks.gutter ? marks : { lines, gutter };
  },
  provide: (f) => [
    EditorView.decorations.from(f, (m) => m.lines),
    gutterLineClass.from(f, (m) => m.gutter),
  ],
});

/** A short highlight on the line a jump landed on. */
const flashField = StateField.define<DecorationSet>({
  create: () => Decoration.none,
  update(deco, tr) {
    deco = deco.map(tr.changes);
    for (const e of tr.effects) {
      if (!e.is(setFlashLine)) continue;
      deco = e.value == null ? Decoration.none : Decoration.set([flashLineDeco.range(e.value)]);
    }
    return deco;
  },
  provide: (f) => EditorView.decorations.from(f),
});

/** Colours come from the app's CSS variables, so light and dark mode both fit. */
const texHighlight = HighlightStyle.define([
  { tag: t.tagName, color: "var(--accent)" },
  { tag: t.atom, color: "var(--info)" },
  { tag: t.keyword, color: "var(--warn)" },
  { tag: [t.string, t.number], color: "var(--success)" },
  { tag: t.comment, color: "var(--text-muted)", fontStyle: "italic" },
  { tag: t.bracket, color: "var(--text-secondary)" },
  { tag: t.invalid, color: "var(--danger)" },
  { tag: t.standard(t.variableName), color: "var(--info)" },
]);

const editorTheme = EditorView.theme({
  "&": {
    height: "100%",
    fontSize: "13px",
    backgroundColor: "var(--bg-card)",
    color: "var(--code-text)",
  },
  ".cm-scroller": {
    fontFamily: "var(--font-mono)",
    lineHeight: "1.65",
  },
  ".cm-content": {
    caretColor: "var(--text-primary)",
    padding: "10px 0 40px",
  },
  ".cm-line": { padding: "0 14px 0 10px" },
  ".cm-cursor, .cm-dropCursor": { borderLeftColor: "var(--text-primary)", borderLeftWidth: "2px" },
  "&.cm-focused > .cm-scroller > .cm-selectionLayer .cm-selectionBackground, .cm-selectionBackground, .cm-content ::selection":
    { backgroundColor: "var(--le-selection)" },
  ".cm-gutters": {
    backgroundColor: "var(--bg-subtle)",
    color: "var(--text-muted)",
    border: "none",
    borderRight: "1px solid var(--border)",
  },
  ".cm-lineNumbers .cm-gutterElement": { padding: "0 8px 0 12px", minWidth: "36px" },
  ".cm-activeLine": { backgroundColor: "var(--le-active-line)" },
  ".cm-activeLineGutter": { backgroundColor: "var(--le-active-line)", color: "var(--text-primary)" },
  "&.cm-focused .cm-matchingBracket": {
    backgroundColor: "var(--le-bracket)",
    outline: "1px solid var(--accent-border)",
  },
  "&.cm-focused .cm-nonmatchingBracket": { backgroundColor: "var(--danger-soft)" },
  ".cm-selectionMatch": { backgroundColor: "var(--le-selection-match)" },
  ".cm-searchMatch": { backgroundColor: "var(--warn-soft)", outline: "1px solid var(--warn-border)" },
  ".cm-searchMatch.cm-searchMatch-selected": { backgroundColor: "var(--warn-border)" },
  ".cm-panels": { backgroundColor: "var(--bg-subtle)", color: "var(--text-primary)" },
  ".cm-panels.cm-panels-top": { borderBottom: "1px solid var(--border)" },
  ".cm-panels.cm-panels-bottom": { borderTop: "1px solid var(--border)" },
  ".cm-panel.cm-search": { padding: "6px 10px", fontFamily: "var(--font-sans)", fontSize: "12px" },
  ".cm-panel.cm-search input, .cm-panel.cm-search button, .cm-panel.cm-search label": { fontSize: "12px" },
  ".cm-textfield": {
    backgroundColor: "var(--bg-input)",
    color: "var(--text-primary)",
    border: "1px solid var(--border-control)",
    borderRadius: "4px",
  },
  ".cm-button": {
    backgroundImage: "none",
    backgroundColor: "var(--bg-card)",
    color: "var(--text-primary)",
    border: "1px solid var(--border-strong)",
    borderRadius: "4px",
  },
  ".cm-panel.cm-search [name=close]": { color: "var(--text-muted)" },
});

/** CodeMirror's own UI text (search panel etc.) in Chinese. */
const PHRASES_ZH: Record<string, string> = {
  Find: "查找",
  Replace: "替换为",
  next: "下一个",
  previous: "上一个",
  all: "全部",
  "match case": "区分大小写",
  regexp: "正则",
  "by word": "整词",
  replace: "替换",
  "replace all": "全部替换",
  close: "关闭",
  "current match": "当前匹配",
  "on line": "在行",
  "replaced $ matches": "替换了 $ 处",
  "replaced match on line $": "替换了第 $ 行的匹配",
  "Go to line": "跳到行",
  go: "跳转",
  "Selection deleted": "已删除选中内容",
  "Control character": "控制字符",
  "folded code": "折叠的代码",
  unfold: "展开",
  to: "到",
};

let sharedBase: Extension | null = null;

function baseExtensions(): Extension {
  if (!sharedBase) {
    sharedBase = [
      lineNumbers(),
      highlightActiveLineGutter(),
      highlightSpecialChars(),
      history(),
      drawSelection(),
      dropCursor(),
      indentOnInput(),
      bracketMatching(),
      highlightActiveLine(),
      highlightSelectionMatches(),
      search({ top: true }),
      EditorView.lineWrapping,
      syntaxHighlighting(texHighlight),
      errorField,
      flashField,
      editorTheme,
      EditorState.phrases.of(PHRASES_ZH),
      EditorView.contentAttributes.of({ spellcheck: "false", autocorrect: "off", autocapitalize: "off" }),
      keymap.of([...searchKeymap, ...historyKeymap, ...defaultKeymap, indentWithTab]),
    ];
  }
  return sharedBase;
}

const texLanguage = StreamLanguage.define(stex);

function languageFor(file: string): Extension {
  return file.toLowerCase().endsWith(".tex") ? texLanguage : [];
}

/* --------------------------------------------------------------------------
   Utilities
   -------------------------------------------------------------------------- */

/** The app window; null outside Tauri (e.g. a plain browser during tests). */
function currentWindow(): TauriWindow | null {
  try {
    return getCurrentWindow();
  } catch {
    return null;
  }
}

function prefersDark(): boolean {
  try {
    return window.matchMedia("(prefers-color-scheme: dark)").matches;
  } catch {
    return false;
  }
}

/** "./sub\\a.tex" -> "sub/a.tex" (backend paths are already like this; be lenient). */
function normalizeRel(p: string): string {
  let s = p.replace(/\\/g, "/");
  while (s.startsWith("./")) s = s.slice(2);
  return s;
}

function formatClock(d: Date): string {
  return d.toLocaleTimeString("zh-CN", { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function errText(e: unknown): string {
  if (e instanceof Error) return e.message;
  if (typeof e === "string") return e;
  try {
    return JSON.stringify(e);
  } catch {
    return String(e);
  }
}

function clampSplit(v: number): number {
  return Math.min(0.8, Math.max(0.2, v));
}

function loadSplit(): number {
  try {
    const v = Number(window.localStorage.getItem(SPLIT_KEY));
    return v > 0 ? clampSplit(v) : 0.5;
  } catch {
    return 0.5;
  }
}

function saveSplit(v: number) {
  try {
    window.localStorage.setItem(SPLIT_KEY, String(Math.round(v * 1000) / 1000));
  } catch {
    // storage unavailable: the ratio just is not remembered
  }
}
