import { useEffect, useMemo, useState } from "react";
import { ApiError, get, post } from "../api";
import { useApi } from "../hooks";
import { ErrorBox } from "./ui";
import { BuilderInfo, ChatLog, Mode, PendingApproval, ProviderPick, useChat } from "./ChatFeed";
import type { View } from "./VmForms";

// The chat panel next to a range's map. Three modes: Question (read-only look), Plan (a draft spec + VALOR's plan)
// and Code (works inside the VMs; every change waits for approval unless the person allowed all for this chat).
// When the assistant changes the draft, the map reloads it.

interface ChatSummary { id: number; title: string; updated: number; status: string }

const MODES: { mode: Mode; label: string; hint: string; placeholder: string }[] = [
  { mode: "question", label: "Question", hint: "Answers about this range; may look inside the VMs with read-only commands.", placeholder: "Who can reach the database? Is nginx running on web?" },
  { mode: "plan", label: "Plan", hint: "Proposes a change as a draft on the map, with VALOR's plan to approve.", placeholder: "Add a second web server behind the same policy" },
  { mode: "code", label: "Code", hint: "Works in the VMs (commands, files, services, power, snapshots) and records changes in the spec. Changes wait for your approval.", placeholder: "Install and configure PostgreSQL on db, reachable from web" },
];

const EMPTY_HINT = "Ask about this range or describe a change. Conversations are saved (Datacenter → Chats).";

export function RangeChat({ name, chatId, onView, onReview, onClose }: {
  name: string; chatId?: number; onView: (view: View) => void; onReview: () => void; onClose: () => void;
}) {
  const info = useApi<BuilderInfo>("/api/builder");
  const list = useApi<{ chats: ChatSummary[] }>(`/api/chats?range=${encodeURIComponent(name)}`);
  const [current, setCurrent] = useState<number | null>(chatId ?? null);
  const [mode, setMode] = useState<Mode>("question");
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [providerPick, setProviderPick] = useState("");      // the provider for a new conversation ("" = the default)
  const [err, setErr] = useState<ApiError | null>(null);
  const refreshMap = () => get<View>(`/api/ranges/${name}/draft`).then(onView).catch(() => undefined);
  const conversation = useChat(current, (message) => { if (message.meta.draft_changed) refreshMap(); });
  const modeInfo = useMemo(() => MODES.find((entry) => entry.mode === mode)!, [mode]);

  useEffect(() => { if (chatId) setCurrent(chatId); }, [chatId]);
  useEffect(() => {                                   // open the newest conversation about this range
    if (current === null && list.data?.chats.length) setCurrent(list.data.chats[0].id);
  }, [list.data]);

  const send = async (message: string, asMode: Mode = mode) => {
    message = message.trim();
    if (!message || sending || conversation.busy) return;
    setSending(true); setErr(null);
    try {
      if (!conversation.chat) {                       // the first message starts a new conversation
        const created = await post<{ id: number }>("/api/chats", { kind: "range", range: name, ...(providerPick ? { provider: providerPick } : {}) });
        await post(`/api/chats/${created.id}/messages`, { message, mode: asMode });
        setCurrent(created.id);
        list.reload();
      } else {
        await conversation.send(message, asMode);
      }
      setText("");
    } catch (e) { setErr(e as ApiError); }
    finally { setSending(false); }
  };
  const chat = conversation.chat;
  const readonly = !!chat?.readonly;                  // an admin reading someone else's conversation
  // Answers to question cards go back in the mode the question was asked in.
  const lastMode = chat?.messages.filter((message) => message.role === "user").slice(-1)[0]?.mode ?? mode;

  return (
    <aside className="range-chat card" aria-label="Range chat">
      <div className="card-head">
        <h2>Chat</h2>
        <select name="conversation" className="chat-pick grow" value={current ?? ""} aria-label="Conversation"
          onChange={(e) => setCurrent(e.target.value ? Number(e.target.value) : null)}>
          <option value="">New conversation</option>
          {(list.data?.chats ?? []).map((summary) => <option key={summary.id} value={summary.id}>{summary.title || `Conversation ${summary.id}`}</option>)}
        </select>
        <button className="btn ghost small" onClick={onClose} aria-label="Close chat">✕</button>
      </div>
      {info.data && !info.data.configured ? (
        <div className="card-body muted">The chat needs an AI provider: an administrator can connect one in Datacenter → AI providers.</div>
      ) : (
        <>
          <div className="seg-control" role="tablist">
            {MODES.map((entry) => (
              <button key={entry.mode} role="tab" aria-selected={mode === entry.mode} className={mode === entry.mode ? "active" : ""}
                onClick={() => setMode(entry.mode)}>{entry.label}</button>
            ))}
          </div>
          <div className="muted small" style={{ padding: "0 12px" }}>{modeInfo.hint}</div>
          {info.data && info.data.providers.length > 1 && (
            <label className="provider-row small">Model
              <ProviderPick info={info.data} value={chat ? chat.provider : providerPick} disabled={readonly || sending || conversation.busy}
                onChange={(id) => chat ? conversation.setProvider(id).catch((e) => setErr(e as ApiError)) : setProviderPick(id)} />
            </label>
          )}
          {mode === "code" && chat && !readonly && (
            <label className="allow-all small">
              <input name="allow-all" type="checkbox" checked={chat.allow_all} onChange={(e) => {
                if (!e.target.checked || window.confirm("Let the assistant run commands and other changes in this chat without asking first?"))
                  conversation.setAllowAll(e.target.checked).catch((x) => setErr(x as ApiError));
              }} /> Run changes without asking (this conversation)
            </label>
          )}
          <div className="chat-log range">
            {chat ? <ChatLog chat={chat} ctl={conversation} onReview={onReview} onAnswer={(answer) => send(answer, lastMode)}
              empty={<div className="muted small">{EMPTY_HINT}</div>} />
              : <div className="muted small">{EMPTY_HINT}</div>}
          </div>
          <PendingApproval chat={chat} ctl={conversation} />
          <ErrorBox error={err ?? conversation.error} />
          {readonly ? <div className="card-body muted small">This is {chat?.owner}'s conversation (read-only).</div> : (
            <form className="chat-input" onSubmit={(e) => { e.preventDefault(); send(text); }}>
              <textarea name="message" rows={3} value={text} disabled={sending || conversation.busy}
                placeholder={conversation.busy ? "The assistant is working…" : modeInfo.placeholder}
                onChange={(e) => setText(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(text); } }} />
              <button className="btn primary" disabled={sending || conversation.busy || !text.trim()}>Send</button>
            </form>
          )}
        </>
      )}
    </aside>
  );
}
