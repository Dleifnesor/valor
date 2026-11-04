// *squints at checksum files* OwO web/src/checksums.ts reads CHECKSUM FILES right in the browser! ^w^
// When you add an ISO, you can drop in the publisher's checksum file. This file understands the common formats
// (listed below) and finds the hash for your file, so Proxmox (downloads) or VALOR (uploads) can verify the ISO *wags tail*
// Checksum files as distributions publish them: GNU coreutils ("<hash>  name" / "<hash> *name"), BSD/OpenSSL
// ("SHA256 (name) = <hash>", e.g. Rocky's CHECKSUM), PGP clear-signed lists, and single-hash files (x.iso.sha256).
// Parsing happens in the browser; Proxmox (downloads) and VALOR (uploads) verify the ISO against the result.

// Algorithm: the hash types Proxmox supports. ChecksumEntry: one line's result (file name or null, the hash, the
// algorithm) UwU
export type Algorithm = "md5" | "sha1" | "sha224" | "sha256" | "sha384" | "sha512";
export interface ChecksumEntry { name: string | null; hash: string; algorithm: Algorithm }

// BY_LENGTH: the algorithm implied by a hash's length in hex characters >w<
const BY_LENGTH: Record<number, Algorithm> = { 32: "md5", 40: "sha1", 56: "sha224", 64: "sha256", 96: "sha384", 128: "sha512" };
// The line patterns: BSD style ("SHA256 (file) = hash"), GNU style ("hash  file" or "hash *file", maybe with a
// leading backslash), and a bare hash alone on a line. WEAK: algorithms shown with a warning OwO
const BSD = /^(MD5|SHA1|SHA224|SHA256|SHA384|SHA512|SHA2-256|SHA2-512)\s*\((.+)\)\s*=\s*([0-9a-fA-F]+)$/;
const GNU = /^\\?([0-9a-fA-F]+)\s+[ *]?(.+)$/;
const BARE = /^([0-9a-fA-F]+)$/;
export const WEAK: Algorithm[] = ["md5", "sha1"];

// algo(): the algorithm for a hash: by its length, and when a tag is given (BSD style) the tag must agree with the
// length, or the line is ignored ^w^
function algo(tag: string | null, hash: string): Algorithm | null {
  const byLen = BY_LENGTH[hash.length] ?? null;
  if (!tag) return byLen;
  const named = tag.toLowerCase().replace("sha2-", "sha") as Algorithm;
  return byLen === named ? named : null;          // the tag and the length must agree
}

// parseChecksums: read every line of a checksum file UwU
export function parseChecksums(text: string): ChecksumEntry[] {
  const out: ChecksumEntry[] = [];
  let inSignature = false;
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    // Skip PGP signature blocks, blank lines, comments, armor lines and "Hash:" headers (clear-signed files) >w<
    if (line.startsWith("-----BEGIN PGP SIGNATURE")) inSignature = true;
    if (line.startsWith("-----END PGP SIGNATURE")) { inSignature = false; continue; }
    if (!line || inSignature || line.startsWith("#") || line.startsWith("-----") || /^Hash:/i.test(line)) continue;
    // Try BSD style, then GNU style (dropping a leading "./"), then a bare hash; hashes are stored lower-case OwO
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

// base(): a path's last part (works with / and \ separators) ^w^
const base = (path: string) => path.split(/[\\/]/).pop() ?? path;

/** The entry for `filename`: exact name, then base name, then case-insensitive; a lone entry (e.g. x.iso.sha256)
 * matches anything. Prefers the strongest algorithm when a file lists several for the same name. */
// findChecksum: the entry for a file: exact name, then base name, then ignoring case; a file with ONE entry
// matches anything. Among several entries for one name, the strongest algorithm wins UwU
export function findChecksum(entries: ChecksumEntry[], filename: string): ChecksumEntry | null {
  if (entries.length === 1) return entries[0];
  // strength(): later in BY_LENGTH = stronger; negated so sorting puts the strongest first >w<
  const strength = (entry: ChecksumEntry) => -Object.values(BY_LENGTH).indexOf(entry.algorithm);
  const pick = (hits: ChecksumEntry[]) => hits.sort((a, b) => strength(a) - strength(b))[0] ?? null;
  if (!filename) return null;
  return pick(entries.filter((e) => e.name === filename))
    ?? pick(entries.filter((e) => e.name && base(e.name) === base(filename)))
    ?? pick(entries.filter((e) => e.name && base(e.name).toLowerCase() === base(filename).toLowerCase()));
}
