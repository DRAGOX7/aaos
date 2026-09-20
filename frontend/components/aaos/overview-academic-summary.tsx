import Link from "next/link";
import { ArrowUpRight, CalendarDays } from "lucide-react";

import { CourseProofCard } from "@/components/aaos/course-proof-card";
import { OverviewAssignmentPanel } from "@/components/aaos/overview-assignment-panel";
import { OverviewAskPanel } from "@/components/aaos/overview-ask-panel";
import { OverviewDocumentPanel } from "@/components/aaos/overview-document-panel";
import { OverviewMetricGrid } from "@/components/aaos/overview-metric-grid";
import { OverviewSystemHealthPanel } from "@/components/aaos/overview-system-health-panel";
import { SectionCard } from "@/components/aaos/section-card";

export function OverviewAcademicSummary() {
  return (
    <div className="mx-auto w-full max-w-7xl space-y-7">
      <section className="relative overflow-hidden rounded-[var(--aaos-radius-panel)] border border-primary/15 bg-[linear-gradient(135deg,rgba(255,255,255,0.94),rgba(234,243,255,0.82))] px-6 py-5 shadow-[var(--aaos-shadow-float)] sm:px-8 sm:py-6">
        <div className="relative z-[1] max-w-2xl">
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-primary">
            Academic AI operating system
          </p>
          <h2 className="mt-2 max-w-[34rem] text-balance text-2xl font-semibold tracking-[-0.025em] text-primary-deep sm:text-3xl">
            Your academic workspace, ready when you are.
          </h2>
          <p className="mt-2 max-w-xl text-sm leading-5 text-muted-foreground sm:text-base">
            Keep trusted course context, grounded answers, and human decisions in one calm place.
          </p>
          <Link
            href="/ask"
            className="mt-4 inline-flex items-center gap-2 rounded-full bg-primary px-4 py-2.5 text-sm font-semibold text-primary-foreground shadow-[0_10px_22px_rgb(22_119_242_/_0.2)] transition-transform duration-200 hover:-translate-y-0.5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
          >
            Open Ask AAOS
            <ArrowUpRight aria-hidden="true" className="size-4" strokeWidth={1.9} />
          </Link>
        </div>
        <div aria-hidden="true" className="absolute -right-16 -top-20 size-72 rounded-full bg-primary/10 blur-3xl" />
        <div aria-hidden="true" className="absolute bottom-[-5rem] right-20 size-56 rounded-full bg-teal/10 blur-3xl" />
        <div aria-hidden="true" className="absolute right-12 top-1/2 hidden size-36 -translate-y-1/2 rounded-full border border-white/70 bg-white/30 shadow-[inset_0_1px_0_rgb(255_255_255_/_0.8)] sm:block" />
        <div aria-hidden="true" className="absolute right-[4.5rem] top-1/2 hidden size-24 -translate-y-1/2 rounded-full border border-primary/15 sm:block" />
        <div aria-hidden="true" className="absolute right-[7.1rem] top-[46%] hidden size-2 rounded-full bg-teal/70 shadow-[0_0_0_6px_rgb(11_158_168_/_0.1)] sm:block" />
      </section>

      <OverviewMetricGrid />

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1.35fr)_minmax(18rem,0.65fr)]">
        <OverviewAskPanel />

        <div className="space-y-5">
          <SectionCard title="Today">
            <div className="flex items-start gap-3">
              <span className="flex size-10 shrink-0 items-center justify-center rounded-2xl bg-teal-soft text-teal">
                <CalendarDays aria-hidden="true" className="size-5" strokeWidth={1.8} />
              </span>
              <div>
                <p className="font-medium text-foreground">No schedule data available.</p>
                <p className="mt-1 text-sm leading-5 text-muted-foreground">
                  AAOS will show a schedule here when a trusted source is connected.
                </p>
              </div>
            </div>
          </SectionCard>
          <OverviewSystemHealthPanel />
        </div>
      </div>

      <div className="grid gap-5 xl:grid-cols-3">
        <CourseProofCard />
        <OverviewAssignmentPanel />
        <OverviewDocumentPanel />
        <SectionCard title="Learning activity" className="xl:col-span-2">
          <p className="text-sm leading-6 text-muted-foreground">
            Activity insights are not available yet. AAOS will only add this view when it has a trustworthy time series.
          </p>
        </SectionCard>
      </div>
    </div>
  );
}
