/**
 * Shell state shared across pages: the New Worker dialog and the Live Operations panel. Opening either is a
 * view change, not data, so it lives here rather than in the URL.
 */
import { useSyncExternalStore } from "react";

type OpsTab = "activity" | "alerts" | "approvals";
interface ShellState {
  creating: boolean;
  ops: OpsTab | null; // null = closed
  sideOpen: boolean; // the sidebar drawer on phones
}

let s: ShellState = { creating: false, ops: null, sideOpen: false };
const subs = new Set<() => void>();
const set = (p: Partial<ShellState>) => {
  s = { ...s, ...p };
  subs.forEach((f) => f());
};

export const useShell = () => useSyncExternalStore((f) => (subs.add(f), () => subs.delete(f)), () => s);
export const openCreateWorker = () => set({ creating: true, sideOpen: false });
export const closeCreateWorker = () => set({ creating: false });
export const openOps = (tab: OpsTab = "activity") => set({ ops: tab });
export const closeOps = () => set({ ops: null });
export const toggleOps = () => set({ ops: s.ops ? null : "activity" });
export const setSideOpen = (v: boolean) => set({ sideOpen: v });
export type { OpsTab };
