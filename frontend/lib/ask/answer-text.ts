// Presentation of a grounded answer's text. The backend's rendered text ends
// with one "[n] Document (pages)" line per citation and, for answers drawn
// only from past courses, starts with a notice line. The Ask page shows
// citations in its Sources list and the notice as a label, so both are lifted
// out of the paragraph text here. Nothing else in the text changes.

const CITATION_LINE = /^\[\d+\]\s/;
const MAX_NOTICE_LENGTH = 100;

export type AnswerParts = Readonly<{
  notice: string | null;
  body: string;
}>;

export function splitAnswerText(
  text: string,
  { hasCitations, fromPastCourse }: Readonly<{ hasCitations: boolean; fromPastCourse: boolean }>,
): AnswerParts {
  let lines = text.split("\n");
  let notice: string | null = null;

  if (fromPastCourse) {
    const first = lines.findIndex((line) => line.trim() !== "");
    const candidate = first >= 0 ? lines[first].trim() : "";
    if (candidate.endsWith(":") && candidate.length <= MAX_NOTICE_LENGTH) {
      notice = candidate.slice(0, -1).trim();
      lines = lines.slice(first + 1);
    }
  }
  if (hasCitations) {
    lines = lines.filter((line) => !CITATION_LINE.test(line.trim()));
  }

  const body = lines.join("\n").trim();
  if (!body) {
    return { notice: null, body: text.trim() };
  }
  return { notice, body };
}
