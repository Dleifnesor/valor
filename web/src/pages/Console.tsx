// *presses its nose against a VM's screen* OwO web/src/pages/Console.tsx: a VM's CONSOLE in the browser! ^w^
// The graphical screen (noVNC) or the serial console (xterm.js), relayed by the VALOR VM. The browser never talks to
// Proxmox: VALOR opens the console with its own API token and relays the stream. Buttons type the range username and
// password for you *wags tail*
import { useEffect, useRef, useState } from "react";
// noVNC's client (RFB), xterm.js and its fit add-on (+ its CSS), API calls, go, and UI pieces UwU
import RFB from "@novnc/novnc";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import "@xterm/xterm/css/xterm.css";
import { ApiError, get, post } from "../api";
import { go } from "../hooks";
import { DropButton, ErrorBox } from "../components/ui";

// A VM's screen (noVNC) or serial console (xterm.js), relayed by the VALOR VM. Proxmox is never contacted
// by the browser: VALOR opens the console with its API token and relays the stream.

// Kind: which console. State: the connection's state >w<
type Kind = "vnc" | "serial";
type State = "connecting" | "connected" | "closed" | "error";

/** A username that signs in on this VM with the range password (Linux guest user, local or domain Administrator). */
interface Username { username: string; kind: "linux" | "windows" | "domain" }
// Opened: what POST .../console returns: a one-time session, the VNC password, whether a login exists, usernames OwO
interface Opened { session: string; kind: Kind; vnc_password: string | null; login: boolean; usernames?: Username[] }

// utf8len(): text length in bytes (the serial protocol counts bytes) ^w^
const utf8len = (s: string) => new TextEncoder().encode(s).length;

// Characters that need Shift on a US keyboard. QEMU turns a bare keysym into the key without its modifiers, so
// "_" would arrive as "-" (and capitals flip Caps Lock); holding Shift around them types what was meant.
// SHIFTED: characters that need Shift on a US keyboard; XK_SHIFT_L: the Left Shift keysym UwU
const SHIFTED = /[A-Z~!@#$%^&*()_+{}|:"<>?]/;
const XK_SHIFT_L = 0xffe1;

/** Types text on a VNC screen key by key, as if on the keyboard (no Enter). */
// typeKeys(): "type" text on the VNC screen key by key, holding Shift around shifted characters >w<
function typeKeys(vnc: RFB, text: string) {
  for (const char of text) {
    const shift = SHIFTED.test(char);
    if (shift) vnc.sendKey(XK_SHIFT_L, "ShiftLeft", true);
    vnc.sendKey(char.charCodeAt(0), null);
    if (shift) vnc.sendKey(XK_SHIFT_L, "ShiftLeft", false);
  }
  vnc.focus();
}

/** `windowed`: in a console window of its own (App's console/<range>/<host> route) rather than the VM's page. */
// ConsolePage: one console; `windowed` = inside the pop-out window (links stay in that window) OwO
export function ConsolePage({ range, host, kind, windowed = false }: { range: string; host: string; kind: Kind; windowed?: boolean }) {
  // link(): the route for the other console kind ^w^
  const link = (which: Kind) => (windowed ? `console/${range}/${host}` : `ranges/${range}/vm/${host}/console`) + (which === "serial" ? "/serial" : "");
  // The screen element, the VNC client, the serial websocket, state, error, login info, and a counter to reconnect UwU
  const screen = useRef<HTMLDivElement>(null);
  const rfb = useRef<RFB | null>(null);
  const ws = useRef<WebSocket | null>(null);
  const [state, setState] = useState<State>("connecting");
  const [err, setErr] = useState<ApiError | string | null>(null);
  const [login, setLogin] = useState(false);
  const [usernames, setUsernames] = useState<Username[]>([]);
  const [attempt, setAttempt] = useState(0);

  // Connect whenever range/host/kind change or Reconnect is pressed; the cleanup disconnects >w<
  useEffect(() => {
    let disposed = false;
    let term: Terminal | null = null;
    let fit: FitAddon | null = null;
    let ping: number | undefined;
    // onResize(): fit the terminal to its box and tell the server the new size ("1:cols:rows:") OwO
    const onResize = () => {
      if (!term || !fit || !ws.current || ws.current.readyState !== WebSocket.OPEN) return;
      fit.fit();
      ws.current.send(`1:${term.cols}:${term.rows}:`);
    };
    setState("connecting");
    setErr(null);
    // Ask VALOR for a console session ^w^
    post<Opened>(`/api/ranges/${range}/vms/${host}/console`, { kind })
      .then((opened) => {
        if (disposed || !screen.current) return;
        // Remember the login info, build the websocket URL (wss on https), and clear the screen element UwU
        setLogin(opened.login);
        setUsernames(opened.usernames ?? []);
        const url = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/api/console/${opened.session}`;
        screen.current.innerHTML = "";
        // VNC: connect noVNC with the one-time password, scale to fit, black background, and handle connect/disconnect and
        // a refused session >w<
        if (kind === "vnc") {
          const vnc = new RFB(screen.current, url, { credentials: { password: opened.vnc_password ?? "" }, wsProtocols: ["binary"] });
          vnc.scaleViewport = true;
          vnc.resizeSession = false;
          vnc.background = "#000";
          vnc.addEventListener("connect", () => { setState("connected"); vnc.focus(); });
          vnc.addEventListener("disconnect", (e: Event) => {
            const clean = (e as CustomEvent).detail?.clean;
            setState(clean ? "closed" : "error");
            if (!clean) setErr("The console connection was lost.");
          });
          vnc.addEventListener("securityfailure", () => { setState("error"); setErr("Proxmox refused the console session."); });
          rfb.current = vnc;
        } else {
          // Serial: an xterm.js terminal that fits its box OwO
          term = new Terminal({ cursorBlink: true, fontSize: 14, fontFamily: "ui-monospace, Menlo, Consolas, monospace",
            theme: { background: "#000000" }, scrollback: 5000 });
          fit = new FitAddon();
          term.loadAddon(fit);
          term.open(screen.current);
          fit.fit();
          // The websocket (binary) ^w^
          const sock = new WebSocket(url, ["binary"]);
          sock.binaryType = "arraybuffer";
          // Opened: say "connected", send the size, press Enter to wake the login prompt, and ping every 30 s to keep the
          // connection alive UwU
          sock.onopen = () => {
            setState("connected");
            onResize();
            sock.send("0:1:\r");                    // wake the login prompt
            term?.focus();
            ping = window.setInterval(() => sock.readyState === WebSocket.OPEN && sock.send("2"), 30000);
          };
          // Messages: text or bytes go to the terminal, skipping the proxy's "OK" greeting >w<
          sock.onmessage = (e) => {
            if (typeof e.data === "string") {
              if (e.data !== "OK") term?.write(e.data);
            } else {
              const bytes = new Uint8Array(e.data);
              if (!(bytes.length === 2 && bytes[0] === 79 && bytes[1] === 75)) term?.write(bytes);   // skip "OK"
            }
          };
          // Closed: clean (1000/1005) or not; 4401 = the session expired or isn't yours OwO
          sock.onclose = (e) => { setState(e.code === 1000 || e.code === 1005 ? "closed" : "error"); if (e.code === 4401) setErr("This console session expired or is not yours. Reconnect."); };
          // Keys typed into the terminal go out as "0:<byte length>:<text>" ^w^
          term.onData((input) => sock.readyState === WebSocket.OPEN && sock.send(`0:${utf8len(input)}:${input}`));
          window.addEventListener("resize", onResize);
          ws.current = sock;
        }
      })
      // Opening failed UwU
      .catch((e: ApiError) => { if (!disposed) { setState("error"); setErr(e); } });
    // Cleanup: stop everything and free the terminal >w<
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

  // typeText(): type text on the VNC screen, or send it to the serial console OwO
  const typeText = (text: string) => {
    if (kind === "vnc" && rfb.current) typeKeys(rfb.current, text);
    else if (ws.current?.readyState === WebSocket.OPEN) ws.current.send(`0:${utf8len(text)}:${text}`);
  };

  // typePassword(): fetch the range password (an audited view) and type it ^w^
  const typePassword = async () => {
    try {
      const creds = await get<{ password: string }>(`/api/ranges/${range}/credentials`);
      typeText(creds.password);
    } catch (e) {
      setErr(e as ApiError);
    }
  };

  // Full screen, whether the buttons are disabled, and "sign in as X or Y" UwU
  const fullscreen = () => screen.current?.requestFullscreen?.();
  const offline = state !== "connected";
  const signInAs = usernames.map((u, i) => (
    <span key={u.username}>{i > 0 && " or "}<span className="mono">{u.username}</span></span>
  ));

  // The page >w<
  return (
    <div className="console-page">
      {/* The toolbar */}
      <div className="row">
        {/* Screen / Serial tabs, then the connection state */}
        <div className="seg-control compact" role="tablist">
          <button role="tab" aria-selected={kind === "vnc"} className={kind === "vnc" ? "active" : ""}
            onClick={() => go(link("vnc"))}>Screen</button>
          <button role="tab" aria-selected={kind === "serial"} className={kind === "serial" ? "active" : ""}
            onClick={() => go(link("serial"))}>Serial</button>
        </div>
        <span className={`badge ${state === "connected" ? "ok" : state === "error" ? "bad" : ""}`}>{state}</span>
        <span className="grow" />
        {/* Type username: a button for one username, a menu for several (domain/local marked) */}
        {login && usernames.length === 1 && (
          <button className="btn" onClick={() => typeText(usernames[0].username)} disabled={offline}
            title={`Types ${usernames[0].username}`}>Type username</button>
        )}
        {login && usernames.length > 1 && (
          <DropButton label="Type username" disabled={offline} items={usernames.map((u) => ({
            label: `${u.username}${u.kind === "domain" ? " (domain)" : u.kind === "windows" ? " (local)" : ""}`,
            onClick: () => typeText(u.username),
          }))} />
        )}
        {/* Type password, Ctrl+Alt+Del (VNC), Full screen, Reconnect */}
        {login && <button className="btn" onClick={typePassword} disabled={offline} title="Types the range password (audited)">Type password</button>}
        {kind === "vnc" && <button className="btn" onClick={() => rfb.current?.sendCtrlAltDel()} disabled={offline}>Ctrl+Alt+Del</button>}
        <button className="btn" onClick={fullscreen}>Full screen</button>
        {state !== "connected" && state !== "connecting" && <button className="btn primary" onClick={() => setAttempt(attempt + 1)}>Reconnect</button>}
      </div>
      {/* Errors, then the console itself */}
      <ErrorBox error={err} />
      <div className="console-screen" ref={screen} />
      {/* How to sign in, and a reminder that console sessions are audited */}
      <div className="muted small">
        {usernames.length
          ? <>Sign in as {signInAs} with the range password (Type username, then Type password; Login on the range shows both).</>
          : "Sign in with the range password (Login on the range, or Type password)."}
        {" "}Each console session is recorded in the audit log.
      </div>
    </div>
  );
}
