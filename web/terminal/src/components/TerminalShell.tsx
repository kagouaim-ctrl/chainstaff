import { Activity, Bot, LayoutGrid, MoreHorizontal, RefreshCw, Target, WifiOff } from "lucide-react";
import type { ReactNode } from "react";
import { queryClient } from "../lib/api";
import { useHealth } from "../lib/health";
import { useAtApprovals } from "../lib/at";
import type { Route } from "../lib/util";
import { TerminalHeader } from "./TerminalHeader";

const MOBILE: [string, string, typeof Bot][] = [
  ["overview", "Overview", LayoutGrid],
  ["agents", "Agents", Bot],
  ["missions", "Missions", Target],
  ["activity", "Activity", Activity],
  ["more", "More", MoreHorizontal],
];
const UNDER_MORE = ["more", "workspace", "settings", "squads", "onchain", "skills", "tasks", "terminal", "board"];

/** Compact top navigation on desktop, a five-item bottom bar on phones; pages fill the rest. */
export function TerminalShell({ route, children }: { route: Route; children: ReactNode }) {
  const approvals = useAtApprovals("pending");
  const pending = approvals.data?.length || 0;
  const current = UNDER_MORE.includes(route.page) ? "more" : route.page;
  return (
    <div className="app">
      <TerminalHeader route={route} />
      <ConnectionBanner />
      <main className="main">{children}</main>
      <nav className="mnav" aria-label="底部导航">
        {MOBILE.map(([id, label, Icon]) => (
          <a key={id} href={`#/${id}`} aria-current={current === id ? "page" : undefined}>
            <Icon size={19} strokeWidth={1.8} />
            {label}
            {id === "missions" && pending > 0 && <span className="count-dot">{pending}</span>}
          </a>
        ))}
      </nav>
    </div>
  );
}

/** Live mode only: the server is not answering, so what is on screen is the last thing it said, not current state. */
function ConnectionBanner() {
  const h = useHealth();
  if (!h.down) return null;
  const at = h.lastOk ? new Date(h.lastOk).toLocaleTimeString() : null;
  return (
    <div className="conn-down" role="alert">
      <WifiOff size={14} />
      <span>
        <b>{h.reason}</b>
        {at ? `。页面上的数据停在 ${at}，不是实时数据。` : "。还没有从服务器读到任何数据。"}正在自动重试。
      </span>
      <div className="grow" />
      <button className="btn sm" onClick={() => queryClient.invalidateQueries()}><RefreshCw size={12} /> 重试</button>
    </div>
  );
}
