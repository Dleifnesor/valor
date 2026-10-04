import { useEffect, useRef, useState } from "react";
import { ApiError, post, put } from "../api";
import { go, useApi } from "../hooks";
import { Topology } from "../types";
import { ErrorBox, Loading } from "../components/ui";
import { TopologyMap } from "../components/Topology";

// Chat builder: describe an environment, the AI writes the range spec, VALOR validates it and shows it on the map.
// Nothing is built from here: the spec goes to the editor or is saved as a range, then planned and approved.

interface Msg { role: "user" | "assistant"; content: string; problems?: string[] }
interface ChatResponse {
  reply: string;
  yaml: string | null;
  ok: boolean;
  problems: string[];
  attempts: number;
  topology: Topology | null;
  usage: { input_tokens: number; output_tokens: number };
}

const KEY = "valor-builder";
const EXAMPLES = [
  "A small web lab: an nginx server in a DMZ with internet access and a Linux client on a separate LAN that may only browse it.",
  "An Active Directory lab: two domain controllers on Windows Server 2022 Core, an IIS server joined to the domain and a Windows 11 client.",
  "A pentest range: a Kali attacker, a vulnerable web server and a database the web server can reach, no internet for the targets.",
];

function load(): { messages: Msg[]; yaml: string; topology: Topology | null } {
  try {
    const v = JSON.parse(sessionStorage.getItem(KEY) || "null");
    if (v && Array.isArray(v.messages)) return v;
  } catch {
    /* nothing saved */
  }
  return { messages: [], yaml: "", topology: null };
}

export function Builder({ isAdmin }: { isAdmin: boolean }) {
  const info = useApi<{ configured: boolean; provider: string; model: string }>("/api/builder");
  const [state, setState] = useState(load);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<ApiError | null>(null);
  const [showYaml, setShowYaml] = useState(false);
  const end = useRef<HTMLDivElement>(null);

  useEffect(() => {
    try {
      sessionStorage.setItem(KEY, JSON.stringify(state));
    } catch {
      /* not persisted */
    }
    end.current?.scrollIntoView({ block: "end" });
  }, [state]);

  if (info.loading && !info.data) return <Loading />;
  if (!info.data?.configured) {
    return (
      <section className="card"><div className="card-body">
        <h2 style={{ marginTop: 0 }}>The chat builder is off</h2>
        <p className="muted">
          It needs an AI provider (Anthropic's Claude, or an OpenAI-compatible endpoint such as a model server on your
          network). {isAdmin ? <>Connect one in <button className="link" onClick={() => go("settings")}>Settings → AI provider</button>.</>
            : "Ask an administrator to connect one in Settings."}
        </p>
        <p className="muted">You can always write a spec yourself: <button className="link" onClick={() => go("ranges/new")}>New range</button>.</p>
      </div></section>
    );
  }

  const send = async (text: string) => {
    if (!text.trim() || busy) return;
    const messages: Msg[] = [...state.messages, { role: "user", content: text.trim() }];
    setState({ ...state, messages });
    setInput("");
    setBusy(true);
    setErr(null);
    try {
      const r = await post<ChatResponse>("/api/builder/chat", {
        messages: messages.map(({ role, content }) => ({ role, content })),
        spec: state.yaml,
      });
      const reply: Msg = { role: "assistant", content: r.reply || "(no explanation)", problems: r.ok ? undefined : r.problems };
      setState({
        messages: [...messages, reply],
        yaml: r.yaml && r.ok ? r.yaml : state.yaml,
        topology: r.topology ?? state.topology,
      });
    } catch (e) {
      setErr(e as ApiError);
      setState({ ...state, messages });
    } finally {
      setBusy(false);
    }
  };
  const openInEditor = () => {
    try {
      sessionStorage.setItem("valor-editor-draft", state.yaml);
    } catch {
      /* the editor starts from its example */
    }
    go("ranges/new");
  };
  const saveRange = async () => {
    const name = /^name:\s*([a-z0-9-]+)/m.exec(state.yaml)?.[1];
    if (!name) return;
    setErr(null);
    try {
      await put(`/api/ranges/${name}/spec`, { yaml: state.yaml });
      go(`ranges/${name}`);
    } catch (e) {
      setErr(e as ApiError);
    }
  };
  const reset = () => setState({ messages: [], yaml: "", topology: null });

  return (
    <div className="builder">
      <section className="card builder-chat">
        <div className="card-head">
          <h2>Describe the environment</h2>
          <span className="muted small">{info.data.model}</span>
          {state.messages.length > 0 && <button className="btn ghost small" onClick={reset}>New conversation</button>}
        </div>
        <div className="card-body chat-log">
          {state.messages.length === 0 && (
            <div className="stack">
              <div className="muted">Say what you need; the builder writes a spec, VALOR checks it against this cluster.</div>
              {EXAMPLES.map((x) => <button key={x} className="btn example" onClick={() => send(x)}>{x}</button>)}
            </div>
          )}
          {state.messages.map((m, i) => (
            <div key={i} className={`bubble ${m.role}`}>
              <div style={{ whiteSpace: "pre-wrap" }}>{m.content}</div>
              {m.problems && (
                <div className="alert warn small" style={{ marginTop: 8 }}>
                  The spec still has problems: {m.problems.join("; ")}
                </div>
              )}
            </div>
          ))}
          {busy && <div className="bubble assistant"><span className="spinner" /> Designing and checking the spec…</div>}
          <div ref={end} />
        </div>
        <form className="chat-input" onSubmit={(e) => { e.preventDefault(); send(input); }}>
          <textarea rows={3} value={input} onChange={(e) => setInput(e.target.value)} disabled={busy}
            placeholder={state.yaml ? "Ask for a change, e.g. add a second web server behind the same policy" : "Describe the lab you need"}
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(input); } }} />
          <button className="btn primary" disabled={busy || !input.trim()}>Send</button>
        </form>
        <ErrorBox error={err} />
      </section>
      <section className="card builder-preview">
        <div className="card-head">
          <h2>Spec</h2>
          {state.yaml && (
            <div className="row" style={{ gap: 8 }}>
              <button className="btn small" onClick={() => setShowYaml(!showYaml)}>{showYaml ? "Map" : "YAML"}</button>
              <button className="btn small" onClick={openInEditor}>Open in editor</button>
              <button className="btn small primary" onClick={saveRange}>Save as range</button>
            </div>
          )}
        </div>
        {!state.yaml ? <div className="empty">The spec appears here once it passes VALOR's checks.</div>
          : showYaml || !state.topology ? <pre className="mono" style={{ margin: 12, maxHeight: 560, overflow: "auto" }}>{state.yaml}</pre>
          : <TopologyMap topology={state.topology} tall />}
      </section>
    </div>
  );
}
