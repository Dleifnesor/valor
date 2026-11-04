import { useEffect, useState } from "react";
import { Modal } from "./ui";
import type { Question } from "./ChatFeed";

// The model's questions to the person (its ask_questions tool). A dialog asks them one at a time, with the
// recommended answer preselected; the answers go back to the chat as one message.

type Picks = Record<number, string[]>;     // question id -> the options chosen
type Others = Record<number, string>;      // question id -> the person's own answer

const chosen = (question: Question, picked: Picks, other: Others) =>
  [...(picked[question.id] ?? []), ...(other[question.id]?.trim() ? [other[question.id].trim()] : [])];

/** The line under an answer with questions. The dialog opens by itself once per answer; "Answer" opens it again
 *  with the choices kept. */
export function QuestionCards({ id, questions, onAnswer, disabled }: {
  id: number; questions: Question[]; onAnswer: (text: string) => void; disabled?: boolean;
}) {
  const [picked, setPicked] = useState<Picks>(() =>
    Object.fromEntries(questions.map((question) => [question.id, question.recommended ? [question.recommended] : []])));
  const [other, setOther] = useState<Others>({});
  const [answered, setAnswered] = useState<Set<number>>(new Set());   // answered or confirmed by the person
  const seen = `valor-asked-${id}`;
  const [open, setOpen] = useState(() => { try { return !sessionStorage.getItem(seen); } catch { return true; } });
  const close = () => { setOpen(false); try { sessionStorage.setItem(seen, "1"); } catch { /* shown again next time */ } };
  const send = () => {
    close();
    onAnswer("Answers:\n" + questions.map((question) => {
      const parts = chosen(question, picked, other);
      return `${question.id}. ${question.question} -> ${parts.length ? parts.join(", ") : "(no preference - use your default)"}`;
    }).join("\n"));
  };
  const skip = () => { close(); onAnswer("Use your defaults for these questions and go ahead."); };
  const count = questions.length;
  return (
    <div className="questions">
      <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
        <span className="small">{count} question{count === 1 ? "" : "s"} before the assistant goes on{answered.size ? ` (${answered.size} answered)` : ""}.</span>
        <button className="btn small primary" disabled={disabled} onClick={() => setOpen(true)}>Answer</button>
        <button className="btn small" disabled={disabled} onClick={skip}>Skip - use your defaults</button>
      </div>
      {open && !disabled && <QuestionDialog questions={questions} picked={picked} setPicked={setPicked} other={other}
        setOther={setOther} answered={answered} confirm={(questionId) => setAnswered((ids) => new Set(ids).add(questionId))}
        onSend={send} onSkip={skip} onClose={close} />}
    </div>
  );
}

/** One question per page, then a review page. Keys 1-9 pick an option, Enter moves on. */
function QuestionDialog({ questions, picked, setPicked, other, setOther, answered, confirm, onSend, onSkip, onClose }: {
  questions: Question[]; picked: Picks; setPicked: (picks: Picks) => void; other: Others; setOther: (others: Others) => void;
  answered: Set<number>; confirm: (id: number) => void; onSend: () => void; onSkip: () => void; onClose: () => void;
}) {
  // the page shown: a question's index, or questions.length for the review page
  const [page, setPage] = useState(() => Math.max(0, questions.findIndex((question) => !answered.has(question.id))));
  const review = page >= questions.length;
  const current = questions[Math.min(page, questions.length - 1)];
  const next = () => { if (!review) confirm(current.id); setPage(page + 1); };    // moving on accepts what is selected
  const choose = (option: string) => {
    confirm(current.id);
    const selected = picked[current.id] ?? [];
    setPicked({ ...picked, [current.id]: current.multiple
      ? (selected.includes(option) ? selected.filter((x) => x !== option) : [...selected, option])
      : [option] });
    if (!current.multiple) {
      setOther({ ...other, [current.id]: "" });
      const at = page;
      window.setTimeout(() => setPage((shown) => (shown === at ? shown + 1 : shown)), 180);   // one answer: on to the next question
    }
  };
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (["INPUT", "TEXTAREA", "SELECT"].includes((e.target as HTMLElement).tagName)) return;
      const number = Number(e.key);
      if (!review && number >= 1 && number <= current.options.length) choose(current.options[number - 1]);
      else if (e.key === "Enter") { e.preventDefault(); if (review) onSend(); else next(); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });
  return (
    <Modal title="The assistant asks" onClose={onClose} footer={
      <>
        <button className="btn ghost" onClick={onSkip}>Skip - use your defaults</button>
        <span className="grow" />
        {page > 0 && <button className="btn" onClick={() => setPage(page - 1)}>Back</button>}
        {review ? <button className="btn primary" onClick={onSend}>Send answers</button>
          : <button className="btn primary" onClick={next}>{page === questions.length - 1 ? "Review" : "Next"}</button>}
      </>
    }>
      <div className="qw-chips" role="tablist" aria-label="Questions">
        {questions.map((question, j) => {
          const done = answered.has(question.id);
          return (
            <button key={question.id} role="tab" aria-selected={j === page} className={`qw-chip${j === page ? " on" : ""}${done ? " done" : ""}`}
              onClick={() => setPage(j)}>{done ? "✓ " : ""}{question.header || `Question ${j + 1}`}</button>
          );
        })}
        <button role="tab" aria-selected={review} className={`qw-chip${review ? " on" : ""}`} onClick={() => setPage(questions.length)}>Review</button>
      </div>
      {review ? (
        <ol className="qw-review">
          {questions.map((question, j) => (
            <li key={question.id}>
              <button className="link" onClick={() => setPage(j)}>{question.question}</button>
              <div className="muted small">{chosen(question, picked, other).join(", ") || "No preference - the assistant decides"}</div>
            </li>
          ))}
        </ol>
      ) : (
        <>
          <div>
            <div className="muted small">Question {page + 1} of {questions.length}{current.multiple ? " - choose any" : ""}</div>
            <div className="qw-question">{current.question}</div>
          </div>
          <div className="qw-options">
            {current.options.map((option, k) => {
              const on = (picked[current.id] ?? []).includes(option);
              return (
                <button key={option} type="button" className={`qw-option${on ? " on" : ""}`} aria-pressed={on} onClick={() => choose(option)}>
                  <span className="qw-key">{k + 1}</span>
                  <span className="grow">{option}</span>
                  {current.recommended === option && <span className="badge">Recommended</span>}
                </button>
              );
            })}
            <input name="answer-other" className="qw-other" value={other[current.id] ?? ""}
              placeholder={current.options.length ? "Other - type your own answer" : "Your answer"}
              onChange={(e) => {
                confirm(current.id);
                setOther({ ...other, [current.id]: e.target.value });
                if (!current.multiple && e.target.value) setPicked({ ...picked, [current.id]: [] });
              }}
              onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); next(); } }} />
          </div>
        </>
      )}
    </Modal>
  );
}
