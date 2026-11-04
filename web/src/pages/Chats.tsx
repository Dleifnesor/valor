// *flips through old conversations* OwO web/src/pages/Chats.tsx lists saved CONVERSATIONS! ^w^
// Your chat-builder chats and every range chat. Admins can switch to "Everyone" to read all of them (for auditing),
// but only the owner can continue a conversation *wags tail*
import { useState } from "react";
// del + ApiError, go/useApi/when (navigation, data loading, dates), ErrorBox/Loading, and the right-click menu
// helpers UwU
import { ApiError, del } from "../api";
import { go, useApi, when } from "../hooks";
import { ErrorBox, Loading } from "../components/ui";
import { Menu, newTab, showMenu } from "../components/ContextMenu";

// Saved conversations: the chat builder's and every range chat's. People see their own; administrators can also
// read everyone's (for auditing) but only the owner continues a conversation.

// ChatRow: one conversation in the list >w<
interface ChatRow { id: number; owner: string; kind: "builder" | "range"; range: string | null; title: string; updated: number; messages: number; status: string }

// STATUS: the badge class and label for each answer status OwO
const STATUS: Record<string, [string, string]> = {
  running: ["info", "working"], waiting: ["warn", "waiting for approval"], failed: ["bad", "failed"],
  stopped: ["", "stopped"], done: ["ok", "done"],
};

// Chats: the page; isAdmin shows the Mine/Everyone switch ^w^
export function Chats({ isAdmin }: { isAdmin: boolean }) {
  // Filters (whose, which kind), the list (refreshed every 15 s) and the last error UwU
  const [scope, setScope] = useState<"mine" | "all">("mine");
  const [kind, setKind] = useState("");
  const list = useApi<{ chats: ChatRow[] }>(`/api/chats?scope=${scope}${kind ? `&kind=${kind}` : ""}`, 15000);
  const [err, setErr] = useState<ApiError | null>(null);
  // href(): where a conversation opens (the builder, or the range page's chat); open() goes there >w<
  const href = (chat: ChatRow) => chat.kind === "builder" ? `builder/${chat.id}` : `ranges/${chat.range}/chat/${chat.id}`;
  const open = (chat: ChatRow) => go(href(chat));
  // menu(): the right-click menu: open, open in a new tab, open the range, delete OwO
  const menu = (chat: ChatRow): Menu => ({
    title: chat.title || `Conversation ${chat.id}`,
    items: [
      { label: "Open", icon: "chat", onClick: () => open(chat) },
      newTab(href(chat)),
      chat.kind === "range" && { label: `Open range '${chat.range}'`, icon: "ranges", onClick: () => go(`ranges/${chat.range}`) },
      "-",
      { label: "Delete", icon: "trash", danger: true, onClick: () => remove(chat) },
    ],
  });
  // remove(): delete after a confirmation, then reload the list ^w^
  const remove = async (chat: ChatRow) => {
    if (!window.confirm(`Delete the conversation "${chat.title || chat.id}"${chat.owner ? ` (${chat.owner})` : ""}?`)) return;
    try { await del(`/api/chats/${chat.id}`); list.reload(); } catch (e) { setErr(e as ApiError); }
  };
  // The card: a header with the filters and "New", then the table UwU
  return (
    <section className="card">
      {/* Header: title, the kind filter, Mine/Everyone (admins), and a button to the chat builder >w< */}
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
      {/* Errors, then: loading, an empty message, or the table OwO */}
      <ErrorBox error={err ?? list.error} />
      {!list.data ? <Loading /> : !list.data.chats.length ? (
        <div className="empty">No conversations yet. Start one in the chat builder or in the Chat panel on a range's map.</div>
      ) : (
        <div className="table-wrap">
          <table>
            <thead><tr><th>Conversation</th><th>Where</th>{scope === "all" && <th>Owner</th>}<th>Messages</th><th>Updated</th><th>Status</th><th /></tr></thead>
            <tbody>
              {/* One clickable row per conversation (right-click for the menu); the Delete button stops the click from also
                 opening it ^w^ */}
              {list.data.chats.map((chat) => {
                const [cls, label] = STATUS[chat.status] ?? ["", chat.status];
                return (
                  <tr key={chat.id} className="clickable" onClick={() => open(chat)} onContextMenu={(e) => showMenu(e, menu(chat))}>
                    {/* Title, where (builder or which range), owner (in Everyone view), message count, updated, status badge, Delete UwU */}
                    <td>{chat.title || <span className="muted">(empty)</span>}</td>
                    <td>{chat.kind === "builder" ? "Chat builder" : <>Range <b>{chat.range}</b></>}</td>
                    {scope === "all" && <td>{chat.owner}</td>}
                    <td>{chat.messages}</td>
                    <td className="nowrap">{when(chat.updated)}</td>
                    <td><span className={`badge ${cls}`}>{label}</span></td>
                    <td><button className="btn ghost small" onClick={(e) => { e.stopPropagation(); remove(chat); }}>Delete</button></td>
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
