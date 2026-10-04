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
  const t = tag.toLowerCase().replace("sha2-", "sha") as Algorithm;
  return byLen === t ? t : null;                  // the tag and the length must agree
}

export function parseChecksums(text: string): ChecksumEntry[] {
  const out: ChecksumEntry[] = [];
  let inSignature = false;
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    if (line.startsWith("-----BEGIN PGP SIGNATURE")) inSignature = true;
    if (line.startsWith("-----END PGP SIGNATURE")) { inSignature = false; continue; }
    if (!line || inSignature || line.startsWith("#") || line.startsWith("-----") || /^Hash:/i.test(line)) continue;
    let m = BSD.exec(line);
    if (m) {
      const a = algo(m[1], m[3]);
      if (a) out.push({ name: m[2].trim(), hash: m[3].toLowerCase(), algorithm: a });
      continue;
    }
    m = GNU.exec(line);
    if (m) {
      const a = algo(null, m[1]);
      if (a) out.push({ name: m[2].trim().replace(/^\.\//, ""), hash: m[1].toLowerCase(), algorithm: a });
      continue;
    }
    m = BARE.exec(line);
    if (m) {
      const a = algo(null, m[1]);
      if (a) out.push({ name: null, hash: m[1].toLowerCase(), algorithm: a });
    }
  }
  return out;
}

const base = (n: string) => n.split(/[\\/]/).pop() ?? n;

/** The entry for `filename`: exact name, then base name, then case-insensitive; a lone entry (e.g. x.iso.sha256)
 * matches anything. Prefers the strongest algorithm when a file lists several for the same name. */
export function findChecksum(entries: ChecksumEntry[], filename: string): ChecksumEntry | null {
  if (entries.length === 1) return entries[0];
  const strength = (e: ChecksumEntry) => -Object.values(BY_LENGTH).indexOf(e.algorithm);
  const pick = (hits: ChecksumEntry[]) => hits.sort((a, b) => strength(a) - strength(b))[0] ?? null;
  if (!filename) return null;
  return pick(entries.filter((e) => e.name === filename))
    ?? pick(entries.filter((e) => e.name && base(e.name) === base(filename)))
    ?? pick(entries.filter((e) => e.name && base(e.name).toLowerCase() === base(filename).toLowerCase()));
}
