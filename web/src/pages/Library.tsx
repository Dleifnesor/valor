// *sorts the shiny disc collection* OwO web/src/pages/Library.tsx is the ISO LIBRARY page! ^w^
// Images on the cluster's ISO storages, one-click downloads from VALOR's catalog (checksums checked), your own URL
// downloads, and uploads, with optional checksums *wags tail*
import { FormEvent, useRef, useState } from "react";
// API calls (and the CSRF token for raw uploads), hooks, the checksum drop zone, and UI pieces UwU
import { ApiError, csrfToken, del, post } from "../api";
import { useApi, when } from "../hooks";
import { ChecksumDrop } from "../components/ChecksumDrop";
import { Card, ErrorBox, Loading } from "../components/ui";

// The ISO library: images on the cluster's ISO storages, downloads from VALOR's catalog (checksums checked), and
// your own uploads or URL downloads with an optional checksum.

// Iso / Entry / Task: an ISO, a catalog entry, and a running or recent download/upload >w<
interface Iso { volid: string; storage: string; name: string; size: number; ctime: number; writable: boolean }
interface Entry { id: string; title: string; kind: string; family?: string; install?: string; signed: boolean; checksum: boolean; note: string; present: string[] }
interface Task { upid: string; kind: string; filename: string; user: string; started: number; running: boolean; ok: boolean; exitstatus?: string; progress: number | null; last: string; verified?: string }

// gib(): bytes as GiB or MiB OwO
const gib = (b: number) => (b >= 2 ** 30 ? `${(b / 2 ** 30).toFixed(1)} GiB` : `${(b / 2 ** 20).toFixed(0)} MiB`);

// Library: canAdd = operators may download/upload; canDelete = admins may delete ^w^
export function Library({ canAdd, canDelete }: { canAdd: boolean; canDelete: boolean }) {
  // The library, the catalog (every 30 s) and the tasks (every 4 s, for progress) UwU
  const lib = useApi<{ target: string; storages: string[]; isos: Iso[]; error: string | null }>("/api/isos", 30000);
  const cat = useApi<{ entries: Entry[] }>("/api/isos/catalog", 30000);
  const tasks = useApi<{ tasks: Task[] }>("/api/isos/tasks", 4000);
  const [err, setErr] = useState<ApiError | string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  // refresh(): reload all three. fetchEntry(): start a catalog download >w<
  const refresh = () => { lib.reload(); cat.reload(); tasks.reload(); };
  const fetchEntry = async (id: string) => {
    setBusy(id); setErr(null);
    try { await post("/api/isos/download", { catalog: id }); refresh(); } catch (e) { setErr(e as ApiError); } finally { setBusy(null); }
  };

  if (lib.loading && !lib.data) return <Loading />;
  // Running tasks, and the 5 most recent finished ones OwO
  const running = tasks.data?.tasks.filter((task) => task.running) ?? [];
  const recent = tasks.data?.tasks.filter((task) => !task.running).slice(0, 5) ?? [];
  // The page ^w^
  return (
    <>
      {/* Errors and storage problems */}
      <ErrorBox error={err ?? lib.error} />
      {lib.data?.error && <div className="alert warn">ISO storage unavailable: {lib.data.error}</div>}
      {/* Downloads and uploads in progress (with progress bars) and the last few results */}
      {(running.length > 0 || recent.length > 0) && (
        <Card title="Downloads and uploads">
          {/* One block per task */}
          <div className="stack">
            {[...running, ...recent].map((task) => (
              <div key={task.upid}>
                {/* File, state, who/when/how verified; a progress bar while running; the last log line if it failed */}
                <div className="row">
                  <b className="mono">{task.filename}</b>
                  <span className={`badge ${task.running ? "info" : task.ok ? "ok" : "bad"}`}>{task.running ? task.kind + "ing" : task.ok ? "done" : task.exitstatus}</span>
                  <span className="muted small">{task.user} · {when(task.started)} · {task.verified}</span>
                </div>
                {task.running && (
                  <div className="progress"><div style={{ width: `${task.progress ?? 2}%` }} /></div>
                )}
                {!task.ok && !task.running && <div className="small muted">{task.last}</div>}
              </div>
            ))}
          </div>
        </Card>
      )}
      {/* The catalog: one card per entry */}
      <Card title="Catalog" actions={<span className="muted small">downloads go to <b>{lib.data?.target || "–"}</b></span>}>
        <div className="grid cols-2">
          {cat.data?.entries.map((entry) => (
            <div key={entry.id} className="card" style={{ padding: 14 }}>
              {/* Title and kind */}
              <div className="row" style={{ gap: 6 }}>
                <b className="grow">{entry.title}</b>
                <span className={`badge ${entry.kind === "windows" ? "info" : ""}`}>{entry.kind}</span>
              </div>
              {/* How it's verified, and whether it installs unattended */}
              <div className="small muted" style={{ margin: "6px 0" }}>
                {entry.signed ? "Signed checksum list (key pinned)" : entry.checksum ? "Checksum over HTTPS" : "HTTPS only - " + entry.note}
                {entry.install && <> · unattended install: {entry.install}</>}
              </div>
              {/* On the cluster already, a Download button, or "not downloaded" */}
              <div className="row">
                {entry.present.length ? <span className="badge ok">on the cluster: {entry.present.join(", ")}</span>
                  : canAdd ? <button className="btn small primary" disabled={busy === entry.id} onClick={() => fetchEntry(entry.id)}>
                      {busy === entry.id ? "Starting…" : "Download"}</button>
                  : <span className="muted small">not downloaded</span>}
              </div>
            </div>
          ))}
        </div>
      </Card>
      {/* Operators: download from a URL, and upload */}
      {canAdd && (
        <div className="grid cols-2">
          <UrlDownload onDone={refresh} onError={setErr} />
          <Upload onDone={refresh} onError={setErr} />
        </div>
      )}
      {/* Every ISO on the cluster: file, storage, size, added, and Delete (admins, VALOR's own storage only) */}
      <Card title="ISOs on the cluster" bodyClass="">
        {!lib.data?.isos.length ? <div className="empty">No ISOs yet.</div> : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>File</th><th>Storage</th><th>Size</th><th>Added</th><th></th></tr></thead>
              <tbody>
                {lib.data.isos.map((iso) => (
                  <tr key={iso.volid}>
                    {/* The row's cells */}
                    <td className="mono">{iso.name}</td>
                    <td>{iso.storage}</td>
                    <td className="nowrap">{gib(iso.size)}</td>
                    <td className="nowrap">{when(iso.ctime)}</td>
                    <td className="right">
                      {canDelete && iso.writable && (
                        <button className="btn small danger" onClick={async () => {
                          if (!confirm(`Delete ${iso.name} from ${iso.storage}?`)) return;
                          try { await del(`/api/isos/${encodeURIComponent(iso.volid)}`); refresh(); } catch (e) { setErr(e as ApiError); }
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

// ALGORITHMS: hash choices. safeName(): a file name with unsafe characters replaced UwU
const ALGORITHMS = ["sha256", "sha512", "sha384", "sha224", "sha1", "md5"];
const safeName = (name: string) => name.replace(/[^A-Za-z0-9._+=-]/g, "_");

// UrlDownload: download an ISO from an https URL (the file name is guessed from the URL) >w<
function UrlDownload({ onDone, onError }: { onDone: () => void; onError: (e: ApiError) => void }) {
  const [url, setUrl] = useState("");
  const [filename, setFilename] = useState("");
  const [checksum, setChecksum] = useState("");
  const [algorithm, setAlgorithm] = useState("sha256");
  // submit(): start the download (with the checksum when given) and clear the form OwO
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    try {
      await post("/api/isos/download", { url, filename, ...(checksum ? { checksum, algorithm } : {}) });
      setUrl(""); setFilename(""); setChecksum(""); onDone();
    } catch (x) { onError(x as ApiError); }
  };
  return (
    <Card title="Download from a URL">
      {/* URL, file name, checksum and algorithm, a checksum-file drop zone, and the button */}
      <form className="stack" onSubmit={submit}>
        <label className="field">URL (https)<input name="url" value={url} onChange={(e) => {
          setUrl(e.target.value);
          if (!filename) setFilename((e.target.value.split("?")[0].split("/").pop() || "").replace(/[^A-Za-z0-9._+=-]/g, ""));
        }} placeholder="https://…/installer.iso" required /></label>
        <label className="field">File name<input name="filename" value={filename} onChange={(e) => setFilename(e.target.value)} placeholder="name.iso" required /></label>
        <div className="row">
          <label className="field grow">Checksum <span className="hint">optional, strongly recommended</span>
            <input name="checksum" value={checksum} onChange={(e) => setChecksum(e.target.value.trim())} className="mono" />
          </label>
          <label className="field" style={{ width: 120 }}>Algorithm
            <select name="algorithm" value={algorithm} onChange={(e) => setAlgorithm(e.target.value)}>
              {ALGORITHMS.map((name) => <option key={name}>{name}</option>)}
            </select>
          </label>
        </div>
        <ChecksumDrop filename={filename} onChecksum={(hash, algo) => { setChecksum(hash); setAlgorithm(algo); }} />
        <div className="row end"><button className="btn primary" disabled={!url || !filename}>Download to the cluster</button></div>
      </form>
    </Card>
  );
}

// Upload: upload an ISO from this computer ^w^
function Upload({ onDone, onError }: { onDone: () => void; onError: (e: ApiError | string) => void }) {
  const file = useRef<HTMLInputElement>(null);
  const [name, setName] = useState("");
  const [checksum, setChecksum] = useState("");
  const [algorithm, setAlgorithm] = useState("");
  const [pct, setPct] = useState<number | null>(null);
  const [phase, setPhase] = useState("");
  // submit(): send the raw file with XMLHttpRequest (for upload progress), with the CSRF token and the checksum as URL
  // parameters; then VALOR copies it to the cluster UwU
  const submit = (e: FormEvent) => {
    e.preventDefault();
    const chosen = file.current?.files?.[0];
    if (!chosen) return;
    const name = safeName(chosen.name);
    const query = new URLSearchParams({ filename: name, ...(checksum ? { checksum, ...(algorithm ? { algorithm } : {}) } : {}) });
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `/api/isos/upload?${query}`);
    xhr.setRequestHeader("Content-Type", "application/octet-stream");
    xhr.setRequestHeader("X-CSRF-Token", csrfToken());
    xhr.upload.onprogress = (event) => { if (event.lengthComputable) setPct((event.loaded / event.total) * 100); };
    xhr.upload.onload = () => setPhase("VALOR is copying the file to the cluster…");
    xhr.onload = () => {
      setPct(null); setPhase("");
      if (xhr.status === 200) { onDone(); if (file.current) file.current.value = ""; setName(""); setChecksum(""); setAlgorithm(""); }
      else { try { onError(JSON.parse(xhr.responseText).message); } catch { onError(`Upload failed (HTTP ${xhr.status})`); } }
    };
    xhr.onerror = () => { setPct(null); setPhase(""); onError("Upload failed (network)."); };
    setPct(0); setPhase("Uploading to VALOR…");
    xhr.send(chosen);
  };
  return (
    <Card title="Upload an ISO">
      {/* File, checksum (or a checksum file), progress, and the button */}
      <form className="stack" onSubmit={submit}>
        <input name="iso-file" type="file" accept=".iso" ref={file} required onChange={(e) => setName(safeName(e.target.files?.[0]?.name ?? ""))} />
        <label className="field">Checksum <span className="hint">optional; VALOR refuses the file if it does not match (algorithm from its length)</span>
          <input name="checksum" value={checksum} onChange={(e) => { setChecksum(e.target.value.trim()); setAlgorithm(""); }} className="mono" />
        </label>
        <ChecksumDrop filename={name} onChecksum={(hash, algo) => { setChecksum(hash); setAlgorithm(algo); }} />
        {pct !== null && <><div className="progress"><div style={{ width: `${pct}%` }} /></div><div className="small muted">{phase} {pct < 100 ? `${pct.toFixed(0)}%` : ""}</div></>}
        <div className="row end"><button className="btn primary" disabled={pct !== null}>Upload</button></div>
      </form>
    </Card>
  );
}
