import { Boxes, ChevronRight, LogOut, Settings, SquareArrowOutUpRight } from "lucide-react";
import { DEMO } from "../lib/demo";
import { setToken } from "../lib/api";

const ITEMS: [string, string, string, typeof Boxes][] = [
  ["workspace", "Workspace", "Repositories, contracts, tokens, wallets, endpoints, watchers", Boxes],
  ["settings", "Settings", "Models, API keys, tools, permissions, runtime, audit", Settings],
];

/** Phone hub for the pages that don't fit in the bottom bar. */
export default function More() {
  return (
    <div className="page">
      <div className="toolbar"><h1>More</h1></div>
      <div className="m-list">
        {ITEMS.map(([id, label, sub, Icon]) => (
          <a key={id} href={`#/${id}`} className="m-row">
            <Icon size={18} className="muted" />
            <span className="col gap2 grow"><span className="strong">{label}</span><span className="muted xs">{sub}</span></span>
            <ChevronRight size={16} className="muted" />
          </a>
        ))}
        {!DEMO && <a href="/" className="m-row"><SquareArrowOutUpRight size={18} className="muted" /><span className="grow">Classic console</span><ChevronRight size={16} className="muted" /></a>}
        <button className="m-row" onClick={() => setToken("")}><LogOut size={18} className="muted" /><span className="grow">Sign out</span></button>
      </div>
    </div>
  );
}
