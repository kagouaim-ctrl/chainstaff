import { useCallback, useEffect, useMemo, useState, useSyncExternalStore } from "react";

// ---------- routing: hash based, so the FastAPI static mount needs no rewrites ----------

export interface Route {
  page: string;
  id?: string;
  params: URLSearchParams;
}

// Old links keep working: the first terminal's roster and "workers" open Agents; the old Overview opens the chat
// workspace, which replaced it as the home page.
const LEGACY: Record<string, string> = { workforce: "agents", workers: "agents", overview: "chat" };

function parse(): Route {
  const h = location.hash.replace(/^#\/?/, "");
  const [path, qs] = h.split("?");
  const [page, id] = path.split("/");
  return { page: LEGACY[page] || page || "chat", id: id ? decodeURIComponent(id) : undefined, params: new URLSearchParams(qs || "") };
}

export function useRoute(): Route {
  const h = useSyncExternalStore(
    (f) => {
      window.addEventListener("hashchange", f);
      return () => window.removeEventListener("hashchange", f);
    },
    () => location.hash,
  );
  return useMemo(parse, [h]);
}

/** Change some query parameters of the current page without touching the rest. */
export function setParams(route: Route, changes: Record<string, string | null | undefined>, id: string | undefined = route.id) {
  const p = new URLSearchParams(route.params);
  for (const [k, v] of Object.entries(changes)) v ? p.set(k, v) : p.delete(k);
  go(route.page, id, Object.fromEntries(p));
}

export function go(page: string, id?: string, params?: Record<string, string>) {
  const qs = params ? new URLSearchParams(Object.entries(params).filter(([, v]) => v)).toString() : "";
  location.hash = `#/${page}${id ? "/" + encodeURIComponent(id) : ""}${qs ? "?" + qs : ""}`;
}

// ---------- persisted per-viewer preferences ----------

export function usePref<T>(key: string, initial: T): [T, (v: T | ((p: T) => T)) => void] {
  const [v, setV] = useState<T>(() => {
    try {
      const raw = localStorage.getItem("cst:" + key);
      return raw === null ? initial : (JSON.parse(raw) as T);
    } catch {
      return initial;
    }
  });
  const set = useCallback(
    (nv: T | ((p: T) => T)) =>
      setV((prev) => {
        const next = typeof nv === "function" ? (nv as (p: T) => T)(prev) : nv;
        try {
          localStorage.setItem("cst:" + key, JSON.stringify(next));
        } catch {
          /* private mode */
        }
        return next;
      }),
    [key],
  );
  return [v, set];
}

export function useMedia(q: string): boolean {
  const [m, setM] = useState(() => window.matchMedia(q).matches);
  useEffect(() => {
    const mq = window.matchMedia(q);
    const f = () => setM(mq.matches);
    mq.addEventListener("change", f);
    return () => mq.removeEventListener("change", f);
  }, [q]);
  return m;
}

export const useIsMobile = () => useMedia("(max-width: 760px)");

/** Re-render every `ms` so running clocks tick; one interval per component that shows one. */
export function useNow(ms = 1000, on = true): number {
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    if (!on) return;
    const t = window.setInterval(() => setNow(Date.now()), ms);
    return () => window.clearInterval(t);
  }, [ms, on]);
  return now;
}

// ---------- formatting ----------

export function initials(name?: string | null): string {
  if (!name) return "?";
  const words = name.replace(/[^\p{L}\p{N} ]/gu, " ").trim().split(/\s+/).filter(Boolean);
  if (!words.length) return name.slice(0, 2).toUpperCase();
  if (words.length === 1) return words[0].slice(0, 2).toUpperCase();
  return (words[0][0] + words[1][0]).toUpperCase();
}

export function pct(v?: number | null, d = 1): string {
  return v === null || v === undefined ? "—" : `${(v * 100).toFixed(d)}%`;
}

export function secs(v?: number | null): string {
  if (v === null || v === undefined) return "—";
  if (v < 1) return `${Math.round(v * 1000)}ms`;
  if (v < 60) return `${v.toFixed(1)}s`;
  return `${Math.floor(v / 60)}m${Math.round(v % 60)}s`;
}

export const short = (a?: string | null, n = 6) => (a && a.length > 2 * n + 2 ? `${a.slice(0, n)}…${a.slice(-4)}` : a || "");

export function fmtTime(iso?: string | null, withDate = true): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(+d)) return iso;
  return d.toLocaleString("zh-CN", { hour12: false, ...(withDate ? { month: "2-digit", day: "2-digit" } : {}), hour: "2-digit", minute: "2-digit", second: withDate ? undefined : "2-digit" });
}

export function ago(iso?: string | null): string {
  if (!iso) return "—";
  const s = (Date.now() - +new Date(iso)) / 1000;
  if (s < 5) return "now";
  if (s < 60) return `${Math.floor(s)}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h`;
  return `${Math.floor(s / 86400)}d`;
}

export function duration(a?: string | null, b?: string | null): string {
  if (!a) return "—";
  const s = ((b ? +new Date(b) : Date.now()) - +new Date(a)) / 1000;
  if (s < 1) return `${Math.round(s * 1000)}ms`;
  if (s < 60) return `${s.toFixed(1)}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m${Math.round(s % 60)}s`;
  return `${Math.floor(s / 3600)}h${Math.floor((s % 3600) / 60)}m`;
}

export function usd(v?: string | number | null): string {
  const n = Number(v || 0);
  if (n === 0) return "$0";
  return n < 0.01 ? `$${n.toFixed(4)}` : `$${n.toFixed(2)}`;
}

export const num = (v?: number | string | null, d = 0) =>
  v === null || v === undefined || v === "" ? "—" : Number(v).toLocaleString("en-US", { maximumFractionDigits: d });

export function weiToGwei(w?: string | null): string {
  return w ? (Number(w) / 1e9).toLocaleString("en-US", { maximumFractionDigits: 3 }) : "—";
}

export function weiToBnb(w?: string | null): string {
  return w ? (Number(w) / 1e18).toLocaleString("en-US", { maximumFractionDigits: 8 }) : "—";
}

export async function copy(text: string) {
  try {
    await navigator.clipboard.writeText(text);
    toast("已复制");
  } catch {
    toast("复制失败，请手动选择", true);
  }
}

// ---------- toasts ----------

type Toast = { id: number; text: string; bad?: boolean };
let toasts: Toast[] = [];
const toastSubs = new Set<() => void>();
export function toast(text: string, bad = false) {
  const t = { id: Date.now() + Math.random(), text, bad };
  toasts = [...toasts, t];
  toastSubs.forEach((f) => f());
  setTimeout(() => {
    toasts = toasts.filter((x) => x.id !== t.id);
    toastSubs.forEach((f) => f());
  }, bad ? 6000 : 2800);
}
export function useToasts() {
  return useSyncExternalStore(
    (f) => {
      toastSubs.add(f);
      return () => toastSubs.delete(f);
    },
    () => toasts,
  );
}

export const errText = (e: unknown) => (e instanceof Error ? e.message : String(e));

export const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

/** The service behind the server's shared OpenAI-compatible model (AI_PROVIDER), as the console names it. */
export const SERVICE: Record<string, string> = {
  qwen: "Qwen", gemini: "Gemini", groq: "Groq", openrouter: "OpenRouter", local: "本地模型", openai_compatible: "OpenAI 兼容",
};
