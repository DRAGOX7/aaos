import type { components } from "@/types/api.generated";

export type ModelStatus = components["schemas"]["ModelStatusResponse"];
export type ModelProvider = ModelStatus["provider"];

/** Refusals the backend can give for a model switch; anything else is "unknown". */
export type ModelSwitchFailure =
  | "CLOUD_CONFIRMATION_REQUIRED"
  | "GEMINI_NOT_READY"
  | "CHATGPT_PLAN_NOT_READY"
  | "MODEL_BUSY"
  | "MODEL_SWITCH_FAILED"
  | "MUTATION_NOT_ALLOWED"
  | "INVALID_MODEL"
  | "MODEL_LIST_FAILED"
  | "UNKNOWN";

const KNOWN_FAILURES = new Set<string>([
  "CLOUD_CONFIRMATION_REQUIRED",
  "GEMINI_NOT_READY",
  "CHATGPT_PLAN_NOT_READY",
  "MODEL_BUSY",
  "MODEL_SWITCH_FAILED",
  "MUTATION_NOT_ALLOWED",
  "INVALID_MODEL",
  "MODEL_LIST_FAILED",
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
 * Ask the backend to switch the answering model (and, for Gemini, which one). The
 * request carries the provider, the cloud confirmation and a model name only: the Gemini API key lives in the backend's
 * environment and is never sent from, or shown in, the browser.
 */
export async function fetchGeminiModels(signal?: AbortSignal): Promise<string[]> {
  const response = await fetch("/api/v1/model/gemini-models", {
    method: "GET",
    headers: { Accept: "application/json" },
    signal,
  });
  if (!response.ok) throw new Error("AAOS could not get the list of Gemini models.");
  const body = (await response.json()) as { models?: unknown };
  return Array.isArray(body.models)
    ? body.models.filter((name): name is string => typeof name === "string")
    : [];
}

/**
 * The models the signed-in ChatGPT plan offers (names only). The sign-in itself happens in
 * a terminal (`aaos model chatgpt-plan sign-in`); no token ever reaches the browser.
 */
export async function fetchChatGPTPlanModels(signal?: AbortSignal): Promise<string[]> {
  const response = await fetch("/api/v1/model/chatgpt-plan-models", {
    method: "GET",
    headers: { Accept: "application/json" },
    signal,
  });
  if (!response.ok) throw new Error("AAOS could not get the list of ChatGPT plan models.");
  const body = (await response.json()) as { models?: unknown };
  return Array.isArray(body.models)
    ? body.models.filter((name): name is string => typeof name === "string")
    : [];
}

export async function selectModel(
  provider: ModelProvider,
  confirmCloud: boolean,
  model?: string,
): Promise<ModelStatus> {
  // The model name goes in the field of the provider it belongs to; nothing else is sent.
  const modelField =
    model === undefined
      ? {}
      : provider === "chatgpt_plan"
        ? { chatgpt_plan_model: model }
        : { gemini_model: model };
  const response = await fetch("/api/v1/model", {
    method: "POST",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: JSON.stringify({ provider, confirm_cloud: confirmCloud, ...modelField }),
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
