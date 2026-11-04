// *watches the AI think in real time* OwO web/src/components/ChatFeed.tsx: the CHAT itself, with its live feed! ^w^
// A turn runs on the server; while it runs, the browser polls the answer and its steps form a LIVE FEED: thinking,
// text, tool calls with output, VALOR's checks. Finished steps fold into one line each (▸ opens them again).
// Actions that change something wait on an approval card. The file's order is explained just below *wags tail*
import { ReactNode, useCallback, useEffect, useRef, useState } from "react";
// API calls, the map type, and the question cards UwU
import { ApiError, get, patch, post } from "../api";
import { Topology } from "../types";
import { QuestionCards } from "./ChatQuestions";

// Saved chats (chat builder and range chats). A turn runs on the server; while it runs the assistant message is
// polled and its steps form a live feed: reasoning, text, tool calls with their output, VALOR's checks. Finished
// steps fold into one line each (a ▸ opens them again). Actions that change something wait on an approval card.
// The file goes top to bottom: the API's types, useChat (loading and polling), the feed's steps, the approval
// card, then the message bubbles and the scrolling log that the builder and range chat pages use.

// Mode: the chat modes. Question: one question card. Step: one feed step (thinking, text, a tool call, or a VALOR
// check) with everything the UI shows about it >w<
export type Mode = "build" | "question" | "plan" | "code";
export interface Question { id: number; header: string; question: string; options: string[]; multiple: boolean; recommended: string | null }
export interface Step {
  id: string; type: "thinking" | "text" | "tool" | "check"; text?: string; open?: boolean; ts?: number; ended?: number;
  name?: string; summary?: string; purpose?: string; input?: Record<string, any>; readonly?: boolean; approval?: boolean;
  status?: "queued" | "pending" | "approved" | "denied" | "running" | "done" | "failed" | "refused" | "cancelled";
  output?: string; exit?: number | null; seconds?: number; diff?: string; decided_by?: string;
}
// PlanInfo: VALOR's plan for a draft. Meta: everything an answer carries besides its text (steps, questions,
// problems, fixes, warnings, draft, plan, diff, the spec, token usage, an error, and what a fix chat did next) OwO
export interface PlanInfo { summary: Record<string, number>; actions: { host: string; action: string; reasons: string[] }[] }
export interface Meta {
  steps?: Step[]; preparing?: string; questions?: Question[]; problems?: string[]; fixed?: string[]; warnings?: string[];
  draft?: Record<string, number> | null; draft_changed?: boolean; plan?: PlanInfo; diff?: string; yaml?: string;
  usage?: { input_tokens: number; output_tokens: number }; error?: { code: string; message: string; hint?: string };
  rebuild?: { job?: string; note: string };   // a fix chat in mode "fix and rebuild": what VALOR did next
}
// Message: one chat message (the assistant's has a status and a version that bumps on every change) ^w^
export interface Message {
  id: number; chat_id: number; ts: number; role: "user" | "assistant"; mode: Mode; content: string;
  status: "running" | "waiting" | "done" | "failed" | "stopped"; version: number; meta: Meta;
}
// Chat: one conversation with its messages (builder chats also carry their spec and map) UwU
export interface Chat {
  id: number; owner: string; kind: "builder" | "range"; range: string | null; title: string; created: number;
  updated: number; allow_all: boolean; provider?: string | null; readonly?: boolean; spec?: string;
  topology?: Topology | null; messages: Message[];
}
/** The AI providers chats may use (GET /api/builder): no secrets. */
// ProviderInfo / BuilderInfo: what GET /api/builder says about AI providers (no secrets) >w<
export interface ProviderInfo { id: string; name: string; provider: string; model: string }
export interface BuilderInfo { configured: boolean; default: string; providers: ProviderInfo[] }

/** Which provider a chat uses: a picker when there are several, else the model's name. "" = the default. */
// ProviderPick: a provider dropdown when there are several, otherwise just the model's name ("" = the default) OwO
export function ProviderPick({ info, value, onChange, disabled }: {
  info: BuilderInfo; value: string | null | undefined; onChange: (id: string) => void; disabled?: boolean;
}) {
  const current = info.providers.find((provider) => provider.id === value) ?? info.providers.find((provider) => provider.id === info.default);
  if (info.providers.length < 2) return <span className="muted small">{current?.model}</span>;
  return (
    <select name="provider" className="provider-pick" value={current?.id ?? ""} disabled={disabled} aria-label="AI provider"
      title={current ? `${current.name}: ${current.model}` : ""}
      onChange={(e) => onChange(e.target.value === info.default ? "" : e.target.value)}>
      {/* One option per provider, the default marked */}
      {info.providers.map((provider) => (
        <option key={provider.id} value={provider.id}>{provider.name}{provider.id === info.default ? " (default)" : ""} - {provider.model}</option>
      ))}
    </select>
  );
}

// an answer is polled while it runs; "waiting" (on an approval) only changes when the person decides
// LIVE: statuses that are polled ^w^
const LIVE = ["running"];

/** A chat with its messages; running answers are polled until they finish. onSettled runs when one finishes. */
// useChat: load a chat and poll its running answers until they finish; returns the chat and its actions UwU
export function useChat(chatId: number | null, onSettled?: (message: Message) => void) {
  const [chat, setChat] = useState<Chat | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  // onSettled kept in a ref, so the poll always calls the newest one >w<
  const settled = useRef(onSettled);
  settled.current = onSettled;

  // load(): fetch the whole chat OwO
  const load = useCallback(async () => {
    if (!chatId) { setChat(null); return; }
    try { setChat(await get<Chat>(`/api/chats/${chatId}`)); setError(null); } catch (e) { setError(e as ApiError); }
  }, [chatId]);
  useEffect(() => { setChat(null); load(); }, [load]);

  // The running answers, and a key that changes whenever one of them changes ^w^
  const running = chat?.messages.filter((message) => LIVE.includes(message.status)) ?? [];
  const key = running.map((message) => `${message.id}:${message.version}`).join(",");
  // every poll re-arms the next one: an "unchanged" answer (a local model still reading the prompt) changes no key
  const [tick, setTick] = useState(0);
  // Poll every 0.6 s (3 s when the tab is hidden): ask for each running answer with its version; "unchanged" answers
  // cost a few bytes. A finished answer calls onSettled and (when done) reloads the chat (its title/spec may change).
  // `tick` re-arms the next poll even when nothing changed UwU
  useEffect(() => {
    if (!chat || !running.length) return;
    let stop = false;
    const timer = window.setTimeout(async () => {
      for (const message of running) {
        try {
          const fresh = await get<Message & { unchanged?: boolean }>(`/api/chats/${chat.id}/messages/${message.id}?v=${message.version}`);
          if (stop || fresh.unchanged) continue;
          setChat((old) => old && { ...old, messages: old.messages.map((existing) => (existing.id === fresh.id ? fresh : existing)) });
          if (!LIVE.includes(fresh.status)) {
            settled.current?.(fresh);
            if (fresh.status === "done") load();          // the chat's spec / title may have changed
          }
        } catch (e) { if (!stop) setError(e as ApiError); }
      }
      if (!stop) setTick((count) => count + 1);
    }, document.visibilityState === "visible" ? 600 : 3000);
    return () => { stop = true; window.clearTimeout(timer); };
  }, [chat?.id, key, tick]);

  // send(): post a message; the reply contains the question and the (running) answer >w<
  const send = async (message: string, mode: Mode) => {
    if (!chat) return;
    const added = await post<{ messages: Message[] }>(`/api/chats/${chat.id}/messages`, { message, mode });
    setChat((old) => old && { ...old, title: old.title || message.slice(0, 80), messages: [...old.messages, ...added.messages] });
  };
  // decide(): approve/deny pending tool calls (and maybe allow all from now on) OwO
  const decide = async (message: Message, decisions: Record<string, "approve" | "deny">, allowAll = false) => {
    const fresh = await post<Message>(`/api/chats/${message.chat_id}/messages/${message.id}/decide`, { decisions, allow_all: allowAll });
    setChat((old) => old && { ...old, allow_all: old.allow_all || allowAll, messages: old.messages.map((existing) => (existing.id === fresh.id ? fresh : existing)) });
  };
  // stop(): stop an answer and reload shortly after ^w^
  const stop = async (message: Message) => {
    await post(`/api/chats/${message.chat_id}/messages/${message.id}/stop`);
    window.setTimeout(load, 400);
  };
  // setAllowAll() / setProvider(): change the chat's settings UwU
  const setAllowAll = async (on: boolean) => {
    if (!chat) return;
    const saved = await patch<Chat>(`/api/chats/${chat.id}`, { allow_all: on });
    setChat((old) => old && { ...old, allow_all: saved.allow_all });
  };
  const setProvider = async (id: string) => {
    if (!chat) return;
    const saved = await patch<Chat>(`/api/chats/${chat.id}`, { provider: id });
    setChat((old) => old && { ...old, provider: saved.provider });
  };
  // busy = an answer is running or waiting for approval >w<
  const busy = !!chat?.messages.some((message) => message.status === "running" || message.status === "waiting");
  return { chat, setChat, error, setError, load, send, decide, stop, setAllowAll, setProvider, busy };
}

/** A fold that is open while live and closes once when it stops being live (the person can open it again). */
// Fold: a <details> box that's open while its step is live and closes once when it stops (the person can reopen it) OwO
function Fold({ live, summary, className, children }: { live: boolean; summary: ReactNode; className?: string; children: ReactNode }) {
  const [open, setOpen] = useState(live);
  const wasLive = useRef(live);
  // Open when it becomes live, close when it stops being live ^w^
  useEffect(() => {
    if (wasLive.current && !live) setOpen(false);
    if (!wasLive.current && live) setOpen(true);
    wasLive.current = live;
  }, [live]);
  return (
    <details className={className} open={open} onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)}>
      {/* The one-line summary, then the content */}
      <summary>{summary}</summary>
      {children}
    </details>
  );
}

// ---- The feed's steps -------------------------------------------------------------------------------------------

/** A tool step's status: its icon and the words after it. */
// STATUS: each tool status's icon and words UwU
const STATUS: Record<string, [string, string]> = {
  queued: ["…", "waits"], pending: ["⏸", "waits for you"], approved: ["…", "approved"], running: ["", "running"],
  done: ["✓", ""], failed: ["✗", "failed"], denied: ["⊘", "denied"], refused: ["⊘", "not allowed in this mode"],
  cancelled: ["⊘", "cancelled"],
};

/** "12 s" or "3 min" from start to end (or now). */
// duration(): "12 s" or "3 min" from start to end (or now) >w<
function duration(start?: number, end?: number) {
  if (!start) return "";
  const seconds = Math.max(1, Math.round(((end ?? Date.now() / 1000) - start)));
  return seconds < 90 ? `${seconds} s` : `${Math.round(seconds / 60)} min`;
}

// Diff: a unified diff with added/removed/hunk lines colored OwO
function Diff({ text }: { text: string }) {
  return (
    <pre className="diff mono">{text.split("\n").map((line, i) => (
      <span key={i} className={line.startsWith("+") && !line.startsWith("+++") ? "add" : line.startsWith("-") && !line.startsWith("---") ? "del" : line.startsWith("@@") ? "hunk" : ""}>{line}{"\n"}</span>
    ))}</pre>
  );
}

// ToolStep: one tool call: icon, name, summary, status, exit code, time, "read-only"; open it for the purpose, the
// command or file content, the spec diff, and the output ^w^
function ToolStep({ step, live }: { step: Step; live: boolean }) {
  const [icon, label] = STATUS[step.status ?? "done"] ?? ["", step.status];
  const input = step.input ?? {};
  // The opened part UwU
  const body = (
    <div className="step-body">
      {/* Purpose, command/file content, diff, output */}
      {step.purpose && <div className="muted small">{step.purpose}</div>}
      {step.name === "run_command" && <pre className="mono step-code">{String(input.command ?? "")}</pre>}
      {step.name === "write_file" && <pre className="mono step-code">{String(input.content ?? "")}</pre>}
      {step.diff && <Diff text={step.diff} />}
      {step.output && <pre className="mono step-output">{step.output}</pre>}
    </div>
  );
  // Folded unless it's running or waiting for approval >w<
  return (
    <Fold live={live && (step.status === "running" || step.status === "pending")} className={`step tool ${step.status}`}
      summary={<>
        <span className={`step-icon ${step.status}`}>{step.status === "running" ? <span className="spinner" /> : icon}</span>
        <span className="step-name">{step.name?.replace(/_/g, " ")}</span>
        <span className="step-summary mono">{step.summary}</span>
        <span className="muted small step-meta">
          {label}{step.exit != null && step.exit !== 0 ? ` (exit ${step.exit})` : ""}{step.seconds ? ` · ${step.seconds} s` : ""}
          {step.readonly ? " · read-only" : ""}
        </span>
      </>}>
      {/* The details */}
      {body}
    </Fold>
  );
}

// ThinkingStep: the model's reasoning: "Thinking…" while streaming, then "Thought for 12 s" (open to read it) OwO
function ThinkingStep({ step, live }: { step: Step; live: boolean }) {
  const streaming = live && !!step.open;
  return (
    <Fold live={streaming} className="step thinking"
      summary={<span className="muted">{streaming ? <><span className="spinner" /> Thinking…</> : `Thought for ${duration(step.ts, step.ended)}`}</span>}>
      {/* The reasoning text */}
      <div className="thinking-text">{step.text}</div>
    </Fold>
  );
}

/** Text the model wrote between steps (or an earlier attempt VALOR sent back): live while streaming, then folded. */
// TextStep: text the model wrote between steps: short text shows as is; longer text folds to its first line ^w^
function TextStep({ step, live }: { step: Step; live: boolean }) {
  const text = step.text ?? "";
  const first = text.trim().split("\n")[0].slice(0, 140);
  const lines = text.trim().split("\n").length;
  if (lines <= 2 && text.length <= 240) return <div className="step text">{text}</div>;
  return (
    <Fold live={live && !!step.open} className="step text"
      summary={<span className="muted">{first}{lines > 1 ? ` (${lines} lines)` : "…"}</span>}>
      {/* The full text */}
      <div className="thinking-text">{text}</div>
    </Fold>
  );
}

/** Approve / deny the actions an answer waits on. */
// Approvals: approve or deny the actions an answer waits on (all at once) UwU
function Approvals({ message, steps, decide, readonly }: { message: Message; steps: Step[]; decide: Decide; readonly?: boolean }) {
  const pending = steps.filter((step) => step.type === "tool" && step.status === "pending");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<ApiError | null>(null);
  if (!pending.length) return null;
  // go(): send the same decision for every pending step (and maybe allow all) >w<
  const go = async (decision: "approve" | "deny", allowAll = false) => {
    setBusy(true); setErr(null);
    try { await decide(message, Object.fromEntries(pending.map((step) => [step.id, decision])), allowAll); }
    catch (e) { setErr(e as ApiError); }
    finally { setBusy(false); }
  };
  // The approval card OwO
  return (
    <div className="approval card">
      {/* What the assistant wants to do, one item per action */}
      <b>{pending.length === 1 ? "The assistant wants to:" : `The assistant wants to do ${pending.length} things:`}</b>
      <ul>{pending.map((step) => {
        // Commands and file contents can be opened to read them in full ^w^
        const body = step.name === "run_command" ? step.input?.command : step.name === "write_file" ? step.input?.content : undefined;
        return (
          <li key={step.id}>
            {body ? (
              <details><summary><span className="mono">{step.summary}</span>{step.purpose ? <span className="muted"> - {step.purpose}</span> : null}</summary>
                <pre className="mono step-code">{String(body)}</pre></details>
            ) : <><span className="mono">{step.summary}</span>{step.purpose ? <span className="muted"> - {step.purpose}</span> : null}</>}
          </li>
        );
      })}</ul>
      {/* Read-only viewers wait; the owner gets Approve, Deny, and Approve-and-allow-all (asks first) */}
      {readonly ? <div className="muted small">Waiting for the chat's owner.</div> : (
        <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
          {/* The three buttons */}
          <button className="btn small primary" disabled={busy} onClick={() => go("approve")}>Approve</button>
          <button className="btn small" disabled={busy} onClick={() => go("deny")}>Deny</button>
          <button className="btn small ghost" disabled={busy} title="Approve this and let the assistant run later actions in this chat without asking"
            onClick={() => window.confirm("Approve, and let the assistant run every later action in this chat without asking?") && go("approve", true)}>
            Approve and allow all in this chat
          </button>
        </div>
      )}
      {/* Any error */}
      {err && <div className="alert error small">{err.message}</div>}
    </div>
  );
}

// Decide: the type of useChat's decide() UwU
type Decide = (message: Message, decisions: Record<string, "approve" | "deny">, allowAll?: boolean) => Promise<void>;

/** What VALOR's checks did behind the scenes: problems the model fixed, and warnings about the final spec. */
// CheckNotes: what VALOR's checks did: warnings about the spec, and problems the model fixed before answering >w<
function CheckNotes({ fixed, warnings }: { fixed?: string[]; warnings?: string[] }) {
  return (
    <>
      {/* Warnings, then the fixed problems (folded) */}
      {!!warnings?.length && warnings.map((warning, i) => <div key={i} className="alert warn small" style={{ marginTop: 8 }}>{warning}</div>)}
      {!!fixed?.length && (
        <details className="check-notes">
          <summary>VALOR checked the spec: the model fixed {fixed.length} problem{fixed.length === 1 ? "" : "s"} before answering</summary>
          <ul>{fixed.map((problem, i) => <li key={i}>{problem}</li>)}</ul>
        </details>
      )}
    </>
  );
}

// ---- Messages and the log ----------------------------------------------------------------------------------------

/** One answer: its live feed, then the outcome (text, errors, VALOR's checks, the draft and plan, questions). */
// AssistantMessage: one answer: its feed, then the outcome OwO
function AssistantMessage({ message, last, readonly, stop, onAnswer, onReview, busy }: {
  message: Message; last: boolean; readonly?: boolean; stop: (message: Message) => void;
  onAnswer: (text: string) => void; onReview?: () => void; busy: boolean;
}) {
  // live = still running or waiting ^w^
  const live = message.status === "running" || message.status === "waiting";
  const steps = message.meta.steps ?? [];
  const meta = message.meta;
  return (
    <div className={`bubble assistant${live ? " live" : ""}`}>
      {/* The live feed: one component per step kind */}
      {steps.length > 0 && (
        <div className="feed">
          {steps.map((step) => step.type === "thinking" ? <ThinkingStep key={step.id} step={step} live={live} />
            : step.type === "tool" ? <ToolStep key={step.id} step={step} live={live} />
            : step.type === "check" ? <div key={step.id} className="step check muted small">{step.text}</div>
            : <TextStep key={step.id} step={step} live={live} />)}
        </div>
      )}
      {/* While running: what it's doing and a Stop button; while waiting: where to approve */}
      {message.status === "running" && (
        <div className="row muted small" style={{ gap: 8, marginTop: 6 }}>
          <span className="spinner" /> {meta.preparing ? `Preparing ${meta.preparing.replace(/_/g, " ")}…` : steps.length ? "Working…" : "Thinking…"}
          {!readonly && <button className="btn ghost small" onClick={() => stop(message)}>Stop</button>}
        </div>
      )}
      {message.status === "waiting" && (
        <div className="muted small" style={{ marginTop: 6 }}>{readonly ? "Waiting for the chat's owner to approve." : "Waiting for your approval below."}</div>
      )}
      {/* The answer text, what a fix chat did next, and any error */}
      {message.content && <div className="answer">{message.content}</div>}
      {meta.rebuild && (
        <div className="alert info small" style={{ marginTop: 8 }}>
          {meta.rebuild.note}{meta.rebuild.job && <> <a href={`#/jobs/${meta.rebuild.job}`}>Follow the build</a></>}
        </div>
      )}
      {meta.error && (
        <div className="alert error small" style={{ marginTop: 8 }}>
          {meta.error.message}{meta.error.hint && <div className="muted">{meta.error.hint}</div>}
        </div>
      )}
      {/* Remaining spec problems and VALOR's check notes */}
      {!!meta.problems?.length && <div className="alert warn small" style={{ marginTop: 8 }}>The spec still has problems: {meta.problems.join("; ")}</div>}
      <CheckNotes fixed={meta.fixed} warnings={meta.warnings} />
      {/* The draft's change counts */}
      {meta.draft && (
        <div className="row small" style={{ marginTop: 8, gap: 6, flexWrap: "wrap" }}>
          Draft: {Object.entries(meta.draft).map(([change, count]) => <span key={change} className={`badge c-${change}`}>{count} {change}</span>)}
        </div>
      )}
      {/* VALOR's plan for the draft, with "Review and approve…" */}
      {meta.plan && (
        <div className="small" style={{ marginTop: 8 }}>
          <b>VALOR's plan</b>
          <ul className="plan-list">{meta.plan.actions.map((action) => <li key={action.host}><span className={`badge c-${action.action === "converge" ? "update" : action.action}`}>{action.action}</span> {action.host}{action.reasons.length ? ` - ${action.reasons.join("; ")}` : ""}</li>)}</ul>
          {onReview && !readonly && <button className="btn small primary" onClick={onReview}>Review and approve…</button>}
        </div>
      )}
      {/* A diff, the question cards (only on the newest, finished answer; earlier ones just list the questions), and tokens */}
      {meta.diff && <Diff text={meta.diff} />}
      {meta.questions && (last && !readonly && message.status === "done"
        ? <QuestionCards id={message.id} questions={meta.questions} onAnswer={onAnswer} disabled={busy} />
        : <ol className="asked small muted">{meta.questions.map((question) => <li key={question.id}>{question.question}</li>)}</ol>)}
      {meta.usage && !live && <div className="usage muted">{(meta.usage.input_tokens + meta.usage.output_tokens).toLocaleString()} tokens</div>}
    </div>
  );
}

/** The conversation: user bubbles and live assistant answers. It sits in a scrolling box (its parent) and keeps
 *  the newest output in view while the person is at the end, also when the box itself gets smaller; scrolling up
 *  stops that until they scroll back down or a new message starts. */
// ChatLog: the conversation. It keeps the newest output in view while you're at the bottom (also when the box
// resizes); scrolling up stops that until you scroll back down or a new message starts UwU
export function ChatLog({ chat, ctl, onAnswer, onReview, empty }: {
  chat: Chat; ctl: ReturnType<typeof useChat>; onAnswer: (text: string) => void; onReview?: () => void; empty?: ReactNode;
}) {
  const stream = useRef<HTMLDivElement>(null);
  const follow = useRef(true);
  // toEnd(): scroll to the bottom when following >w<
  const toEnd = () => {
    const log = stream.current?.parentElement;
    if (log && follow.current) log.scrollTop = log.scrollHeight;
  };
  // Watch scrolling (following = within 30 px of the bottom) and size changes (a ResizeObserver) OwO
  useEffect(() => {
    const log = stream.current?.parentElement;
    if (!log || !stream.current) return;
    const onScroll = () => { follow.current = log.scrollHeight - log.clientHeight - log.scrollTop < 30; };
    const resized = new ResizeObserver(toEnd);
    resized.observe(log);
    resized.observe(stream.current);
    log.addEventListener("scroll", onScroll);
    return () => { resized.disconnect(); log.removeEventListener("scroll", onScroll); };
  }, []);
  // A new chat or message: follow again ^w^
  useEffect(() => { follow.current = true; toEnd(); }, [chat.id, chat.messages.length]);
  return (
    <div className="chat-stream" ref={stream}>
      {/* The empty state, then one bubble per message (user bubbles show their mode) */}
      {chat.messages.length === 0 && empty}
      {chat.messages.map((message, i) => message.role === "user"
        ? <div key={message.id} className="bubble user">{message.mode !== "build" && <span className="mode-tag">{message.mode}</span>}{message.content}</div>
        : <AssistantMessage key={message.id} message={message} last={i === chat.messages.length - 1} readonly={chat.readonly}
            stop={ctl.stop} onAnswer={onAnswer} onReview={onReview} busy={ctl.busy} />)}
    </div>
  );
}

/** A chat's open approval, between its log and the message box: the buttons stay in one place while the log
 *  scrolls. */
// PendingApproval: the open approval card, between the log and the input, so its buttons don't scroll away UwU
export function PendingApproval({ chat, ctl }: { chat: Chat | null; ctl: ReturnType<typeof useChat> }) {
  const waiting = [...(chat?.messages ?? [])].reverse().find((message) => message.role === "assistant" && message.status === "waiting");
  if (!chat || !waiting) return null;
  return (
    <div className="approval-bar">
      {/* The card and a Stop button */}
      <Approvals message={waiting} steps={waiting.meta.steps ?? []} decide={ctl.decide} readonly={chat.readonly} />
      {!chat.readonly && <button className="btn ghost small" onClick={() => ctl.stop(waiting)}>Stop this answer</button>}
    </div>
  );
}
