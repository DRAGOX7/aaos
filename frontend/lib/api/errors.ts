export type SafeApiErrorCode =
  | "COURSE_NOT_FOUND"
  | "DOCUMENT_NOT_FOUND"
  | "INVALID_REQUEST"
  | "INTERNAL_ERROR";

const SAFE_MESSAGES: Record<SafeApiErrorCode, string> = {
  COURSE_NOT_FOUND: "The requested course is not available.",
  DOCUMENT_NOT_FOUND: "The requested document is not available.",
  INVALID_REQUEST: "The request is invalid.",
  INTERNAL_ERROR: "AAOS could not complete the request.",
};

function isSafeApiErrorCode(value: unknown): value is SafeApiErrorCode {
  return (
    value === "COURSE_NOT_FOUND" ||
    value === "DOCUMENT_NOT_FOUND" ||
    value === "INVALID_REQUEST" ||
    value === "INTERNAL_ERROR"
  );
}

function matchesStatus(code: SafeApiErrorCode, status: number): boolean {
  if (code === "INVALID_REQUEST") {
    return status === 422;
  }
  if (code === "COURSE_NOT_FOUND" || code === "DOCUMENT_NOT_FOUND") {
    return status === 404;
  }
  return status === 500;
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: SafeApiErrorCode;

  constructor(status: number, code: SafeApiErrorCode, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

export async function createApiError(
  response: Response,
  resource: string,
): Promise<ApiError> {
  let payload: unknown;
  if (typeof response.json === "function") {
    try {
      payload = await response.json();
    } catch {
      payload = undefined;
    }
  }

  const candidate =
    typeof payload === "object" && payload !== null && "error" in payload
      ? (payload as { error?: unknown }).error
      : undefined;
  const candidateCode =
    typeof candidate === "object" && candidate !== null && "code" in candidate
      ? (candidate as { code?: unknown }).code
      : undefined;
  const safeCode =
    isSafeApiErrorCode(candidateCode) &&
    matchesStatus(candidateCode, response.status)
      ? candidateCode
      : undefined;

  return new ApiError(
    response.status,
    safeCode ?? "INTERNAL_ERROR",
    safeCode
      ? SAFE_MESSAGES[safeCode]
      : `AAOS ${resource} request failed (${response.status})`,
  );
}
