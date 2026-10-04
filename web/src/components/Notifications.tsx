import { useEffect, useRef, useState } from "react";
import { post } from "../api";
import { ago, useApi } from "../hooks";
import { Icon } from "./ui";

interface Item {
  id: number;
  ts: number;
  level: "info" | "success" | "warning" | "error";
  title: string;
  body: string;
  link: string;
  read: boolean;
}

const LEVEL: Record<Item["level"], string> = { info: "info", success: "ok", warning: "warn", error: "bad" };

export function Notifications() {
  const { data, reload } = useApi<{ items: Item[]; unread: number }>("/api/notifications", 30000);
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const on = (e: MouseEvent) => ref.current && !ref.current.contains(e.target as Node) && setOpen(false);
    document.addEventListener("mousedown", on);
    return () => document.removeEventListener("mousedown", on);
  }, [open]);

  const markAll = async () => {
    await post("/api/notifications/read", { all: true });
    reload();
  };

  return (
    <div style={{ position: "relative" }} ref={ref}>
      <button className="btn ghost icon-btn" onClick={() => setOpen(!open)} aria-label="Notifications">
        <Icon name="bell" />
        {!!data?.unread && <span className="count">{data.unread > 99 ? "99+" : data.unread}</span>}
      </button>
      {open && (
        <div className="popover" role="dialog" aria-label="Notifications">
          <div className="row" style={{ padding: "10px 14px", borderBottom: "1px solid var(--border)" }}>
            <b className="grow">Notifications</b>
            {!!data?.unread && <button className="btn small" onClick={markAll}>Mark all read</button>}
          </div>
          {!data?.items.length && <div className="empty">Nothing yet.</div>}
          {data?.items.map((n) => (
            <div key={n.id} className={`item${n.read ? "" : " unread"}`}>
              <div className="row">
                <span className={`dot ${LEVEL[n.level]}`} />
                <b className="grow">{n.title}</b>
                <span className="muted small nowrap">{ago(n.ts)}</span>
              </div>
              {n.body && <div className="muted small" style={{ whiteSpace: "pre-wrap" }}>{n.body}</div>}
              {n.link && n.link.includes("#/") && (
                <a className="small" href={n.link.slice(n.link.indexOf("#/"))} onClick={() => setOpen(false)}>Open</a>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
