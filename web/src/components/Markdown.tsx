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
      out.push(<List key={key}>{items.map((item, n) => <li key={n}>{inline(item)}</li>)}</List>);
    } else {
      const para = [line.trim()];
      for (i++; i < lines.length && lines[i].trim() && !startsBlock(lines, i); i++) para.push(lines[i].trim());
      out.push(<p key={key}>{inline(para.join(" "))}</p>);
    }
  }
  return out;
}

function startsBlock(lines: string[], i: number) {
  const line = lines[i];
  return line.startsWith("```") || line.startsWith(">") || /^#{1,6}\s/.test(line) || LIST_ITEM.test(line) || line.trim().startsWith("<!--")
    || /^(-{3,}|\*{3,}|_{3,})\s*$/.test(line) || (line.trim().startsWith("|") && TABLE_RULE.test(lines[i + 1] ?? ""));
}

/** The cells of a table row; `\|` is a literal bar inside a cell. */
function cells(row: string) {
  const parts = row.trim().replace(/^\|/, "").replace(/\|$/, "").split(/(?<!\\)\|/);
  return parts.map((cell) => cell.trim().replace(/\\\|/g, "|"));
}

function Table({ head, rows }: { head: string[]; rows: string[][] }) {
  // a header of empty cells ("| | |") is a key/value table: no header row, the keys in bold
  const keyValue = head.every((heading) => !heading);
  return (
    <div className="table-wrap">
      <table className={keyValue ? "md-kv" : undefined}>
        {!keyValue && <thead><tr>{head.map((heading, n) => <th key={n}>{inline(heading)}</th>)}</tr></thead>}
        <tbody>
          {rows.map((row, n) => (
            <tr key={n}>{head.map((_, col) => <td key={col}>{inline(row[col] ?? "")}</td>)}</tr>
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
  for (const match of text.matchAll(INLINE)) {
    if (match.index > last) out.push(text.slice(last, match.index));
    const key = out.length;
    if (match[1] !== undefined) out.push(<code key={key}>{match[1]}</code>);
    else if (match[2] !== undefined) out.push(<strong key={key}>{inline(match[2])}</strong>);
    else if (match[3] !== undefined || match[4] !== undefined) out.push(<em key={key}>{inline(match[3] ?? match[4])}</em>);
    else if (match[7] !== undefined) out.push(match[7] === "✅"     // as the UI's own marks: emoji fonts are not everywhere
      ? <span key={key} className="md-pass" title="pass">✓</span> : <span key={key} className="md-fail" title="fail">✗</span>);
    else if (/^https?:\/\//.test(match[6])) out.push(<a key={key} href={match[6]} target="_blank" rel="noreferrer">{inline(match[5])}</a>);
    else out.push(match[0]);                                   // only web links: no javascript: or other schemes
    last = match.index + match[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}
