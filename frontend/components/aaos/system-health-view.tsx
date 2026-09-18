"use client";

import type { components } from "@/types/api.generated";
import { useQuery } from "@tanstack/react-query";

import { PageHeader } from "@/components/aaos/page-header";
import { SectionCard } from "@/components/aaos/section-card";
import { Skeleton } from "@/components/ui/skeleton";
import { systemHealthQueryOptions } from "@/lib/query/health";

type HealthComponent = components["schemas"]["HealthComponent"];
type HealthStatus = components["schemas"]["HealthStatus"];

function label(value: string): string {
  return value.replaceAll("_", " ");
}

function detailLabel(component: HealthComponent): string {
  switch (component.detail_code) {
    case "DATABASE_UNAVAILABLE":
      return "Database status unavailable";
    case "SCHEMA_NOT_READY":
      return "Database schema is not ready";
    case "ACADEMIC_READ_UNAVAILABLE":
      return "Academic data status unavailable";
    case "RAG_INDEX_READY":
      return "RAG index is ready";
    case "RAG_INDEX_PARTIAL":
      return "RAG index is partially ready";
    case "RAG_INDEX_NOT_CONFIGURED":
      return "RAG index is not configured";
    case "PASSIVE_STATUS_UNAVAILABLE":
      return "Passive status is unavailable";
    case "INTERNAL_STATUS_UNAVAILABLE":
      return "Internal status is unavailable";
  }
}

function HealthComponentRow({ component }: { component: HealthComponent }) {
  const status = label(component.status);
  return (
    <li className="flex flex-wrap items-start justify-between gap-3 rounded-lg border border-border px-4 py-3">
      <div className="min-w-0">
        <h3 className="font-medium text-foreground">{label(component.name)}</h3>
        <p className="mt-1 text-sm text-muted-foreground">
          {detailLabel(component)}
        </p>
      </div>
      <span
        aria-label={`${label(component.name)} status ${status}`}
        className="rounded-full border border-border px-2.5 py-1 text-xs font-medium uppercase tracking-wide text-muted-foreground"
      >
        {status}
      </span>
    </li>
  );
}

function ComponentGroup({
  title,
  components,
}: {
  title: string;
  components: HealthComponent[];
}) {
  return (
    <div>
      <h2 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
        {title}
      </h2>
      {components.length === 0 ? (
        <p className="mt-3 text-sm text-muted-foreground">No components reported.</p>
      ) : (
        <ul className="mt-3 space-y-3">
          {components.map((component) => (
            <HealthComponentRow key={component.name} component={component} />
          ))}
        </ul>
      )}
    </div>
  );
}

export function SystemHealthView() {
  const query = useQuery(systemHealthQueryOptions());
  const required = query.data?.components.filter((component) => component.required) ?? [];
  const optional = query.data?.components.filter((component) => !component.required) ?? [];

  return (
    <div className="mx-auto w-full max-w-5xl space-y-6">
      <PageHeader
        title="System health"
        description="Passive operational status from trusted AAOS checks."
      />
      <SectionCard title="System health">
        {query.isPending ? (
          <div aria-label="Loading system health" className="space-y-3">
            {Array.from({ length: 3 }, (_, index) => (
              <Skeleton key={index} className="h-16 w-full rounded-lg" />
            ))}
          </div>
        ) : query.isError ? (
          <p className="text-sm text-muted-foreground">
            System health is temporarily unavailable.
          </p>
        ) : query.data ? (
          <div className="space-y-8">
            <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border px-4 py-3">
              <h2 className="font-semibold text-foreground">Overall status</h2>
              <span className="rounded-full border border-border px-2.5 py-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                {label(query.data.overall as HealthStatus)}
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
