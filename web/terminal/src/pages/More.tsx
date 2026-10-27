import { Boxes, ChevronRight, LogOut, SquareArrowOutUpRight } from "lucide-react";
import { MORE } from "../components/TerminalHeader";
import { setToken } from "../lib/api";
import { DEMO } from "../lib/demo";
import { go } from "../lib/util";

/** Everything the header's More menu holds, plus Workspace, as one list for phones. */
export default function More() {
  const groups = [{ group: "Work", items: [["workspace", "Workspace", "Repositories, contracts, tokens, wallets, watchers", Boxes]] as typeof MORE[number]["items"] }, ...MORE];
  return (
    <div className="page">
      <div className="toolbar"><h1>More</h1></div>
      {groups.map((g) => (
        <section key={g.group} className="m-sec">
          <span className="label">{g.group}</span>
          <div className="m-list card">
            {g.items.map(([page, label, sub, Icon, params]) => (
              <button key={label} className="m-row" onClick={() => go(page, undefined, params)}>
                <span className="more-ic"><Icon size={18} /></span>
                <span className="col gap2 grow"><span className="strong">{label}</span><span className="muted xs">{sub}</span></span>
                <ChevronRight size={16} className="muted" />
              </button>
            ))}
          </div>
        </section>
      ))}
      <section className="m-sec">
        <div className="m-list card">
          {!DEMO && <a href="/" className="m-row"><span className="more-ic"><SquareArrowOutUpRight size={18} /></span><span className="grow">Classic console</span><ChevronRight size={16} className="muted" /></a>}
          <button className="m-row" onClick={() => setToken("")}><span className="more-ic"><LogOut size={18} /></span><span className="grow">Sign out</span></button>
        </div>
      </section>
    </div>
  );
}
