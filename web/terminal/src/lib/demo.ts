/// <reference types="vite/client" />
/**
 * Static demo build (VITE_DEMO=1), for hosting the console without a server, e.g. www.kagouai.com/demo/.
 * Every GET is answered from demo-data.json, a snapshot recorded from a KAGOU server running on sample data
 * (scripted model, simulated chain). Nothing is live and nothing can be changed; the header says so.
 */

export const DEMO = import.meta.env.VITE_DEMO === "1";

interface Snapshot {
  recorded_at: string;
  get: Record<string, unknown>;
  ws: Record<string, unknown>[];
}

// Times in the snapshot are moved forward to "now", so the sample reads as recent activity whenever it is opened.
const ISO = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$/;
function shift(v: unknown, ms: number, key = ""): unknown {
  if (typeof v === "string" && ISO.test(v)) return new Date(Date.parse(v) + ms).toISOString();
  if (typeof v === "number" && (key === "ts_ms" || key.endsWith("_ms_at"))) return v + ms;
  if (typeof v === "number" && (key === "last_head_at" || key === "at_s")) return v + ms / 1000;
  if (Array.isArray(v)) return v.map((x) => shift(x, ms));
  if (v && typeof v === "object") return Object.fromEntries(Object.entries(v).map(([k, x]) => [k, shift(x, ms, k)]));
  return v;
}

let data: Promise<Snapshot> | null = null;
const load = () =>
  (data ??= fetch(import.meta.env.BASE_URL + "demo-data.json")
    .then((r) => r.json() as Promise<Snapshot>)
    .then((d) => shift(d, Math.max(0, Date.now() - Date.parse(d.recorded_at))) as Snapshot));

/** The recorded answer for this path; with other filters or pages, the closest recorded variant of the same path. */
export async function demoGet(path: string): Promise<unknown | undefined> {
  const { get } = await load();
  const bare = path.split("?")[0];
  const key = path in get ? path : bare in get ? bare : Object.keys(get).find((k) => k.split("?")[0] === bare);
  return key === undefined ? undefined : structuredClone(get[key]);
}

export async function demoChainFeed(): Promise<Record<string, unknown>[]> {
  return (await load()).ws;
}

export const DEMO_READ_ONLY = "演示版只能查看，不能修改。完整版需要连接 KAGOU 服务器。";
