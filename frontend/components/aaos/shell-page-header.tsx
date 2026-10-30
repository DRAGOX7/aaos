"use client";

import { useQuery } from "@tanstack/react-query";
import { Search } from "lucide-react";
import { usePathname } from "next/navigation";

import { MobileNav } from "@/components/aaos/mobile-nav";
import { ModelBadge } from "@/components/aaos/model-badge";
import { LanguageToggle } from "@/components/aaos/language-toggle";
import { ThemeToggle } from "@/components/aaos/theme-toggle";
import { useI18n, type MessageKey } from "@/lib/i18n";
import { systemHealthQueryOptions } from "@/lib/query/health";
import { cn } from "@/lib/utils";

const HEALTH_PILL: Record<string, { label: MessageKey; tone: string; dot: string }> = {
  HEALTHY: { label: "All systems healthy", tone: "bg-success-soft text-success-strong", dot: "bg-success" },
  DEGRADED: { label: "Some systems degraded", tone: "bg-warning-soft text-warning-strong", dot: "bg-warning" },
};

function HealthPill() {
  const { t } = useI18n();
  const query = useQuery(systemHealthQueryOptions());
  const overall = query.data && typeof query.data === "object" ? query.data.overall : undefined;
  if (!overall) return null;
  const pill: { label: MessageKey; tone: string; dot: string } = HEALTH_PILL[overall] ?? {
    label: "System health needs attention",
    tone: "bg-danger-soft text-danger-strong",
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
      {t(pill.label)}
    </a>
  );
}

/** The one global top bar: navigation on phones, search, answering model, health and identity. */
export function ShellPageHeader() {
  const { t } = useI18n();
  const overview = usePathname() === "/";
  return (
    <header className="sticky top-0 z-20 border-b border-border/70 bg-[color:var(--aaos-surface-glass)] backdrop-blur-xl">
      <div className="flex min-h-16 items-center gap-3 px-5 sm:px-6 lg:px-10">
        <div className="lg:hidden">
          <MobileNav />
        </div>

        <label className="relative hidden min-w-0 flex-1 md:flex md:max-w-[32rem]">
          <Search
            aria-hidden="true"
            className="pointer-events-none absolute start-3.5 top-1/2 size-4 -translate-y-1/2 text-text-subtle"
            strokeWidth={1.9}
          />
          <input
            aria-label={t("Global workspace search")}
            className="h-11 w-full rounded-xl border border-border bg-card py-2 ps-10 pe-4 text-sm text-foreground outline-none placeholder:text-text-subtle focus-visible:ring-2 focus-visible:ring-ring"
            placeholder={t("Search courses, documents, or assignments")}
            readOnly
            type="search"
          />
        </label>

        <div className="ms-auto flex items-center gap-3">
          {overview ? <HealthPill /> : null}
          <ModelBadge quiet={overview} />
          {!overview ? <HealthPill /> : null}
          <LanguageToggle quiet={overview} />
          <ThemeToggle quiet={overview} />
          <span
            className="flex size-10 items-center justify-center rounded-full bg-hero text-sm font-bold text-white"
            title={t("AAOS workspace")}
          >
            <span aria-hidden="true">A</span>
            <span className="sr-only">{t("AAOS workspace")}</span>
          </span>
        </div>
      </div>
    </header>
  );
}
