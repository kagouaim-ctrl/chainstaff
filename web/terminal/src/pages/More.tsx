import { ChevronRight } from "lucide-react";
import { NAV } from "../components/AppShell";

/** Mobile hub for the pages that don't fit in the bottom bar. */
export default function More() {
  return (
    <div className="page">
      <div className="page-head"><h1>More</h1></div>
      <div className="m-list">
        {NAV.filter(([id]) => ["onchain", "skills", "activity", "settings"].includes(id)).map(([id, label, Icon]) => (
          <a key={id} href={`#/${id}`} className="m-card row sp" style={{ textDecoration: "none", flexDirection: "row" }}>
            <span className="row"><Icon size={18} /> {label}</span>
            <ChevronRight size={16} className="muted" />
          </a>
        ))}
        <a href="/" className="m-card row sp" style={{ textDecoration: "none", flexDirection: "row" }}>
          <span>Classic console</span>
          <ChevronRight size={16} className="muted" />
        </a>
      </div>
    </div>
  );
}
