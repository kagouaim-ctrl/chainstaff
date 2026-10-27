import { RefreshCw, WifiOff } from "lucide-react";
import { useEffect, type ReactNode } from "react";
import { queryClient } from "../lib/api";
import { useAtApprovals } from "../lib/at";
import { useHealth } from "../lib/health";
import { closeOps, setSideOpen, useShell } from "../lib/shell";
import { useMedia, usePref, type Route } from "../lib/util";
import { CreateWorker } from "./CreateWorker";
import { LiveOps } from "./LiveOps";
import { NAV, Sidebar } from "./Sidebar";
import { navCurrent, TerminalHeader } from "./TerminalHeader";

/**
 * Header across the top, the sidebar on the left (an icon rail when collapsed, a drawer on phones), the page in
 * the middle, and Live Operations on the right: floating when opened, a column when pinned.
 */
export function TerminalShell({ route, children }: { route: Route; children: ReactNode }) {
  const approvals = useAtApprovals("pending");
  const pending = approvals.data?.length || 0;
  const shell = useShell();
  const [collapsedPref, setCollapsed] = usePref<boolean>("side-collapsed", false);
  const [pinned, setPinned] = usePref<boolean>("ops-pinned", false);
  const narrow = useMedia("(max-width: 1120px)");
  const phone = useMedia("(max-width: 760px)");
  const collapsed = phone ? false : narrow || collapsedPref;
  const current = navCurrent(route.page);
  const opsCol = !!shell.ops && pinned && !narrow;
  useEffect(() => setSideOpen(false), [route.page, route.id]);
  return (
    <div className="app" data-side={collapsed ? "collapsed" : "open"} data-ops={opsCol ? "pinned" : undefined} data-drawer={shell.sideOpen ? "open" : undefined}>
      <TerminalHeader />
      <ConnectionBanner />
      <Sidebar route={route} collapsed={collapsed} onCollapse={setCollapsed} />
      {shell.sideOpen && <div className="scrim side-scrim" onClick={() => setSideOpen(false)} />}
      <main className="main">
        <div className="route" key={route.page + (route.id || "")}>{children}</div>
      </main>
      {shell.ops && (
        <>
          {!opsCol && <div className="ops-scrim" onClick={closeOps} />}
          <LiveOps tab={shell.ops} pinned={opsCol} onPin={setPinned} />
        </>
      )}
      <nav className="mnav" aria-label="底部导航">
        {NAV.map(([id, label, Icon]) => (
          <a key={id} href={`#/${id}`} aria-current={current === id ? "page" : undefined}>
            <Icon size={20} strokeWidth={1.8} />
            {label}
            {id === "chat" && pending > 0 && <span className="count-dot">{pending}</span>}
          </a>
        ))}
      </nav>
      {shell.creating && <CreateWorker />}
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
