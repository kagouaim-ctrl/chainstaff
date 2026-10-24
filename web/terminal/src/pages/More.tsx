import { Activity, Blocks, ChevronRight, LogOut, Puzzle, Settings, SquareArrowOutUpRight, Users } from "lucide-react";
import { setToken } from "../lib/api";

const ITEMS: [string, string, string, typeof Blocks][] = [
  ["squads", "Squads", "Teams of agents, their manager and policy", Users],
  ["onchain", "On-Chain", "Execution ledger, transactions, agent registry", Blocks],
  ["skills", "Skills", "Web3 skills and who can run them", Puzzle],
  ["activity", "Activity", "Every event, live", Activity],
  ["settings", "Settings", "Scheduled missions, memory, system", Settings],
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
        <a href="/" className="m-row"><SquareArrowOutUpRight size={18} className="muted" /><span className="grow">Classic console</span><ChevronRight size={16} className="muted" /></a>
        <button className="m-row" onClick={() => setToken("")}><LogOut size={18} className="muted" /><span className="grow">Sign out</span></button>
      </div>
    </div>
  );
}
