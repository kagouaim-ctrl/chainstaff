import { Check, Copy } from "lucide-react";
import { Fragment, useState, type ReactNode } from "react";

/**
 * The small Markdown subset agents write in results: headings, paragraphs, lists, tables, quotes, fenced code and
 * inline code / bold / links. Rendered as React elements (never innerHTML), so agent output cannot inject markup.
 */
export function Markdown({ text }: { text: string }) {
  return <div className="md">{blocks(text)}</div>;
}

type Block = { t: "code"; lang: string; body: string } | { t: "h"; level: number; body: string } | { t: "ul" | "ol"; items: string[] }
  | { t: "table"; head: string[]; rows: string[][] } | { t: "quote"; body: string } | { t: "p"; body: string };

function parse(src: string): Block[] {
  const lines = src.replace(/\r\n/g, "\n").split("\n");
  const out: Block[] = [];
  let i = 0;
  const isTableSep = (l: string) => /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(l);
  const cells = (l: string) => l.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());
  while (i < lines.length) {
    const l = lines[i];
    const fence = l.match(/^\s*```\s*([\w+-]*)/);
    if (fence) {
      const body: string[] = [];
      i++;
      while (i < lines.length && !/^\s*```/.test(lines[i])) body.push(lines[i++]);
      i++;
      out.push({ t: "code", lang: fence[1] || "", body: body.join("\n") });
      continue;
    }
    if (!l.trim()) {
      i++;
      continue;
    }
    const h = l.match(/^(#{1,4})\s+(.*)$/);
    if (h) {
      out.push({ t: "h", level: h[1].length, body: h[2] });
      i++;
      continue;
    }
    if (l.includes("|") && i + 1 < lines.length && isTableSep(lines[i + 1])) {
      const head = cells(l);
      const rows: string[][] = [];
      i += 2;
      while (i < lines.length && lines[i].includes("|") && lines[i].trim()) rows.push(cells(lines[i++]));
      out.push({ t: "table", head, rows });
      continue;
    }
    if (/^\s*([-*•])\s+/.test(l) || /^\s*\d+[.)]\s+/.test(l)) {
      const ordered = /^\s*\d+[.)]\s+/.test(l);
      const items: string[] = [];
      while (i < lines.length && (ordered ? /^\s*\d+[.)]\s+/ : /^\s*([-*•])\s+/).test(lines[i])) {
        items.push(lines[i].replace(ordered ? /^\s*\d+[.)]\s+/ : /^\s*([-*•])\s+/, ""));
        i++;
        while (i < lines.length && /^\s{2,}\S/.test(lines[i]) && !/^\s*([-*•]|\d+[.)])\s+/.test(lines[i])) items[items.length - 1] += " " + lines[i++].trim();
      }
      out.push({ t: ordered ? "ol" : "ul", items });
      continue;
    }
    if (/^\s*>/.test(l)) {
      const body: string[] = [];
      while (i < lines.length && /^\s*>/.test(lines[i])) body.push(lines[i++].replace(/^\s*>\s?/, ""));
      out.push({ t: "quote", body: body.join("\n") });
      continue;
    }
    const body: string[] = [];
    while (i < lines.length && lines[i].trim() && !/^\s*```/.test(lines[i]) && !/^#{1,4}\s/.test(lines[i]) && !/^\s*([-*•]|\d+[.)])\s+/.test(lines[i]) && !/^\s*>/.test(lines[i])
      && !(lines[i].includes("|") && i + 1 < lines.length && isTableSep(lines[i + 1]))) body.push(lines[i++]);
    out.push({ t: "p", body: body.join("\n") });
  }
  return out;
}

function blocks(src: string): ReactNode {
  return parse(src).map((b, k) => {
    switch (b.t) {
      case "code": return <CodeBlock key={k} lang={b.lang} body={b.body} />;
      case "h": return b.level <= 2 ? <h3 key={k}>{inline(b.body)}</h3> : <h4 key={k}>{inline(b.body)}</h4>;
      case "ul": return <ul key={k}>{b.items.map((x, j) => <li key={j}>{inline(x)}</li>)}</ul>;
      case "ol": return <ol key={k}>{b.items.map((x, j) => <li key={j}>{inline(x)}</li>)}</ol>;
      case "quote": return <blockquote key={k}>{inline(b.body)}</blockquote>;
      case "table": return (
        <div key={k} className="md-table">
          <table><thead><tr>{b.head.map((c, j) => <th key={j}>{inline(c)}</th>)}</tr></thead>
            <tbody>{b.rows.map((r, j) => <tr key={j}>{b.head.map((_, x) => <td key={x}>{inline(r[x] || "")}</td>)}</tr>)}</tbody></table>
        </div>
      );
      default: return <p key={k}>{inline(b.body)}</p>;
    }
  });
}

const SAFE_URL = /^https?:\/\//i;

function inline(s: string): ReactNode {
  const out: ReactNode[] = [];
  const re = /(`[^`]+`)|(\*\*[^*]+\*\*)|(\[[^\]]+\]\([^)\s]+\))|(https?:\/\/[^\s)]+)|(\n)/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let k = 0;
  while ((m = re.exec(s))) {
    if (m.index > last) out.push(s.slice(last, m.index));
    const v = m[0];
    if (m[1]) out.push(<code key={k++}>{v.slice(1, -1)}</code>);
    else if (m[2]) out.push(<strong key={k++}>{v.slice(2, -2)}</strong>);
    else if (m[3]) {
      const [, label, url] = v.match(/^\[([^\]]+)\]\(([^)\s]+)\)$/)!;
      out.push(SAFE_URL.test(url) ? <a key={k++} href={url} target="_blank" rel="noreferrer noopener">{label}</a> : <Fragment key={k++}>{label}</Fragment>);
    } else if (m[4]) out.push(<a key={k++} href={v} target="_blank" rel="noreferrer noopener">{v}</a>);
    else out.push(<br key={k++} />);
    last = m.index + v.length;
  }
  if (last < s.length) out.push(s.slice(last));
  return out;
}

function CodeBlock({ lang, body }: { lang: string; body: string }) {
  const [done, setDone] = useState(false);
  const isDiff = lang === "diff" || /^(@@|\+\+\+|---) /m.test(body);
  return (
    <div className="md-code">
      <div className="md-code-h">
        <span>{lang || (isDiff ? "diff" : "code")}</span>
        <button className="plain-btn" onClick={() => navigator.clipboard?.writeText(body).then(() => (setDone(true), setTimeout(() => setDone(false), 1200)), () => undefined)}
          aria-label="复制代码">{done ? <Check size={12} /> : <Copy size={12} />}{done ? "已复制" : "复制"}</button>
      </div>
      <pre className={isDiff ? "code diff" : "code"}>
        {isDiff ? body.split("\n").map((l, i) => (
          <span key={i} className={l.startsWith("+") && !l.startsWith("+++") ? "ok" : l.startsWith("-") && !l.startsWith("---") ? "bad" : l.startsWith("@@") ? "info" : ""}>{l}{"\n"}</span>
        )) : body}
      </pre>
    </div>
  );
}
