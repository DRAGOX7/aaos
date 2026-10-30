import { describe, expect, it } from "vitest";
import { alignEvidence } from "@/lib/ask/evidence-alignment";

describe("original evidence alignment", () => {
  it.each([
    ["Header\nExact source passage.\nFooter", "Exact source passage."],
    ["Header\nExact  source\npassage.\nFooter", "Exact source passage."],
    ["The platform provides graphi-\ncal interfaces.", "graphical interfaces."],
    ["The platform provides graphi-\r\ncal interfaces.", "graphical interfaces."],
    ["A “quoted” source passage.", 'A "quoted" source passage.'],
    ["A cafe\u0301 source passage.", "A café source passage."],
    ["عنوان\nتُستخدم الخوارزمية لحل المشكلة.\nنهاية", "تُستخدم الخوارزمية لحل المشكلة."],
    ["توضح المحاضرة استخدام KNIME مع ٣ مجموعات و x = 2.", "استخدام KNIME مع ٣ مجموعات و x = 2."],
    ["An API supports DenseNet121 with x = 2 + 3.", "DenseNet121 with x = 2 + 3."],
  ])("preserves original text while locating normalized passage: %s", (original, passage) => {
    const match = alignEvidence(original, passage);
    expect(match).not.toBeNull();
    const selected = original.slice(match!.start, match!.end);
    expect(selected.length).toBeGreaterThan(0);
    expect(original).toContain(selected);
    expect(match!.start).toBeGreaterThanOrEqual(0);
    expect(match!.end).toBeLessThanOrEqual(original.length);
  });

  it("maps Python code point offsets to JS positions without splitting emoji", () => {
    const original = "🌍 A source passage. End";
    const target = "A source passage.";
    const match = alignEvidence(original, target, { start: 2, end: 19 });
    expect(match?.method).toBe("offset");
    expect(original.slice(match!.start, match!.end)).toBe(target);
  });
  it("uses authoritative offsets to disambiguate a repeated passage", () => {
    const text = "A source passage. A source passage.";
    expect(alignEvidence(text, "A source passage.")).toBeNull();
    expect(alignEvidence(text, "A source passage.", { start: 18, end: 35 })).toEqual({ start: 18, end: 35, method: "offset" });
  });
  it.each([
    ["", "Evidence passage"], ["Source context", ""], ["Source context", "Different passage"],
    ["Identical passage. Identical passage.", "Identical passage."],
    ["The unsupported claim is not in this source.", "The claim is supported."],
    ["superclassification model", "classification model"], ["text", "..."],
    ["Voltage = 2 volts.", "Voltage = -2 volts."],
    ["A relation a-\nb = 2.", "A relation ab = 2."],
    ["A value of x² = 2.", "A value of x2 = 2."],
    ["There are 1,000 values.", "There are 1.000 values."],
  ])("declines missing, ambiguous, unsupported, or partial-token matches", (text, passage) => {
    expect(alignEvidence(text, passage)).toBeNull();
  });
  it("falls back safely from stale/missing offsets only for a unique normalized match", () => {
    expect(alignEvidence("Header. A unique source passage.", "A unique source passage.", { start: 0, end: 4 })?.method).toBe("normalized");
    expect(alignEvidence("A unique source passage.", "A unique source passage.")?.method).toBe("normalized");
    expect(alignEvidence("x".repeat(40000), "original passage")).toBeNull();
  });
  it("does not relocate a later occurrence into a truncated source prefix", () => {
    const prefix = "A repeated source passage. " + "x".repeat(15000);
    expect(alignEvidence(prefix, "A repeated source passage.", { start: 17000, end: 17026 }, false)).toBeNull();
    expect(alignEvidence(prefix, "A repeated source passage.", { start: 0, end: 26 }, false)?.method).toBe("offset");
  });

});
