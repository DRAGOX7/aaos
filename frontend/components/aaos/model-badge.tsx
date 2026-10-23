"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Cloud, HardDrive } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import {
  ModelSwitchError,
  selectModel,
  type ModelProvider,
  type ModelStatus,
  type ModelSwitchFailure,
} from "@/lib/api/model";
import {
  chatgptPlanModelsQueryOptions,
  geminiModelsQueryOptions,
  modelKeys,
  modelQueryOptions,
} from "@/lib/query/model";
import { useI18n, type MessageKey } from "@/lib/i18n";
import { cn } from "@/lib/utils";

type CloudProvider = Exclude<ModelProvider, "lm_studio">;

/** How each cloud option is named, and which company receives the data. */
const CLOUD: Record<CloudProvider, { name: MessageKey; company: string; where: MessageKey }> = {
  gemini: { name: "Gemini", company: "Google", where: "Google's cloud" },
  chatgpt_plan: { name: "ChatGPT plan", company: "OpenAI", where: "OpenAI's cloud, on your ChatGPT plan" },
};

const BLOCKER_TEXT: Record<NonNullable<ModelStatus["gemini_blocker"]>, MessageKey> = {
  GEMINI_API_KEY_MISSING:
    "To enable Gemini, set GEMINI_API_KEY in the terminal that runs the AAOS backend, then restart it. The key is never typed into this page.",
  GEMINI_MODEL_MISSING:
    "To enable Gemini, set AAOS_GEMINI_MODEL in the terminal that runs the AAOS backend, then restart it.",
};

const PLAN_BLOCKER_TEXT: Record<NonNullable<ModelStatus["chatgpt_plan_blocker"]>, MessageKey> = {
  CHATGPT_PLAN_SIGN_IN_REQUIRED:
    "To use your ChatGPT plan, run `aaos model chatgpt-plan sign-in` in a terminal, then reopen this panel. Signing in never happens in this page.",
  CHATGPT_PLAN_USAGE_NOT_GRANTED:
    "The ChatGPT sign-in does not allow plan usage. Run `aaos model chatgpt-plan sign-in` again and allow AAOS to use your plan.",
  CHATGPT_PLAN_MODEL_MISSING: "Pick a ChatGPT plan model below.",
};

const FAILURE_TEXT: Record<ModelSwitchFailure, MessageKey> = {
  CLOUD_CONFIRMATION_REQUIRED: "Confirm that your data may be sent to the cloud provider first.",
  GEMINI_NOT_READY: "Gemini is not set up in the backend yet.",
  CHATGPT_PLAN_NOT_READY:
    "Sign in first: run `aaos model chatgpt-plan sign-in` in a terminal, then pick a model.",
  MODEL_BUSY: "A question is running. Try again when it has finished.",
  MODEL_SWITCH_FAILED: "AAOS could not switch the model. The previous model is still in use.",
  MUTATION_NOT_ALLOWED: "The model can only be changed from the local AAOS page.",
  INVALID_MODEL: "That is not one of the models available to you.",
  MODEL_LIST_FAILED: "AAOS could not get the list of models.",
  UNKNOWN: "AAOS could not switch the model. Nothing was changed.",
};

function sortedChoices(listed: string[] | undefined, configured: string | null | undefined) {
  return Array.from(new Set([...(listed ?? []), ...(configured ? [configured] : [])])).sort();
}

/** Whether answers are produced on this computer or sent to a cloud provider, always visible. */
export function ModelBadge() {
  const { t, dir } = useI18n();
  const query = useQuery(modelQueryOptions());
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [confirming, setConfirming] = useState<CloudProvider | null>(null);
  const [chosenGemini, setChosenGemini] = useState<string | null>(null);
  const [chosenPlan, setChosenPlan] = useState<string | null>(null);

  const geminiReady = query.data?.gemini_ready === true;
  // Signed in is enough to list the plan's models, even before one is chosen.
  const planSignedIn =
    query.data?.chatgpt_plan_ready === true ||
    query.data?.chatgpt_plan_blocker === "CHATGPT_PLAN_MODEL_MISSING";
  const geminiList = useQuery({ ...geminiModelsQueryOptions(), enabled: open && geminiReady });
  const planList = useQuery({ ...chatgptPlanModelsQueryOptions(), enabled: open && planSignedIn });

  const mutation = useMutation<
    ModelStatus,
    ModelSwitchError | Error,
    { provider: ModelProvider; confirmCloud: boolean; model?: string }
  >({
    mutationFn: ({ provider, confirmCloud, model }) => selectModel(provider, confirmCloud, model),
    onSuccess: (status) => {
      queryClient.setQueryData(modelKeys.all, status);
      setConfirming(null);
    },
  });

  const status = query.data;
  if (!status || typeof status !== "object") return null;

  const cloud = status.cloud;
  const active: CloudProvider | null =
    status.provider === "gemini" || status.provider === "chatgpt_plan" ? status.provider : null;
  // The model shown in each picker: the owner's pick, else the configured one.
  const pickedGemini = chosenGemini ?? status.gemini_model ?? "";
  const pickedPlan = chosenPlan ?? status.chatgpt_plan_model ?? "";
  const geminiChoices = sortedChoices(geminiList.data, status.gemini_model);
  const planChoices = sortedChoices(planList.data, status.chatgpt_plan_model);
  const label = active
    ? `${t("Cloud")} · ${t(CLOUD[active].name)} · ${status.model}`
    : `${t("Local")} · ${status.model}`;
  const Icon = cloud ? Cloud : HardDrive;
  const failure =
    mutation.error instanceof ModelSwitchError
      ? t(FAILURE_TEXT[mutation.error.failure])
      : mutation.error
        ? t(FAILURE_TEXT.UNKNOWN)
        : null;
  const pickedFor = (provider: CloudProvider) =>
    (provider === "gemini" ? pickedGemini : pickedPlan) || undefined;

  return (
    <Sheet
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        // Opening the panel refreshes it, so the last problem is the latest one.
        if (next) void query.refetch();
        if (!next) {
          setConfirming(null);
          setChosenGemini(null);
          setChosenPlan(null);
          mutation.reset();
        }
      }}
    >
      <SheetTrigger asChild>
        <button
          type="button"
          aria-label={t("Answering model: {label}. Change model", { label })}
          title={label}
          className={cn(
            "hidden h-9 max-w-[16rem] items-center gap-2 rounded-full px-3 text-[13px] font-semibold transition-opacity hover:opacity-85 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring sm:flex",
            cloud ? "bg-warning-soft text-warning-strong" : "bg-muted text-foreground",
          )}
        >
          <Icon aria-hidden="true" className="size-4 shrink-0" strokeWidth={1.9} />
          <span className="truncate">{label}</span>
        </button>
      </SheetTrigger>
      <SheetContent side={dir === "rtl" ? "left" : "right"}>
        <SheetHeader>
          <SheetTitle>{t("Answering model")}</SheetTitle>
          <SheetDescription>
            {active
              ? t("Answers come from {name} ({model}) in {where}. Your questions and excerpts of your course files are sent to {company}.", {
                  name: t(CLOUD[active].name),
                  model: status.model,
                  where: t(CLOUD[active].where),
                  company: CLOUD[active].company,
                })
              : t("Answers come from the local model ({model}) on this computer. Nothing is sent anywhere.", {
                  model: status.local_model,
                })}
          </SheetDescription>
        </SheetHeader>

        <div className="flex flex-col gap-3 px-4">
          {status.last_failure ? (
            <p
              className="rounded-xl border border-destructive/30 bg-danger-soft p-3 text-sm text-danger-strong"
              role="status"
            >
              {t("Last problem: {problem}", { problem: status.last_failure })}
            </p>
          ) : null}

          <Button
            type="button"
            variant={cloud ? "outline" : "default"}
            disabled={mutation.isPending}
            aria-pressed={!cloud}
            onClick={() => {
              // The active choice stays fully visible; choosing it again does nothing.
              if (cloud) mutation.mutate({ provider: "lm_studio", confirmCloud: false });
            }}
          >
            <HardDrive aria-hidden="true" /> {t("Local model")} · {status.local_model}
          </Button>

          <Button
            type="button"
            variant={active === "gemini" ? "default" : "outline"}
            disabled={mutation.isPending || !status.gemini_ready}
            aria-pressed={active === "gemini"}
            onClick={() => {
              if (active !== "gemini") setConfirming("gemini");
            }}
          >
            <Cloud aria-hidden="true" /> Gemini{pickedGemini ? ` · ${pickedGemini}` : ""}
          </Button>

          {status.gemini_ready ? (
            <label className="flex flex-col gap-1.5 text-sm text-muted-foreground">
              {t("Gemini model")}
              <select
                aria-label={t("Gemini model")}
                className="h-9 rounded-xl border border-border bg-card px-2.5 text-sm text-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-60"
                disabled={mutation.isPending || geminiList.isPending}
                value={pickedGemini}
                onChange={(event) => {
                  const next = event.target.value;
                  setChosenGemini(next);
                  // Already on Gemini: the data goes to Google anyway, so the model changes
                  // at once. From elsewhere, the pick waits for the confirmation.
                  if (active === "gemini" && next !== status.gemini_model) {
                    mutation.mutate({ provider: "gemini", confirmCloud: true, model: next });
                  }
                }}
              >
                {geminiChoices.map((name) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ))}
              </select>
              {geminiList.isError ? (
                <span role="note">{t("The model list could not be loaded; showing the configured model.")}</span>
              ) : null}
            </label>
          ) : null}

          {!status.gemini_ready && status.gemini_blocker ? (
            <p className="text-sm text-muted-foreground" role="note">
              {t(BLOCKER_TEXT[status.gemini_blocker])}
            </p>
          ) : null}

          <Button
            type="button"
            variant={active === "chatgpt_plan" ? "default" : "outline"}
            disabled={mutation.isPending || !planSignedIn || !pickedPlan}
            aria-pressed={active === "chatgpt_plan"}
            onClick={() => {
              if (active !== "chatgpt_plan") setConfirming("chatgpt_plan");
            }}
          >
            <Cloud aria-hidden="true" /> {t("ChatGPT plan")}{pickedPlan ? ` · ${pickedPlan}` : ""}
          </Button>

          {planSignedIn ? (
            <label className="flex flex-col gap-1.5 text-sm text-muted-foreground">
              {t("ChatGPT plan model")}
              <select
                aria-label={t("ChatGPT plan model")}
                className="h-9 rounded-xl border border-border bg-card px-2.5 text-sm text-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-60"
                disabled={mutation.isPending || planList.isPending}
                value={pickedPlan}
                onChange={(event) => {
                  const next = event.target.value;
                  setChosenPlan(next);
                  if (active === "chatgpt_plan" && next !== status.chatgpt_plan_model) {
                    mutation.mutate({ provider: "chatgpt_plan", confirmCloud: true, model: next });
                  }
                }}
              >
                {pickedPlan ? null : (
                  <option value="" disabled>
                    {t("Choose a model")}
                  </option>
                )}
                {planChoices.map((name) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ))}
              </select>
              {planList.isError ? (
                <span role="note">{t("The ChatGPT plan model list could not be loaded.")}</span>
              ) : null}
            </label>
          ) : null}

          {status.chatgpt_plan_blocker &&
          status.chatgpt_plan_blocker !== "CHATGPT_PLAN_MODEL_MISSING" ? (
            <p className="text-sm text-muted-foreground" role="note">
              {t(PLAN_BLOCKER_TEXT[status.chatgpt_plan_blocker])}
            </p>
          ) : null}

          {confirming && active !== confirming ? (
            <div
              role="alertdialog"
              aria-labelledby="cloud-confirm-title"
              className="flex flex-col gap-3 rounded-xl border border-warning/40 bg-warning-soft p-3 text-sm text-warning-strong"
            >
              <p id="cloud-confirm-title" className="font-semibold">
                {t("Send your data to {company}?", { company: CLOUD[confirming].company })}
              </p>
              <p>
                {t("Your questions and the excerpts of your course files used to answer them will be sent to {company}. AAOS returns to the local model whenever the backend restarts.", {
                  company: CLOUD[confirming].company,
                })}
              </p>
              <div className="flex gap-2">
                <Button
                  type="button"
                  size="sm"
                  disabled={mutation.isPending}
                  onClick={() =>
                    mutation.mutate({
                      provider: confirming,
                      confirmCloud: true,
                      model: pickedFor(confirming),
                    })
                  }
                >
                  {t("Send my data to {company}", { company: CLOUD[confirming].company })}
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  disabled={mutation.isPending}
                  onClick={() => setConfirming(null)}
                >
                  {t("Cancel")}
                </Button>
              </div>
            </div>
          ) : null}

          {mutation.isPending ? (
            <p className="text-sm text-muted-foreground" role="status">
              {t("Switching… the first answer afterwards can take a little longer.")}
            </p>
          ) : null}
          {failure ? (
            <p className="text-sm text-destructive" role="alert">
              {failure}
            </p>
          ) : null}
        </div>
      </SheetContent>
    </Sheet>
  );
}
