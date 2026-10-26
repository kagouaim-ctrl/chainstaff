/**
 * Autonomous agents (/api/at/*): types, queries and the activity stream. Everything shown comes from these
 * endpoints; nothing here invents a state. An agent's state is computed by the server from its tasks, runs,
 * approvals, watchers and whether a worker is alive.
 */
import { useQuery } from "@tanstack/react-query";
import { useEffect, useSyncExternalStore } from "react";
import { api, apiUrl, queryClient, token } from "./api";
import { DEMO } from "./demo";
import { markDown, markOk } from "./health";

export type AgentState =
  | "watching" | "running" | "investigating" | "waiting" | "waiting_approval" | "paused" | "offline" | "error" | "queued" | "idle" | "archived";
export type Severity = "info" | "low" | "medium" | "high" | "critical";
export type WorkMode = "event" | "continuous" | "on_demand" | "scheduled";
export type TaskStatusAt = "queued" | "running" | "waiting" | "waiting_approval" | "completed" | "failed" | "cancelled" | "rejected" | "unassigned";

export interface AtAgentRow {
  id: string;
  name: string;
  role: string;
  responsibility: string;
  work_mode: WorkMode;
  workspaces: { id: string; name: string }[];
  status: "active" | "paused" | "archived";
  autonomous: boolean;
  activated: boolean;
  state: AgentState;
  state_detail: string;
  current_task: { id: string; objective: string; kind: string; status: TaskStatusAt } | null;
  last_wake_at: string | null;
  last_activity_at: string | null;
  provider: string | null;
  model: string | null;
  model_ready: boolean;
  spent_usd: string;
  budget_usd: string;
  /** Avatar shape and colour picked in the console; empty for agents created before it existed. */
  appearance?: Appearance;
}

export const AVATAR_SHAPES = ["hex", "orbit", "prism", "grid", "pulse", "shield", "node", "spark"] as const;
export const AVATAR_COLORS = ["gold", "amber", "emerald", "azure", "coral", "slate"] as const;
export type AvatarShape = (typeof AVATAR_SHAPES)[number];
export type AvatarColor = (typeof AVATAR_COLORS)[number];
export interface Appearance {
  shape?: AvatarShape;
  color?: AvatarColor;
}

export interface AtTask {
  id: string;
  workspace_id: string;
  mission_id: string | null;
  from_agent_id: string | null;
  to_agent_id: string | null;
  kind: string;
  objective: string;
  priority: number;
  evidence: unknown;
  required: string[];
  status: TaskStatusAt;
  result: { summary?: string; outcome?: string; details?: unknown; findings?: unknown[]; delegated?: string[]; artifacts?: string[]; tests?: unknown } | null;
  error: string | null;
  parent_id: string | null;
  root_id: string;
  depth: number;
  chain: string[];
  event_id: string | null;
  fingerprint: string | null;
  attempts: number;
  max_attempts: number;
  budget_usd: string;
  cost_usd: string;
  tokens: number;
  lease_owner: string | null;
  deadline_at: string | null;
  created_by: string;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  updated_at: string;
}

export interface AtRun {
  id: string;
  task_id: string;
  agent_id: string;
  worker_id: string | null;
  attempt: number;
  status: string;
  resumed: number;
  provider: string | null;
  model: string | null;
  model_calls: number;
  tool_calls: number;
  tokens_in: number;
  tokens_out: number;
  cost_usd: string;
  summary: string | null;
  error: string | null;
  started_at: string;
  finished_at: string | null;
}

export interface AtStep {
  id: number;
  run_id: string;
  at: string;
  kind: "model" | "tool" | "note" | "error";
  name: string;
  ok: number;
  secs: number | null;
  input: unknown;
  output: unknown;
  tokens_in: number;
  tokens_out: number;
  cost_usd: string;
}

export interface AtMission {
  id: string;
  workspace_id: string;
  name: string;
  goal: string;
  type: "one_time" | "continuous" | "event_driven" | "scheduled";
  lead_agent_id: string | null;
  agent_ids: string[];
  triggers: { watcher_id?: string; source?: string; types?: string[]; min_severity?: Severity; task_kind?: string }[];
  schedule_cron: string | null;
  next_run_at: string | null;
  status: "active" | "paused" | "stopped" | "completed" | "failed";
  permissions: string[];
  budget_usd: string;
  cost_usd: string;
  result: unknown;
  run_count: number;
  last_run_at: string | null;
  responsibility_of: string | null;
  created_by: string;
  created_at: string;
  updated_at: string;
  task_counts?: Record<string, number>;
  agents?: { id: string; name: string }[];
  lead_name?: string;
}

export interface AtActivity {
  id: number;
  at: string;
  type: string;
  severity: Severity;
  workspace_id: string | null;
  agent_id: string | null;
  target_agent_id: string | null;
  mission_id: string | null;
  task_id: string | null;
  run_id: string | null;
  event_id: string | null;
  actor: string;
  title: string;
  reason: string;
  result: string;
  needs_human: number;
  data: unknown;
  agent_name?: string | null;
  target_agent_name?: string | null;
  actor_name?: string;
}

export interface AtEvent {
  id: string;
  source: string;
  watcher_id: string | null;
  workspace_id: string;
  type: string;
  severity: Severity;
  title: string;
  payload: unknown;
  fingerprint: string | null;
  occurrences: number;
  occurred_at: string;
  received_at: string;
  processing_status: string;
  chain_status: string | null;
  attempts: number;
  error: string | null;
  task_ids: string[];
}

export interface AtWatcher {
  id: string;
  workspace_id: string;
  kind: "bsc_events" | "github_webhook" | "ci_failure" | "app_logs" | "api_health";
  name: string;
  config: Record<string, any>;
  status: "active" | "paused" | "error";
  state: Record<string, any>;
  last_heartbeat_at: string | null;
  last_event_at: string | null;
  last_error: string | null;
  healthy?: boolean;
  health?: string;
  has_secret?: boolean;
}

export interface Resources {
  repos?: { name: string; provider: "github" | "local"; url?: string; path?: string; owner?: string; repo?: string; branch: string; test_command?: string }[];
  contracts?: { address: string; label?: string; network?: string }[];
  tokens?: { address: string; symbol?: string; label?: string; network?: string }[];
  wallets?: { address: string; label?: string; network?: string }[];
  docs?: { title: string; url: string; note?: string }[];
  endpoints?: { name: string; url: string }[];
  services?: { name: string; kind?: string; note?: string }[];
}

export interface AtWorkspace {
  id: string;
  name: string;
  description: string;
  archived?: number | boolean;
  resources: Resources;
  watchers: number | (AtWatcher & { healthy: boolean; health: string })[];
  agents: number | AtAgentRow[];
  open_tasks?: number;
  missions?: AtMission[];
}

export interface AtApproval {
  id: number;
  at: string;
  workspace_id: string;
  task_id: string | null;
  agent_id: string | null;
  kind: string;
  title: string;
  detail: { repo?: string; provider?: string; files?: string[]; stat?: string; tests_passed?: boolean | null; pr_url?: string | null };
  artifact_id: string | null;
  status: "pending" | "approved" | "rejected";
  decided_by: string | null;
  decided_at: string | null;
  note: string | null;
  agent_name?: string;
}

export interface AtArtifact {
  id: string;
  task_id: string;
  workspace_id: string;
  agent_id: string | null;
  kind: string;
  title: string;
  body: string;
  content: string;
  meta: Record<string, any>;
  status: string;
  created_at: string;
}

export interface AtNotification {
  id: number;
  at: string;
  severity: Severity;
  title: string;
  body: string;
  agent_id: string | null;
  workspace_id: string | null;
  task_id: string | null;
  mission_id: string | null;
  approval_id: number | null;
  read_at: string | null;
}

export interface RuntimeHealth {
  level: "ok" | "degraded" | "down";
  worker_alive: boolean;
  workers: { id: string; host: string; pid: number; version: string; concurrency: number; running: string[]; status: string; started_at: string; heartbeat_at: string; stats: Record<string, number> }[];
  problems: string[];
  models: { configured: boolean; providers: { provider: string; model: string; label: string }[]; default_model: string };
  gateway: { spent_today_usd: string; spent_month_usd: string; daily_budget_usd: string | null; monthly_budget_usd: string | null; max_concurrent: number };
  chain: Record<string, any> | null;
  sandbox: { kind: string; network_isolated: boolean; env_scrubbed: boolean; limits: Record<string, number> };
  github_token: boolean;
  limits: Record<string, number | string>;
}

export interface AtOverview {
  kpis: {
    total_agents: number;
    active_watchers: number;
    watchers_total: number;
    running_missions: number;
    active_missions: number;
    active_investigations: number;
    pending_approvals: number;
    runtime_health: RuntimeHealth["level"];
  };
  agents: AtAgentRow[];
  health: Pick<RuntimeHealth, "level" | "worker_alive" | "problems" | "models">;
  unread: number;
  last_activity_id: number;
}

export interface Template {
  id: string;
  name: string;
  work_mode: WorkMode;
  responsibility: string;
  permissions: string[];
  skills: string[];
  extra?: string[];
  wake_on?: string[];
  schedule_cron?: string;
}

export interface AtMeta {
  templates: Template[];
  work_modes: Record<WorkMode, string>;
  mission_types: Record<string, string>;
  task_kinds: Record<string, string>;
  severities: Severity[];
  watcher_kinds: Record<string, string>;
  permissions: Record<string, [string, "low" | "medium" | "high"]>;
  human_only: Record<string, string>;
  default_permissions: string[];
  tools: { name: string; description: string; permission: string | null; risk: string; group: string }[];
  limits: Record<string, number | string>;
  models: RuntimeHealth["models"];
  sandbox: RuntimeHealth["sandbox"];
  github_token: boolean;
  skills: { id: string; name: string; description: string; permissions: string[]; enabled: boolean; installed: boolean; missing: string[]; needs_chain: boolean }[];
  last_activity_id: number;
}

export interface AgentDetailAt extends AtAgentRow {
  agent: Record<string, any> & { permissions: string[]; skills: string[]; approval_rules: Record<string, any>; notify_rules: Record<string, any>; wake_on: string[] | null; schedule_cron: string | null; template: string; max_concurrency: number; model_config: Record<string, any> };
  system_prompt: string;
  missions: AtMission[];
  open_tasks: AtTask[];
  recent_tasks: AtTask[];
  delegated: AtTask[];
  runs: AtRun[];
  approvals: AtApproval[];
  task_counts: Record<string, number>;
}

export interface TaskDetailAt {
  task: AtTask;
  root: AtTask;
  tree: AtTask[];
  runs: AtRun[];
  steps: AtStep[];
  event: AtEvent | null;
  artifacts: AtArtifact[];
  approvals: AtApproval[];
  activity: AtActivity[];
  names: Record<string, string>;
}

// ---------------------------------------------------------------- vocabulary

type Tone = "ok" | "bad" | "acc" | "info" | "mute" | "";
export const STATE: Record<AgentState, [string, Tone, boolean?]> = {
  watching: ["Watching", "ok", true],
  running: ["Working", "info", true],
  investigating: ["Investigating", "info", true],
  queued: ["Queued", "info"],
  waiting: ["Waiting", "mute"],
  waiting_approval: ["Waiting Approval", "acc"],
  paused: ["Paused", "mute"],
  offline: ["Offline", "bad"],
  error: ["Error", "bad"],
  idle: ["Idle", "mute"],
  archived: ["Archived", "mute"],
};
export const TASK_ST: Record<TaskStatusAt, [string, Tone, boolean?]> = {
  queued: ["Queued", "mute"],
  running: ["Running", "info", true],
  waiting: ["Waiting", "info"],
  waiting_approval: ["Waiting Approval", "acc"],
  completed: ["Completed", "ok"],
  failed: ["Failed", "bad"],
  cancelled: ["Cancelled", "mute"],
  rejected: ["Rejected", "bad"],
  unassigned: ["Unassigned", "acc"],
};
export const MISSION_ST: Record<string, [string, Tone]> = {
  active: ["Active", "ok"], paused: ["Paused", "mute"], stopped: ["Stopped", "mute"], completed: ["Completed", "ok"], failed: ["Failed", "bad"],
};
export const MODE_LABEL: Record<WorkMode, string> = { event: "Event-driven", continuous: "Continuous", on_demand: "On-demand", scheduled: "Scheduled" };
export const MISSION_TYPE_LABEL: Record<string, string> = { one_time: "One-time", continuous: "Continuous", event_driven: "Event-driven", scheduled: "Scheduled" };
export const SEV_TONE: Record<Severity, Tone> = { info: "mute", low: "mute", medium: "acc", high: "bad", critical: "bad" };
export const WATCHER_LABEL: Record<string, string> = {
  bsc_events: "BSC events", github_webhook: "GitHub webhook", ci_failure: "CI failures", app_logs: "Application errors", api_health: "API health",
};
export const OPEN_TASK: TaskStatusAt[] = ["queued", "running", "waiting", "waiting_approval", "unassigned"];
/** Open tasks of a mission; an active mission with any is "running" (the same rule as the server's overview). */
export const openTasksOf = (m: AtMission) => OPEN_TASK.reduce((n, k) => n + (m.task_counts?.[k] || 0), 0);
export const isRunning = (m: AtMission) => m.status === "active" && openTasksOf(m) > 0;

// ---------------------------------------------------------------- queries

const qs = (params: Record<string, string | number | boolean | undefined | null>) =>
  new URLSearchParams(Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== "").map(([k, v]) => [k, String(v)])).toString();

export const useAtMeta = () => useQuery({ queryKey: ["at", "meta"], queryFn: () => api<AtMeta>("/api/at/meta"), staleTime: 60_000 });
export const useAtOverview = () => useQuery({ queryKey: ["at", "overview"], queryFn: () => api<AtOverview>("/api/at/overview"), refetchInterval: 15_000 });
export const useAtRuntime = () => useQuery({ queryKey: ["at", "runtime"], queryFn: () => api<RuntimeHealth>("/api/at/runtime"), refetchInterval: 15_000 });
export const useAtAgents = (archived = false) =>
  useQuery({ queryKey: ["at", "agents", archived], queryFn: () => api<AtAgentRow[]>(`/api/at/agents?archived=${archived}`), refetchInterval: 15_000 });
export const useAtAgent = (id?: string | null) =>
  useQuery({ queryKey: ["at", "agent", id], queryFn: () => api<AgentDetailAt>(`/api/at/agents/${id}`), enabled: !!id, refetchInterval: 15_000 });
export const useAtMemory = (id: string, q: string) =>
  useQuery({ queryKey: ["at", "memory", id, q], queryFn: () => api<{ id: number; scope: string; owner: string; text: string; source: string; created_at: string }[]>(`/api/at/agents/${id}/memory?q=${encodeURIComponent(q)}`) });
export const useAtWorkspaces = () => useQuery({ queryKey: ["at", "workspaces"], queryFn: () => api<AtWorkspace[]>("/api/at/workspaces") });
export const useAtWorkspace = (id?: string | null) =>
  useQuery({ queryKey: ["at", "workspace", id], queryFn: () => api<AtWorkspace>(`/api/at/workspaces/${id}`), enabled: !!id, refetchInterval: 20_000 });
export const useAtMissions = (p: { workspace_id?: string; status?: string; agent_id?: string } = {}) =>
  useQuery({ queryKey: ["at", "missions", qs(p)], queryFn: () => api<AtMission[]>(`/api/at/missions?${qs(p)}`) });
export const useAtMission = (id?: string | null) =>
  useQuery({ queryKey: ["at", "mission", id], queryFn: () => api<AtMission & { tasks: AtTask[]; activity: AtActivity[] }>(`/api/at/missions/${id}`), enabled: !!id });
export const useAtTasks = (p: Record<string, string | number | undefined>) =>
  useQuery({ queryKey: ["at", "tasks", qs(p)], queryFn: () => api<{ items: AtTask[]; total: number }>(`/api/at/tasks?${qs(p)}`), placeholderData: (x) => x });
export const useAtTask = (id?: string | null) =>
  useQuery({ queryKey: ["at", "task", id], queryFn: () => api<TaskDetailAt>(`/api/at/tasks/${id}`), enabled: !!id, refetchInterval: 10_000 });
export const useAtRun = (id?: string | null) =>
  useQuery({ queryKey: ["at", "run", id], queryFn: () => api<AtRun & { steps: AtStep[] }>(`/api/at/runs/${id}`), enabled: !!id });
export const useAtEvents = (p: Record<string, string | number | undefined>) =>
  useQuery({ queryKey: ["at", "events", qs(p)], queryFn: () => api<AtEvent[]>(`/api/at/events?${qs(p)}`) });
export const useAtActivity = (p: Record<string, string | number | boolean | undefined>) =>
  useQuery({ queryKey: ["at", "activity", qs(p)], queryFn: () => api<AtActivity[]>(`/api/at/activity?${qs(p)}`), placeholderData: (x) => x });
export const useAtWatchers = (workspace_id?: string) =>
  useQuery({ queryKey: ["at", "watchers", workspace_id || ""], queryFn: () => api<AtWatcher[]>(`/api/at/watchers?${qs({ workspace_id })}`), refetchInterval: 20_000 });
export const useAtApprovals = (status = "pending") =>
  useQuery({ queryKey: ["at", "approvals", status], queryFn: () => api<AtApproval[]>(`/api/at/approvals?status=${status}`) });
export const useAtArtifact = (id?: string | null) =>
  useQuery({ queryKey: ["at", "artifact", id], queryFn: () => api<AtArtifact>(`/api/at/artifacts/${id}`), enabled: !!id });
export const useAtNotifications = () =>
  useQuery({ queryKey: ["at", "notifications"], queryFn: () => api<{ items: AtNotification[]; unread: number }>("/api/at/notifications?limit=60"), refetchInterval: 30_000 });
export const useAtUsage = (range: string) =>
  useQuery({ queryKey: ["at", "usage", range], queryFn: () => api<{ totals: Record<string, any>; by_agent: CostRow[]; by_mission: CostRow[]; calls: (AtStep & { agent_id: string; task_id: string; provider: string; model: string })[]; gateway: RuntimeHealth["gateway"] }>(`/api/at/usage?range=${range}`) });

/** Everything under ["at"] refetches after a change; the server is the only source of state. */
export const refreshAt = () => queryClient.invalidateQueries({ queryKey: ["at"] });

// ---------------------------------------------------------------- live activity stream

type Listener = (a: AtActivity) => void;
const listeners = new Set<Listener>();
const stateListeners = new Set<() => void>();
let state: "off" | "connecting" | "live" | "retrying" = "off";
let ctl: AbortController | null = null;
let timer: number | undefined;

function setState(s: typeof state) {
  state = s;
  stateListeners.forEach((f) => f());
}

async function run(after: number) {
  let last = after;
  let delay = 1000;
  while (ctl && !ctl.signal.aborted) {
    setState(last ? "retrying" : "connecting");
    try {
      // Resumes from the last id it saw, so nothing that happened while disconnected is missed.
      const r = await fetch(apiUrl(`/api/at/stream?after=${last}`), { headers: { Authorization: "Bearer " + token() }, signal: ctl.signal });
      if (!r.ok || !r.body) throw new Error(`HTTP ${r.status}`);
      setState("live");
      markOk();
      delay = 1000;
      const reader = r.body.getReader();
      const dec = new TextDecoder();
      let buf = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        let i;
        while ((i = buf.indexOf("\n\n")) >= 0) {
          const chunk = buf.slice(0, i);
          buf = buf.slice(i + 2);
          const data = chunk.split("\n").find((l) => l.startsWith("data: "));
          if (!data) continue;
          const a = JSON.parse(data.slice(6)) as AtActivity;
          last = Math.max(last, a.id);
          listeners.forEach((f) => f(a));
          if (!timer) timer = window.setTimeout(() => ((timer = undefined), refreshAt()), 600);
        }
      }
    } catch (e) {
      if (ctl?.signal.aborted) break;
      markDown(`实时活动流断开（${(e as Error)?.message || "网络错误"}）`);
    }
    setState("retrying");
    await new Promise((res) => setTimeout(res, delay));
    delay = Math.min(delay * 2, 15000);
  }
  setState("off");
}

export function startAtStream(after: number) {
  if (ctl || DEMO) return;
  ctl = new AbortController();
  void run(after);
}
export function stopAtStream() {
  ctl?.abort();
  ctl = null;
}
export const useAtStreamState = () =>
  useSyncExternalStore((f) => (stateListeners.add(f), () => stateListeners.delete(f)), () => state);
export function useAtLive(fn: Listener) {
  useEffect(() => {
    listeners.add(fn);
    return () => {
      listeners.delete(fn);
    };
  }, [fn]);
}

export interface CostRow {
  id: string | null;
  runs: number;
  cost_usd: number;
  tokens: number;
}
