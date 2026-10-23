import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

type SectionCardProps = Readonly<{
  title: string;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}>;

export function SectionCard({
  title,
  action,
  children,
  className,
}: SectionCardProps) {
  return (
    <section
      className={cn(
        "min-w-0 rounded-[var(--aaos-radius-card)] border border-border bg-card shadow-[var(--aaos-shadow-card)]",
        className,
      )}
    >
      <div className="flex items-center justify-between gap-4 border-b border-border px-5 py-4 sm:px-6">
        <h2 className="text-base font-bold tracking-tight text-foreground">
          {title}
        </h2>

        {action}
      </div>

      <div className="px-5 py-5 sm:px-6">
        {children}
      </div>
    </section>
  );
}
