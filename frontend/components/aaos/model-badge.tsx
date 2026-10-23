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
import { cn } from "@/lib/utils";

type CloudProvider = Exclude<ModelProvider, "lm_studio">;

/** How each cloud option is named, and which company receives the data. */
const CLOUD: Record<CloudProvider, { name: string; company: string; where: string }> = {
  gemini: { name: "Gemini", company: "Google", where: "Google's cloud" },
  chatgpt_plan: { name: "ChatGPT plan", company: "OpenAI", where: "OpenAI's cloud, on your ChatGPT plan" },
};

const BLOCKER_TEXT: Record<NonNullable<ModelStatus["gemini_blocker"]>, string> = {
  GEMINI_API_KEY_MISSING:
    "To enable Gemini, set GEMINI_API_KEY in the terminal that runs the AAOS backend, then restart it. The key is never typed into this page.",
  GEMINI_MODEL_MISSING:
    "To enable Gemini, set AAOS_GEMINI_MODEL in the terminal that runs the AAOS backend, then restart it.",
};

const PLAN_BLOCKER_TEXT: Record<NonNullable<ModelStatus["chatgpt_plan_blocker"]>, string> = {
  CHATGPT_PLAN_SIGN_IN_REQUIRED:
    "To use your ChatGPT plan, run `aaos model chatgpt-plan sign-in` in a terminal, then reopen this panel. Signing in never happens in this page.",
  CHATGPT_PLAN_USAGE_NOT_GRANTED:
    "The ChatGPT sign-in does not allow plan usage. Run `aaos model chatgpt-plan sign-in` again and allow AAOS to use your plan.",
  CHATGPT_PLAN_MODEL_MISSING: "Pick a ChatGPT plan model below.",
};

const FAILURE_TEXT: Record<ModelSwitchFailure, string> = {
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
  const label = active ? `Cloud · ${CLOUD[active].name} · ${status.model}` : `Local · ${status.model}`;
  const Icon = cloud ? Cloud : HardDrive;
  const failure =
    mutation.error instanceof ModelSwitchError
      ? FAILURE_TEXT[mutation.error.failure]
      : mutation.error
        ? FAILURE_TEXT.UNKNOWN
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
          aria-label={`Answering model: ${label}. Change model`}
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
      <SheetContent side="right">
        <SheetHeader>
          <SheetTitle>Answering model</SheetTitle>
          <SheetDescription>
            {active
              ? `Answers come from ${CLOUD[active].name} (${status.model}) in ${CLOUD[active].where}. Your questions and excerpts of your course files are sent to ${CLOUD[active].company}.`
              : `Answers come from the local model (${status.local_model}) on this computer. Nothing is sent anywhere.`}
          </SheetDescription>
        </SheetHeader>

        <div className="flex flex-col gap-3 px-4">
          {status.last_failure ? (
            <p
              className="rounded-xl border border-destructive/30 bg-danger-soft p-3 text-sm text-danger-strong"
              role="status"
            >
              Last problem: {status.last_failure}
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
            <HardDrive aria-hidden="true" /> Local model · {status.local_model}
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
              Gemini model
              <select
                aria-label="Gemini model"
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
                <span role="note">The model list could not be loaded; showing the configured model.</span>
              ) : null}
            </label>
          ) : null}

          {!status.gemini_ready && status.gemini_blocker ? (
            <p className="text-sm text-muted-foreground" role="note">
              {BLOCKER_TEXT[status.gemini_blocker]}
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
            <Cloud aria-hidden="true" /> ChatGPT plan{pickedPlan ? ` · ${pickedPlan}` : ""}
          </Button>

          {planSignedIn ? (
            <label className="flex flex-col gap-1.5 text-sm text-muted-foreground">
              ChatGPT plan model
              <select
                aria-label="ChatGPT plan model"
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
                    Choose a model
                  </option>
                )}
                {planChoices.map((name) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ))}
              </select>
              {planList.isError ? (
                <span role="note">The ChatGPT plan model list could not be loaded.</span>
              ) : null}
            </label>
          ) : null}

          {status.chatgpt_plan_blocker &&
          status.chatgpt_plan_blocker !== "CHATGPT_PLAN_MODEL_MISSING" ? (
            <p className="text-sm text-muted-foreground" role="note">
              {PLAN_BLOCKER_TEXT[status.chatgpt_plan_blocker]}
            </p>
          ) : null}

          {confirming && active !== confirming ? (
            <div
              role="alertdialog"
              aria-labelledby="cloud-confirm-title"
              className="flex flex-col gap-3 rounded-xl border border-warning/40 bg-warning-soft p-3 text-sm text-warning-strong"
            >
              <p id="cloud-confirm-title" className="font-semibold">
                Send your data to {CLOUD[confirming].company}?
              </p>
              <p>
                Your questions and the excerpts of your course files used to answer them will be
                sent to {CLOUD[confirming].company}. AAOS returns to the local model whenever the
                backend restarts.
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
                  Send my data to {CLOUD[confirming].company}
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  disabled={mutation.isPending}
                  onClick={() => setConfirming(null)}
                >
                  Cancel
                </Button>
              </div>
            </div>
          ) : null}

          {mutation.isPending ? (
            <p className="text-sm text-muted-foreground" role="status">
              Switching… the first answer afterwards can take a little longer.
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
