export type TaskStatus = "pending" | "assigned" | "running" | "waiting" | "review" | "completed" | "failed" | "cancelled";
export type MsgKind = "user" | "agent" | "system" | "task" | "tool" | "chain";

export interface Agent {
  id: string;
  name: string;
  role: string;
  description: string;
  system_prompt: string;
  provider: "none" | "anthropic" | "openai_compat";
  model: string;
  model_config: { max_tokens?: number; effort?: string; max_tool_calls?: number; max_model_calls?: number; max_run_tokens?: number; call_timeout_s?: number };
  skills: string[];
  permissions: string[];
  is_manager: boolean;
  status: "active" | "paused" | "archived";
  budget_usd: string;
  cost_usd: string;
  tokens_in: number;
  tokens_out: number;
  onchain: { agent_id?: string; address?: string; network?: string; tx?: string };
  created_at: string;
  updated_at: string;
  live?: "busy" | "waiting" | "review" | "idle" | "error" | "paused" | "archived" | "active";
  max_concurrency?: number;
  running?: number;
  squads?: { id: string; name: string; workspace_id: string; role: "manager" | "member"; status: SquadStatus }[];
  current_task?: { id: string; title: string; status: TaskStatus } | null;
  tasks_completed?: number;
  tasks_failed?: number;
  success_rate?: number | null;
  avg_secs?: number | null;
  last_activity_at?: string | null;
}

export interface AgentDetail extends Agent {
  tasks: Task[];
  logs: Message[];
  memory: Memory[];
  workspaces: Workspace[];
  can_run: Record<string, string | null>;
  audit: { id: number; at: string; text: string; actor: string }[];
}

export interface Workspace {
  id: string;
  name: string;
  description: string;
  manager_id: string | null;
  members: string[];
  archived: boolean;
  created_at: string;
  goals?: Task[];
  goal_count?: number;
  squads?: { id: string; name: string; status: SquadStatus; manager_id: string | null }[];
}

export type SquadStatus = "active" | "paused" | "deleted";

/** A team inside a workspace: one manager, members, a policy. Missions are given to a squad. */
export interface Squad {
  id: string;
  workspace_id: string;
  workspace_name: string;
  name: string;
  description: string;
  manager_id: string | null;
  members: string[];
  permissions: string[];
  status: SquadStatus;
  max_parallel: number;
  created_at: string;
  updated_at: string;
  missions: { active: number; review: number; completed: number; failed: number; cancelled: number };
  success_rate: number | null;
  cost_usd: number;
  skills: string[];
  executors: string[];
  running: number;
  ready: boolean;
  issues: PreflightCheck[];
}

export type PreflightFix = "create_squad" | "configure_squad" | "resume_squad" | "assign_manager" | "add_agent" | "create_agent" | "configure_manager";

export interface PreflightCheck {
  key: string;
  level: "ok" | "warn" | "error" | "info";
  text: string;
  fix: PreflightFix | null;
}

export interface Preflight {
  ok: boolean;
  ready: boolean;
  workspace_id: string;
  squad_id: string | null;
  manager_id: string | null;
  planner: "model" | "rules" | null;
  executors: string[];
  checks: PreflightCheck[];
}

export interface Task {
  id: string;
  workspace_id: string;
  squad_id?: string | null;
  parent_id: string | null;
  root_id: string;
  kind: "goal" | "subtask";
  title: string;
  description: string;
  skill: string | null;
  input: Record<string, unknown>;
  assigned_agent: string | null;
  priority: number;
  status: TaskStatus;
  depends_on: string[];
  attempts: number;
  max_attempts: number;
  result: any;
  result_hash: string | null;
  error: string | null;
  cost_usd: string;
  tokens: number;
  steps: number;
  max_steps: number;
  budget_usd: string;
  timeout_s: number;
  deadline_at: string | null;
  planner: string | null;
  created_by: string;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  updated_at: string;
}

export interface Message {
  id: number;
  at: string;
  workspace_id: string;
  task_id: string | null;
  kind: MsgKind;
  event: string;
  sender: string;
  recipient: string | null;
  text: string;
  data: any;
  status: string | null;
  attempts: number;
}

export interface Approval {
  id: number;
  at: string;
  workspace_id: string;
  task_id: string | null;
  kind: "task_result" | "over_budget" | "chain_write";
  title: string;
  detail: Record<string, any>;
  status: "pending" | "approved" | "rejected";
  requested_by: string;
  decided_by: string | null;
  decided_at: string | null;
  note: string | null;
}

export interface ChainRecord {
  id: number;
  at: string;
  task_id: string | null;
  kind: string;
  network: string;
  chain_id: number;
  payload_hash: string;
  from_addr: string | null;
  to_addr: string | null;
  tx_hash: string | null;
  status: "pending" | "confirmed" | "failed";
  block: number | null;
  gas_used: number | null;
  gas_price: string | null;
  fee_wei: string | null;
  error: string | null;
  confirmed_at: string | null;
  tx_url?: string | null;
  confirmations?: number | null;
}

export interface Memory {
  id: number;
  scope: "agent" | "workspace" | "project" | "user";
  owner: string;
  text: string;
  source: string;
  created_at: string;
  expires_at: string | null;
}

export interface Skill {
  id: string;
  name: string;
  category: string;
  description: string;
  input_schema: any;
  output_schema: any;
  permissions: string[];
  version: string;
  developer: string;
  compatibility: string;
  requires: string[];
  installed: boolean;
  enabled: boolean;
  installed_version: string | null;
  update_available: boolean;
  missing: string[];
  needs_chain: boolean;
  agents: string[];
}

export interface Schedule {
  id: number;
  workspace_id: string;
  name: string;
  cron: string;
  goal: string;
  enabled: number;
  next_run_at: string | null;
  last_run_at: string | null;
  last_task_id: string | null;
  last_status: string | null;
  failures: number;
  runs: { id: number; at: string; task_id: string | null; status: string; error: string | null }[];
}

export interface Meta {
  providers: string[];
  permissions: Record<string, string>;
  squad_permissions: Record<string, string>;
  task_statuses: TaskStatus[];
  default_model: string;
  skills: { id: string; name: string; permissions: string[] }[];
  engine: { llm: boolean; compat: string | null; compat_service?: string | null; chain: boolean; etherscan: boolean; anchor: boolean; planner: string };
  network: string | null;
  chain_id: number | null;
  explorer: string | null;
  last_message_id: number;
}

/** GET /api/system: what the server can really do now. The static demo answers with mode "demo". */
export interface SystemInfo {
  mode: "live" | "demo";
  version: string | null;
  started_at: string | null;
  database: { engine: string; persistent: boolean; file: string | null };
  model: {
    configured: boolean; providers: string[]; default_model: string | null; reason: string | null;
    compat?: { service: string; model: string; host: string } | null;
  };
  ai_usage?: AiUsage;
  chain: { configured: boolean; network: string | null; chain_id: number | null; listener: boolean };
  explorer_api: boolean;
  anchor: boolean;
  writes: { payments: boolean; agent_signing: boolean };
  auth: { viewer_token: boolean; cors_origins: string[] };
}

/** Model spend and the limits in force (/api/wf/ai-usage; without `days` inside /api/system). */
export interface AiUsage {
  today_usd: string;
  month_usd: string;
  unpriced_calls_this_month: number;
  daily_budget_usd: string | null;
  monthly_budget_usd: string | null;
  max_concurrent: number;
  in_flight: number;
  max_agent_steps: number | null;
  max_output_tokens: number | null;
  request_timeout_s: number | null;
  breaker: { failures: number; cooldown_s: number; open: Record<string, { failures: number; open_for_s: number }> };
}

export interface ChainStatus {
  configured: boolean;
  network: string | null;
  chain_id: number | null;
  explorer: string | null;
  rpc: { ok: boolean; latency_ms?: number; block?: number; block_time?: number; gas_price_wei?: string; error?: string } | null;
  anchor: { address: string; url: string | null; bnb?: string; error?: string } | null;
}

export interface Overview {
  agents: { total: number; active: number; busy: number; idle: number; error: number; paused: number; waiting?: number };
  squads?: number;
  running_missions?: number;
  tasks: Record<TaskStatus, number>;
  running: number;
  pending_approvals: number;
  cost_usd: number;
  tokens: number;
  series: { hour: string; status: TaskStatus; n: number }[];
  performance: Agent[];
  engine: Meta["engine"];
  recent_tasks: Task[];
  onchain: ChainRecord[];
  activity: Message[];
}

export interface TaskDetail {
  task: Task;
  root: Task;
  subtasks: Task[];
  messages: Message[];
  approvals: Approval[];
  chain: ChainRecord[];
  graph: { nodes: { id: string; title: string; status: TaskStatus; agent: string; skill: string }[]; edges: { from: string; to: string }[] };
}

export type ProofState = "in_progress" | "awaiting_review" | "offchain_completed" | "onchain_pending" | "onchain_confirmed" | "not_completed";

export interface Proof {
  state: ProofState;
  result_hash: string | null;
  record_id: number | null;
  tx_hash: string | null;
  tx_url?: string | null;
  block: number | null;
  network: string | null;
  chain_status: "pending" | "confirmed" | "failed" | null;
  error: string | null;
  confirmed_at: string | null;
}

export interface MissionTask {
  id: string;
  title: string;
  skill: string | null;
  assigned_agent: string | null;
  status: TaskStatus;
  depends_on: string[];
  attempts: number;
  error: string | null;
  result_hash: string | null;
  cost_usd: string;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

/** A mission is a goal task read through the compatibility layer (/api/wf/missions). */
export interface Mission {
  id: string;
  workspace_id: string;
  squad_id: string | null;
  name: string;
  brief: string;
  status: TaskStatus;
  stage: "planning" | "executing" | "review" | "completed" | "failed" | "cancelled";
  priority: number;
  owner: string | null;
  planner: string | null;
  created_by: string;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  deadline_at: string | null;
  updated_at: string;
  budget_usd: string;
  cost_usd: string;
  tokens: number;
  steps: number;
  max_steps: number;
  error: string | null;
  progress: { total: number; settled: number; completed: number; failed: number; cancelled: number; running: number; pending: number };
  agents: string[];
  tasks: MissionTask[];
  approvals: { id: number; kind: Approval["kind"]; title: string; at: string }[];
  deliverable: { result_hash: string | null; report: boolean; findings: number; has_output: boolean } | null;
  proof: Proof;
}

export interface LedgerRow {
  mission: string;
  workspace_id: string;
  name: string;
  status: TaskStatus;
  owner: string | null;
  finished_at: string | null;
  result_hash: string | null;
  proof: Proof;
  executions: { task: string; title: string; agent: string | null; skill: string | null; status: TaskStatus; result_hash: string | null; error: string | null; finished_at: string | null }[];
}

export interface TaskPage {
  items: Task[];
  total: number;
  page: number;
  size: number;
  counts: Record<TaskStatus, number>;
}
