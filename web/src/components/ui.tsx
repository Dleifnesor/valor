import { ReactNode, useEffect } from "react";
import { ApiError } from "../api";

type IconName =
  | "dashboard" | "ranges" | "jobs" | "users" | "audit" | "settings" | "bell" | "logout" | "menu" | "plus"
  | "refresh" | "shield" | "account" | "theme" | "disc" | "copy";

const PATHS: Record<IconName, string> = {
  dashboard: "M3 13h8V3H3zm0 8h8v-6H3zm10 0h8V11h-8zm0-18v6h8V3z",
  ranges: "M12 2 2 7l10 5 10-5zm-10 10 10 5 10-5M2 17l10 5 10-5",
  jobs: "M4 6h16M4 12h10M4 18h7m9-4-3 3-2-2",
  users: "M16 11a4 4 0 1 0-8 0 4 4 0 0 0 8 0zM4 21a8 8 0 0 1 16 0",
  audit: "M9 4h10v16H5V8zM9 4v4H5m4 5h6m-6 4h6",
  settings: "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zm8.4-3a8.4 8.4 0 0 0-.1-1.3l2-1.6-2-3.4-2.4 1a8 8 0 0 0-2.2-1.3L15.3 2h-4l-.4 2.6A8 8 0 0 0 8.7 6l-2.4-1-2 3.4 2 1.6a8.4 8.4 0 0 0 0 2.6l-2 1.6 2 3.4 2.4-1a8 8 0 0 0 2.2 1.3l.4 2.6h4l.4-2.6a8 8 0 0 0 2.2-1.3l2.4 1 2-3.4-2-1.6c.1-.4.1-.9.1-1.3z",
  bell: "M18 16V11a6 6 0 1 0-12 0v5l-2 2h16zM10 20a2 2 0 0 0 4 0",
  logout: "M15 3h4a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-4M10 17l5-5-5-5M15 12H3",
  menu: "M3 6h18M3 12h18M3 18h18",
  plus: "M12 5v14M5 12h14",
  refresh: "M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7",
  shield: "M12 2 4 5v6c0 5 3.4 9.4 8 11 4.6-1.6 8-6 8-11V5z",
  account: "M12 12a5 5 0 1 0 0-10 5 5 0 0 0 0 10zm0 2c-5 0-9 2.5-9 6v2h18v-2c0-3.5-4-6-9-6z",
  theme: "M12 3a9 9 0 1 0 9 9 7 7 0 0 1-9-9z",
  disc: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zm0-6a3 3 0 1 0 0-6 3 3 0 0 0 0 6z",
  copy: "M8 8h12v12H8zM4 16V4h12",
};

export function Icon({ name, size = 18 }: { name: IconName; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8}
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={PATHS[name]} />
    </svg>
  );
}

export function Logo({ size = 30 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" aria-hidden="true">
      <rect width="64" height="64" rx="14" fill="#1e2a4a" />
      <path d="M14 16h9l9 24 9-24h9L37 50h-10z" fill="#5eead4" />
    </svg>
  );
}

export function Modal({ title, children, footer, onClose, wide }: {
  title: string; children: ReactNode; footer?: ReactNode; onClose: () => void; wide?: boolean;
}) {
  useEffect(() => {
    const on = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  }, [onClose]);
  return (
    <div className="backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={`modal${wide ? " wide" : ""}`} role="dialog" aria-modal="true" aria-label={title}>
        <div className="modal-head">
          <h2>{title}</h2>
          <button className="btn ghost small" onClick={onClose} aria-label="Close">✕</button>
        </div>
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-foot">{footer}</div>}
      </div>
    </div>
  );
}

export function ErrorBox({ error }: { error: ApiError | Error | string | null | undefined }) {
  if (!error) return null;
  const msg = typeof error === "string" ? error : error.message;
  const data = error instanceof ApiError ? error.data : null;
  const details: any[] = Array.isArray(data?.details) ? data.details : [];
  return (
    <div className="alert error" role="alert">
      {msg}
      {data?.hint && <div className="small">{data.hint}</div>}
      {details.length > 0 && (
        <ul>
          {details.slice(0, 12).map((d, i) => (
            <li key={i}>{typeof d === "string" ? d : `${d.location ? d.location + ": " : ""}${d.message ?? JSON.stringify(d)}`}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function Loading({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="empty">
      <span className="spinner" /> <span style={{ marginLeft: 8 }}>{label}</span>
    </div>
  );
}

const STATE_CLASS: Record<string, string> = {
  succeeded: "ok", ok: "ok", pass: "ok", running: "info", queued: "", failed: "bad", fail: "bad", problem: "bad",
  stopped: "", unknown: "",
};

export function StateBadge({ state, label }: { state: string; label?: string }) {
  return (
    <span className={`badge ${STATE_CLASS[state] ?? ""}`}>
      {state === "running" && <span className="spinner" style={{ width: 10, height: 10 }} />}
      {label ?? state}
    </span>
  );
}

export function Card({ title, actions, children, bodyClass = "card-body" }: {
  title?: ReactNode; actions?: ReactNode; children: ReactNode; bodyClass?: string;
}) {
  return (
    <section className="card">
      {(title || actions) && (
        <div className="card-head">
          <h2>{title}</h2>
          {actions}
        </div>
      )}
      <div className={bodyClass}>{children}</div>
    </section>
  );
}
