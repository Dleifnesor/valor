import { useEffect, useState } from "react";
import { ApiError, get, post } from "../api";
import { ago, useApi } from "../hooks";
import { ErrorBox, Loading, Modal } from "./ui";
import { runJob } from "./RangeActions";

// Remote access to a range: WireGuard on the range router. Peer configs carry a private key, so only operators
// can open them and every view is audited.

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

const EXAMPLE = `access:
  wireguard:
    peers: [alice, bob]      # one config per person or device
    reach: [dmz, lan]        # segments they may reach (default: all)
    # port: 51820            # UDP port on the router's LAN address
    # network: 10.250.0.0/24 # tunnel addresses
    # endpoint: vpn.example.org:51820   # if peers come in through a port forward`;

export function AccessTab({ range, canOperate }: { range: string; canOperate: boolean }) {
  const { data, error, loading } = useApi<WgStatus>(`/api/ranges/${range}/wireguard`, 15000);
  const [open, setOpen] = useState<string | null>(null);
  if (loading && !data) return <Loading />;
  if (!data) return <div className="card-body"><ErrorBox error={error} /></div>;
  if (!data.configured) {
    return (
      <div className="card-body">
        <p>
          This range has no remote access. Add a WireGuard section to the spec and apply it: VALOR sets up a tunnel on
          the range router and gives every peer its own config (with a QR code for phones).
        </p>
        <pre className="mono">{EXAMPLE}</pre>
      </div>
    );
  }
  return (
    <div className="card-body">
      <dl className="kv">
        <dt>Endpoint</dt>
        <dd className="mono">{data.endpoint ?? <span className="muted">known after the next build</span>}</dd>
        <dt>Tunnel network</dt><dd className="mono">{data.network}</dd>
        <dt>Peers may reach</dt><dd>{(data.reach ?? []).join(", ")}</dd>
      </dl>
      {!data.built && <div className="alert info">Apply the spec to create the keys and configure the router.</div>}
      {data.built && data.router_reachable === false &&
        <div className="alert warn">The router did not answer, so connection status is unknown.</div>}
      <div className="table-wrap" style={{ marginTop: 12 }}>
        <table>
          <thead><tr><th>Peer</th><th>Tunnel address</th><th>Status</th><th></th></tr></thead>
          <tbody>
            {(data.peers ?? []).map((p) => (
              <tr key={p.name}>
                <td className="mono">{p.name}</td>
                <td className="mono">{p.address ?? "—"}</td>
                <td>
                  {!p.ready ? <span className="muted">not built yet</span>
                    : p.connected ? <span className="badge ok">connected · {ago(p.latest_handshake)}</span>
                    : p.latest_handshake ? <span className="badge">last seen {ago(p.latest_handshake)}</span>
                    : <span className="muted">never connected</span>}
                </td>
                <td style={{ textAlign: "right" }}>
                  {canOperate && p.ready && data.endpoint &&
                    <button className="btn small" onClick={() => setOpen(p.name)}>Config…</button>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {open && <PeerModal range={range} peer={open} onClose={() => setOpen(null)} />}
    </div>
  );
}

interface PeerConfig { peer: string; filename: string; config: string; qr_svg: string }

function PeerModal({ range, peer, onClose }: { range: string; peer: string; onClose: () => void }) {
  const [cfg, setCfg] = useState<PeerConfig | null>(null);
  const [err, setErr] = useState<ApiError | null>(null);
  const [rotating, setRotating] = useState(false);
  useEffect(() => {
    get<PeerConfig>(`/api/ranges/${range}/wireguard/peers/${peer}`).then(setCfg).catch(setErr);
  }, [range, peer]);
  const download = () => {
    if (!cfg) return;
    const url = URL.createObjectURL(new Blob([cfg.config], { type: "text/plain" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = cfg.filename;
    a.click();
    URL.revokeObjectURL(url);
  };
  return (
    <Modal title={`WireGuard: ${peer}`} onClose={onClose} wide footer={
      <>
        {cfg && !rotating && <button className="btn" onClick={() => setRotating(true)}>New keys…</button>}
        {cfg && <button className="btn" onClick={download}>Download .conf</button>}
        <button className="btn primary" onClick={onClose}>Done</button>
      </>
    }>
      <ErrorBox error={err} />
      {!cfg && !err && <Loading />}
      {cfg && (
        <>
          <div className="muted">
            Import this config in the WireGuard app (scan the QR code on a phone). It contains {peer}'s private key: share
            it only with them. Opening it is recorded in the audit log.
          </div>
          <div className="row" style={{ alignItems: "flex-start", gap: 16, marginTop: 12, flexWrap: "wrap" }}>
            <div className="qr" dangerouslySetInnerHTML={{ __html: cfg.qr_svg }} />
            <pre className="mono grow" style={{ margin: 0, minWidth: 260 }}>{cfg.config}</pre>
          </div>
          {rotating && (
            <div className="alert warn" style={{ marginTop: 12 }}>
              New keys make this config stop working; {peer} will need the new one. The router is updated by a short job.
              <div className="row" style={{ marginTop: 8 }}>
                <button className="btn" onClick={() => setRotating(false)}>Cancel</button>
                <button className="btn primary"
                  onClick={() => runJob(post(`/api/ranges/${range}/wireguard/peers/${peer}/rotate`), setErr)}>
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
