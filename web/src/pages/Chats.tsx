import { useState } from "react";
import { ApiError, del } from "../api";
import { go, useApi, when } from "../hooks";
import { ErrorBox, Loading } from "../components/ui";

// Saved conversations: the chat builder's and every range chat's. People see their own; administrators can also
// read everyone's (for auditing) but only the owner continues a conversation.

interface ChatRow { id: number; owner: string; kind: "builder" | "range"; range: string | null; title: string; updated: number; messages: number; status: string }

const STATUS: Record<string, [string, string]> = {
  running: ["info", "working"], waiting: ["warn", "waiting for approval"], failed: ["bad", "failed"],
  stopped: ["", "stopped"], done: ["ok", "done"],
};

export function Chats({ isAdmin }: { isAdmin: boolean }) {
  const [scope, setScope] = useState<"mine" | "all">("mine");
  const [kind, setKind] = useState("");
  const list = useApi<{ chats: ChatRow[] }>(`/api/chats?scope=${scope}${kind ? `&kind=${kind}` : ""}`, 15000);
  const [err, setErr] = useState<ApiError | null>(null);
  const open = (c: ChatRow) => go(c.kind === "builder" ? `builder/${c.id}` : `ranges/${c.range}/chat/${c.id}`);
  const remove = async (c: ChatRow) => {
    if (!window.confirm(`Delete the conversation "${c.title || c.id}"${c.owner ? ` (${c.owner})` : ""}?`)) return;
    try { await del(`/api/chats/${c.id}`); list.reload(); } catch (e) { setErr(e as ApiError); }
  };
  return (
    <section className="card">
      <div className="card-head">
        <h2 className="grow">Conversations</h2>
        <select name="kind" value={kind} onChange={(e) => setKind(e.target.value)} aria-label="Kind">
          <option value="">All kinds</option><option value="builder">Chat builder</option><option value="range">Range chats</option>
        </select>
        {isAdmin && (
          <div className="seg-control compact" role="tablist">
            <button role="tab" aria-selected={scope === "mine"} className={scope === "mine" ? "active" : ""} onClick={() => setScope("mine")}>Mine</button>
            <button role="tab" aria-selected={scope === "all"} className={scope === "all" ? "active" : ""} onClick={() => setScope("all")}>Everyone</button>
          </div>
        )}
        <button className="btn small primary" onClick={() => go("builder")}>New in the chat builder</button>
      </div>
      <ErrorBox error={err ?? list.error} />
      {!list.data ? <Loading /> : !list.data.chats.length ? (
        <div className="empty">No conversations yet. Start one in the chat builder or in the Chat panel on a range's map.</div>
      ) : (
        <div className="table-wrap">
          <table>
            <thead><tr><th>Conversation</th><th>Where</th>{scope === "all" && <th>Owner</th>}<th>Messages</th><th>Updated</th><th>Status</th><th /></tr></thead>
            <tbody>
              {list.data.chats.map((c) => {
                const [cls, label] = STATUS[c.status] ?? ["", c.status];
                return (
                  <tr key={c.id} className="clickable" onClick={() => open(c)}>
                    <td>{c.title || <span className="muted">(empty)</span>}</td>
                    <td>{c.kind === "builder" ? "Chat builder" : <>Range <b>{c.range}</b></>}</td>
                    {scope === "all" && <td>{c.owner}</td>}
                    <td>{c.messages}</td>
                    <td className="nowrap">{when(c.updated)}</td>
                    <td><span className={`badge ${cls}`}>{label}</span></td>
                    <td><button className="btn ghost small" onClick={(e) => { e.stopPropagation(); remove(c); }}>Delete</button></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
