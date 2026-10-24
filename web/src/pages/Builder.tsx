import { useState } from "react";
import { ApiError, post, put } from "../api";
import { go, useApi } from "../hooks";
import { ErrorBox, Loading } from "../components/ui";
import { TopologyMap } from "../components/Topology";
import { BuilderInfo, Chat, ChatLog, ProviderPick, useChat } from "../components/ChatFeed";

// Chat builder: describe an environment; the AI asks what it needs to know (clickable question cards), writes the
// range spec, VALOR validates it and shows it on the map. Conversations are saved (Datacenter → Chats) and can be continued.
// Nothing is built from here: the spec goes to the editor or is saved as a range, then planned and approved.

const EXAMPLES = [
  "A small web lab: an nginx server in a DMZ with internet access and a Linux client on a separate LAN that may only browse it.",
  "An Active Directory lab: two domain controllers on Windows Server 2022 Core, an IIS server joined to the domain and a Windows 11 client.",
  "A CUI enclave built toward NIST SP 800-171: file server and database without internet, central logging, an admin workstation.",
  "A pentest range: a Kali attacker, a vulnerable web server and a database the web server can reach, no internet for the targets.",
];

export function Builder({ isAdmin, chatId }: { isAdmin: boolean; chatId: number | null }) {
  const info = useApi<BuilderInfo>("/api/builder");
  const ctl = useChat(chatId);
  const { chat } = ctl;
  const [input, setInput] = useState("");
  const [err, setErr] = useState<ApiError | null>(null);
  const [showYaml, setShowYaml] = useState(false);
  const [sending, setSending] = useState(false);
  const [pick, setPick] = useState("");                  // the provider for a new conversation ("" = the default)

  if (info.loading && !info.data) return <Loading />;
  if (!info.data?.configured) {
    return (
      <section className="card"><div className="card-body">
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
  if (chatId && !chat) return ctl.error ? <ErrorBox error={ctl.error} /> : <Loading />;

  const send = async (text: string) => {
    if (!text.trim() || sending || ctl.busy) return;
    setSending(true); setErr(null);
    try {
      if (!chat) {
        const c = await post<Chat>("/api/chats", { kind: "builder", ...(pick ? { provider: pick } : {}) });
        await post(`/api/chats/${c.id}/messages`, { message: text.trim(), mode: "build" });
        go(`builder/${c.id}`);
      } else {
        await ctl.send(text.trim(), "build");
      }
      setInput("");
    } catch (e) { setErr(e as ApiError); }
    finally { setSending(false); }
  };
  const spec = chat?.spec ?? "";
  const openInEditor = () => {
    try { sessionStorage.setItem("valor-editor-draft", spec); } catch { /* the editor starts from its example */ }
    go("ranges/new");
  };
  const saveRange = async () => {
    const name = /^name:\s*([a-z0-9-]+)/m.exec(spec)?.[1];
    if (!name) return;
    setErr(null);
    try { await put(`/api/ranges/${name}/spec`, { yaml: spec }); go(`ranges/${name}`); }
    catch (e) { setErr(e as ApiError); }
  };
  const readonly = !!chat?.readonly;
  const empty = (
    <div className="stack">
      <div className="muted">Say what you need. The builder asks what it needs to know, writes a spec, and VALOR checks it against this cluster.</div>
      {EXAMPLES.map((x) => <button key={x} className="btn example" disabled={sending} onClick={() => send(x)}>{x}</button>)}
    </div>
  );

  return (
    <div className="builder">
      <section className="card builder-chat">
        <div className="card-head">
          <h2 className="grow" title={chat?.title}>{chat?.title || "Describe the environment"}</h2>
          <ProviderPick info={info.data} value={chat ? chat.provider : pick} disabled={readonly || sending || ctl.busy}
            onChange={(id) => chat ? ctl.setProvider(id).catch((e) => setErr(e as ApiError)) : setPick(id)} />
          <button className="btn ghost small" onClick={() => go("chats")}>All chats</button>
          {chat && <button className="btn ghost small" onClick={() => go("builder")}>New conversation</button>}
        </div>
        <div className="card-body chat-log">
          {chat ? <ChatLog chat={chat} ctl={ctl} onAnswer={send} empty={empty} /> : empty}
          {sending && !chat && <div className="bubble assistant"><span className="spinner" /> Starting…</div>}
        </div>
        {readonly ? <div className="card-body muted small">This is {chat?.owner}'s conversation (read-only).</div> : (
          <form className="chat-input" onSubmit={(e) => { e.preventDefault(); send(input); }}>
            <textarea name="message" rows={3} value={input} onChange={(e) => setInput(e.target.value)} disabled={sending || ctl.busy}
              placeholder={ctl.busy ? "The assistant is working…" : spec ? "Ask for a change, e.g. add a second web server behind the same policy" : "Describe the lab you need"}
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(input); } }} />
            <button className="btn primary" disabled={sending || ctl.busy || !input.trim()}>Send</button>
          </form>
        )}
        <ErrorBox error={err ?? ctl.error} />
      </section>
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
