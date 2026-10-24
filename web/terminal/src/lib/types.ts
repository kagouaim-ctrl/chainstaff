export type TaskStatus = "pending" | "assigned" | "running" | "waiting" | "review" | "completed" | "failed" | "cancelled";
export type MsgKind = "user" | "agent" | "system" | "task" | "tool" | "chain";

export interface Agent {
  id: string;
  name: string;
  role: string;
  description: string;
  system_prompt: string;
  provider: "none" | "anthropic";
  model: string;
  model_config: { max_tokens?: number; effort?: string };
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
  live?: "busy" | "idle" | "error" | "paused" | "archived" | "active";
  current_task?: { id: string; title: string; status: TaskStatus } | null;
  tasks_completed?: number;
  tasks_failed?: number;
  success_rate?: number | null;
  avg_secs?: number | null;
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
}

export interface Task {
  id: string;
  workspace_id: string;
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
  task_statuses: TaskStatus[];
  default_model: string;
  skills: { id: string; name: string; permissions: string[] }[];
  engine: { llm: boolean; chain: boolean; etherscan: boolean; anchor: boolean; planner: string };
  network: string | null;
  chain_id: number | null;
  explorer: string | null;
  last_message_id: number;
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
  agents: { total: number; active: number; busy: number; idle: number; error: number; paused: number };
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
