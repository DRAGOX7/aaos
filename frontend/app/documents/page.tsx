import { DocumentList } from "@/components/aaos/document-list";
import { parseCourseIdParam } from "@/lib/query/course-filter";

type DocumentsPageProps = Readonly<{
  searchParams: Promise<{
    course_id?: string | string[];
  }>;
}>;

export default async function DocumentsPage({
  searchParams,
}: DocumentsPageProps) {
  const params = await searchParams;
  const rawCourseId = params.course_id;
  const parsed = Array.isArray(rawCourseId)
    ? { kind: "INVALID" as const }
    : parseCourseIdParam(rawCourseId ?? null);

  if (parsed.kind === "INVALID") {
    return (
      <div className="mx-auto w-full max-w-5xl space-y-6">
        <p className="text-sm text-muted-foreground">
          The course filter is invalid.
        </p>
      </div>
    );
  }

  return (
    <DocumentList
      courseId={parsed.kind === "VALID" ? parsed.courseId : undefined}
    />
  );
}
