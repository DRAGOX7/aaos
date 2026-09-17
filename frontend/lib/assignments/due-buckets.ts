export type AssignmentDueBucket =
  | "OVERDUE"
  | "DUE_SOON"
  | "THIS_WEEK"
  | "LATER"
  | "NO_DUE_DATE";

const HOURS_48 = 48 * 60 * 60 * 1000;

function nextMondayUtcAfter(now: Date): number {
  const day = now.getUTCDay();
  const daysUntilMonday = (8 - day) % 7 || 7;

  return Date.UTC(
    now.getUTCFullYear(),
    now.getUTCMonth(),
    now.getUTCDate() + daysUntilMonday,
  );
}

export function bucketAssignment(
  dueAt: string | null,
  now: Date,
): AssignmentDueBucket {
  const nowMs = now.getTime();
  if (Number.isNaN(nowMs)) {
    throw new Error("AAOS assignment reference time is invalid.");
  }

  if (dueAt === null) {
    return "NO_DUE_DATE";
  }

  const dueMs = Date.parse(dueAt);
  if (Number.isNaN(dueMs)) {
    throw new Error("AAOS assignment due date is invalid.");
  }

  if (dueMs < nowMs) {
    return "OVERDUE";
  }

  const weekEndMs = nextMondayUtcAfter(now);
  const soonEndMs = Math.min(nowMs + HOURS_48, weekEndMs);

  if (dueMs < soonEndMs) {
    return "DUE_SOON";
  }

  if (dueMs < weekEndMs) {
    return "THIS_WEEK";
  }

  return "LATER";
}
