import { ReactNode } from "react";

// A small Markdown renderer for the range journal (valor/journal.py) and the notes appended to it: headings,
// paragraphs, quotes, lists, tables, fenced code and inline code/bold/italic/links. No raw HTML: everything is
// rendered as React text, so a journal cannot inject markup. A fenced block can be swapped for something richer
// with `fence` (the range view shows the Mermaid topology as the range map).

type Fence = (lang: string, code: string) => ReactNode | undefined;

export function Markdown({ text, fence }: { text: string; fence?: Fence }) {
  return <div className="md">{blocks(text.replace(/\r\n?/g, "\n").split("\n"), fence)}</div>;
}

const TABLE_RULE = /^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$/;
const LIST_ITEM = /^\s*([-*+]|\d+[.)])\s+/;

function blocks(lines: string[], fence?: Fence): ReactNode[] {
  const out: ReactNode[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    const key = out.length;
    if (!line.trim()) {
      i++;
    } else if (line.startsWith("```")) {
      const lang = line.slice(3).trim();
      const body: string[] = [];
      for (i++; i < lines.length && !lines[i].startsWith("```"); i++) body.push(lines[i]);
      i++;                                                 // the closing fence
      const code = body.join("\n");
      out.push(<div key={key}>{fence?.(lang, code) ?? <pre className="md-code">{code}</pre>}</div>);
    } else if (line.trim().startsWith("<!--")) {
      while (i < lines.length && !lines[i].includes("-->")) i++;
      i++;                                                 // comments are notes for whoever edits the file
    } else if (/^#{1,6}\s/.test(line)) {
      const level = line.match(/^#+/)![0].length;
      const H = `h${Math.min(level + 1, 6)}` as "h2";      // the card title is the page's h2 level already
      out.push(<H key={key} className={`md-h${level}`}>{inline(line.slice(level).trim())}</H>);
      i++;
    } else if (line.startsWith(">")) {
      const quote: string[] = [];
      for (; i < lines.length && lines[i].startsWith(">"); i++) quote.push(lines[i].replace(/^>\s?/, ""));
      out.push(<blockquote key={key}>{blocks(quote, fence)}</blockquote>);
    } else if (line.trim().startsWith("|") && TABLE_RULE.test(lines[i + 1] ?? "")) {
      const head = cells(line);
      const rows: string[][] = [];
      for (i += 2; i < lines.length && lines[i].trim().startsWith("|"); i++) rows.push(cells(lines[i]));
      out.push(<Table key={key} head={head} rows={rows} />);
    } else if (/^(-{3,}|\*{3,}|_{3,})\s*$/.test(line)) {
      out.push(<hr key={key} />);
      i++;
    } else if (LIST_ITEM.test(line)) {
      const ordered = /^\s*\d/.test(line);
      const items: string[] = [];
      for (; i < lines.length && lines[i].trim(); i++) {
        if (LIST_ITEM.test(lines[i])) items.push(lines[i].replace(LIST_ITEM, ""));
        else if (startsBlock(lines, i)) break;
        else items[items.length - 1] += " " + lines[i].trim();   // a wrapped item
      }
      const List = ordered ? "ol" : "ul";
      out.push(<List key={key}>{items.map((t, n) => <li key={n}>{inline(t)}</li>)}</List>);
    } else {
      const para = [line.trim()];
      for (i++; i < lines.length && lines[i].trim() && !startsBlock(lines, i); i++) para.push(lines[i].trim());
      out.push(<p key={key}>{inline(para.join(" "))}</p>);
    }
  }
  return out;
}

function startsBlock(lines: string[], i: number) {
  const l = lines[i];
  return l.startsWith("```") || l.startsWith(">") || /^#{1,6}\s/.test(l) || LIST_ITEM.test(l) || l.trim().startsWith("<!--")
    || /^(-{3,}|\*{3,}|_{3,})\s*$/.test(l) || (l.trim().startsWith("|") && TABLE_RULE.test(lines[i + 1] ?? ""));
}

/** The cells of a table row; `\|` is a literal bar inside a cell. */
function cells(row: string) {
  const parts = row.trim().replace(/^\|/, "").replace(/\|$/, "").split(/(?<!\\)\|/);
  return parts.map((c) => c.trim().replace(/\\\|/g, "|"));
}

function Table({ head, rows }: { head: string[]; rows: string[][] }) {
  // a header of empty cells ("| | |") is a key/value table: no header row, the keys in bold
  const keyValue = head.every((h) => !h);
  return (
    <div className="table-wrap">
      <table className={keyValue ? "md-kv" : undefined}>
        {!keyValue && <thead><tr>{head.map((h, n) => <th key={n}>{inline(h)}</th>)}</tr></thead>}
        <tbody>
          {rows.map((r, n) => (
            <tr key={n}>{head.map((_, c) => <td key={c}>{inline(r[c] ?? "")}</td>)}</tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// `code`, **bold**, *italic* / _italic_, [text](http…) in that order of precedence, and the journal's ✅ / ❌ marks
const INLINE = /`([^`]+)`|\*\*(.+?)\*\*|(?<![\w*])\*(?!\s)(.+?)\*(?![\w*])|(?<!\w)_(?!\s)(.+?)_(?!\w)|\[([^\]]+)\]\(([^)\s]+)\)|([✅❌])️?/g;

function inline(text: string): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  for (const m of text.matchAll(INLINE)) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const key = out.length;
    if (m[1] !== undefined) out.push(<code key={key}>{m[1]}</code>);
    else if (m[2] !== undefined) out.push(<strong key={key}>{inline(m[2])}</strong>);
    else if (m[3] !== undefined || m[4] !== undefined) out.push(<em key={key}>{inline(m[3] ?? m[4])}</em>);
    else if (m[7] !== undefined) out.push(m[7] === "✅"     // as the UI's own marks: emoji fonts are not everywhere
      ? <span key={key} className="md-pass" title="pass">✓</span> : <span key={key} className="md-fail" title="fail">✗</span>);
    else if (/^https?:\/\//.test(m[6])) out.push(<a key={key} href={m[6]} target="_blank" rel="noreferrer">{inline(m[5])}</a>);
    else out.push(m[0]);                                   // only web links: no javascript: or other schemes
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}
