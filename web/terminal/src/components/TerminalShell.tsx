import { Bot, Boxes, LayoutGrid, ListChecks, MoreHorizontal } from "lucide-react";
import type { ReactNode } from "react";
import { useApprovals } from "../lib/queries";
import type { Route } from "../lib/util";
import { TerminalHeader } from "./TerminalHeader";

const MOBILE: [string, string, typeof Bot][] = [
  ["terminal", "Terminal", LayoutGrid],
  ["agents", "Agents", Bot],
  ["workspace", "Workspace", Boxes],
  ["tasks", "Tasks", ListChecks],
  ["more", "More", MoreHorizontal],
];
const UNDER_MORE = ["more", "onchain", "skills", "activity", "settings"];

/** Compact top navigation on desktop, a five-item bottom bar on phones; pages fill the rest. */
export function TerminalShell({ route, children }: { route: Route; children: ReactNode }) {
  const approvals = useApprovals("pending");
  const pending = approvals.data?.length || 0;
  const current = UNDER_MORE.includes(route.page) ? "more" : route.page;
  return (
    <div className="app">
      <TerminalHeader route={route} />
      <main className="main">{children}</main>
      <nav className="mnav" aria-label="底部导航">
        {MOBILE.map(([id, label, Icon]) => (
          <a key={id} href={`#/${id}`} aria-current={current === id ? "page" : undefined}>
            <Icon size={19} strokeWidth={1.8} />
            {label}
            {id === "workspace" && pending > 0 && <span className="count-dot">{pending}</span>}
          </a>
        ))}
      </nav>
    </div>
  );
}
