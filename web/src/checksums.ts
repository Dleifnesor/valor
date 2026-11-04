// Checksum files as distributions publish them: GNU coreutils ("<hash>  name" / "<hash> *name"), BSD/OpenSSL
// ("SHA256 (name) = <hash>", e.g. Rocky's CHECKSUM), PGP clear-signed lists, and single-hash files (x.iso.sha256).
// Parsing happens in the browser; Proxmox (downloads) and VALOR (uploads) verify the ISO against the result.

export type Algorithm = "md5" | "sha1" | "sha224" | "sha256" | "sha384" | "sha512";
export interface ChecksumEntry { name: string | null; hash: string; algorithm: Algorithm }

const BY_LENGTH: Record<number, Algorithm> = { 32: "md5", 40: "sha1", 56: "sha224", 64: "sha256", 96: "sha384", 128: "sha512" };
const BSD = /^(MD5|SHA1|SHA224|SHA256|SHA384|SHA512|SHA2-256|SHA2-512)\s*\((.+)\)\s*=\s*([0-9a-fA-F]+)$/;
const GNU = /^\\?([0-9a-fA-F]+)\s+[ *]?(.+)$/;
const BARE = /^([0-9a-fA-F]+)$/;
export const WEAK: Algorithm[] = ["md5", "sha1"];

function algo(tag: string | null, hash: string): Algorithm | null {
  const byLen = BY_LENGTH[hash.length] ?? null;
  if (!tag) return byLen;
  const named = tag.toLowerCase().replace("sha2-", "sha") as Algorithm;
  return byLen === named ? named : null;          // the tag and the length must agree
}

export function parseChecksums(text: string): ChecksumEntry[] {
  const out: ChecksumEntry[] = [];
  let inSignature = false;
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    if (line.startsWith("-----BEGIN PGP SIGNATURE")) inSignature = true;
    if (line.startsWith("-----END PGP SIGNATURE")) { inSignature = false; continue; }
    if (!line || inSignature || line.startsWith("#") || line.startsWith("-----") || /^Hash:/i.test(line)) continue;
    let match = BSD.exec(line);
    if (match) {
      const found = algo(match[1], match[3]);
      if (found) out.push({ name: match[2].trim(), hash: match[3].toLowerCase(), algorithm: found });
      continue;
    }
    match = GNU.exec(line);
    if (match) {
      const found = algo(null, match[1]);
      if (found) out.push({ name: match[2].trim().replace(/^\.\//, ""), hash: match[1].toLowerCase(), algorithm: found });
      continue;
    }
    match = BARE.exec(line);
    if (match) {
      const found = algo(null, match[1]);
      if (found) out.push({ name: null, hash: match[1].toLowerCase(), algorithm: found });
    }
  }
  return out;
}

const base = (path: string) => path.split(/[\\/]/).pop() ?? path;

/** The entry for `filename`: exact name, then base name, then case-insensitive; a lone entry (e.g. x.iso.sha256)
 * matches anything. Prefers the strongest algorithm when a file lists several for the same name. */
export function findChecksum(entries: ChecksumEntry[], filename: string): ChecksumEntry | null {
  if (entries.length === 1) return entries[0];
  const strength = (entry: ChecksumEntry) => -Object.values(BY_LENGTH).indexOf(entry.algorithm);
  const pick = (hits: ChecksumEntry[]) => hits.sort((a, b) => strength(a) - strength(b))[0] ?? null;
  if (!filename) return null;
  return pick(entries.filter((e) => e.name === filename))
    ?? pick(entries.filter((e) => e.name && base(e.name) === base(filename)))
    ?? pick(entries.filter((e) => e.name && base(e.name).toLowerCase() === base(filename).toLowerCase()));
}
