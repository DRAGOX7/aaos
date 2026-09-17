export function documentHref(documentId: number): string {
  if (!Number.isSafeInteger(documentId) || documentId <= 0) {
    throw new Error("AAOS document link requires a positive integer ID.");
  }

  return `/documents/${documentId}`;
}
