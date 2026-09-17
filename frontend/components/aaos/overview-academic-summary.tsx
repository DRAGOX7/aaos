import { CourseProofCard } from "@/components/aaos/course-proof-card";
import { OverviewAssignmentPanel } from "@/components/aaos/overview-assignment-panel";
import { OverviewDocumentPanel } from "@/components/aaos/overview-document-panel";

export function OverviewAcademicSummary() {
  return (
    <div className="grid gap-6 xl:grid-cols-3">
      <CourseProofCard />
      <OverviewAssignmentPanel />
      <OverviewDocumentPanel />
    </div>
  );
}
