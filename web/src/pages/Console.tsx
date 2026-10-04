import { useEffect, useRef, useState } from "react";
import RFB from "@novnc/novnc";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import "@xterm/xterm/css/xterm.css";
import { ApiError, get, post } from "../api";
import { go } from "../hooks";
import { ErrorBox } from "../components/ui";

// A VM's screen (noVNC) or serial console (xterm.js), relayed by the VALOR VM. Proxmox is never contacted
// by the browser: VALOR opens the console with its API token and relays the stream.

type Kind = "vnc" | "serial";
type State = "connecting" | "connected" | "closed" | "error";

interface Opened { session: string; kind: Kind; vnc_password: string | null; login: boolean }

const utf8len = (s: string) => new TextEncoder().encode(s).length;

export function ConsolePage({ range, host, kind }: { range: string; host: string; kind: Kind }) {
  const screen = useRef<HTMLDivElement>(null);
  const rfb = useRef<RFB | null>(null);
  const ws = useRef<WebSocket | null>(null);
  const [state, setState] = useState<State>("connecting");
  const [err, setErr] = useState<ApiError | string | null>(null);
  const [login, setLogin] = useState(false);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let disposed = false;
    let term: Terminal | null = null;
    let fit: FitAddon | null = null;
    let ping: number | undefined;
    const onResize = () => {
      if (!term || !fit || !ws.current || ws.current.readyState !== WebSocket.OPEN) return;
      fit.fit();
      ws.current.send(`1:${term.cols}:${term.rows}:`);
    };
    setState("connecting");
    setErr(null);
    post<Opened>(`/api/ranges/${range}/vms/${host}/console`, { kind })
      .then((o) => {
        if (disposed || !screen.current) return;
        setLogin(o.login);
        const url = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/api/console/${o.session}`;
        screen.current.innerHTML = "";
        if (kind === "vnc") {
          const r = new RFB(screen.current, url, { credentials: { password: o.vnc_password ?? "" }, wsProtocols: ["binary"] });
          r.scaleViewport = true;
          r.resizeSession = false;
          r.background = "#000";
          r.addEventListener("connect", () => { setState("connected"); r.focus(); });
          r.addEventListener("disconnect", (e: Event) => {
            const clean = (e as CustomEvent).detail?.clean;
            setState(clean ? "closed" : "error");
            if (!clean) setErr("The console connection was lost.");
          });
          r.addEventListener("securityfailure", () => { setState("error"); setErr("Proxmox refused the console session."); });
          rfb.current = r;
        } else {
          term = new Terminal({ cursorBlink: true, fontSize: 14, fontFamily: "ui-monospace, Menlo, Consolas, monospace",
            theme: { background: "#000000" }, scrollback: 5000 });
          fit = new FitAddon();
          term.loadAddon(fit);
          term.open(screen.current);
          fit.fit();
          const sock = new WebSocket(url, ["binary"]);
          sock.binaryType = "arraybuffer";
          sock.onopen = () => {
            setState("connected");
            onResize();
            sock.send("0:1:\r");                    // wake the login prompt
            term?.focus();
            ping = window.setInterval(() => sock.readyState === WebSocket.OPEN && sock.send("2"), 30000);
          };
          sock.onmessage = (e) => {
            if (typeof e.data === "string") {
              if (e.data !== "OK") term?.write(e.data);
            } else {
              const bytes = new Uint8Array(e.data);
              if (!(bytes.length === 2 && bytes[0] === 79 && bytes[1] === 75)) term?.write(bytes);   // skip "OK"
            }
          };
          sock.onclose = (e) => { setState(e.code === 1000 || e.code === 1005 ? "closed" : "error"); if (e.code === 4401) setErr("This console session expired or is not yours. Reconnect."); };
          term.onData((d) => sock.readyState === WebSocket.OPEN && sock.send(`0:${utf8len(d)}:${d}`));
          window.addEventListener("resize", onResize);
          ws.current = sock;
        }
      })
      .catch((e: ApiError) => { if (!disposed) { setState("error"); setErr(e); } });
    return () => {
      disposed = true;
      window.removeEventListener("resize", onResize);
      if (ping) window.clearInterval(ping);
      rfb.current?.disconnect();
      rfb.current = null;
      ws.current?.close();
      ws.current = null;
      term?.dispose();
    };
  }, [range, host, kind, attempt]);

  const typePassword = async () => {
    try {
      const c = await get<{ password: string }>(`/api/ranges/${range}/credentials`);
      if (kind === "vnc" && rfb.current) {
        for (const ch of c.password) rfb.current.sendKey(ch.charCodeAt(0), null);
        rfb.current.focus();
      } else if (ws.current?.readyState === WebSocket.OPEN) {
        ws.current.send(`0:${utf8len(c.password)}:${c.password}`);
      }
    } catch (e) {
      setErr(e as ApiError);
    }
  };

  const fullscreen = () => screen.current?.requestFullscreen?.();

  return (
    <div className="stack" style={{ height: "calc(100vh - 140px)" }}>
      <div className="row">
        <button className="btn ghost" onClick={() => go(`ranges/${range}`)}>← {range}</button>
        <b>{host}</b>
        <span className={`badge ${state === "connected" ? "ok" : state === "error" ? "bad" : ""}`}>{state}</span>
        <span className="grow" />
        <div className="tabs" style={{ border: 0 }}>
          <button className={kind === "vnc" ? "active" : ""} onClick={() => go(`ranges/${range}/console/${host}/vnc`)}>Screen</button>
          <button className={kind === "serial" ? "active" : ""} onClick={() => go(`ranges/${range}/console/${host}/serial`)}>Serial</button>
        </div>
        {login && <button className="btn" onClick={typePassword} disabled={state !== "connected"} title="Types the range password (audited)">Type password</button>}
        {kind === "vnc" && <button className="btn" onClick={() => rfb.current?.sendCtrlAltDel()} disabled={state !== "connected"}>Ctrl+Alt+Del</button>}
        <button className="btn" onClick={fullscreen}>Full screen</button>
        {state !== "connected" && state !== "connecting" && <button className="btn primary" onClick={() => setAttempt(attempt + 1)}>Reconnect</button>}
      </div>
      <ErrorBox error={err} />
      <div className="console-screen" ref={screen} />
      <div className="muted small">
        Sign in as <span className="mono">valor</span> with the range password ("Login" on the range page, or Type password).
        Each console session is recorded in the audit log.
      </div>
    </div>
  );
}
