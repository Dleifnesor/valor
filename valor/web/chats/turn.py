"""The background turn of a chat: the model's answer is written by a thread while the browser polls it.

The assistant message's `meta.steps` is a live feed of what the model does - its reasoning (when the provider
exposes it), text, every tool call with its output, VALOR's checks and spec edits. On a range, Question mode may look
inside the VMs with read-only tools; Code mode operates the range (commands, files, power, snapshots, the spec) and
stops at every action that changes something until the owner approves or denies it, unless they allowed all actions
for that chat. The model's side of a turn (frozen system prompt, history, tool calls and results) is kept with the
message, so a turn resumes exactly where it stopped.

    start / resume / stop    called by the routes; a thread per answer runs `run`
    Feed                     the assistant message as the browser polls it
    Turn                     one answer: the chat builder, a range's Plan mode, or the agent loop (Question/Code)
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time

from ... import agent, ai, drafts, state
from ...cluster import load_catalog, range_vms
from ...errors import ValorError
from ...plan import make_plan
from ...pve import PVE
from ...spec import ROUTER, normalize, parse_spec
from ...validate import validate_cluster
from .. import db, editing
from ..builder import record_usage, reference_for, resolve, spec_check
from ..ranges import _load, _plan_hash
from . import autofix

log = logging.getLogger("valor.chats")

MAX_ROUNDS = 40                 # model calls per turn in the agent loop
WRAP_UP = 8                     # a fix turn is told to finish this many model calls before it runs out
FLUSH = 0.4                     # seconds between feed writes while the model streams
_active: dict[int, threading.Event] = {}          # assistant message id -> stop flag of the thread running it
_lock = threading.Lock()

# Tool steps still in flight; when a turn ends early they are marked cancelled.
UNFINISHED = ("pending", "queued", "approved", "running")


# ---- Starting, resuming and stopping a turn ----------------------------------------------------------------

def start(app, conn, chat_id: int, username: str, mode: str, text: str) -> tuple[int, int]:
    """A message to the assistant and its answer, which a thread writes in the background. Returns both ids."""
    now = time.time()
    user_msg_id = conn.execute("INSERT INTO chat_messages(chat_id, ts, role, mode, content) VALUES (?, ?, 'user', ?, ?)",
                               (chat_id, now, mode, text)).lastrowid
    with _lock:
        answer_id = conn.execute("INSERT INTO chat_messages(chat_id, ts, role, mode, status, meta) VALUES (?, ?, "
                                 "'assistant', ?, 'running', ?)", (chat_id, now, mode, json.dumps({"steps": []}))).lastrowid
        stop_flag = _active[answer_id] = threading.Event()
    conn.execute("UPDATE chats SET updated = ? WHERE id = ?", (now, chat_id))
    spawn(app, chat_id, answer_id, username, False, stop_flag)
    return user_msg_id, answer_id


def resume(app, conn, chat_id: int, msg_id: int, username: str, meta: dict) -> None:
    """Every pending action of a waiting answer is decided (meta holds the decisions): go on in the background."""
    with _lock:
        conn.execute("UPDATE chat_messages SET meta = ?, status = 'running', version = version + 1 WHERE id = ?",
                     (json.dumps(meta), msg_id))
        stop_flag = _active.setdefault(msg_id, threading.Event())
    spawn(app, chat_id, msg_id, username, True, stop_flag)


def stop(msg_id: int) -> bool:
    """Ask the thread writing this answer to stop after its current step. False when no thread is writing it."""
    stop_flag = _active.get(msg_id)
    if stop_flag is not None:
        stop_flag.set()
    return stop_flag is not None


def mark_interrupted(conn, row) -> None:
    """A turn still marked running whose thread is gone (VALOR restarted): mark it failed. A thread is registered
    under _lock in the same step that marks its message running and writes its final status before it unregisters,
    so checking and updating under _lock (only while the row still says running) never hits a live turn."""
    if row["status"] != "running":
        return
    meta = json.loads(row["meta"] or "{}")
    meta["error"] = {"code": "interrupted", "message": "VALOR restarted while this answer was being written"}
    with _lock:
        if row["id"] not in _active:
            conn.execute("UPDATE chat_messages SET status = 'failed', meta = ?, version = version + 1 "
                         "WHERE id = ? AND status = 'running'", (json.dumps(meta), row["id"]))


def spawn(app, chat_id: int, msg_id: int, username: str, resume: bool, stop_flag: threading.Event) -> None:
    """Start the turn's thread; the caller registered stop_flag in _active when it marked the message running."""
    thread = threading.Thread(target=run, args=(app, chat_id, msg_id, username, resume, stop_flag), daemon=True,
                              name=f"chat-{msg_id}")
    thread.start()


class Stopped(Exception):
    """The person pressed Stop: the turn ends at the next step."""


# ---- The live feed -------------------------------------------------------------------------------------------

class Feed:
    """The assistant message as the browser sees it: steps appended and streamed, written at most every FLUSH s."""

    def __init__(self, conn, msg_id: int, stop: threading.Event):
        self.conn, self.id, self.stop = conn, msg_id, stop
        row = conn.execute("SELECT meta FROM chat_messages WHERE id = ?", (msg_id,)).fetchone()
        self.meta = json.loads(row["meta"] or "{}")
        self.meta.setdefault("steps", [])
        self.last_write = 0.0
        self.count = len(self.meta["steps"])          # step ids are s1, s2, ... in the order they were added

    @property
    def steps(self) -> list[dict]:
        return self.meta["steps"]

    def add(self, step: dict) -> dict:
        self.close()
        self.count += 1
        step.setdefault("id", f"s{self.count}")
        step.setdefault("ts", time.time())
        self.steps.append(step)
        self.meta.pop("preparing", None)
        self.flush(True)
        return step

    def close(self) -> None:
        """End the streaming text / thinking steps (the next piece starts a new one)."""
        for step in self.steps:
            if step.get("type") in ("thinking", "text") and step.get("open"):
                step["open"] = False
                step["ended"] = time.time()

    def delta(self, kind: str, text: str) -> None:
        """A streamed piece from the provider: "thinking", "text", or "tool" (the model started writing a call)."""
        if self.stop.is_set():
            raise Stopped()
        if kind == "tool":
            self.close()
            self.meta["preparing"] = text
        else:
            last = self.steps[-1] if self.steps else None
            if last and last.get("type") == kind and last.get("open"):
                last["text"] += text
            else:
                self.close()
                self.count += 1
                self.steps.append({"id": f"s{self.count}", "type": kind, "text": text, "open": True, "ts": time.time()})
        self.flush()

    def flush(self, force: bool = False, status: str | None = None, content: str | None = None) -> None:
        """Write the message (meta, and the status / final content when given). Streaming writes are throttled to
        one per FLUSH seconds; every version bump tells the polling browser there is something new."""
        now = time.time()
        if not force and now - self.last_write < FLUSH:
            return
        self.last_write = now
        sets, args = ["meta = ?", "version = version + 1"], [json.dumps(self.meta, default=str)]
        if status:
            sets.append("status = ?")
            args.append(status)
        if content is not None:
            sets.append("content = ?")
            args.append(content)
        self.conn.execute(f"UPDATE chat_messages SET {', '.join(sets)} WHERE id = ?", (*args, self.id))

    def cancel_unfinished_tools(self) -> None:
        for step in self.steps:
            if step.get("type") == "tool" and step.get("status") in UNFINISHED:
                step["status"] = "cancelled"


# ---- The thread ----------------------------------------------------------------------------------------------

def _provider(app, conn, chat):
    provider, settings = resolve(conn, app.state.box, json.loads(chat["state"] or "{}").get("provider"))
    provider.max_total = ai.TURN_MAX_TOTAL           # a slow local model may think for a long time
    return provider, settings


def _usage(error: ValorError) -> dict:
    """Tokens a failed model call still used (cut_off and friends put them in details)."""
    return error.details if isinstance(error.details, dict) else {}


def run(app, chat_id: int, msg_id: int, username: str, resume: bool, stop_flag: threading.Event) -> None:
    """The thread of one answer. Whatever happens, the message ends as done, waiting, stopped or failed.

    When the provider fails and autofix knows a fallback (fix chats), the turn starts over with that provider."""
    conn = db.connect(app.state.wcfg.db)
    feed = None
    settings: dict = {}                              # the provider answering now (charged for a failed call)
    try:
        feed = Feed(conn, msg_id, stop_flag)
        chat = conn.execute("SELECT * FROM chats WHERE id = ?", (chat_id,)).fetchone()
        msg = conn.execute("SELECT * FROM chat_messages WHERE id = ?", (msg_id,)).fetchone()
        while True:
            provider, settings = _provider(app, conn, chat)
            try:
                content, status = Turn(app, conn, chat, msg, username, provider, settings, feed).run(resume)
                break
            except ValorError as e:
                fallback = autofix.fallback(app, conn, chat, settings, e)
                if fallback is None:
                    raise
                _switch_provider(conn, username, settings, fallback, e, feed)
                chat = conn.execute("SELECT * FROM chats WHERE id = ?", (chat_id,)).fetchone()
                msg = conn.execute("SELECT * FROM chat_messages WHERE id = ?", (msg_id,)).fetchone()
                resume = False                   # the next provider starts the turn over
        feed.close()
        chat = conn.execute("SELECT * FROM chats WHERE id = ?", (chat_id,)).fetchone()
        if status == "done" and json.loads(chat["state"] or "{}").get("fix") == "auto":
            feed.meta["rebuild"] = autofix.rebuild(app, conn, chat, username)
        feed.flush(True, status=status, content=content)
    except Stopped:
        feed.close()
        feed.cancel_unfinished_tools()
        feed.flush(True, status="stopped", content="(stopped)")
    except ValorError as e:
        used = _usage(e)
        if settings and used:
            record_usage(conn, username, settings, int(used.get("input_tokens", 0)),
                         int(used.get("output_tokens", 0)), False)
        if feed is not None:
            feed.close()
            feed.cancel_unfinished_tools()
            feed.meta["error"] = {"code": e.code, "message": e.message, "hint": e.hint}
            feed.flush(True, status="failed")
    except Exception as e:                           # pragma: no cover - logged, shown as failed
        log.exception("chat turn %s failed", msg_id)
        if feed is not None:
            feed.meta["error"] = {"code": "internal", "message": f"internal error: {e.__class__.__name__}"}
            feed.flush(True, status="failed")
    finally:
        with _lock:
            if _active.get(msg_id) is stop_flag:     # a resumed turn may already have registered its own thread
                _active.pop(msg_id)
        conn.execute("UPDATE chats SET updated = ? WHERE id = ?", (time.time(), chat_id))
        conn.close()


def _switch_provider(conn, username: str, settings: dict, fallback: dict, error: ValorError, feed: Feed) -> None:
    """Charge what the failed provider used and tell the person (in the feed) which provider takes over."""
    used = _usage(error)
    if used:
        record_usage(conn, username, settings, int(used.get("input_tokens", 0)), int(used.get("output_tokens", 0)),
                     False)
    feed.close()
    feed.add({"type": "check", "text": f"{settings.get('name', 'The AI provider')} could not answer "
                                       f"({error.message[:240]}) - VALOR asks {fallback['name']} instead."})


# ---- One answer ------------------------------------------------------------------------------------------------

class Turn:
    def __init__(self, app, conn, chat, msg, username: str, prov: ai.Provider, settings: dict, feed: Feed):
        self.app, self.conn, self.chat, self.msg, self.username = app, conn, chat, msg, username
        self.prov, self.settings, self.feed = prov, settings, feed
        self.cfg = app.state.cfg
        self.mode = msg["mode"]
        usage = feed.meta.get("usage") or {}             # a resumed turn keeps counting
        self.used = [int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0))]
        try:
            self.pve = PVE(self.cfg)
        except ValorError:
            self.pve = None

    def run(self, resume: bool) -> tuple[str, str]:
        if self.chat["kind"] == "builder":
            return self.builder()
        if self.mode == "plan":
            return self.plan()
        return self.agent(resume)

    # ---- shared ----

    def history(self) -> list[dict]:
        """Earlier turns as plain text (their tool calls summarized), then this turn's question."""
        rows = self.conn.execute("SELECT * FROM chat_messages WHERE chat_id = ? AND id < ? ORDER BY id",
                                 (self.chat["id"], self.msg["id"])).fetchall()
        out = []
        for row in rows:
            if row["role"] == "user":
                out.append({"role": "user", "content": row["content"]})
            else:
                out.append({"role": "assistant", "content": _answer_as_text(row)})
        return ai.trim(out)

    def record(self, reply: ai.Reply) -> None:
        self.used[0] += reply.input_tokens
        self.used[1] += reply.output_tokens
        record_usage(self.conn, self.username, self.settings, reply.input_tokens, reply.output_tokens, True)
        self.feed.meta["usage"] = {"input_tokens": self.used[0], "output_tokens": self.used[1]}

    def check_budget(self) -> None:
        """Before every further model call of a turn (the first was checked when the message was sent)."""
        limit = int(self.settings.get("daily_tokens_per_user") or 0)
        if limit:
            spent = self.conn.execute("SELECT COALESCE(SUM(input_tokens + output_tokens), 0) FROM ai_usage WHERE "
                                      "username = ? AND ts > ?", (self.username, time.time() - 86400)).fetchone()[0]
            if spent >= limit:
                raise ValorError("ai_budget", f"You used your daily AI budget ({limit} tokens).")

    def finish_text(self, text: str) -> str:
        """The final answer: questions become cards, and the streamed copy of it leaves the feed."""
        questions, text = ai.extract_questions(text)
        if questions:
            self.feed.meta["questions"] = questions
        self.drop_streamed_answer()
        return text.strip() or ("(questions below)" if questions else "(no answer)")

    def drop_streamed_answer(self) -> None:
        """The final text was streamed into the feed; it is shown as the answer instead."""
        steps = self.feed.steps
        if steps and steps[-1].get("type") == "text":
            steps.pop()

    def note(self, problems: list[str]) -> None:
        self.feed.add({"type": "check",
                       "text": "VALOR checked the spec and asked the model to fix: " + "; ".join(problems)[:1500]})

    # ---- the chat builder ----

    def builder(self) -> tuple[str, str]:
        """Write or change a spec for a new range (the builder page). The spec goes to the chat, not a range."""
        system = ai.builder_prompt(reference_for(self.cfg, self.pve))
        messages = self.history()
        spec = self.chat["spec"]
        if spec.strip() and messages and messages[-1]["role"] == "user":
            messages[-1] = {"role": "user", "content": messages[-1]["content"] +
                            f"\n\nThe current spec in the builder:\n```yaml\n{spec.strip()}\n```"}
        own_name = (re.search(r"^name:\s*([a-z0-9-]+)", spec, re.M) or [None, ""])[1]
        base_check = spec_check(self.cfg, self.pve, own_name)

        def check(text: str) -> list[str]:
            # a range of that name existing is fine here: the person picks the name when saving
            problems = [p for p in base_check(text) if not p.startswith("the range is called")]
            check.warnings = base_check.warnings
            return problems
        check.warnings = []

        result = ai.build(self.prov, system, messages, check, on_delta=self.feed.delta, on_check=self.note)
        self.record(ai.Reply("", result["input_tokens"], result["output_tokens"]))
        meta = self.feed.meta
        if result["questions"]:
            meta["questions"] = result["questions"]
        if result["yaml"] and not result["problems"]:
            meta.update(yaml=result["yaml"], fixed=result["fixed"], warnings=check.warnings)
            self.conn.execute("UPDATE chats SET spec = ? WHERE id = ?", (result["yaml"], self.chat["id"]))
        elif result["problems"]:
            meta["problems"] = result["problems"]
        self.drop_streamed_answer()
        return result["reply"] or ("(questions below)" if result["questions"] else "(no explanation)"), "done"

    # ---- a range: Plan mode (a draft spec + VALOR's plan) ----

    def working(self) -> tuple[str, bool]:
        """The spec the chat works on: the range's draft if it has one, else its saved spec."""
        _, saved = _load(self.cfg, self.chat["range"])
        draft = drafts.load(self.cfg, self.chat["range"])
        return (draft[0] if draft else saved), draft is not None

    def plan(self) -> tuple[str, str]:
        name = self.chat["range"]
        working, has_draft = self.working()
        system = ai.range_prompt("plan", reference_for(self.cfg, self.pve), working,
                                 _live(self.cfg, self.pve, name, has_draft))
        check = spec_check(self.cfg, self.pve, name)
        result = ai.build(self.prov, system, self.history(), check, on_delta=self.feed.delta, on_check=self.note)
        self.record(ai.Reply("", result["input_tokens"], result["output_tokens"]))
        meta = self.feed.meta
        meta["problems"] = result["problems"]
        if result["questions"]:
            meta["questions"] = result["questions"]
        if not result["problems"]:
            meta.update(fixed=result["fixed"], warnings=check.warnings)
        if result["yaml"] and not result["problems"]:
            self.save_plan_draft(name, result["yaml"], meta)
        self.drop_streamed_answer()
        return result["reply"] or "(no explanation)", "done"

    def save_plan_draft(self, name: str, text: str, meta: dict) -> None:
        """Save the model's spec as the range's draft and show VALOR's plan for it (nothing is built yet)."""
        drafts.save(self.cfg, name, text, self.username, "chat:plan")
        view = editing.view(self.cfg, name)
        meta["draft"] = view["draft"]["summary"] if view["draft"] else None
        meta["draft_changed"] = True
        if self.pve is None:
            return
        spec = normalize(parse_spec(text), self.cfg.default_os)
        try:
            if validate_cluster(self.pve, spec)["ok"]:
                plan = make_plan(self.pve, spec)
                meta["plan"] = {"summary": plan["summary"], "plan_hash": _plan_hash(spec, plan),
                                "actions": [{key: action.get(key) for key in ("host", "action", "reasons")}
                                            for action in plan["actions"] if action["action"] not in ("keep", "restamp")]}
        except ValorError as e:
            meta["problems"] = [e.message]

    # ---- a range: Question / Code mode (the agent loop with tools) ----

    def context(self) -> agent.Context:
        """What the tools need: the range's VMs, their OS families, and callbacks for status and the spec."""
        name = self.chat["range"]
        if self.pve is None:
            raise ValorError("cluster_unreachable", "VALOR cannot reach the Proxmox cluster")
        spec, _ = _load(self.cfg, name)
        catalog = load_catalog(self.cfg)
        families = {ROUTER: catalog.get(spec.router.os or "", {}).get("family", "debian"),
                    **{host.name: catalog.get(host.os or "", {}).get("family", "debian") for host in spec.hosts}}
        vms = {vm.host: vm for vm in range_vms(self.pve, name) if vm.host}

        def status() -> dict:
            working, has_draft = self.working()
            live = _live(self.cfg, self.pve, name, has_draft)
            live["hosts"] = {ROUTER: {"status": vms.get(ROUTER).status if ROUTER in vms else "missing",
                                      "family": families[ROUTER]}}
            for host in spec.hosts:
                live["hosts"][host.name] = {"status": vms[host.name].status if host.name in vms else "missing",
                                            "os": host.os, "family": families[host.name],
                                            "address": str(host.address), "segment": host.segment}
            return live

        def check_spec(text: str):
            problems = spec_check(self.cfg, self.pve, name)(text)
            working, _ = self.working()
            return problems, drafts.unified_diff(working, text, name), None

        def save_spec(text: str) -> dict:
            drafts.save(self.cfg, name, text, self.username, "chat:code")
            view = editing.view(self.cfg, name)
            self.feed.meta["draft_changed"] = True
            return {"draft": view["draft"]["summary"] if view["draft"] else None,
                    "diff": view["draft"]["diff"] if view["draft"] else ""}

        return agent.Context(self.cfg, self.pve, name, self.username, vms, families, status, check_spec, save_spec)

    def agent(self, resume: bool) -> tuple[str, str]:
        """The agent loop: call the model, run its tool calls, give it the results, until it answers without
        calls (done) or a call needs the person's approval (waiting - `resume` continues after the decision)."""
        name = self.chat["range"]
        model_state = json.loads(self.msg["model"] or "{}")
        ctx = self.context()
        tools = agent.tool_specs(self.mode)
        if not resume or not model_state:
            working, has_draft = self.working()
            live = _live(self.cfg, self.pve, name, has_draft)
            # frozen for the whole turn: resuming after an approval must send the model the same conversation
            model_state = {"system": ai.range_prompt(self.mode, reference_for(self.cfg, self.pve), working, live),
                           "history": self.history(), "transcript": []}
        else:
            self.run_pending(model_state, ctx)
        fixing = "job" in json.loads(self.chat["state"] or "{}")       # a fix chat for a failed build
        for i in range(MAX_ROUNDS):
            if self.feed.stop.is_set():
                raise Stopped()
            if i or resume:
                self.check_budget()
            if fixing and i == MAX_ROUNDS - WRAP_UP:
                _tell_to_wrap_up(model_state)
            reply = self.prov.complete(model_state["system"], model_state["history"] + model_state["transcript"],
                                       tools=tools, on_delta=self.feed.delta)
            self.record(reply)
            self.feed.close()
            if reply.truncated:
                error = ai.cut_off(self.prov, reply)
                error.details = None                 # its tokens are counted already
                raise error
            model_state["transcript"].append({"role": "assistant", "content": reply.text,
                                              "tool_calls": reply.tool_calls})
            if not reply.tool_calls:
                self.save_state(model_state)
                return self.finish_text(reply.text), "done"
            waiting = self.handle_calls(reply.tool_calls, model_state, ctx)
            self.save_state(model_state)
            if waiting:
                return "", "waiting"
        raise ValorError("ai_too_many_steps", f"the assistant used {MAX_ROUNDS} steps without finishing",
                         hint="Ask it to continue, or split the task.")

    def save_state(self, model_state: dict) -> None:
        self.conn.execute("UPDATE chat_messages SET model = ? WHERE id = ?", (json.dumps(model_state), self.msg["id"]))

    def allow_all(self) -> bool:
        """The owner allowed every action in this chat (read fresh: they can switch it on while a turn runs)."""
        row = self.conn.execute("SELECT state FROM chats WHERE id = ?", (self.chat["id"],)).fetchone()
        return bool(json.loads(row["state"] or "{}").get("allow_all"))

    def handle_calls(self, calls: list[dict], model_state: dict, ctx: agent.Context) -> bool:
        """Run the calls in order until one needs the person: it and the ones after it wait. True when waiting."""
        results: dict[str, dict] = {}
        waiting = False
        allow_all = self.allow_all()
        for call in calls:
            args = call["input"]
            problem = agent.validate(call["name"], args, ctx)
            step = {"type": "tool", "id": call["id"], "name": call["name"],
                    "summary": agent.summary(call["name"], args or {}),
                    "purpose": (args or {}).get("purpose", ""), "input": _shown(call["name"], args or {})}
            if problem:
                self.feed.add({**step, "status": "failed", "output": problem})
                results[call["id"]] = {"content": problem, "error": True}
                continue
            family = ctx.families.get(args.get("host", ""), "debian")
            readonly = agent.is_readonly(call["name"], args, family)
            step["readonly"] = readonly
            if call["name"] == "update_spec":
                # checked before the approval card, so the person approves a spec that VALOR can use
                problems, diff, _ = ctx.check_spec(args["yaml"])
                if problems:
                    message = "VALOR could not use that spec:\n" + "\n".join(f"- {p}" for p in problems)
                    self.feed.add({**step, "status": "failed", "output": message})
                    results[call["id"]] = {"content": message, "error": True}
                    continue
                step["diff"] = diff
            if not readonly and self.mode != "code":
                message = "Question mode cannot change anything: tell the person to switch to Code mode for this."
                self.feed.add({**step, "status": "refused", "output": message})
                results[call["id"]] = {"content": message, "error": True}
                continue
            if waiting or (not readonly and not allow_all):
                # this call needs approval, or runs after one that does (calls keep their order)
                needs_approval = not readonly and not allow_all
                self.feed.add({**step, "status": "pending" if needs_approval else "queued", "approval": needs_approval})
                waiting = True
                continue
            results[call["id"]] = self.execute(step, call, ctx, approved_by="auto" if readonly else "allow_all")
        model_state["results"] = results
        if not waiting:
            self.append_results(model_state, calls)
        else:
            model_state["pending_calls"] = calls
        return waiting

    def execute(self, step: dict, call: dict, ctx: agent.Context, approved_by: str) -> dict:
        """Run one call, show it in the feed and audit it."""
        existing = next((s for s in self.feed.steps if s.get("id") == call["id"] and s.get("type") == "tool"), None)
        shown = existing or self.feed.add(step)
        shown["status"], shown["started"] = "running", time.time()
        self.feed.flush(True)
        result = agent.execute(call["name"], call["input"], ctx)
        shown.update(status="failed" if result.get("error") else "done",
                     output=result.get("output") or result["content"], exit=result.get("exit"),
                     seconds=result.get("seconds") or round(time.time() - shown["started"], 1),
                     **({"meta": result["meta"]} if result.get("meta") else {}))
        self.feed.flush(True)
        if call["name"] != "range_status":
            db.audit(self.conn, "chat.action", "fail" if result.get("error") else "ok", username=self.username,
                     target=ctx.range, detail={"chat": self.chat["id"], "tool": call["name"],
                                               "summary": shown.get("summary", "")[:500], "approved_by": approved_by})
        return {"content": result["content"], "error": bool(result.get("error"))}

    def run_pending(self, model_state: dict, ctx: agent.Context) -> None:
        """After the person decided: run approved and queued calls in order, answer denied ones, then continue."""
        calls = model_state.pop("pending_calls", [])
        results = model_state.get("results", {})
        steps_by_id = {step["id"]: step for step in self.feed.steps if step.get("type") == "tool"}
        for call in calls:
            if call["id"] in results:
                continue
            step = steps_by_id.get(call["id"])
            if step is None:
                continue
            if self.feed.stop.is_set():
                raise Stopped()
            if step["status"] == "denied":
                results[call["id"]] = {"content": "The person denied this action. Do not run it again; continue "
                                                  "another way or ask them.", "error": True}
            elif step["status"] in ("approved", "queued"):
                results[call["id"]] = self.execute(step, call, ctx, approved_by=step.get("decided_by") or "auto")
        model_state["results"] = results
        self.append_results(model_state, calls)

    @staticmethod
    def append_results(model_state: dict, calls: list[dict]) -> None:
        """Answer every call of the model's last message, in its order (a call that never ran says so)."""
        results = model_state.pop("results", {})
        model_state["transcript"].append({"role": "tool", "results": [
            {"id": call["id"], "name": call["name"], **results.get(call["id"], {"content": "not run", "error": True})}
            for call in calls]})


# ---- Helpers ---------------------------------------------------------------------------------------------------

def _answer_as_text(row) -> str:
    """An earlier answer for the model's history: its text, the actions it took and the questions it asked."""
    meta = json.loads(row["meta"] or "{}")
    steps = meta.get("steps", [])
    text = row["content"] or ""
    if not text:                         # an answer that ended early: what the model wrote along the way
        text = "\n".join(step["text"] for step in steps if step.get("type") == "text" and step.get("text"))[-3000:]
    actions = [f"- {step.get('summary')} ({step.get('status')}"
               f"{', exit ' + str(step['exit']) if step.get('exit') is not None else ''})"
               for step in steps if step.get("type") == "tool"]
    if actions:
        text += "\n\n[Actions in that answer]\n" + "\n".join(actions[-30:])
    if meta.get("questions"):
        text += "\n\n[Questions asked]\n" + "\n".join(
            f"{q['id']}. {q['question']}" + (f" Options: {', '.join(q['options'])}" if q.get("options") else "")
            for q in meta["questions"])
    return text or "(no answer)"


def _tell_to_wrap_up(model_state: dict) -> None:
    """Near the end of a fix turn's steps: tell the model to make the fix now. Said in a tool result, not a user
    message: some providers refuse a user message after tool results."""
    transcript = model_state["transcript"]
    if transcript[-1:] and transcript[-1]["role"] == "tool":
        transcript[-1]["results"][-1]["content"] += (
            f"\n\n[VALOR] {WRAP_UP} steps are left in this turn. Stop investigating: make the fix now (record "
            "it with update_spec) and finish, or say what you found - VALOR rebuilds the range when you finish.")


def _shown(name: str, args: dict) -> dict:
    """The call's input as the approval card shows it (long fields shortened; the spec is shown as a diff)."""
    out = {}
    for key, value in args.items():
        if key == "yaml":
            continue
        if isinstance(value, str) and len(value) > 4000:
            value = value[:4000] + f"\n... ({len(value):,} characters)"
        out[key] = value
    return out


def _live(cfg, pve, name: str, has_draft: bool) -> dict:
    """What the model should know beyond the spec: VM states and the last build / verification."""
    out: dict = {"draft": has_draft}
    if pve is not None:
        try:
            out["vms"] = {vm.host: vm.status for vm in range_vms(pve, name) if vm.host}
        except ValorError:
            pass
    last_apply = state.load(cfg, name, "apply") or {}
    if last_apply:
        out["last_build"] = {key: last_apply.get(key) for key in ("ok", "started", "error", "message")}
    last_verify = state.load(cfg, name, "verify") or {}
    if last_verify:
        out["last_verification"] = _verification_digest(last_verify)
    return out


def _verification_digest(verification: dict) -> dict:
    """The summary plus only what failed (the model doesn't need the passing tests)."""
    failed_tests = [{key: test.get(key) for key in ("name", "target", "expect", "observed", "detail")}
                    for test in verification.get("tests", []) if not test.get("pass")][:20]
    failed_controls = {host: [row.get("control") for row in rows if row.get("after") != "pass"]
                       for host, rows in (verification.get("baseline") or {}).items()
                       if any(row.get("after") != "pass" for row in rows)}
    return {"summary": verification.get("summary"), "failed_tests": failed_tests, "failed_controls": failed_controls}
