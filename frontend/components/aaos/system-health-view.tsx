"use client";

import type { components } from "@/types/api.generated";
import { useQuery } from "@tanstack/react-query";
import { CircleAlert, CircleCheck, CircleDashed, type LucideIcon } from "lucide-react";

import { PageHeader } from "@/components/aaos/page-header";
import { SectionCard } from "@/components/aaos/section-card";
import { Skeleton } from "@/components/ui/skeleton";
import { systemHealthQueryOptions } from "@/lib/query/health";
import { cn } from "@/lib/utils";

type HealthComponent = components["schemas"]["HealthComponent"];
type HealthComponentName = components["schemas"]["HealthComponentName"];
type HealthStatus = components["schemas"]["HealthStatus"];

const COMPONENT_NAMES: Record<HealthComponentName, string> = {
  DATABASE: "Database",
  ACADEMIC_DATA: "Academic data",
  ACADEMIC_SYNC: "Moodle sync",
  DOCUMENT_PIPELINE: "Document pipeline",
  RAG_INDEX: "Document index",
  INFERENCE: "Local model",
  GOOGLE_CALENDAR: "Google Calendar",
};

type StatusLook = Readonly<{ label: string; pill: string; dot: string }>;

const STATUS: Record<string, StatusLook> = {
  HEALTHY: { label: "Healthy", pill: "bg-success-soft text-[#066b47]", dot: "bg-success" },
  DEGRADED: { label: "Degraded", pill: "bg-warning-soft text-[#8a5300]", dot: "bg-warning" },
  NOT_CONFIGURED: { label: "Not configured", pill: "bg-muted text-muted-foreground", dot: "bg-text-subtle" },
  UNAVAILABLE: { label: "Unavailable", pill: "bg-[#fdecee] text-[#a62a33]", dot: "bg-destructive" },
};

function statusLook(status: string): StatusLook {
  return STATUS[status] ?? { label: "Unknown", pill: "bg-muted text-muted-foreground", dot: "bg-text-subtle" };
}

function detailLabel(component: HealthComponent): string {
  switch (component.detail_code) {
    case "DATABASE_READY":
      return "The local database is reachable and its schema is current.";
    case "DATABASE_UNAVAILABLE":
      return "Database status unavailable.";
    case "SCHEMA_NOT_READY":
      return "Database schema is not ready.";
    case "ACADEMIC_DATA_READY":
      return "Courses and assignments can be read.";
    case "ACADEMIC_READ_UNAVAILABLE":
      return "Academic data status unavailable.";
    case "RAG_INDEX_READY":
      return "Course files are indexed and searchable.";
    case "RAG_INDEX_PARTIAL":
      return "Some course files are indexed.";
    case "RAG_INDEX_NOT_CONFIGURED":
      return "The document index is not set up.";
    case "PASSIVE_STATUS_UNAVAILABLE":
      return "Status is not reported for this component.";
    case "INTERNAL_STATUS_UNAVAILABLE":
      return "Internal status is unavailable.";
    default:
      return "Status detail is unavailable.";
  }
}

function StatusPill({ status, name }: { status: string; name: string }) {
  const look = statusLook(status);
  return (
    <span
      aria-label={`${name} status ${look.label}`}
      className={cn("inline-flex shrink-0 items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold", look.pill)}
    >
      <span aria-hidden="true" className={cn("size-1.5 rounded-full", look.dot)} />
      {look.label}
    </span>
  );
}

function HealthComponentRow({ component }: { component: HealthComponent }) {
  const name = COMPONENT_NAMES[component.name] ?? component.name;
  return (
    <li className="flex flex-col items-start justify-between gap-2 py-3.5 sm:flex-row sm:items-center sm:gap-4">
      <div className="min-w-0">
        <h3 className="text-[15px] font-semibold text-foreground">{name}</h3>
        <p className="mt-0.5 text-sm text-muted-foreground">{detailLabel(component)}</p>
      </div>
      <StatusPill status={component.status} name={name} />
    </li>
  );
}

function ComponentGroup({ title, components }: { title: string; components: HealthComponent[] }) {
  return (
    <section>
      <h2 className="text-xs font-semibold uppercase tracking-[0.08em] text-muted-foreground">{title}</h2>
      {components.length === 0 ? (
        <p className="mt-3 text-sm text-muted-foreground">No components reported.</p>
      ) : (
        <ul className="mt-1 divide-y divide-border">
          {components.map((component) => (
            <HealthComponentRow key={component.name} component={component} />
          ))}
        </ul>
      )}
    </section>
  );
}

const OVERALL: Record<string, { title: string; icon: LucideIcon; tone: string }> = {
  HEALTHY: { title: "All systems healthy", icon: CircleCheck, tone: "bg-success-soft text-[#066b47]" },
  DEGRADED: { title: "Some systems are degraded", icon: CircleAlert, tone: "bg-warning-soft text-[#8a5300]" },
};

const checkedFormat = new Intl.DateTimeFormat("en-GB", {
  hour: "2-digit",
  minute: "2-digit",
  day: "numeric",
  month: "short",
});

export function SystemHealthView() {
  const query = useQuery(systemHealthQueryOptions());
  const required = query.data?.components.filter((component) => component.required) ?? [];
  const optional = query.data?.components.filter((component) => !component.required) ?? [];
  const overall = query.data ? (query.data.overall as HealthStatus) : null;
  const summary = overall
    ? (OVERALL[overall] ?? { title: "Systems need attention", icon: CircleDashed, tone: "bg-[#fdecee] text-[#a62a33]" })
    : null;

  return (
    <div className="mx-auto w-full max-w-4xl space-y-6">
      <PageHeader title="System health" description="Passive operational status from trusted AAOS checks." />
      <SectionCard title="System health">
        {query.isPending ? (
          <div aria-label="Loading system health" className="space-y-3">
            {Array.from({ length: 3 }, (_, index) => (
              <Skeleton key={index} className="h-16 w-full rounded-lg" />
            ))}
          </div>
        ) : query.isError ? (
          <p className="text-sm text-muted-foreground">System health is temporarily unavailable.</p>
        ) : query.data && summary && overall ? (
          <div className="space-y-7">
            <div className={cn("flex flex-col items-start justify-between gap-3 rounded-xl px-4 py-3.5 sm:flex-row sm:items-center", summary.tone)}>
              <div className="flex items-center gap-3">
                <summary.icon aria-hidden="true" className="size-6 shrink-0" strokeWidth={2} />
                <div>
                  <h2 className="font-bold">Overall status</h2>
                  <p className="text-sm font-medium">{summary.title}</p>
                </div>
              </div>
              <span className="text-xs font-medium opacity-80">
                Checked {checkedFormat.format(new Date(query.data.checked_at))}
              </span>
            </div>
            <ComponentGroup title="Required components" components={required} />
            <ComponentGroup title="Optional components" components={optional} />
          </div>
        ) : null}
      </SectionCard>
    </div>
  );
}
