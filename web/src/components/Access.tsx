// *hands out secret tunnel keys carefully* OwO web/src/components/Access.tsx is a range's ACCESS tab (WireGuard VPN)! ^w^
// It shows the VPN endpoint, the peers and whether they're connected, and lets operators open a peer's config (with
// a QR code for phones). Configs contain a PRIVATE key, so only operators may open them and every view is audited
// *wags tail*
import { useEffect, useState } from "react";
// API calls, hooks, UI pieces, and runJob (start a job and open its task view) UwU
import { ApiError, get, post } from "../api";
import { ago, useApi } from "../hooks";
import { ErrorBox, Loading, Modal } from "./ui";
import { runJob } from "./RangeActions";

// Remote access to a range: WireGuard on the range router. Peer configs carry a private key, so only operators
// can open them and every view is audited.

// Peer / WgStatus: the shapes of GET /api/ranges/<name>/wireguard >w<
interface Peer { name: string; address: string | null; ready: boolean; latest_handshake: number | null; connected: boolean }
interface WgStatus {
  configured: boolean;
  built?: boolean;
  port?: number;
  network?: string;
  reach?: string[];
  endpoint?: string | null;
  peers?: Peer[];
  router_reachable?: boolean;
}

// EXAMPLE: the YAML snippet shown when a range has no VPN yet (a template literal, so its lines are text) OwO
const EXAMPLE = `access:
  wireguard:
    peers: [alice, bob]      # one config per person or device
    reach: [dmz, lan]        # segments they may reach (default: all)
    # port: 51820            # UDP port on the router's LAN address
    # network: 10.250.0.0/24 # tunnel addresses
    # endpoint: vpn.example.org:51820   # if peers come in through a port forward`;

// AccessTab: the tab; refreshed every 15 s; `open` = the peer whose config dialog is open ^w^
export function AccessTab({ range, canOperate }: { range: string; canOperate: boolean }) {
  const { data, error, loading } = useApi<WgStatus>(`/api/ranges/${range}/wireguard`, 15000);
  const [open, setOpen] = useState<string | null>(null);
  if (loading && !data) return <Loading />;
  if (!data) return <div className="card-body"><ErrorBox error={error} /></div>;
  // No VPN in the spec: explain how to add one UwU
  if (!data.configured) {
    return (
      <div className="card-body">
        {/* What adding WireGuard does, then the example */}
        <p>
          This range has no remote access. Add a WireGuard section to the spec and apply it: VALOR sets up a tunnel on
          the range router and gives every peer its own config (with a QR code for phones).
        </p>
        <pre className="mono">{EXAMPLE}</pre>
      </div>
    );
  }
  // The VPN's facts, notices, and the peer table >w<
  return (
    <div className="card-body">
      {/* Endpoint (known after a build), tunnel network, reachable segments */}
      <dl className="kv">
        <dt>Endpoint</dt>
        <dd className="mono">{data.endpoint ?? <span className="muted">known after the next build</span>}</dd>
        <dt>Tunnel network</dt><dd className="mono">{data.network}</dd>
        <dt>Peers may reach</dt><dd>{(data.reach ?? []).join(", ")}</dd>
      </dl>
      {/* Not built yet, or the router didn't answer (status unknown) */}
      {!data.built && <div className="alert info">Apply the spec to create the keys and configure the router.</div>}
      {data.built && data.router_reachable === false &&
        <div className="alert warn">The router did not answer, so connection status is unknown.</div>}
      {/* One row per peer: name, tunnel address, status (connected / last seen / never), and a Config button for operators */}
      <div className="table-wrap" style={{ marginTop: 12 }}>
        <table>
          <thead><tr><th>Peer</th><th>Tunnel address</th><th>Status</th><th></th></tr></thead>
          <tbody>
            {(data.peers ?? []).map((peer) => (
              <tr key={peer.name}>
                <td className="mono">{peer.name}</td>
                <td className="mono">{peer.address ?? "—"}</td>
                <td>
                  {!peer.ready ? <span className="muted">not built yet</span>
                    : peer.connected ? <span className="badge ok">connected · {ago(peer.latest_handshake)}</span>
                    : peer.latest_handshake ? <span className="badge">last seen {ago(peer.latest_handshake)}</span>
                    : <span className="muted">never connected</span>}
                </td>
                <td style={{ textAlign: "right" }}>
                  {canOperate && peer.ready && data.endpoint &&
                    <button className="btn small" onClick={() => setOpen(peer.name)}>Config…</button>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {/* The config dialog */}
      {open && <PeerModal range={range} peer={open} onClose={() => setOpen(null)} />}
    </div>
  );
}

interface PeerConfig { peer: string; filename: string; config: string; qr_svg: string }

// PeerModal: one peer's config: QR code, the .conf text, download, and new keys OwO
function PeerModal({ range, peer, onClose }: { range: string; peer: string; onClose: () => void }) {
  const [cfg, setCfg] = useState<PeerConfig | null>(null);
  const [err, setErr] = useState<ApiError | null>(null);
  const [rotating, setRotating] = useState(false);
  // Load the config when opened (this is the audited view) ^w^
  useEffect(() => {
    get<PeerConfig>(`/api/ranges/${range}/wireguard/peers/${peer}`).then(setCfg).catch(setErr);
  }, [range, peer]);
  // download(): save the config as a .conf file (a temporary blob link, clicked, then freed) UwU
  const download = () => {
    if (!cfg) return;
    const url = URL.createObjectURL(new Blob([cfg.config], { type: "text/plain" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = cfg.filename;
    link.click();
    URL.revokeObjectURL(url);
  };
  // The dialog; footer: New keys, Download, Done >w<
  return (
    <Modal title={`WireGuard: ${peer}`} onClose={onClose} wide footer={
      <>
        {cfg && !rotating && <button className="btn" onClick={() => setRotating(true)}>New keys…</button>}
        {cfg && <button className="btn" onClick={download}>Download .conf</button>}
        <button className="btn primary" onClick={onClose}>Done</button>
      </>
    }>
      {/* Errors and loading */}
      <ErrorBox error={err} />
      {!cfg && !err && <Loading />}
      {/* The config: a warning, the QR code (an SVG made by VALOR's server) next to the text, and the new-keys confirmation */}
      {cfg && (
        <>
          {/* Keep it private: it holds the peer's private key */}
          <div className="muted">
            Import this config in the WireGuard app (scan the QR code on a phone). It contains {peer}'s private key: share
            it only with them. Opening it is recorded in the audit log.
          </div>
          {/* QR code and config side by side */}
          <div className="row" style={{ alignItems: "flex-start", gap: 16, marginTop: 12, flexWrap: "wrap" }}>
            <div className="qr" dangerouslySetInnerHTML={{ __html: cfg.qr_svg }} />
            <pre className="mono grow" style={{ margin: 0, minWidth: 260 }}>{cfg.config}</pre>
          </div>
          {/* "New keys" asks first: the old config stops working, and a short job updates the router */}
          {rotating && (
            <div className="alert warn" style={{ marginTop: 12 }}>
              New keys make this config stop working; {peer} will need the new one. The router is updated by a short job.
              <div className="row" style={{ marginTop: 8 }}>
                <button className="btn" onClick={() => setRotating(false)}>Cancel</button>
                <button className="btn primary"
                  onClick={() => runJob(post(`/api/ranges/${range}/wireguard/peers/${peer}/rotate`), setErr, onClose)}>
                  Create new keys
                </button>
              </div>
            </div>
          )}
        </>
      )}
    </Modal>
  );
}
