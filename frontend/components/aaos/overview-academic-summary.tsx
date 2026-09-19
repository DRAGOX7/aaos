import Link from "next/link";
import { ArrowUpRight, CalendarDays, MessageCircle } from "lucide-react";

import { CourseProofCard } from "@/components/aaos/course-proof-card";
import { OverviewAssignmentPanel } from "@/components/aaos/overview-assignment-panel";
import { OverviewDocumentPanel } from "@/components/aaos/overview-document-panel";
import { OverviewMetricGrid } from "@/components/aaos/overview-metric-grid";
import { OverviewSystemHealthPanel } from "@/components/aaos/overview-system-health-panel";
import { SectionCard } from "@/components/aaos/section-card";

export function OverviewAcademicSummary() {
  return (
    <div className="mx-auto w-full max-w-7xl space-y-7">
      <section className="relative overflow-hidden rounded-[var(--aaos-radius-panel)] border border-primary/15 bg-[linear-gradient(135deg,rgba(255,255,255,0.94),rgba(234,243,255,0.82))] px-6 py-7 shadow-[var(--aaos-shadow-float)] sm:px-8 sm:py-9">
        <div className="relative z-[1] max-w-2xl">
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-primary">
            Academic AI operating system
          </p>
          <h2 className="mt-3 text-3xl font-semibold tracking-[-0.03em] text-primary-deep sm:text-4xl">
            Your academic workspace, ready when you are.
          </h2>
          <p className="mt-3 max-w-xl text-sm leading-6 text-muted-foreground sm:text-base">
            Keep trusted course context, grounded answers, and human decisions in one calm place.
          </p>
          <Link
            href="/ask"
            className="mt-6 inline-flex items-center gap-2 rounded-full bg-primary px-4 py-2.5 text-sm font-semibold text-primary-foreground shadow-[0_10px_22px_rgb(22_119_242_/_0.2)] transition-transform duration-200 hover:-translate-y-0.5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
          >
            Open Ask AAOS
            <ArrowUpRight aria-hidden="true" className="size-4" strokeWidth={1.9} />
          </Link>
        </div>
        <div aria-hidden="true" className="absolute -right-16 -top-20 size-72 rounded-full bg-primary/10 blur-3xl" />
        <div aria-hidden="true" className="absolute bottom-[-5rem] right-20 size-56 rounded-full bg-teal/10 blur-3xl" />
      </section>

      <OverviewMetricGrid />

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1.35fr)_minmax(18rem,0.65fr)]">
        <SectionCard
          title="Ask AAOS"
          className="border-primary/20 bg-[linear-gradient(145deg,rgba(255,255,255,0.96),rgba(239,248,255,0.9))]"
          action={
            <MessageCircle aria-hidden="true" className="size-5 text-primary" strokeWidth={1.8} />
          }
        >
          <div className="flex flex-col justify-between gap-6 sm:flex-row sm:items-end">
            <div className="max-w-xl">
              <p className="text-xl font-semibold tracking-tight text-primary-deep">
                Ask a question grounded in your academic knowledge base.
              </p>
              <p className="mt-2 text-sm leading-6 text-muted-foreground">
                AAOS keeps citations and insufficient-evidence states visible so you can judge the answer.
              </p>
            </div>
            <Link
              href="/ask"
              className="inline-flex shrink-0 items-center justify-center gap-2 rounded-full border border-primary/20 bg-white/80 px-4 py-2.5 text-sm font-semibold text-primary transition-colors hover:bg-primary-soft focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              Start a grounded question
              <ArrowUpRight aria-hidden="true" className="size-4" strokeWidth={1.8} />
            </Link>
          </div>
        </SectionCard>

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
      </div>

      <div className="grid gap-5 xl:grid-cols-3">
        <CourseProofCard />
        <OverviewAssignmentPanel />
        <OverviewDocumentPanel />
        <OverviewSystemHealthPanel />
        <SectionCard title="Learning activity" className="xl:col-span-2">
          <p className="text-sm leading-6 text-muted-foreground">
            Activity insights are not available yet. AAOS will only add this view when it has a trustworthy time series.
          </p>
        </SectionCard>
      </div>
    </div>
  );
}
