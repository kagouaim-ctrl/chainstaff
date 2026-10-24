import { Columns3 } from "lucide-react";
import { memo, useMemo, useRef, useState, useEffect, type ReactNode } from "react";
import { usePref } from "../lib/util";

export interface Column<T> {
  id: string;
  header: ReactNode;
  title?: string; // name in the column menu when the header is an icon
  cell: (row: T) => ReactNode;
  sort?: (row: T) => string | number;
  align?: "r" | "c";
  width?: number;
  hideable?: boolean; // default true
  defaultHidden?: boolean;
}

interface Props<T> {
  id: string; // persists column choices per table
  rows: T[];
  columns: Column<T>[];
  rowKey: (r: T) => string;
  onRowClick?: (r: T) => void;
  selectedKey?: string | null;
  rowClass?: (r: T) => string;
  selectable?: boolean;
  selection?: Set<string>;
  onSelection?: (s: Set<string>) => void;
  serverSort?: { by: string; desc: boolean; onChange: (by: string, desc: boolean) => void };
  initialSort?: { by: string; desc: boolean };
  empty?: ReactNode;
  /** Row ids to briefly highlight because their live state just changed. */
  flash?: Set<string>;
}

function DataTableInner<T>({ id, rows, columns, rowKey, onRowClick, selectedKey, rowClass, selectable, selection, onSelection, serverSort, initialSort, empty, flash }: Props<T>) {
  const [hidden, setHidden] = usePref<string[]>(`cols:${id}`, columns.filter((c) => c.defaultHidden).map((c) => c.id));
  const [sort, setSort] = useState<{ by: string; desc: boolean } | null>(initialSort || null);
  const [menu, setMenu] = useState(false);
  const menuRef = useRef<HTMLTableCellElement>(null);
  useEffect(() => {
    if (!menu) return;
    const f = (e: MouseEvent) => menuRef.current && !menuRef.current.contains(e.target as Node) && setMenu(false);
    document.addEventListener("mousedown", f);
    return () => document.removeEventListener("mousedown", f);
  }, [menu]);
  const visible = columns.filter((c) => !hidden.includes(c.id));

  const sorted = useMemo(() => {
    if (serverSort || !sort) return rows;
    const col = columns.find((c) => c.id === sort.by);
    if (!col?.sort) return rows;
    const f = col.sort;
    return [...rows].sort((a, b) => {
      const x = f(a), y = f(b);
      const r = x < y ? -1 : x > y ? 1 : 0;
      return sort.desc ? -r : r;
    });
  }, [rows, sort, columns, serverSort]);

  const active = serverSort ? { by: serverSort.by, desc: serverSort.desc } : sort;
  const clickHead = (c: Column<T>) => {
    if (!c.sort) return;
    const desc = active?.by === c.id ? !active.desc : true;
    serverSort ? serverSort.onChange(c.id, desc) : setSort({ by: c.id, desc });
  };
  const allKeys = sorted.map(rowKey);
  const allOn = selectable && allKeys.length > 0 && allKeys.every((k) => selection?.has(k));

  return (
    <div className="table-wrap">
      <table className="dt">
        <thead>
          <tr>
            {selectable && (
              <th style={{ width: 30 }}>
                <input type="checkbox" aria-label="全选" checked={!!allOn} onChange={() => onSelection?.(allOn ? new Set() : new Set(allKeys))} />
              </th>
            )}
            {visible.map((c) => (
              <th
                key={c.id}
                className={`${c.sort ? "sortable" : ""} ${c.align || ""}`}
                style={c.width ? { width: c.width } : undefined}
                onClick={() => clickHead(c)}
                aria-sort={active?.by === c.id ? (active.desc ? "descending" : "ascending") : undefined}
                title={c.title}
              >
                {c.header}
                {active?.by === c.id && <span className="arr">{active.desc ? "▼" : "▲"}</span>}
              </th>
            ))}
            <th style={{ width: 34 }} className="rel" ref={menuRef}>
              <button className="btn ghost xs icon" aria-label="自定义列" title="自定义列" onClick={() => setMenu((m) => !m)}>
                <Columns3 size={12} />
              </button>
              {menu && (
                <div className="menu">
                  <span className="label">Columns</span>
                  {columns
                    .filter((c) => c.hideable !== false)
                    .map((c) => (
                      <label key={c.id}>
                        <input type="checkbox" checked={!hidden.includes(c.id)} onChange={() => setHidden((h) => (h.includes(c.id) ? h.filter((x) => x !== c.id) : [...h, c.id]))} />
                        {c.title || c.header}
                      </label>
                    ))}
                </div>
              )}
            </th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((r) => {
            const k = rowKey(r);
            return (
              <tr
                key={k}
                className={`${onRowClick ? "click" : ""} ${flash?.has(k) ? "flash" : ""} ${rowClass?.(r) || ""}`}
                aria-selected={selectedKey === k}
                onClick={() => onRowClick?.(r)}
              >
                {selectable && (
                  <td onClick={(e) => e.stopPropagation()}>
                    <input
                      type="checkbox"
                      aria-label="选择"
                      checked={!!selection?.has(k)}
                      onChange={() => {
                        const s = new Set(selection);
                        s.has(k) ? s.delete(k) : s.add(k);
                        onSelection?.(s);
                      }}
                    />
                  </td>
                )}
                {visible.map((c) => <td key={c.id} className={c.align || ""}>{c.cell(r)}</td>)}
                <td />
              </tr>
            );
          })}
        </tbody>
      </table>
      {!rows.length && (empty ?? <div className="empty">没有数据</div>)}
    </div>
  );
}

export const DataTable = memo(DataTableInner) as typeof DataTableInner;

/** Ids whose `key(row)` changed since the last render, held for ~1.4s, so live updates are visible. */
export function useFlash<T>(rows: T[] | undefined, id: (r: T) => string, key: (r: T) => string): Set<string> {
  const prev = useRef<Map<string, string> | null>(null);
  const timer = useRef<number>();
  const [on, setOn] = useState<Set<string>>(new Set());
  useEffect(() => {
    if (!rows) return;
    const next = new Map(rows.map((r) => [id(r), key(r)]));
    const changed = prev.current ? [...next].filter(([k, v]) => prev.current!.has(k) && prev.current!.get(k) !== v).map(([k]) => k) : [];
    prev.current = next;
    if (!changed.length) return;
    setOn(new Set(changed));
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setOn(new Set()), 1400);
  }, [rows, id, key]);
  useEffect(() => () => window.clearTimeout(timer.current), []);
  return on;
}
