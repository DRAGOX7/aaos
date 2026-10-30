/** Match only pipeline-supplied passages, never the generated answer.
 * Offsets returned here index the unchanged original JS string (UTF-16).
 * Pipeline offsets count Unicode code points, so convert at that boundary.
 */
export type Match = { start: number; end: number; method: "offset" | "normalized" };
type Normalized = { text: string; starts: number[]; ends: number[] };

function normalize(source: string): Normalized {
  const units = Array.from(new Intl.Segmenter(undefined, { granularity: "grapheme" }).segment(source));
  const normalized: Normalized = { text: "", starts: [], ends: [] };
  for (let i = 0; i < units.length; i++) {
    const { segment, index } = units[i];
    // PDF word-\nwrapping: remove only a hyphen joining letters across a line.
    if (/^[-\u00ad]$/.test(segment) && i > 0 && /\p{L}$/u.test(units[i - 1].segment)) {
      let next = i + 1;
      let wrap = "";
      while (next < units.length && /^\s+$/.test(units[next].segment)) wrap += units[next++].segment;
      if (/[\r\n]/.test(wrap) && next < units.length && /^\p{L}/u.test(units[next].segment)) {
        let left = i - 1;
        let right = next;
        while (left >= 0 && /^\p{L}[\p{M}]*$/u.test(units[left].segment)) left--;
        while (right < units.length && /^\p{L}[\p{M}]*$/u.test(units[right].segment)) right++;
        // Do not turn a - b across a line break into the variable ab.
        const prefix = i - 1 - left;
        const suffix = right - next;
        if (prefix >= 2 && suffix >= 2 && prefix + suffix >= 6) {
          i = next - 1;
          continue;
        }
      }
    }
    for (const character of segment.normalize("NFC")) {
      // Preserve math signs, decimal punctuation, subscripts, and Arabic marks.
      const quotes: Record<string, string> = { "“": '"', "”": '"', "‘": "'", "’": "'" };
      const value = /\s/u.test(character) ? " " : (quotes[character] ?? character);
      if (value === " " && (!normalized.text || normalized.text.endsWith(" "))) continue;
      normalized.text += value;
      for (let k = 0; k < value.length; k++) {
        normalized.starts.push(index);
        normalized.ends.push(index + segment.length);
      }
    }
  }
  if (normalized.text.endsWith(" ")) {
    normalized.text = normalized.text.slice(0, -1);
    normalized.starts.pop(); normalized.ends.pop();
  }
  return normalized;
}

export function alignEvidence(source: string, passage: string, offsets?: { start: number; end: number }, completeSource = true): Match | null {
  if (!source || !passage || source.length > 32000 || passage.length > 32000
      || typeof Intl.Segmenter !== "function") return null;
  const needle = normalize(passage).text;
  if (!needle) return null;
  if (offsets && Number.isSafeInteger(offsets.start) && Number.isSafeInteger(offsets.end)
      && offsets.start >= 0 && offsets.end > offsets.start) {
    const points = Array.from(source);
    if (offsets.end <= points.length) {
      const start = points.slice(0, offsets.start).join("").length;
      const end = start + points.slice(offsets.start, offsets.end).join("").length;
      if (normalize(source.slice(start, end)).text === needle) return { start, end, method: "offset" };
    }
  }
  // A short or repeated lexical match cannot reliably relocate a source span.
  if (!completeSource || needle.length < 8) return null;
  const haystack = normalize(source);
  const at = haystack.text.indexOf(needle);
  if (at < 0 || haystack.text.indexOf(needle, at + 1) >= 0) return null;
  // Require token boundaries; matching a substring inside a technical term is unsafe.
  const before = Array.from(haystack.text.slice(0, at)).at(-1);
  const after = Array.from(haystack.text.slice(at + needle.length))[0];
  if ((before && /[\p{L}\p{N}]/u.test(before)) || (after && /[\p{L}\p{N}]/u.test(after))) return null;
  return { start: haystack.starts[at], end: haystack.ends[at + needle.length - 1], method: "normalized" };
}
