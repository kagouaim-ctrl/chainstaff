"""How the AI manager turns a goal into subtasks.

Two planners, and the timeline always says which one ran:
- model: the manager's own model (Anthropic) reads the goal and the team and returns a JSON plan,
  which is validated against the real roster, skills and input schemas before anything runs.
- rules: used when the manager has no model or no API key. It only understands on-chain analysis
  goals that contain addresses, and says so instead of guessing.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Callable

from agent.skills.translator.translator import MODEL

from .skills import ADDRESS_RE, SKILL_BY_ID, SkillError, validate

if TYPE_CHECKING:  # pragma: no cover
    from .engine import Workforce

MAX_STEPS_PER_PLAN = 12


class PlanError(Exception):
    pass


class PlanCancelled(Exception):
    """The goal was cancelled or timed out while it was being planned; the plan is thrown away.
    usage is what a model call that finished after the cancel cost, so it can still be charged."""

    def __init__(self, msg: str, usage: dict | None = None) -> None:
        super().__init__(msg)
        self.usage = usage


def _retryable(e: Exception) -> bool:
    """Timeouts, dropped connections, rate limits and server errors are worth another try; a bad request is not."""
    if isinstance(e, (TimeoutError, ConnectionError)):
        return True
    try:
        import anthropic
    except ImportError:  # pragma: no cover - the SDK is a dependency
        return False
    if isinstance(e, (anthropic.APITimeoutError, anthropic.APIConnectionError, anthropic.RateLimitError)):
        return True
    return isinstance(e, anthropic.APIStatusError) and e.status_code >= 500


@dataclass
class Step:
    key: str
    title: str
    skill: str
    agent_id: str
    input: dict
    depends_on: list[str] = field(default_factory=list)


@dataclass
class Plan:
    steps: list[Step]
    planner: str  # rules | model
    notes: list[str] = field(default_factory=list)
    usage: dict | None = None

    @property
    def planner_label(self) -> str:
        return "模型规划" if self.planner == "model" else "规则规划，未使用模型"


class Planner:
    def __init__(self, llm: Callable[[], Any] | None, price_per_mtok: tuple[Decimal, Decimal] | None = None, model: str = MODEL,
                 timeout_s: float = 60.0, retries: int = 2, backoff_s: float = 1.0) -> None:
        self.llm = llm
        self.price = price_per_mtok
        self.model = model
        self.timeout_s = timeout_s  # per model request; the SDK's own retries are off so this is the real ceiling
        self.retries = retries
        self.backoff_s = backoff_s

    def label(self) -> str:
        return "model+rules" if self.llm else "rules"

    # ---- choosing who does what ----

    def pick(self, members: list[dict], skill: str, wf: "Workforce") -> dict | None:
        able = [a for a in members if wf.can_run(a, skill) is None]
        if not able:
            return None
        load = {a["id"]: wf._open_tasks_of(a["id"]) for a in able}
        return sorted(able, key=lambda a: (a["is_manager"], load[a["id"]], a["created_at"]))[0]

    # ---- planning ----

    def plan(self, goal: str, manager: dict, members: list[dict], wf: "Workforce", *, use_model: bool = True,
             cancelled: Callable[[], bool] | None = None) -> Plan:
        if not members:
            raise PlanError("工作区里没有在岗的员工")
        if use_model and self.llm and manager["provider"] == "anthropic":
            try:
                return self._model_plan(goal, manager, members, wf, cancelled)
            except PlanError as e:
                try:
                    plan = self._rule_plan(goal, members, wf)
                except PlanError as e2:
                    raise PlanError(f"模型计划不能用（{e}）；{e2}") from e2
                plan.notes.insert(0, f"模型给出的计划不能用（{e}），改用规则规划")
                return plan
        return self._rule_plan(goal, members, wf)

    def _rule_plan(self, goal: str, members: list[dict], wf: "Workforce") -> Plan:
        addrs = list(dict.fromkeys(a.lower() for a in ADDRESS_RE.findall(goal)))[:3]
        if not addrs:
            raise PlanError("规则规划器只能处理带 0x 地址的链上分析任务；给主管配置模型（Anthropic）后才能理解其他目标")
        steps: list[Step] = []
        notes: list[str] = []
        for i, addr in enumerate(addrs):
            wallet = self.pick(members, "wallet_analysis", wf)
            if wallet:
                # The manager adds contract or wallet follow-ups once it knows which this address is.
                steps.append(Step(f"w{i}", f"钱包分析 {addr[:10]}…", "wallet_analysis", wallet["id"], {"address": addr}))
                continue
            for skill in ("contract_analysis", "token_info", "contract_events", "tx_history"):
                a = self.pick(members, skill, wf)
                if a:
                    steps.append(Step(f"{skill}{i}", f"{SKILL_BY_ID[skill].name} {addr[:10]}…", skill, a["id"], {"address": addr}))
                else:
                    notes.append(f"团队里没有会 {SKILL_BY_ID[skill].name} 的员工")
        if not steps:
            raise PlanError("团队里没有能做链上分析的员工（需要钱包分析、合约分析等技能）")
        reporter = self.pick(members, "data_report", wf)
        if reporter:
            steps.append(Step("report", "汇总报告", "data_report", reporter["id"], {"title": goal.splitlines()[0][:80]}, [s.key for s in steps]))
        else:
            notes.append("团队里没有会数据报告的员工，结果不会汇总成报告")
        return Plan(steps, "rules", list(dict.fromkeys(notes)))

    def _model_plan(self, goal: str, manager: dict, members: list[dict], wf: "Workforce", cancelled: Callable[[], bool] | None = None) -> Plan:
        roster = [{"agent_id": a["id"], "name": a["name"], "role": a["role"],
                   "skills": [s for s in a["skills"] if wf.can_run(a, s) is None]} for a in members]
        skills = {sid: {"name": s.name, "description": s.description, "input_schema": s.input_schema}
                  for sid, s in SKILL_BY_ID.items() if any(sid in r["skills"] for r in roster)}
        if not skills:
            raise PlanError("团队里没有可用的技能")
        system = (manager["system_prompt"] or "You are the manager of a team of AI employees working on BNB Smart Chain.") + (
            "\nSplit the user's goal into at most 12 subtasks. Each subtask runs exactly one skill, assigned to one agent that has it."
            " Use depends_on (step keys) only when a step needs another step's result; independent steps run in parallel."
            " If a data_report skill is available, end with one data_report step that depends on all analysis steps."
            " input_json must be a JSON object string valid for the skill's input_schema. Never invent addresses.")
        user = json.dumps({"goal": goal, "team": roster, "skills": skills}, ensure_ascii=False)
        ids = [r["agent_id"] for r in roster]
        schema = {"type": "object", "additionalProperties": False, "required": ["steps"], "properties": {
            "steps": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                                 "required": ["key", "title", "skill", "agent_id", "input_json", "depends_on"],
                                                 "properties": {"key": {"type": "string"}, "title": {"type": "string"},
                                                                "skill": {"type": "string", "enum": sorted(skills)},
                                                                "agent_id": {"type": "string", "enum": ids},
                                                                "input_json": {"type": "string"},
                                                                "depends_on": {"type": "array", "items": {"type": "string"}}}}}}}
        text, usage = self._call(manager, system, user, schema, max_tokens=4000, cancelled=cancelled)
        try:
            raw = json.loads(text)["steps"]
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            raise PlanError("模型返回的不是合法 JSON") from e
        if not raw:
            raise PlanError("模型没有给出任何步骤")
        if len(raw) > MAX_STEPS_PER_PLAN:
            raise PlanError(f"步骤太多（{len(raw)}，上限 {MAX_STEPS_PER_PLAN}）")
        by_id = {a["id"]: a for a in members}
        steps: list[Step] = []
        seen: set[str] = set()
        for r in raw:
            key = str(r["key"])[:40]
            if key in seen:
                raise PlanError(f"步骤 key 重复：{key}")
            agent = by_id.get(r["agent_id"])
            if agent is None or wf.can_run(agent, r["skill"]) is not None:
                raise PlanError(f"{r['agent_id']} 不能执行 {r['skill']}")
            try:
                inp = json.loads(r["input_json"] or "{}")
                validate(SKILL_BY_ID[r["skill"]].input_schema, inp)
            except (json.JSONDecodeError, SkillError) as e:
                raise PlanError(f"{key} 的参数不合法：{e}") from e
            deps = [str(d) for d in r["depends_on"]]
            if any(d not in seen for d in deps):
                raise PlanError(f"{key} 依赖了不存在或排在后面的步骤")  # also rules out cycles
            # Addresses must come from the goal, not from the model's imagination.
            for v in json.dumps(inp).lower().split('"'):
                if ADDRESS_RE.fullmatch(v) and v not in goal.lower():
                    raise PlanError(f"{key} 用了目标里没有的地址 {v}")
            steps.append(Step(key, str(r["title"])[:120], r["skill"], agent["id"], inp, deps))
            seen.add(key)
        return Plan(steps, "model", [], usage)

    def summarize(self, agent: dict, report_md: str) -> tuple[str, dict]:
        system = (agent["system_prompt"] or "You are a careful on-chain analyst.") + (
            "\nWrite a short executive summary (at most 6 bullet points, in the report's language) of the report."
            " Use only facts that are in the report. Do not add numbers that are not there.")
        text, usage = self._call(agent, system, report_md[:30000], None, max_tokens=1500)
        return text.strip(), usage

    def _call(self, agent: dict, system: str, user: str, schema: dict | None, max_tokens: int,
              cancelled: Callable[[], bool] | None = None) -> tuple[str, dict]:
        cfg = agent.get("model_config") or {}
        kwargs: dict[str, Any] = {
            "model": agent.get("model") or self.model,
            "max_tokens": int(cfg.get("max_tokens") or max_tokens),
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "betas": ["server-side-fallback-2026-07-01"],
            "fallbacks": "default",
            "output_config": {"effort": cfg.get("effort") or "medium", **({"format": {"type": "json_schema", "schema": schema}} if schema else {})},
        }
        client = self.llm()
        if hasattr(client, "with_options"):
            client = client.with_options(timeout=self.timeout_s, max_retries=0)
        for attempt in range(self.retries + 1):
            if cancelled and cancelled():
                raise PlanCancelled("任务已取消")
            try:
                response = client.beta.messages.create(**kwargs)
                break
            except Exception as e:
                if not _retryable(e) or attempt == self.retries:
                    raise PlanError(f"模型调用失败（{type(e).__name__}，第 {attempt + 1} 次）：{str(e)[:160]}") from e
                time.sleep(self.backoff_s * 2 ** attempt)
        if response.stop_reason == "refusal":
            raise PlanError("模型拒绝了这个请求")
        if response.stop_reason == "max_tokens":
            raise PlanError("模型输出被截断")
        text = next((b.text for b in response.content if b.type == "text"), "")
        tin, tout = int(response.usage.input_tokens), int(response.usage.output_tokens)
        cost = Decimal(0)
        if self.price:
            cost = (Decimal(tin) * self.price[0] + Decimal(tout) * self.price[1]) / Decimal(1_000_000)
        usage = {"input_tokens": tin, "output_tokens": tout, "cost_usd": str(cost)}
        if cancelled and cancelled():
            raise PlanCancelled("任务已取消", usage)  # the answer came back after the goal ended; do not use it
        return text, usage
