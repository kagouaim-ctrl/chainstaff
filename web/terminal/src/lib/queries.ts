import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback } from "react";
import { api, post } from "./api";
import type {
  Agent, AgentDetail, Approval, ChainRecord, ChainStatus, LedgerRow, Memory, Meta, Mission, Overview, Preflight, Schedule, Skill, Squad, TaskDetail, TaskPage, Workspace,
} from "./types";
import { errText, toast } from "./util";

const qs = (params: Record<string, string | number | undefined | null>) =>
  new URLSearchParams(Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== "") as [string, string][]).toString();

export const useMeta = () => useQuery({ queryKey: ["meta"], queryFn: () => api<Meta>("/api/wf/meta"), staleTime: 60_000 });
export const useAgents = () => useQuery({ queryKey: ["agents"], queryFn: () => api<Agent[]>("/api/wf/agents") });
export const useAgent = (id?: string | null) =>
  useQuery({ queryKey: ["agents", id], queryFn: () => api<AgentDetail>(`/api/wf/agents/${id}`), enabled: !!id });
export const useWorkspaces = () => useQuery({ queryKey: ["workspaces"], queryFn: () => api<Workspace[]>("/api/wf/workspaces") });
export const useOverview = (range: string) =>
  useQuery({ queryKey: ["overview", range], queryFn: () => api<Overview>(`/api/wf/overview?range=${range}`), refetchInterval: 30_000 });
export const useTasks = (params: Record<string, string | number | undefined>) => {
  const q = qs(params);
  return useQuery({ queryKey: ["tasks", q], queryFn: () => api<TaskPage>(`/api/wf/tasks?${q}`), placeholderData: (p) => p });
};
export const useTask = (id?: string | null) =>
  useQuery({ queryKey: ["task", id], queryFn: () => api<TaskDetail>(`/api/wf/tasks/${id}`), enabled: !!id });
export const useSquads = (workspace?: string) =>
  useQuery({ queryKey: ["squads", workspace || ""], queryFn: () => api<Squad[]>(`/api/wf/squads${workspace ? `?workspace=${workspace}` : ""}`) });

/** What would stop a mission, asked as the composer changes; nothing is created. */
export const usePreflight = (body: { workspace_id?: string; squad_id?: string; manager_id?: string; text: string; budget_usd: string; attachments?: { name: string; text: string }[] }, enabled = true) =>
  useQuery({
    queryKey: ["preflight", body],
    queryFn: () => post<Preflight>("/api/wf/missions/preflight", body),
    enabled: enabled && !!body.workspace_id,
    placeholderData: (p) => p,
    staleTime: 2_000,
  });

export const useMissions = (params: { workspace?: string; squad?: string; status?: string; agent?: string; q?: string; limit?: number } = {}) => {
  const q = qs(params);
  return useQuery({ queryKey: ["missions", q], queryFn: () => api<Mission[]>(`/api/wf/missions?${q}`), placeholderData: (p) => p });
};
export const useMission = (id?: string | null) =>
  useQuery({ queryKey: ["missions", "one", id], queryFn: () => api<Mission>(`/api/wf/missions/${id}`), enabled: !!id });
export const useLedger = () => useQuery({ queryKey: ["ledger"], queryFn: () => api<LedgerRow[]>("/api/wf/ledger") });
export const useApprovals = (status = "pending") =>
  useQuery({ queryKey: ["approvals", status], queryFn: () => api<Approval[]>(`/api/wf/approvals?status=${status}`) });
export const useChain = () => useQuery({ queryKey: ["chain"], queryFn: () => api<ChainStatus>("/api/wf/chain"), refetchInterval: 15_000 });
export const useRecords = () => useQuery({ queryKey: ["records"], queryFn: () => api<ChainRecord[]>("/api/wf/chain/records") });
export const useSkills = () => useQuery({ queryKey: ["skills"], queryFn: () => api<Skill[]>("/api/wf/skills") });
export const useSchedules = () => useQuery({ queryKey: ["schedules"], queryFn: () => api<Schedule[]>("/api/wf/schedules") });
export const useMemory = (q: string, scope: string) =>
  useQuery({ queryKey: ["memory", q, scope], queryFn: () => api<Memory[]>(`/api/wf/memory?q=${encodeURIComponent(q)}&scope=${scope}`) });

/** Agent id → agent, from the cached roster. */
export function useAgentLookup() {
  const { data } = useAgents();
  return useCallback((id?: string | null) => (id ? data?.find((a) => a.id === id) : undefined), [data]);
}

/** Agent id → name, from the cached roster. */
export function useAgentName() {
  const { data } = useAgents();
  return useCallback(
    (id?: string | null) => {
      if (!id) return "—";
      if (id === "user") return "You";
      if (id === "system") return "System";
      return data?.find((a) => a.id === id)?.name || id;
    },
    [data],
  );
}

/** POST/PUT/DELETE with a toast on success or failure, then refresh the given query keys. */
export function useAction<A>(fn: (a: A) => Promise<unknown>, keys: string[], ok?: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: () => {
      if (ok) toast(ok);
      keys.forEach((k) => qc.invalidateQueries({ queryKey: [k] }));
    },
    onError: (e) => toast(errText(e), true),
  });
}

export const decideApproval = (id: number, approve: boolean, note: string, anchor: boolean) =>
  post(`/api/wf/approvals/${id}`, { approve, note, anchor });
