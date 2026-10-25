import { QueryClient } from "@tanstack/react-query";
import { DEMO, DEMO_READ_ONLY, demoGet } from "./demo";

// Same token the classic console keeps, so signing in once covers both.
const KEY = "cs-token";

export function getToken(): string {
  try {
    return sessionStorage.getItem(KEY) || "";
  } catch {
    return "";
  }
}

export function setToken(t: string) {
  if (DEMO) return; // the static demo has no session to leave
  try {
    if (t) sessionStorage.setItem(KEY, t);
    else sessionStorage.removeItem(KEY);
  } catch {
    /* storage blocked: the token lives for this page only */
  }
  memToken = t;
  listeners.forEach((f) => f());
}

let memToken = DEMO ? "demo" : getToken();
const listeners = new Set<() => void>();
export const onAuthChange = (f: () => void) => {
  listeners.add(f);
  return () => listeners.delete(f);
};
export const token = () => memToken;

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export async function api<T = any>(path: string, opt: RequestInit & { json?: unknown } = {}): Promise<T> {
  const { json, ...rest } = opt;
  if (DEMO) {
    if ((rest.method || "GET") !== "GET") throw new ApiError(403, DEMO_READ_ONLY);
    const found = await demoGet(path);
    if (found === undefined) throw new ApiError(404, "演示数据里没有这一项");
    return found as T;
  }
  const r = await fetch(path, {
    ...rest,
    body: json !== undefined ? JSON.stringify(json) : rest.body,
    headers: { Authorization: "Bearer " + memToken, "Content-Type": "application/json", ...(rest.headers || {}) },
  });
  if (r.status === 401) {
    setToken("");
    throw new ApiError(401, "令牌不对或已失效");
  }
  const ct = r.headers.get("content-type") || "";
  const body = ct.includes("json") ? await r.json().catch(() => ({})) : await r.text();
  if (!r.ok) throw new ApiError(r.status, (body && (body as any).detail) || `请求失败 ${r.status}`);
  return body as T;
}

export const post = <T = any>(p: string, json?: unknown) => api<T>(p, { method: "POST", json: json ?? {} });

export function newRequestKey(): string {
  try {
    return crypto.randomUUID();
  } catch {
    return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
  }
}

/**
 * POST that creates something exactly once: the Idempotency-Key header lets the server hand back
 * what the first attempt created, so a dropped response is retried here without a second mission.
 */
export async function postOnce<T = any>(p: string, json: unknown, key: string, retries = 2): Promise<T> {
  for (let i = 0; ; i++) {
    try {
      return await api<T>(p, { method: "POST", json, headers: { "Idempotency-Key": key } });
    } catch (e) {
      const retryable = !(e instanceof ApiError) || e.status >= 500; // network failure or server error
      if (!retryable || i >= retries) throw e;
      await new Promise((r) => setTimeout(r, 400 * 2 ** i));
    }
  }
}
export const put = <T = any>(p: string, json: unknown) => api<T>(p, { method: "PUT", json });
export const del = <T = any>(p: string) => api<T>(p, { method: "DELETE" });

export async function download(path: string, filename: string) {
  if (DEMO) throw new ApiError(403, DEMO_READ_ONLY);
  const r = await fetch(path, { headers: { Authorization: "Bearer " + memToken } });
  if (!r.ok) throw new ApiError(r.status, "导出失败");
  const url = URL.createObjectURL(await r.blob());
  const a = Object.assign(document.createElement("a"), { href: url, download: filename });
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: { staleTime: 5_000, refetchOnWindowFocus: false, retry: (n, e) => !(e instanceof ApiError && e.status < 500) && n < 2 },
  },
});
