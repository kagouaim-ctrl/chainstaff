import { useCallback, useEffect, useMemo, useState, useSyncExternalStore } from "react";

// ---------- routing: hash based, so the FastAPI static mount needs no rewrites ----------

export interface Route {
  page: string;
  id?: string;
  params: URLSearchParams;
}

function parse(): Route {
  const h = location.hash.replace(/^#\/?/, "");
  const [path, qs] = h.split("?");
  const [page, id] = path.split("/");
  return { page: page || "overview", id: id ? decodeURIComponent(id) : undefined, params: new URLSearchParams(qs || "") };
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

// ---------- formatting ----------

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
  if (s < 5) return "刚刚";
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
