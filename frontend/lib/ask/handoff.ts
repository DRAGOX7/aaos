// A question started elsewhere (the Overview's Ask box) and sent once by the
// Ask page. Kept in sessionStorage, never the URL, so a question does not land
// in browser history; read once, then removed.
const HANDOFF_KEY = "aaos.ask.handoff";
const MAX_QUESTION_LENGTH = 4096;

export function writeAskHandoff(question: string): boolean {
  const text = question.trim();
  if (!text || text.length > MAX_QUESTION_LENGTH) return false;
  try {
    window.sessionStorage.setItem(HANDOFF_KEY, text);
    return true;
  } catch {
    return false;
  }
}

export function takeAskHandoff(): string | null {
  try {
    const text = window.sessionStorage.getItem(HANDOFF_KEY);
    window.sessionStorage.removeItem(HANDOFF_KEY);
    const trimmed = text?.trim();
    return trimmed && trimmed.length <= MAX_QUESTION_LENGTH ? trimmed : null;
  } catch {
    return null;
  }
}
