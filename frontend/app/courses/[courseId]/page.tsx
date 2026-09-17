import {
  CourseDetailView,
  parseCourseRouteId,
} from "@/components/aaos/course-detail-view";

type CourseDetailPageProps = Readonly<{
  params: Promise<{
    courseId: string;
  }>;
}>;

export default async function CourseDetailPage({
  params,
}: CourseDetailPageProps) {
  const { courseId: rawCourseId } = await params;
  const courseId = parseCourseRouteId(rawCourseId);

  return (
    <div className="mx-auto w-full max-w-5xl">
      <CourseDetailView courseId={courseId} />
    </div>
  );
}
