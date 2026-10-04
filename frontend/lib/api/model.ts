import type { components } from "@/types/api.generated";

export type ModelStatus = components["schemas"]["ModelStatusResponse"];
export type ModelProvider = ModelStatus["provider"];

/** Refusals the backend can give for a model switch; anything else is "unknown". */
export type ModelSwitchFailure =
  | "CLOUD_CONFIRMATION_REQUIRED"
  | "GEMINI_NOT_READY"
  | "MODEL_BUSY"
  | "MODEL_SWITCH_FAILED"
  | "MUTATION_NOT_ALLOWED"
  | "UNKNOWN";

const KNOWN_FAILURES = new Set<string>([
  "CLOUD_CONFIRMATION_REQUIRED",
  "GEMINI_NOT_READY",
  "MODEL_BUSY",
  "MODEL_SWITCH_FAILED",
  "MUTATION_NOT_ALLOWED",
]);

export class ModelSwitchError extends Error {
  readonly failure: ModelSwitchFailure;

  constructor(failure: ModelSwitchFailure) {
    super(`The model could not be switched (${failure}).`);
    this.name = "ModelSwitchError";
    this.failure = failure;
  }
}

export async function fetchModelStatus(signal?: AbortSignal): Promise<ModelStatus> {
  const response = await fetch("/api/v1/model", {
    method: "GET",
    headers: { Accept: "application/json" },
    signal,
  });
  if (!response.ok) throw new Error("AAOS could not read which model is answering.");
  return (await response.json()) as ModelStatus;
}

/**
 * Ask the backend to switch the answering model. The request carries the provider
 * and the cloud confirmation only: the Gemini API key lives in the backend's
 * environment and is never sent from, or shown in, the browser.
 */
export async function selectModel(
  provider: ModelProvider,
  confirmCloud: boolean,
): Promise<ModelStatus> {
  const response = await fetch("/api/v1/model", {
    method: "POST",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: JSON.stringify({ provider, confirm_cloud: confirmCloud }),
  });
  if (!response.ok) {
    let code: unknown;
    try {
      const body: unknown = await response.json();
      code = (body as { error?: { code?: unknown } }).error?.code;
    } catch {
      code = undefined;
    }
    throw new ModelSwitchError(
      typeof code === "string" && KNOWN_FAILURES.has(code)
        ? (code as ModelSwitchFailure)
        : "UNKNOWN",
    );
  }
  return (await response.json()) as ModelStatus;
}
