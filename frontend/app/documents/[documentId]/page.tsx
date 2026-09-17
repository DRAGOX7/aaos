import {
  DocumentDetailView,
  parseDocumentRouteId,
} from "@/components/aaos/document-detail-view";

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
