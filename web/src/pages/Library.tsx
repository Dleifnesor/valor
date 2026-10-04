import { FormEvent, useRef, useState } from "react";
import { ApiError, csrfToken, del, post } from "../api";
import { useApi, when } from "../hooks";
import { Card, ErrorBox, Loading } from "../components/ui";

interface Iso { volid: string; storage: string; name: string; size: number; ctime: number; writable: boolean }
interface Entry { id: string; title: string; kind: string; family?: string; install?: string; signed: boolean; checksum: boolean; note: string; present: string[] }
interface Task { upid: string; kind: string; filename: string; user: string; started: number; running: boolean; ok: boolean; exitstatus?: string; progress: number | null; last: string; verified?: string }

const gib = (b: number) => (b >= 2 ** 30 ? `${(b / 2 ** 30).toFixed(1)} GiB` : `${(b / 2 ** 20).toFixed(0)} MiB`);

export function Library({ canAdd, canDelete }: { canAdd: boolean; canDelete: boolean }) {
  const lib = useApi<{ target: string; storages: string[]; isos: Iso[]; error: string | null }>("/api/isos", 30000);
  const cat = useApi<{ entries: Entry[] }>("/api/isos/catalog", 30000);
  const tasks = useApi<{ tasks: Task[] }>("/api/isos/tasks", 4000);
  const [err, setErr] = useState<ApiError | string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const refresh = () => { lib.reload(); cat.reload(); tasks.reload(); };
  const fetchEntry = async (id: string) => {
    setBusy(id); setErr(null);
    try { await post("/api/isos/download", { catalog: id }); refresh(); } catch (e) { setErr(e as ApiError); } finally { setBusy(null); }
  };

  if (lib.loading && !lib.data) return <Loading />;
  const running = tasks.data?.tasks.filter((t) => t.running) ?? [];
  const recent = tasks.data?.tasks.filter((t) => !t.running).slice(0, 5) ?? [];
  return (
    <>
      <ErrorBox error={err ?? lib.error} />
      {lib.data?.error && <div className="alert warn">ISO storage unavailable: {lib.data.error}</div>}
      {(running.length > 0 || recent.length > 0) && (
        <Card title="Downloads and uploads">
          <div className="stack">
            {[...running, ...recent].map((t) => (
              <div key={t.upid}>
                <div className="row">
                  <b className="mono">{t.filename}</b>
                  <span className={`badge ${t.running ? "info" : t.ok ? "ok" : "bad"}`}>{t.running ? t.kind + "ing" : t.ok ? "done" : t.exitstatus}</span>
                  <span className="muted small">{t.user} · {when(t.started)} · {t.verified}</span>
                </div>
                {t.running && (
                  <div className="progress"><div style={{ width: `${t.progress ?? 2}%` }} /></div>
                )}
                {!t.ok && !t.running && <div className="small muted">{t.last}</div>}
              </div>
            ))}
          </div>
        </Card>
      )}
      <Card title="Catalog" actions={<span className="muted small">downloads go to <b>{lib.data?.target || "–"}</b></span>}>
        <div className="grid cols-2">
          {cat.data?.entries.map((e) => (
            <div key={e.id} className="card" style={{ padding: 14 }}>
              <div className="row" style={{ gap: 6 }}>
                <b className="grow">{e.title}</b>
                <span className={`badge ${e.kind === "windows" ? "info" : ""}`}>{e.kind}</span>
              </div>
              <div className="small muted" style={{ margin: "6px 0" }}>
                {e.signed ? "Signed checksum list (key pinned)" : e.checksum ? "Checksum over HTTPS" : "HTTPS only - " + e.note}
                {e.install && <> · unattended install: {e.install}</>}
              </div>
              <div className="row">
                {e.present.length ? <span className="badge ok">on the cluster: {e.present.join(", ")}</span>
                  : canAdd ? <button className="btn small primary" disabled={busy === e.id} onClick={() => fetchEntry(e.id)}>
                      {busy === e.id ? "Starting…" : "Download"}</button>
                  : <span className="muted small">not downloaded</span>}
              </div>
            </div>
          ))}
        </div>
      </Card>
      {canAdd && (
        <div className="grid cols-2">
          <UrlDownload onDone={refresh} onError={setErr} />
          <Upload onDone={refresh} onError={setErr} />
        </div>
      )}
      <Card title="ISOs on the cluster" bodyClass="">
        {!lib.data?.isos.length ? <div className="empty">No ISOs yet.</div> : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>File</th><th>Storage</th><th>Size</th><th>Added</th><th></th></tr></thead>
              <tbody>
                {lib.data.isos.map((i) => (
                  <tr key={i.volid}>
                    <td className="mono">{i.name}</td>
                    <td>{i.storage}</td>
                    <td className="nowrap">{gib(i.size)}</td>
                    <td className="nowrap">{when(i.ctime)}</td>
                    <td className="right">
                      {canDelete && i.writable && (
                        <button className="btn small danger" onClick={async () => {
                          if (!confirm(`Delete ${i.name} from ${i.storage}?`)) return;
                          try { await del(`/api/isos/${encodeURIComponent(i.volid)}`); refresh(); } catch (e) { setErr(e as ApiError); }
                        }}>Delete</button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </>
  );
}

function UrlDownload({ onDone, onError }: { onDone: () => void; onError: (e: ApiError) => void }) {
  const [url, setUrl] = useState("");
  const [filename, setFilename] = useState("");
  const [checksum, setChecksum] = useState("");
  const [algorithm, setAlgorithm] = useState("sha256");
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    try {
      await post("/api/isos/download", { url, filename, ...(checksum ? { checksum, algorithm } : {}) });
      setUrl(""); setFilename(""); setChecksum(""); onDone();
    } catch (x) { onError(x as ApiError); }
  };
  return (
    <Card title="Download from a URL">
      <form className="stack" onSubmit={submit}>
        <label className="field">URL (https)<input value={url} onChange={(e) => {
          setUrl(e.target.value);
          if (!filename) setFilename((e.target.value.split("?")[0].split("/").pop() || "").replace(/[^A-Za-z0-9._+=-]/g, ""));
        }} placeholder="https://…/installer.iso" required /></label>
        <label className="field">File name<input value={filename} onChange={(e) => setFilename(e.target.value)} placeholder="name.iso" required /></label>
        <div className="row">
          <label className="field grow">Checksum <span className="hint">optional, strongly recommended</span>
            <input value={checksum} onChange={(e) => setChecksum(e.target.value.trim())} className="mono" />
          </label>
          <label className="field" style={{ width: 120 }}>Algorithm
            <select value={algorithm} onChange={(e) => setAlgorithm(e.target.value)}>
              <option>sha256</option><option>sha512</option><option>sha1</option>
            </select>
          </label>
        </div>
        <div className="row end"><button className="btn primary" disabled={!url || !filename}>Download to the cluster</button></div>
      </form>
    </Card>
  );
}

function Upload({ onDone, onError }: { onDone: () => void; onError: (e: ApiError | string) => void }) {
  const file = useRef<HTMLInputElement>(null);
  const [checksum, setChecksum] = useState("");
  const [pct, setPct] = useState<number | null>(null);
  const [phase, setPhase] = useState("");
  const submit = (e: FormEvent) => {
    e.preventDefault();
    const f = file.current?.files?.[0];
    if (!f) return;
    const name = f.name.replace(/[^A-Za-z0-9._+=-]/g, "_");
    const q = new URLSearchParams({ filename: name, ...(checksum ? { checksum, algorithm: checksum.length > 64 ? "sha512" : "sha256" } : {}) });
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `/api/isos/upload?${q}`);
    xhr.setRequestHeader("Content-Type", "application/octet-stream");
    xhr.setRequestHeader("X-CSRF-Token", csrfToken());
    xhr.upload.onprogress = (ev) => { if (ev.lengthComputable) setPct((ev.loaded / ev.total) * 100); };
    xhr.upload.onload = () => setPhase("VALOR is copying the file to the cluster…");
    xhr.onload = () => {
      setPct(null); setPhase("");
      if (xhr.status === 200) { onDone(); if (file.current) file.current.value = ""; }
      else { try { onError(JSON.parse(xhr.responseText).message); } catch { onError(`Upload failed (HTTP ${xhr.status})`); } }
    };
    xhr.onerror = () => { setPct(null); setPhase(""); onError("Upload failed (network)."); };
    setPct(0); setPhase("Uploading to VALOR…");
    xhr.send(f);
  };
  return (
    <Card title="Upload an ISO">
      <form className="stack" onSubmit={submit}>
        <input type="file" accept=".iso" ref={file} required />
        <label className="field">SHA-256 or SHA-512 <span className="hint">optional; VALOR refuses the file if it does not match</span>
          <input value={checksum} onChange={(e) => setChecksum(e.target.value.trim())} className="mono" />
        </label>
        {pct !== null && <><div className="progress"><div style={{ width: `${pct}%` }} /></div><div className="small muted">{phase} {pct < 100 ? `${pct.toFixed(0)}%` : ""}</div></>}
        <div className="row end"><button className="btn primary" disabled={pct !== null}>Upload</button></div>
      </form>
    </Card>
  );
}
