import { describe, expect, it } from "vitest";

import { splitAnswerText } from "@/lib/ask/answer-text";

const PAST_ANSWER = [
  "From a past course (archived 2 Oct 2026):",
  "",
  "The instructor of AI 495C is Prof. Sulieman Bani-Ahmad.",
  "",
  "[1] Course Syllabus.pdf (pages 1-2) · past course",
].join("\n");

describe("splitAnswerText", () => {
  it("lifts the past-course notice out and drops citation lines the Sources list shows", () => {
    expect(splitAnswerText(PAST_ANSWER, { hasCitations: true, fromPastCourse: true })).toEqual({
      notice: "From a past course (archived 2 Oct 2026)",
      body: "The instructor of AI 495C is Prof. Sulieman Bani-Ahmad.",
    });
  });

  it("handles the Arabic notice the same way", () => {
    const text = "من مادة سابقة (أُرشفت في 2026-10-02):\n\nالمدرس هو د. سليمان.\n\n[1] Syllabus.pdf (page 1)";
    expect(splitAnswerText(text, { hasCitations: true, fromPastCourse: true })).toEqual({
      notice: "من مادة سابقة (أُرشفت في 2026-10-02)",
      body: "المدرس هو د. سليمان.",
    });
  });

  it("keeps citation lines when there is no Sources list to show them", () => {
    const text = "Answer.\n\n[1] Notes.pdf (page 3)";
    expect(splitAnswerText(text, { hasCitations: false, fromPastCourse: false })).toEqual({
      notice: null,
      body: text,
    });
  });

  it("keeps a first line ending in a colon when the answer is not from a past course", () => {
    const text = "The weights are:\n- Final exam 40%";
    expect(splitAnswerText(text, { hasCitations: true, fromPastCourse: false })).toEqual({
      notice: null,
      body: text,
    });
  });

  it("keeps an inline [1] marker inside a sentence", () => {
    const text = "The final exam is worth 40% [1].\n\n[1] Syllabus.pdf (page 2)";
    expect(splitAnswerText(text, { hasCitations: true, fromPastCourse: false }).body).toBe(
      "The final exam is worth 40% [1].",
    );
  });

  it("never returns an empty body for a non-empty answer", () => {
    const text = "[1] Syllabus.pdf (page 2)";
    expect(splitAnswerText(text, { hasCitations: true, fromPastCourse: false }).body).toBe(text);
  });
});
