"use client";

import { useQuery } from "@tanstack/react-query";
import { Search } from "lucide-react";

import { MobileNav } from "@/components/aaos/mobile-nav";
import { ModelBadge } from "@/components/aaos/model-badge";
import { systemHealthQueryOptions } from "@/lib/query/health";
import { cn } from "@/lib/utils";

const HEALTH_PILL: Record<string, { label: string; tone: string; dot: string }> = {
  HEALTHY: { label: "All systems healthy", tone: "bg-success-soft text-[#066b47]", dot: "bg-success" },
  DEGRADED: { label: "Some systems degraded", tone: "bg-warning-soft text-[#8a5300]", dot: "bg-warning" },
};

function HealthPill() {
  const query = useQuery(systemHealthQueryOptions());
  const overall = query.data && typeof query.data === "object" ? query.data.overall : undefined;
  if (!overall) return null;
  const pill = HEALTH_PILL[overall] ?? {
    label: "System health needs attention",
    tone: "bg-[#fdecee] text-[#a62a33]",
    dot: "bg-destructive",
  };
  return (
    <a
      href="/system-health"
      className={cn(
        "hidden h-9 items-center gap-2 rounded-full px-3 text-[13px] font-semibold transition-opacity hover:opacity-85 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring sm:flex",
        pill.tone,
      )}
    >
      <span aria-hidden="true" className={cn("size-2 rounded-full", pill.dot)} />
      {pill.label}
    </a>
  );
}

/** The one global top bar: navigation on phones, search, answering model, health and identity. */
export function ShellPageHeader() {
  return (
    <header className="sticky top-0 z-20 border-b border-border/70 bg-[color:var(--aaos-surface-glass)] backdrop-blur-xl">
      <div className="flex min-h-16 items-center gap-3 px-5 sm:px-6 lg:px-10">
        <div className="lg:hidden">
          <MobileNav />
        </div>

        <label className="relative hidden min-w-0 flex-1 md:flex md:max-w-[32rem]">
          <Search
            aria-hidden="true"
            className="pointer-events-none absolute left-3.5 top-1/2 size-4 -translate-y-1/2 text-text-subtle"
            strokeWidth={1.9}
          />
          <input
            aria-label="Global workspace search"
            className="h-11 w-full rounded-xl border border-border bg-white py-2 pl-10 pr-4 text-sm text-foreground outline-none placeholder:text-text-subtle focus-visible:ring-2 focus-visible:ring-ring"
            placeholder="Search courses, documents, or assignments"
            readOnly
            type="search"
          />
        </label>

        <div className="ml-auto flex items-center gap-3">
          <ModelBadge />
          <HealthPill />
          <span
            className="flex size-10 items-center justify-center rounded-full bg-hero text-sm font-bold text-white"
            title="AAOS workspace"
          >
            <span aria-hidden="true">A</span>
            <span className="sr-only">AAOS workspace</span>
          </span>
        </div>
      </div>
    </header>
  );
}
