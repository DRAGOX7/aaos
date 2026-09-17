import { DocumentDetailView } from "@/components/aaos/document-detail-view";
import { parseDocumentRouteId } from "@/lib/routing/route-ids";

type DocumentPageProps = Readonly<{
  params: Promise<{
    documentId: string;
  }>;
}>;

export default async function DocumentPage({
  params,
}: DocumentPageProps) {
  const { documentId } = await params;

  return <DocumentDetailView documentId={parseDocumentRouteId(documentId)} />;
}
