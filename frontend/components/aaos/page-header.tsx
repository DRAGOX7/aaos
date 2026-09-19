import type { ReactNode } from "react";
import { Bell, Search } from "lucide-react";

type PageHeaderProps = Readonly<{
  title?: string;
  description?: string;
  mobileNavigation?: ReactNode;
}>;

export function PageHeader({
  title = "Overview",
  description = "Your academic workspace",
  mobileNavigation,
}: PageHeaderProps) {
  return (
    <header className="sticky top-0 z-20 border-b border-border/80 bg-[color:var(--aaos-surface-glass)] backdrop-blur-xl">
      <div className="flex min-h-20 items-center gap-3 px-5 sm:px-6 lg:px-8">
        {mobileNavigation ? (
          <div className="lg:hidden">
            {mobileNavigation}
          </div>
        ) : null}

        <div className="min-w-0 shrink-0">
          <h1 className="text-xl font-semibold tracking-tight text-foreground sm:text-2xl">
            {title}
          </h1>
          <p className="mt-0.5 text-sm text-muted-foreground">
            {description}
          </p>
        </div>

        <div className="ml-auto hidden min-w-0 items-center gap-3 md:flex">
          <label className="relative hidden min-w-0 flex-1 lg:flex lg:w-[min(34vw,28rem)]">
            <Search
              aria-hidden="true"
              className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-text-subtle"
              strokeWidth={1.8}
            />
            <input
              aria-label="Global workspace search"
              className="h-10 w-full rounded-full border border-border-strong bg-white/70 py-2 pl-9 pr-4 text-sm text-foreground outline-none placeholder:text-text-subtle focus-visible:ring-2 focus-visible:ring-ring"
              placeholder="Search courses, documents, or assignments"
              readOnly
              type="search"
            />
          </label>

          <span
            aria-label="Notifications are not connected"
            className="flex size-10 items-center justify-center rounded-full border border-border bg-white/70 text-text-muted"
            title="Notifications are not connected"
          >
            <Bell aria-hidden="true" className="size-4" strokeWidth={1.8} />
          </span>

          <div className="flex items-center gap-2 rounded-full border border-border bg-white/70 py-1.5 pl-1.5 pr-3">
            <span className="flex size-7 items-center justify-center rounded-full bg-primary-soft text-xs font-semibold text-primary">
              AA
            </span>
            <span className="hidden text-sm font-medium text-foreground xl:inline">
              AAOS workspace
            </span>
          </div>
        </div>
      </div>
    </header>
  );
}
