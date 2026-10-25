import { useSyncExternalStore } from "react";

/**
 * Live mode's connection to the KAGOU server. Every API call and the event stream report here; when the server
 * cannot be reached the console says so in a banner instead of quietly showing what it last had as if it were current.
 * There is no fallback to sample data in live mode.
 */

export interface Health {
  down: boolean;
  reason: string;
  since: number | null; // when the server stopped answering
  lastOk: number | null; // last successful answer: the data on screen is from then
}

let health: Health = { down: false, reason: "", since: null, lastOk: null };
const subs = new Set<() => void>();

function set(next: Health) {
  health = next;
  subs.forEach((f) => f());
}

export function markOk() {
  if (!health.down && health.lastOk && Date.now() - health.lastOk < 1000) return; // nothing new to tell subscribers
  set({ down: false, reason: "", since: null, lastOk: Date.now() });
}

export function markDown(reason: string) {
  if (health.down && health.reason === reason) return;
  set({ down: true, reason, since: health.since ?? Date.now(), lastOk: health.lastOk });
}

/** A failed fetch() (no answer at all) or a gateway/unavailable status means the server is not reachable. */
export const unreachable = (status: number) => status === 502 || status === 503 || status === 504;

export function useHealth(): Health {
  return useSyncExternalStore(
    (f) => {
      subs.add(f);
      return () => subs.delete(f);
    },
    () => health,
  );
}
