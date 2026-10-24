import { Columns3 } from "lucide-react";
import { memo, useMemo, useState, type ReactNode } from "react";
import { usePref } from "../lib/util";

export interface Column<T> {
  id: string;
  header: string;
  cell: (row: T) => ReactNode;
  sort?: (row: T) => string | number;
  align?: "r";
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
  selectable?: boolean;
  selection?: Set<string>;
  onSelection?: (s: Set<string>) => void;
  serverSort?: { by: string; desc: boolean; onChange: (by: string, desc: boolean) => void };
  empty?: ReactNode;
}

function DataTableInner<T>({ id, rows, columns, rowKey, onRowClick, selectedKey, selectable, selection, onSelection, serverSort, empty }: Props<T>) {
  const [hidden, setHidden] = usePref<string[]>(`cols:${id}`, columns.filter((c) => c.defaultHidden).map((c) => c.id));
  const [sort, setSort] = useState<{ by: string; desc: boolean } | null>(null);
  const [menu, setMenu] = useState(false);
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
              <th style={{ width: 32 }}>
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
              >
                {c.header}
                {active?.by === c.id ? (active.desc ? " ↓" : " ↑") : ""}
              </th>
            ))}
            <th style={{ width: 36 }} className="rel">
              <button className="btn ghost sm icon" aria-label="自定义列" title="自定义列" onClick={() => setMenu((m) => !m)}>
                <Columns3 size={13} />
              </button>
              {menu && (
                <div className="menu" onMouseLeave={() => setMenu(false)}>
                  {columns
                    .filter((c) => c.hideable !== false)
                    .map((c) => (
                      <label key={c.id}>
                        <input
                          type="checkbox"
                          checked={!hidden.includes(c.id)}
                          onChange={() => setHidden((h) => (h.includes(c.id) ? h.filter((x) => x !== c.id) : [...h, c.id]))}
                        />
                        {c.header}
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
              <tr key={k} className={onRowClick ? "click" : ""} aria-selected={selectedKey === k} onClick={() => onRowClick?.(r)}>
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
                {visible.map((c) => (
                  <td key={c.id} className={c.align || ""}>
                    {c.cell(r)}
                  </td>
                ))}
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
