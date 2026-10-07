import { ReactNode, useCallback, useEffect, useRef, useState } from "react";
import { ApiError, get, patch, post } from "../api";
import { Topology } from "../types";

// Saved chats (chat builder and range chats). A turn runs on the server; while it runs the assistant message is
// polled and its steps form a live feed: reasoning, text, tool calls with their output, VALOR's checks. Finished
// steps fold into one line each (a ▸ opens them again). Actions that change something wait on an approval card.

export type Mode = "build" | "question" | "plan" | "code";
export interface Question { id: number; header: string; question: string; options: string[]; multiple: boolean; recommended: string | null }
export interface Step {
  id: string; type: "thinking" | "text" | "tool" | "check"; text?: string; open?: boolean; ts?: number; ended?: number;
  name?: string; summary?: string; purpose?: string; input?: Record<string, any>; readonly?: boolean; approval?: boolean;
  status?: "queued" | "pending" | "approved" | "denied" | "running" | "done" | "failed" | "refused" | "cancelled";
  output?: string; exit?: number | null; seconds?: number; diff?: string; decided_by?: string;
}
export interface PlanInfo { summary: Record<string, number>; actions: { host: string; action: string; reasons: string[] }[] }
export interface Meta {
  steps?: Step[]; preparing?: string; questions?: Question[]; problems?: string[]; fixed?: string[]; warnings?: string[];
  draft?: Record<string, number> | null; draft_changed?: boolean; plan?: PlanInfo; diff?: string; yaml?: string;
  usage?: { input_tokens: number; output_tokens: number }; error?: { code: string; message: string; hint?: string };
}
export interface Message {
  id: number; chat_id: number; ts: number; role: "user" | "assistant"; mode: Mode; content: string;
  status: "running" | "waiting" | "done" | "failed" | "stopped"; version: number; meta: Meta;
}
export interface Chat {
  id: number; owner: string; kind: "builder" | "range"; range: string | null; title: string; created: number;
  updated: number; allow_all: boolean; readonly?: boolean; spec?: string; topology?: Topology | null; messages: Message[];
}

const LIVE = ["running"];

/** A chat with its messages; running answers are polled until they finish. onSettled runs when one finishes. */
export function useChat(chatId: number | null, onSettled?: (m: Message) => void) {
  const [chat, setChat] = useState<Chat | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const settled = useRef(onSettled);
  settled.current = onSettled;

  const load = useCallback(async () => {
    if (!chatId) { setChat(null); return; }
    try { setChat(await get<Chat>(`/api/chats/${chatId}`)); setError(null); } catch (e) { setError(e as ApiError); }
  }, [chatId]);
  useEffect(() => { setChat(null); load(); }, [load]);

  const running = chat?.messages.filter((m) => LIVE.includes(m.status)) ?? [];
  const key = running.map((m) => `${m.id}:${m.version}`).join(",");
  useEffect(() => {
    if (!chat || !running.length) return;
    let stop = false;
    const t = window.setTimeout(async () => {
      for (const m of running) {
        try {
          const r = await get<Message & { unchanged?: boolean }>(`/api/chats/${chat.id}/messages/${m.id}?v=${m.version}`);
          if (stop || r.unchanged) continue;
          setChat((c) => c && { ...c, messages: c.messages.map((x) => (x.id === r.id ? r : x)) });
          if (!LIVE.includes(r.status)) {
            settled.current?.(r);
            if (r.status === "done") load();          // the chat's spec / title may have changed
          }
        } catch (e) { if (!stop) setError(e as ApiError); }
      }
    }, document.visibilityState === "visible" ? 600 : 3000);
    return () => { stop = true; window.clearTimeout(t); };
  }, [chat?.id, key]);

  const send = async (message: string, mode: Mode) => {
    if (!chat) return;
    const r = await post<{ messages: Message[] }>(`/api/chats/${chat.id}/messages`, { message, mode });
    setChat((c) => c && { ...c, title: c.title || message.slice(0, 80), messages: [...c.messages, ...r.messages] });
  };
  const decide = async (m: Message, decisions: Record<string, "approve" | "deny">, allowAll = false) => {
    const r = await post<Message>(`/api/chats/${m.chat_id}/messages/${m.id}/decide`, { decisions, allow_all: allowAll });
    setChat((c) => c && { ...c, allow_all: c.allow_all || allowAll, messages: c.messages.map((x) => (x.id === r.id ? r : x)) });
  };
  const stop = async (m: Message) => {
    await post(`/api/chats/${m.chat_id}/messages/${m.id}/stop`);
    window.setTimeout(load, 400);
  };
  const setAllowAll = async (on: boolean) => {
    if (!chat) return;
    const r = await patch<Chat>(`/api/chats/${chat.id}`, { allow_all: on });
    setChat((c) => c && { ...c, allow_all: r.allow_all });
  };
  const busy = !!chat?.messages.some((m) => m.status === "running" || m.status === "waiting");
  return { chat, setChat, error, setError, load, send, decide, stop, setAllowAll, busy };
}

/** A fold that is open while live and closes once when it stops being live (the person can open it again). */
function Fold({ live, summary, className, children }: { live: boolean; summary: ReactNode; className?: string; children: ReactNode }) {
  const [open, setOpen] = useState(live);
  const was = useRef(live);
  useEffect(() => { if (was.current && !live) setOpen(false); if (!was.current && live) setOpen(true); was.current = live; }, [live]);
  return (
    <details className={className} open={open} onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)}>
      <summary>{summary}</summary>
      {children}
    </details>
  );
}

const STATUS: Record<string, [string, string]> = {
  queued: ["…", "waits"], pending: ["⏸", "waits for you"], approved: ["…", "approved"], running: ["", "running"],
  done: ["✓", ""], failed: ["✗", "failed"], denied: ["⊘", "denied"], refused: ["⊘", "not allowed in this mode"],
  cancelled: ["⊘", "cancelled"],
};

function secs(a?: number, b?: number) {
  if (!a) return "";
  const s = Math.max(1, Math.round(((b ?? Date.now() / 1000) - a)));
  return s < 90 ? `${s} s` : `${Math.round(s / 60)} min`;
}

function Diff({ text }: { text: string }) {
  return (
    <pre className="diff mono">{text.split("\n").map((l, i) => (
      <span key={i} className={l.startsWith("+") && !l.startsWith("+++") ? "add" : l.startsWith("-") && !l.startsWith("---") ? "del" : l.startsWith("@@") ? "hunk" : ""}>{l}{"\n"}</span>
    ))}</pre>
  );
}

function ToolStep({ s, live }: { s: Step; live: boolean }) {
  const [icon, label] = STATUS[s.status ?? "done"] ?? ["", s.status];
  const input = s.input ?? {};
  const body = (
    <div className="step-body">
      {s.purpose && <div className="muted small">{s.purpose}</div>}
      {s.name === "run_command" && <pre className="mono step-code">{String(input.command ?? "")}</pre>}
      {s.name === "write_file" && <pre className="mono step-code">{String(input.content ?? "")}</pre>}
      {s.diff && <Diff text={s.diff} />}
      {s.output && <pre className="mono step-output">{s.output}</pre>}
    </div>
  );
  return (
    <Fold live={live && (s.status === "running" || s.status === "pending")} className={`step tool ${s.status}`}
      summary={<>
        <span className={`step-icon ${s.status}`}>{s.status === "running" ? <span className="spinner" /> : icon}</span>
        <span className="step-name">{s.name?.replace(/_/g, " ")}</span>
        <span className="step-summary mono">{s.summary}</span>
        <span className="muted small step-meta">
          {label}{s.exit != null && s.exit !== 0 ? ` (exit ${s.exit})` : ""}{s.seconds ? ` · ${s.seconds} s` : ""}
          {s.readonly ? " · read-only" : ""}
        </span>
      </>}>
      {body}
    </Fold>
  );
}

function ThinkingStep({ s, live }: { s: Step; live: boolean }) {
  const streaming = live && !!s.open;
  return (
    <Fold live={streaming} className="step thinking"
      summary={<span className="muted">{streaming ? <><span className="spinner" /> Thinking…</> : `Thought for ${secs(s.ts, s.ended)}`}</span>}>
      <div className="thinking-text">{s.text}</div>
    </Fold>
  );
}

/** Approve / deny the actions an answer waits on. */
function Approvals({ m, steps, decide, readonly }: { m: Message; steps: Step[]; decide: Decide; readonly?: boolean }) {
  const pending = steps.filter((s) => s.type === "tool" && s.status === "pending");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<ApiError | null>(null);
  if (!pending.length) return null;
  const go = async (d: "approve" | "deny", allowAll = false) => {
    setBusy(true); setErr(null);
    try { await decide(m, Object.fromEntries(pending.map((s) => [s.id, d])), allowAll); }
    catch (e) { setErr(e as ApiError); }
    finally { setBusy(false); }
  };
  return (
    <div className="approval card">
      <b>{pending.length === 1 ? "The assistant wants to:" : `The assistant wants to do ${pending.length} things:`}</b>
      <ul>{pending.map((s) => <li key={s.id}><span className="mono">{s.summary}</span>{s.purpose ? <span className="muted"> - {s.purpose}</span> : null}</li>)}</ul>
      {readonly ? <div className="muted small">Waiting for the chat's owner.</div> : (
        <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
          <button className="btn small primary" disabled={busy} onClick={() => go("approve")}>Approve</button>
          <button className="btn small" disabled={busy} onClick={() => go("deny")}>Deny</button>
          <button className="btn small ghost" disabled={busy} title="Approve this and let the assistant run later actions in this chat without asking"
            onClick={() => window.confirm("Approve, and let the assistant run every later action in this chat without asking?") && go("approve", true)}>
            Approve and allow all in this chat
          </button>
        </div>
      )}
      {err && <div className="alert error small">{err.message}</div>}
    </div>
  );
}

type Decide = (m: Message, d: Record<string, "approve" | "deny">, allowAll?: boolean) => Promise<void>;

/** Clickable answers to the model's questions; the answers go back as one message. */
export function QuestionCards({ questions, onAnswer, disabled }: { questions: Question[]; onAnswer: (text: string) => void; disabled?: boolean }) {
  const [picked, setPicked] = useState<Record<number, string[]>>(() =>
    Object.fromEntries(questions.map((q) => [q.id, q.recommended ? [q.recommended] : []])));
  const [other, setOther] = useState<Record<number, string>>({});
  const toggle = (q: Question, o: string) => setPicked((p) => {
    const cur = p[q.id] ?? [];
    return { ...p, [q.id]: q.multiple ? (cur.includes(o) ? cur.filter((x) => x !== o) : [...cur, o]) : [o] };
  });
  const answer = () => onAnswer("Answers:\n" + questions.map((q) => {
    const parts = [...(picked[q.id] ?? []), ...(other[q.id]?.trim() ? [other[q.id].trim()] : [])];
    return `${q.id}. ${q.question} -> ${parts.length ? parts.join(", ") : "(no preference - use your default)"}`;
  }).join("\n"));
  return (
    <div className="questions">
      {questions.map((q) => (
        <fieldset key={q.id} className="question" disabled={disabled}>
          <legend>{q.header && <span className="badge">{q.header}</span>} {q.question}</legend>
          <div className="options">
            {q.options.map((o) => (
              <label key={o} className={(picked[q.id] ?? []).includes(o) ? "option on" : "option"}>
                <input type={q.multiple ? "checkbox" : "radio"} name={`q${q.id}`} checked={(picked[q.id] ?? []).includes(o)}
                  onChange={() => toggle(q, o)} />
                {o}{q.recommended === o && <span className="muted small"> (suggested)</span>}
              </label>
            ))}
            <input className="other" placeholder={q.options.length ? "Other…" : "Your answer"} value={other[q.id] ?? ""}
              onChange={(e) => setOther({ ...other, [q.id]: e.target.value })} />
          </div>
        </fieldset>
      ))}
      <div className="row" style={{ gap: 8 }}>
        <button className="btn small primary" disabled={disabled} onClick={answer}>Send answers</button>
        <button className="btn small" disabled={disabled} onClick={() => onAnswer("Use your defaults for these questions and go ahead.")}>Skip - use your defaults</button>
      </div>
    </div>
  );
}

/** What VALOR's checks did behind the scenes: problems the model fixed, and warnings about the final spec. */
export function CheckNotes({ fixed, warnings }: { fixed?: string[]; warnings?: string[] }) {
  return (
    <>
      {!!warnings?.length && warnings.map((w, i) => <div key={i} className="alert warn small" style={{ marginTop: 8 }}>{w}</div>)}
      {!!fixed?.length && (
        <details className="check-notes">
          <summary>VALOR checked the spec: the model fixed {fixed.length} problem{fixed.length === 1 ? "" : "s"} before answering</summary>
          <ul>{fixed.map((f, i) => <li key={i}>{f}</li>)}</ul>
        </details>
      )}
    </>
  );
}

export function AssistantMessage({ m, last, readonly, decide, stop, onAnswer, onReview, busy }: {
  m: Message; last: boolean; readonly?: boolean; decide: Decide; stop: (m: Message) => void;
  onAnswer: (text: string) => void; onReview?: () => void; busy: boolean;
}) {
  const live = m.status === "running" || m.status === "waiting";
  const steps = m.meta.steps ?? [];
  const meta = m.meta;
  return (
    <div className={`bubble assistant${live ? " live" : ""}`}>
      {steps.length > 0 && (
        <div className="feed">
          {steps.map((s) => s.type === "thinking" ? <ThinkingStep key={s.id} s={s} live={live} />
            : s.type === "tool" ? <ToolStep key={s.id} s={s} live={live} />
            : s.type === "check" ? <div key={s.id} className="step check muted small">{s.text}</div>
            : <div key={s.id} className="step text">{s.text}</div>)}
        </div>
      )}
      {m.status === "running" && (
        <div className="row muted small" style={{ gap: 8, marginTop: 6 }}>
          <span className="spinner" /> {meta.preparing ? `Preparing ${meta.preparing.replace(/_/g, " ")}…` : steps.length ? "Working…" : "Thinking…"}
          {!readonly && <button className="btn ghost small" onClick={() => stop(m)}>Stop</button>}
        </div>
      )}
      {m.status === "waiting" && <Approvals m={m} steps={steps} decide={decide} readonly={readonly} />}
      {m.status === "waiting" && !readonly && <button className="btn ghost small" onClick={() => stop(m)}>Stop this answer</button>}
      {m.content && <div className="answer">{m.content}</div>}
      {meta.error && (
        <div className="alert error small" style={{ marginTop: 8 }}>
          {meta.error.message}{meta.error.hint && <div className="muted">{meta.error.hint}</div>}
        </div>
      )}
      {!!meta.problems?.length && <div className="alert warn small" style={{ marginTop: 8 }}>The spec still has problems: {meta.problems.join("; ")}</div>}
      <CheckNotes fixed={meta.fixed} warnings={meta.warnings} />
      {meta.draft && (
        <div className="row small" style={{ marginTop: 8, gap: 6, flexWrap: "wrap" }}>
          Draft: {Object.entries(meta.draft).map(([k, v]) => <span key={k} className={`badge c-${k}`}>{v} {k}</span>)}
        </div>
      )}
      {meta.plan && (
        <div className="small" style={{ marginTop: 8 }}>
          <b>VALOR's plan</b>
          <ul className="plan-list">{meta.plan.actions.map((a) => <li key={a.host}><span className={`badge c-${a.action === "converge" ? "update" : a.action}`}>{a.action}</span> {a.host}{a.reasons.length ? ` - ${a.reasons.join("; ")}` : ""}</li>)}</ul>
          {onReview && !readonly && <button className="btn small primary" onClick={onReview}>Review and approve…</button>}
        </div>
      )}
      {meta.diff && <Diff text={meta.diff} />}
      {meta.questions && (last && !readonly && m.status === "done"
        ? <QuestionCards questions={meta.questions} onAnswer={onAnswer} disabled={busy} />
        : <ol className="asked small muted">{meta.questions.map((q) => <li key={q.id}>{q.question}</li>)}</ol>)}
      {meta.usage && !live && <div className="usage muted">{(meta.usage.input_tokens + meta.usage.output_tokens).toLocaleString()} tokens</div>}
    </div>
  );
}

/** The conversation: user bubbles and live assistant answers. */
export function ChatLog({ chat, ctl, onAnswer, onReview, empty }: {
  chat: Chat; ctl: ReturnType<typeof useChat>; onAnswer: (text: string) => void; onReview?: () => void; empty?: ReactNode;
}) {
  const end = useRef<HTMLDivElement>(null);
  const lastKey = chat.messages.length ? `${chat.messages.length}:${chat.messages[chat.messages.length - 1].version}` : "";
  useEffect(() => { end.current?.scrollIntoView({ block: "end" }); }, [lastKey]);
  return (
    <>
      {chat.messages.length === 0 && empty}
      {chat.messages.map((m, i) => m.role === "user"
        ? <div key={m.id} className="bubble user">{m.mode !== "build" && <span className="mode-tag">{m.mode}</span>}{m.content}</div>
        : <AssistantMessage key={m.id} m={m} last={i === chat.messages.length - 1} readonly={chat.readonly}
            decide={ctl.decide} stop={ctl.stop} onAnswer={onAnswer} onReview={onReview} busy={ctl.busy} />)}
      <div ref={end} />
    </>
  );
}
