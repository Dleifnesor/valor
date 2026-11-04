// *shows clickable question cards* OwO web/src/components/ChatQuestions.tsx: the AI's QUESTIONS to you! ^w^
// When the model uses a ```questions block, VALOR shows a dialog asking them one at a time, with the recommended
// answer already picked; your answers go back to the chat as one message *wags tail*
import { useEffect, useState } from "react";
// Modal (the dialog) and the Question type UwU
import { Modal } from "./ui";
import type { Question } from "./ChatFeed";

// The model's questions to the person (its ask_questions tool). A dialog asks them one at a time, with the
// recommended answer preselected; the answers go back to the chat as one message.

// Picks: question id -> the options chosen. Others: question id -> your own typed answer >w<
type Picks = Record<number, string[]>;     // question id -> the options chosen
type Others = Record<number, string>;      // question id -> the person's own answer

// chosen(): everything answered for a question: picked options plus the typed answer (trimmed) OwO
const chosen = (question: Question, picked: Picks, other: Others) =>
  [...(picked[question.id] ?? []), ...(other[question.id]?.trim() ? [other[question.id].trim()] : [])];

/** The line under an answer with questions. The dialog opens by itself once per answer; "Answer" opens it again
 *  with the choices kept. */
// QuestionCards: the line under an answer with questions: "N questions", Answer, and Skip ^w^
export function QuestionCards({ id, questions, onAnswer, disabled }: {
  id: number; questions: Question[]; onAnswer: (text: string) => void; disabled?: boolean;
}) {
  // Start with each question's recommended option picked UwU
  const [picked, setPicked] = useState<Picks>(() =>
    Object.fromEntries(questions.map((question) => [question.id, question.recommended ? [question.recommended] : []])));
  const [other, setOther] = useState<Others>({});
  // Which questions the person answered or confirmed >w<
  const [answered, setAnswered] = useState<Set<number>>(new Set());   // answered or confirmed by the person
  // The dialog opens by itself once per answer (remembered in sessionStorage); "Answer" opens it again OwO
  const seen = `valor-asked-${id}`;
  const [open, setOpen] = useState(() => { try { return !sessionStorage.getItem(seen); } catch { return true; } });
  const close = () => { setOpen(false); try { sessionStorage.setItem(seen, "1"); } catch { /* shown again next time */ } };
  // send(): turn every answer into one "Answers:" message ("no preference" when empty) ^w^
  const send = () => {
    close();
    onAnswer("Answers:\n" + questions.map((question) => {
      const parts = chosen(question, picked, other);
      return `${question.id}. ${question.question} -> ${parts.length ? parts.join(", ") : "(no preference - use your default)"}`;
    }).join("\n"));
  };
  // skip(): tell the assistant to use its defaults UwU
  const skip = () => { close(); onAnswer("Use your defaults for these questions and go ahead."); };
  const count = questions.length;
  // The line under the answer >w<
  return (
    <div className="questions">
      {/* The count (and how many answered), Answer and Skip */}
      <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
        <span className="small">{count} question{count === 1 ? "" : "s"} before the assistant goes on{answered.size ? ` (${answered.size} answered)` : ""}.</span>
        <button className="btn small primary" disabled={disabled} onClick={() => setOpen(true)}>Answer</button>
        <button className="btn small" disabled={disabled} onClick={skip}>Skip - use your defaults</button>
      </div>
      {/* The dialog, while open */}
      {open && !disabled && <QuestionDialog questions={questions} picked={picked} setPicked={setPicked} other={other}
        setOther={setOther} answered={answered} confirm={(questionId) => setAnswered((ids) => new Set(ids).add(questionId))}
        onSend={send} onSkip={skip} onClose={close} />}
    </div>
  );
}

/** One question per page, then a review page. Keys 1-9 pick an option, Enter moves on. */
// QuestionDialog: one question per page, then a review page. Keys 1-9 pick an option, Enter moves on OwO
function QuestionDialog({ questions, picked, setPicked, other, setOther, answered, confirm, onSend, onSkip, onClose }: {
  questions: Question[]; picked: Picks; setPicked: (picks: Picks) => void; other: Others; setOther: (others: Others) => void;
  answered: Set<number>; confirm: (id: number) => void; onSend: () => void; onSkip: () => void; onClose: () => void;
}) {
  // the page shown: a question's index, or questions.length for the review page
  // The page shown: the first unanswered question (or the review page = questions.length) ^w^
  const [page, setPage] = useState(() => Math.max(0, questions.findIndex((question) => !answered.has(question.id))));
  const review = page >= questions.length;
  const current = questions[Math.min(page, questions.length - 1)];
  // next(): moving on counts as accepting what's selected UwU
  const next = () => { if (!review) confirm(current.id); setPage(page + 1); };    // moving on accepts what is selected
  // choose(): pick an option (toggle it for multiple-choice). A single-choice pick clears the typed answer and moves
  // to the next page after 180 ms (only if the person is still on that page) >w<
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
  // Keyboard: number keys pick, Enter goes next (or sends on the review page); ignored while typing in a field OwO
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
  // The dialog; footer: Skip, Back, and Next/Review/Send ^w^
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
      {/* Chips across the top: one per question (✓ when answered) and Review */}
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
      {/* The review page lists every question with its answer; otherwise the current question */}
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
          {/* "Question N of M", then the question */}
          <div>
            <div className="muted small">Question {page + 1} of {questions.length}{current.multiple ? " - choose any" : ""}</div>
            <div className="qw-question">{current.question}</div>
          </div>
          {/* The options (numbered, the recommended one badged), then a field for your own answer */}
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
            {/* Typing your own answer clears a single-choice pick; Enter moves on */}
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
