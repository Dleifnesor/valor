import { Component, ReactNode, useEffect, useRef, useState } from "react";
import { ApiError } from "../api";

export type IconName =
  | "dashboard" | "ranges" | "jobs" | "users" | "audit" | "settings" | "bell" | "logout" | "menu" | "plus"
  | "refresh" | "shield" | "account" | "theme" | "disc" | "copy" | "chat" | "chats" | "server" | "vm" | "play"
  | "power" | "stop" | "terminal" | "camera" | "check" | "key" | "file" | "book" | "clock" | "info" | "map"
  | "caret" | "search" | "edit" | "trash" | "mail" | "link" | "bot" | "lock" | "download";

const PATHS: Record<IconName, string> = {
  dashboard: "M3 13h8V3H3zm0 8h8v-6H3zm10 0h8V11h-8zm0-18v6h8V3z",
  ranges: "M10 3h4v4h-4zM3 17h4v4H3zm7 0h4v4h-4zm7 0h4v4h-4zM12 7v5M5 17v-5h14v5",
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
  chat: "M4 5h16v11H9l-5 4z",
  chats: "M3 4h13v9H7l-4 3zm6 12h8l4 3V9h-2",
  server: "M4 4h16v6H4zm0 10h16v6H4zM8 7h.01M8 17h.01",
  vm: "M3 4h18v12H3zM8 20h8M12 16v4",
  play: "M7 4l13 8-13 8z",
  power: "M12 3v8M6.3 6.3a8 8 0 1 0 11.4 0",
  stop: "M6 6h12v12H6z",
  terminal: "M3 4h18v16H3zM7 9l3 3-3 3m5 0h5",
  camera: "M3 7h4l2-3h6l2 3h4v13H3zm9 10a4 4 0 1 0 0-8 4 4 0 0 0 0 8z",
  check: "M4 12l5 5L20 6",
  key: "M7.5 21a4.5 4.5 0 1 0 0-9 4.5 4.5 0 0 0 0 9zm3.2-7.7L21 3m-4 4 3 3m-5.5-.5 2 2",
  file: "M6 2h9l5 5v15H6zm8 0v6h6M9 13h8m-8 4h6",
  book: "M5 3h14v16H7a2 2 0 0 0 0 4h12v-4M5 21V5M9 7h6",
  clock: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zm0-14v5l3 2",
  info: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zm0-10v5m0-8h.01",
  map: "M9 4 3 6v14l6-2 6 2 6-2V4l-6 2zm0 0v14m6-12v14",
  caret: "M6 9l6 6 6-6",
  search: "M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14zm10 3-5-5",
  edit: "M4 20h4L19 9l-4-4L4 16zm10-14 4 4",
  trash: "M4 7h16m-10 4v6m4-6v6M6 7l1 13h10l1-13M9 7V4h6v3",
  mail: "M3 5h18v14H3zm0 0 9 8 9-8",
  link: "M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1",
  bot: "M5 8h14v11H5zm7-5v5M9 13h.01M15 13h.01M2 12v4m20-4v4",
  lock: "M5 11h14v10H5zm3 0V7a4 4 0 0 1 8 0v4",
  download: "M12 3v12m-5-5 5 5 5-5M5 20h14",
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
    <svg width={size} height={size} viewBox="0 0 64 64" aria-hidden="true" fill="currentColor">
      {/* two stacked chevrons; the colour comes from the surrounding text colour */}
      <path d="M6 8h13l13 15 13-15h13L32 37z" />
      <path d="M6 28h13l13 15 13-15h13L32 57z" opacity="0.55" />
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
  stopped: "", unknown: "", warn: "warn",
};

/** A state as a coloured mark and words: "● succeeded", spinner while running. */
export function StateBadge({ state, label }: { state: string; label?: string }) {
  return (
    <span className={`status ${STATE_CLASS[state] ?? ""}`}>
      {state === "running" ? <span className="spinner" /> : <i aria-hidden="true" />}
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

export interface MenuEntry { id: string; label: string; icon: IconName; href: string; sub?: boolean }

/** The content panel of one object (VALOR itself, a range, a VM), laid out like Proxmox: a title bar with the
 *  object's actions, a list of its sections on the left and the section on the right. */
export function View({ title, tools, menu, active, children, fill }: {
  title: ReactNode; tools?: ReactNode; menu?: MenuEntry[]; active?: string; children: ReactNode; fill?: boolean;
}) {
  return (
    <section className="view">
      <header className="view-head">
        <h1>{title}</h1>
        {tools && <div className="view-tools">{tools}</div>}
      </header>
      <div className={`view-main${menu ? " with-menu" : ""}`}>
        {menu && (
          <nav className="view-menu" aria-label="Sections">
            {menu.map((m) => (
              <a key={m.id} href={`#/${m.href}`} className={`${m.id === active ? "active" : ""}${m.sub ? " sub" : ""}`}
                aria-current={m.id === active ? "page" : undefined}>
                <Icon name={m.icon} size={16} /> {m.label}
              </a>
            ))}
          </nav>
        )}
        <div className={`view-body${fill ? " fill" : ""}`}>{children}</div>
      </div>
    </section>
  );
}

export interface MenuAction { label: string; icon?: IconName; onClick: () => void; danger?: boolean; disabled?: boolean }

/** A toolbar button that opens a menu ("Shutdown ▾", "More ▾"). */
export function DropButton({ label, icon, items, disabled }: { label: string; icon?: IconName; items: MenuAction[]; disabled?: boolean }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const on = (e: MouseEvent) => ref.current && !ref.current.contains(e.target as Node) && setOpen(false);
    document.addEventListener("mousedown", on);
    return () => document.removeEventListener("mousedown", on);
  }, [open]);
  return (
    <div className="drop" ref={ref}>
      <button className="btn" onClick={() => setOpen(!open)} aria-haspopup="menu" aria-expanded={open} disabled={disabled}>
        {icon && <Icon name={icon} size={14} />}<span className="label">{label}</span><Icon name="caret" size={12} />
      </button>
      {open && (
        <div className="popover menu" role="menu">
          {items.map((i) => (
            <button key={i.label} role="menuitem" className={`menu-item${i.danger ? " danger" : ""}`} disabled={i.disabled}
              onClick={() => { setOpen(false); i.onClick(); }}>
              {i.icon && <Icon name={i.icon} size={15} />} {i.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

/** A page that fails to render shows what went wrong instead of leaving the window blank; the rest of the UI stays. */
export class PageGuard extends Component<{ children: ReactNode; page?: string }, { error: Error | null }> {
  state = { error: null as Error | null };
  static getDerivedStateFromError(error: Error) {
    return { error };
  }
  componentDidUpdate(prev: { page?: string }) {
    if (this.state.error && prev.page !== this.props.page) this.setState({ error: null });   // another page: try again
  }
  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="view-body">
        <div className="alert error" role="alert">
          This page failed to show: {this.state.error.message}
          <div className="row" style={{ marginTop: 8 }}>
            <button className="btn" onClick={() => window.location.reload()}>Reload</button>
          </div>
        </div>
      </div>
    );
  }
}
