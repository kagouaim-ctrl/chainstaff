import type { Agent, Skill } from "./types";

/** The professional groups on the Agent Terminal rail. */
export const CATEGORIES = ["Analytics", "Development", "Monitoring", "Research", "Reporting", "Management"] as const;
export type Category = (typeof CATEGORIES)[number];

// Same as agent/workforce/skills.py; used until the skills list has loaded.
const SKILL_CATEGORY: Record<string, string> = {
  wallet_analysis: "Analytics", tx_history: "Analytics", token_info: "Research", contract_analysis: "Research",
  contract_events: "Monitoring", onchain_monitor: "Monitoring", data_report: "Reporting",
};
const ROLE_HINT: [Category, RegExp][] = [
  ["Development", /dev|engineer|solidity|code|开发|工程/i],
  ["Management", /manager|lead|主管|经理/i],
  ["Monitoring", /monitor|watch|监控/i],
  ["Reporting", /report|writer|报告|写/i],
  ["Research", /research|研究/i],
  ["Analytics", /analy|分析/i],
];

/** An agent belongs to every group its skills fall in, plus what its role says; managers are Management. */
export function categoriesOf(a: Agent, skills?: Skill[]): Set<Category> {
  const out = new Set<Category>();
  if (a.is_manager) out.add("Management");
  for (const s of a.skills) {
    const c = skills?.find((x) => x.id === s)?.category || SKILL_CATEGORY[s];
    if ((CATEGORIES as readonly string[]).includes(c)) out.add(c as Category);
  }
  for (const [c, re] of ROLE_HINT) if (re.test(a.role)) out.add(c);
  return out;
}

export type View = "all" | "mine" | "active" | "idle" | "running" | "favorites" | "attention";

export function inView(a: Agent, v: View, favs: string[]): boolean {
  switch (v) {
    case "all": return true;
    case "mine": return a.status !== "archived";
    case "active": return a.status === "active";
    case "idle": return a.live === "idle";
    case "running": return a.live === "busy";
    case "favorites": return favs.includes(a.id);
    case "attention": return a.live === "error";
  }
}
