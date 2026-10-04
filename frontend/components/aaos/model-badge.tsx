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
import { modelKeys, modelQueryOptions } from "@/lib/query/model";
import { cn } from "@/lib/utils";

const BLOCKER_TEXT: Record<NonNullable<ModelStatus["gemini_blocker"]>, string> = {
  GEMINI_API_KEY_MISSING:
    "To enable Gemini, set GEMINI_API_KEY in the terminal that runs the AAOS backend, then restart it. The key is never typed into this page.",
  GEMINI_MODEL_MISSING:
    "To enable Gemini, set AAOS_GEMINI_MODEL in the terminal that runs the AAOS backend, then restart it.",
};

const FAILURE_TEXT: Record<ModelSwitchFailure, string> = {
  CLOUD_CONFIRMATION_REQUIRED: "Confirm that your data may be sent to Google first.",
  GEMINI_NOT_READY: "Gemini is not set up in the backend yet.",
  MODEL_BUSY: "A question is running. Try again when it has finished.",
  MODEL_SWITCH_FAILED: "AAOS could not switch the model. The previous model is still in use.",
  MUTATION_NOT_ALLOWED: "The model can only be changed from the local AAOS page.",
  UNKNOWN: "AAOS could not switch the model. Nothing was changed.",
};

/** Whether answers are produced on this computer or sent to Google, always visible. */
export function ModelBadge() {
  const query = useQuery(modelQueryOptions());
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [confirming, setConfirming] = useState(false);

  const mutation = useMutation<
    ModelStatus,
    ModelSwitchError | Error,
    { provider: ModelProvider; confirmCloud: boolean }
  >({
    mutationFn: ({ provider, confirmCloud }) => selectModel(provider, confirmCloud),
    onSuccess: (status) => {
      queryClient.setQueryData(modelKeys.all, status);
      setConfirming(false);
    },
  });

  const status = query.data;
  if (!status || typeof status !== "object") return null;

  const cloud = status.cloud;
  const label = cloud ? `Cloud · Gemini · ${status.model}` : `Local · ${status.model}`;
  const Icon = cloud ? Cloud : HardDrive;
  const failure =
    mutation.error instanceof ModelSwitchError
      ? FAILURE_TEXT[mutation.error.failure]
      : mutation.error
        ? FAILURE_TEXT.UNKNOWN
        : null;

  return (
    <Sheet
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        // Opening the panel refreshes it, so the last problem is the latest one.
        if (next) void query.refetch();
        if (!next) {
          setConfirming(false);
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
            cloud ? "bg-warning-soft text-[#8a5300]" : "bg-muted text-foreground",
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
            {cloud
              ? `Answers come from Gemini (${status.model}) in Google's cloud. Your questions and excerpts of your course files are sent to Google.`
              : `Answers come from the local model (${status.local_model}) on this computer. Nothing is sent anywhere.`}
          </SheetDescription>
        </SheetHeader>

        <div className="flex flex-col gap-3 px-4">
          {status.last_failure ? (
            <p
              className="rounded-xl border border-destructive/30 bg-[#fdecee] p-3 text-sm text-[#a62a33]"
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
            variant={cloud ? "default" : "outline"}
            disabled={mutation.isPending || !status.gemini_ready}
            aria-pressed={cloud}
            onClick={() => {
              if (!cloud) setConfirming(true);
            }}
          >
            <Cloud aria-hidden="true" /> Gemini{status.gemini_model ? ` · ${status.gemini_model}` : ""}
          </Button>

          {!status.gemini_ready && status.gemini_blocker ? (
            <p className="text-sm text-muted-foreground" role="note">
              {BLOCKER_TEXT[status.gemini_blocker]}
            </p>
          ) : null}

          {confirming && !cloud ? (
            <div
              role="alertdialog"
              aria-labelledby="cloud-confirm-title"
              className="flex flex-col gap-3 rounded-xl border border-warning/40 bg-warning-soft p-3 text-sm text-[#8a5300]"
            >
              <p id="cloud-confirm-title" className="font-semibold">
                Send your data to Google?
              </p>
              <p>
                Your questions and the excerpts of your course files used to answer them will be
                sent to Google. AAOS returns to the local model whenever the backend restarts.
              </p>
              <div className="flex gap-2">
                <Button
                  type="button"
                  size="sm"
                  disabled={mutation.isPending}
                  onClick={() => mutation.mutate({ provider: "gemini", confirmCloud: true })}
                >
                  Send my data to Google
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  disabled={mutation.isPending}
                  onClick={() => setConfirming(false)}
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
