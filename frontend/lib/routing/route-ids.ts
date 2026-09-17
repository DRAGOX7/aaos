export function parseCourseRouteId(value: string): number | null {
  if (!/^[0-9]+$/.test(value)) {
    return null;
  }

  const courseId = Number(value);

  return Number.isSafeInteger(courseId) && courseId > 0 ? courseId : null;
}

export function parseDocumentRouteId(value: string): number | null {
  if (!/^[0-9]+$/.test(value)) {
    return null;
  }

  const documentId = Number(value);
  return Number.isSafeInteger(documentId) && documentId > 0
    ? documentId
    : null;
}
