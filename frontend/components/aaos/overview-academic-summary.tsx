import { OverviewCourses } from "@/components/aaos/overview-courses";
import { OverviewHero } from "@/components/aaos/overview-hero";
import { OverviewUpNext } from "@/components/aaos/overview-up-next";
import { OverviewWorkspace } from "@/components/aaos/overview-workspace";

/**
 * The Overview (refresh direction A): Ask first, then this semester's courses,
 * then what is due and what is waiting. Every value comes from an existing
 * query; panels without a trustworthy source are not shown.
 */
export function OverviewAcademicSummary() {
  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-7">
      <OverviewHero />
      <OverviewCourses />
      <div className="grid grid-cols-1 gap-3.5 lg:grid-cols-3">
        <div className="min-w-0 lg:col-span-2">
          <OverviewUpNext />
        </div>
        <OverviewWorkspace />
      </div>
    </div>
  );
}
