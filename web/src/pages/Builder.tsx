// *chats excitedly about a new lab* OwO web/src/pages/Builder.tsx is the CHAT BUILDER page! ^w^
// Describe the environment you want; the AI asks what it needs to know (clickable question cards), writes the range
// spec, VALOR checks it and shows it on the map. Conversations are saved and can be continued. Nothing is BUILT from
// here: the spec goes to the editor or is saved as a range, then planned and approved *wags tail*
import { useState } from "react";
// post/put/ApiError, go/useApi, ErrorBox/Loading, the map, and the chat pieces (log, approvals, provider picker,
// the chat hook) UwU
import { ApiError, post, put } from "../api";
import { go, useApi } from "../hooks";
import { ErrorBox, Loading } from "../components/ui";
import { TopologyMap } from "../components/Topology";
import { BuilderInfo, Chat, ChatLog, PendingApproval, ProviderPick, useChat } from "../components/ChatFeed";

// Chat builder: describe an environment; the AI asks what it needs to know (clickable question cards), writes the
// range spec, VALOR validates it and shows it on the map. Conversations are saved (Datacenter → Chats) and can be continued.
// Nothing is built from here: the spec goes to the editor or is saved as a range, then planned and approved.

// EXAMPLES: one-click example requests shown in an empty conversation >w<
const EXAMPLES = [
  "A small web lab: an nginx server in a DMZ with internet access and a Linux client on a separate LAN that may only browse it.",
  "An Active Directory lab: two domain controllers on Windows Server 2022 Core, an IIS server joined to the domain and a Windows 11 client.",
  "A CUI enclave built toward NIST SP 800-171: file server and database without internet, central logging, an admin workstation.",
  "A pentest range: a Kali attacker, a vulnerable web server and a database the web server can reach, no internet for the targets.",
];

// Builder: isAdmin (for the "connect a provider" link); chatId = the conversation to open (null = a new one) OwO
export function Builder({ isAdmin, chatId }: { isAdmin: boolean; chatId: number | null }) {
  // AI settings, the chat state, the typed text, errors, map/YAML toggle, sending, and the provider for a new chat ^w^
  const info = useApi<BuilderInfo>("/api/builder");
  const ctl = useChat(chatId);
  const { chat } = ctl;
  const [input, setInput] = useState("");
  const [err, setErr] = useState<ApiError | null>(null);
  const [showYaml, setShowYaml] = useState(false);
  const [sending, setSending] = useState(false);
  const [pick, setPick] = useState("");                  // the provider for a new conversation ("" = the default)

  // No AI provider: explain how to connect one (admins) or ask an admin, and point to writing a spec by hand UwU
  if (info.loading && !info.data) return <Loading />;
  if (!info.data?.configured) {
    return (
      <section className="card"><div className="card-body">
        {/* Why it's off, and who can turn it on */}
        <h2 style={{ marginTop: 0 }}>The chat builder is off</h2>
        <p className="muted">
          It needs an AI provider (OpenRouter, or an OpenAI-compatible endpoint such as a model server on your
          network). {isAdmin ? <>Connect one in <button className="link" onClick={() => go("settings/ai")}>Datacenter → AI providers</button>.</>
            : "Ask an administrator to connect one (Datacenter → AI providers)."}
        </p>
        <p className="muted">You can always write a spec yourself: <button className="link" onClick={() => go("ranges/new")}>Create range</button>.</p>
      </div></section>
    );
  }
  // A chat id that hasn't loaded yet: spinner or error >w<
  if (chatId && !chat) return ctl.error ? <ErrorBox error={ctl.error} /> : <Loading />;

  // send(): the first message creates the conversation (with the chosen provider) and opens it; later ones go to the
  // existing chat OwO
  const send = async (text: string) => {
    if (!text.trim() || sending || ctl.busy) return;
    setSending(true); setErr(null);
    try {
      if (!chat) {
        const created = await post<Chat>("/api/chats", { kind: "builder", ...(pick ? { provider: pick } : {}) });
        await post(`/api/chats/${created.id}/messages`, { message: text.trim(), mode: "build" });
        go(`builder/${created.id}`);
      } else {
        await ctl.send(text.trim(), "build");
      }
      setInput("");
    } catch (e) { setErr(e as ApiError); }
    finally { setSending(false); }
  };
  // The spec the conversation produced so far ^w^
  const spec = chat?.spec ?? "";
  // openInEditor(): hand the spec to the range editor (through sessionStorage) and go there UwU
  const openInEditor = () => {
    try { sessionStorage.setItem("valor-editor-draft", spec); } catch { /* the editor starts from its example */ }
    go("ranges/new");
  };
  // saveRange(): save the spec as a range under its own name and open it >w<
  const saveRange = async () => {
    const name = /^name:\s*([a-z0-9-]+)/m.exec(spec)?.[1];
    if (!name) return;
    setErr(null);
    try { await put(`/api/ranges/${name}/spec`, { yaml: spec }); go(`ranges/${name}`); }
    catch (e) { setErr(e as ApiError); }
  };
  // readonly = an admin reading someone else's chat; `empty` = what an empty conversation shows (with examples) OwO
  const readonly = !!chat?.readonly;
  const empty = (
    <div className="stack">
      {/* A short explanation and the example buttons */}
      <div className="muted">Say what you need. The builder asks what it needs to know, writes a spec, and VALOR checks it against this cluster.</div>
      {EXAMPLES.map((example) => <button key={example} className="btn example" disabled={sending} onClick={() => send(example)}>{example}</button>)}
    </div>
  );

  // Two panels: the chat on the left, the spec preview on the right ^w^
  return (
    <div className="builder">
      {/* The chat panel */}
      <section className="card builder-chat">
        {/* Header: title, provider picker, links to all chats and a new conversation */}
        <div className="card-head">
          <h2 className="grow" title={chat?.title}>{chat?.title || "Describe the environment"}</h2>
          <ProviderPick info={info.data} value={chat ? chat.provider : pick} disabled={readonly || sending || ctl.busy}
            onChange={(id) => chat ? ctl.setProvider(id).catch((e) => setErr(e as ApiError)) : setPick(id)} />
          <button className="btn ghost small" onClick={() => go("chats")}>All chats</button>
          {chat && <button className="btn ghost small" onClick={() => go("builder")}>New conversation</button>}
        </div>
        {/* The conversation (or the empty state), and "Starting…" while the first message is sent */}
        <div className="card-body chat-log">
          {chat ? <ChatLog chat={chat} ctl={ctl} onAnswer={send} empty={empty} /> : empty}
          {sending && !chat && <div className="bubble assistant"><span className="spinner" /> Starting…</div>}
        </div>
        {/* Approval cards, then the input (or a read-only note), then errors */}
        <PendingApproval chat={chat} ctl={ctl} />
        {readonly ? <div className="card-body muted small">This is {chat?.owner}'s conversation (read-only).</div> : (
          <form className="chat-input" onSubmit={(e) => { e.preventDefault(); send(input); }}>
            {/* Enter sends, Shift+Enter makes a new line */}
            <textarea name="message" rows={3} value={input} onChange={(e) => setInput(e.target.value)} disabled={sending || ctl.busy}
              placeholder={ctl.busy ? "The assistant is working…" : spec ? "Ask for a change, e.g. add a second web server behind the same policy" : "Describe the lab you need"}
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(input); } }} />
            <button className="btn primary" disabled={sending || ctl.busy || !input.trim()}>Send</button>
          </form>
        )}
        <ErrorBox error={err ?? ctl.error} />
      </section>
      {/* The spec panel: Map/YAML toggle, Open in editor, Save as range, then the map or the YAML */}
      <section className="card builder-preview">
        <div className="card-head">
          <h2>Spec</h2>
          {spec && (
            <div className="row" style={{ gap: 8 }}>
              <button className="btn small" onClick={() => setShowYaml(!showYaml)}>{showYaml ? "Map" : "YAML"}</button>
              <button className="btn small" onClick={openInEditor}>Open in editor</button>
              {!readonly && <button className="btn small primary" onClick={saveRange}>Save as range</button>}
            </div>
          )}
        </div>
        {!spec ? <div className="empty">The spec appears here once it passes VALOR's checks.</div>
          : showYaml || !chat?.topology ? <pre className="mono" style={{ margin: 12, maxHeight: 560, overflow: "auto" }}>{spec}</pre>
          : <TopologyMap topology={chat.topology} tall />}
      </section>
    </div>
  );
}
