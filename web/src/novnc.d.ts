// OwO *sniffs* web/src/novnc.d.ts is a TYPE declaration file: it contains no code that runs! ^w^
// The noVNC package (the VNC viewer used by the VM console) ships without TypeScript types, so this file describes
// the bits of it VALOR uses, so TypeScript can check our calls *wags tail*
// Minimal typings for noVNC (the package ships none).
declare module "@novnc/novnc" {
  // RFB is noVNC's VNC client. It's an EventTarget, so we can listen to "connect", "disconnect" and friends UwU
  export default class RFB extends EventTarget {
    // new RFB(element, websocketUrl, options): draws the remote screen inside `element`; options carry the VNC password
    // and the websocket subprotocols >w<
    constructor(target: HTMLElement, url: string, options?: { credentials?: { password?: string }; wsProtocols?: string[] });
    // Settings: scale the screen to fit, ask the server to resize, view-only mode, focus on click, and the background
    // color around the screen OwO
    scaleViewport: boolean;
    resizeSession: boolean;
    viewOnly: boolean;
    focusOnClick: boolean;
    background: string;
    // Methods: disconnect, send Ctrl+Alt+Del, send one key (by X11 keysym), send credentials later, and focus the
    // viewer ^w^
    disconnect(): void;
    sendCtrlAltDel(): void;
    sendKey(keysym: number, code: string | null, down?: boolean): void;
    sendCredentials(credentials: { password: string }): void;
    focus(): void;
  }
}
