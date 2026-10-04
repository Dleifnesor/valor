import { DragEvent, useEffect, useRef, useState } from "react";
import { Algorithm, ChecksumEntry, WEAK, findChecksum, parseChecksums } from "../checksums";

// Drop (or pick) a checksum file such as SHA256SUMS, CHECKSUM or name.iso.sha256: the entry for `filename` fills
// in the checksum. Nothing is uploaded here - Proxmox / VALOR verify the ISO against the hash.

const MAX_BYTES = 1024 * 1024;

export function ChecksumDrop({ filename, onChecksum }: {
  filename: string;
  onChecksum: (hash: string, algorithm: Algorithm) => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  const [source, setSource] = useState<string | null>(null);
  const [entries, setEntries] = useState<ChecksumEntry[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [chosen, setChosen] = useState<ChecksumEntry | null>(null);

  // Match again whenever the ISO's name changes (the checksum file may be dropped first).
  useEffect(() => {
    if (!entries.length) return;
    const hit = findChecksum(entries, filename);
    setChosen(hit);
    if (hit) onChecksum(hit.hash, hit.algorithm);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [entries, filename]);

  const read = async (file: File | undefined) => {
    setError(null);
    if (!file) return;
    if (file.size > MAX_BYTES) {
      setError(`${file.name} is too large for a checksum file.`);
      return;
    }
    const found = parseChecksums(await file.text());
    setSource(file.name);
    setEntries(found);
    if (!found.length) setError(`No checksums found in ${file.name}.`);
  };
  const drop = (e: DragEvent) => {
    e.preventDefault();
    setOver(false);
    void read(e.dataTransfer.files?.[0]);
  };
  const pickByHand = (i: string) => {
    const e = entries[Number(i)];
    if (!e) return;
    setChosen(e);
    onChecksum(e.hash, e.algorithm);
  };

  return (
    <div>
      <div className={`dropzone${over ? " over" : ""}`} role="button" tabIndex={0}
        onClick={() => input.current?.click()}
        onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.current?.click(); } }}
        onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)} onDrop={drop}>
        Drop a checksum file here (SHA256SUMS, CHECKSUM, <span className="mono">.sha256</span>) or click to choose one
        <input ref={input} type="file" hidden accept=".sha256,.sha512,.sha1,.md5,.txt,.sum,.sums,text/plain,*"
          onChange={(e) => { void read(e.target.files?.[0]); e.target.value = ""; }} />
      </div>
      {error && <div className="small error-text" style={{ marginTop: 6 }}>{error}</div>}
      {source && entries.length > 0 && chosen && (
        <div className="small" style={{ marginTop: 6 }}>
          ✓ {chosen.algorithm.toUpperCase()} from <span className="mono">{source}</span>
          {chosen.name ? <> for <span className="mono">{chosen.name}</span></> : null}
          {chosen.name && filename && chosen.name.split("/").pop() !== filename && entries.length === 1 &&
            <span className="error-text"> - the file names differ; check it is the right list</span>}
          {WEAK.includes(chosen.algorithm) && <span className="muted"> ({chosen.algorithm.toUpperCase()} is weak; prefer SHA-256 if the publisher offers it)</span>}
        </div>
      )}
      {source && entries.length > 1 && !chosen && (
        <label className="field" style={{ marginTop: 6 }}>
          <span className="small">{filename ? `No entry for ${filename} in ${source}` : `${source} lists ${entries.length} files`}: pick one</span>
          <select defaultValue="" onChange={(e) => pickByHand(e.target.value)}>
            <option value="" disabled>Choose the ISO's entry…</option>
            {entries.map((e, i) => <option key={i} value={i}>{e.name ?? "(no name)"} · {e.algorithm}</option>)}
          </select>
        </label>
      )}
    </div>
  );
}
