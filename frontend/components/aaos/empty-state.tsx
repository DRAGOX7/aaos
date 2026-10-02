import Link from "next/link";
import type { LucideIcon } from "lucide-react";

type EmptyStateProps = Readonly<{
  icon: LucideIcon;
  title: string;
  description: string;
  action?: Readonly<{ href: string; label: string }>;
}>;

/** A calm empty state: what is missing, why, and what to do next. */
export function EmptyState({ icon: Icon, title, description, action }: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center gap-3 px-4 py-10 text-center">
      <span className="flex size-12 items-center justify-center rounded-2xl bg-primary-soft text-primary">
        <Icon aria-hidden="true" className="size-[22px]" strokeWidth={2} />
      </span>
      <p className="text-[15px] font-semibold text-foreground">{title}</p>
      <p className="max-w-sm text-sm leading-6 text-muted-foreground">{description}</p>
      {action ? (
        <Link
          href={action.href}
          className="mt-1 inline-flex h-10 items-center rounded-xl border border-border bg-white px-4 text-sm font-semibold text-primary-strong transition-colors hover:border-[#bcd3f4] hover:bg-primary-soft focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          {action.label}
        </Link>
      ) : null}
    </div>
  );
}
