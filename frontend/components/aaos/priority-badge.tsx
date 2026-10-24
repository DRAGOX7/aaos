import type { ReactNode } from "react";

/** High reads red, normal amber, low green; the tokens carry their own dark-theme values. */
const PRIORITY_CLASSES: Record<string, string> = {
  HIGH: "bg-danger-soft text-danger-strong",
  NORMAL: "bg-warning-soft text-warning-strong",
  LOW: "bg-success-soft text-success-strong",
};

export function PriorityBadge({ priority, children }: { priority: string; children: ReactNode }) {
  return (
    <span
      data-priority={priority}
      className={`inline-flex rounded-md px-2 py-0.5 text-xs font-semibold ${
        PRIORITY_CLASSES[priority] ?? "bg-muted text-muted-foreground"
      }`}
    >
      {children}
    </span>
  );
}
