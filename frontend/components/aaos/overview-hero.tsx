"use client";

import { type FormEvent, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowUp } from "lucide-react";

import { writeAskHandoff } from "@/lib/ask/handoff";
import { useI18n, type MessageKey } from "@/lib/i18n";
import { useClientMinute } from "@/lib/time/use-client-minute";

const SUGGESTIONS: readonly MessageKey[] = [
  "What's due this week?",
  "What changed in Moodle since yesterday?",
  "Plan my study week",
];

function greeting(now: Date): MessageKey {
  const hour = now.getHours();
  if (hour < 12) return "Good morning";
  if (hour < 18) return "Good afternoon";
  return "Good evening";
}

const DATE_OPTIONS: Intl.DateTimeFormatOptions = {
  weekday: "long",
  day: "numeric",
  month: "long",
};

export function OverviewHero() {
  const router = useRouter();
  const { t, dateLocale } = useI18n();
  const [question, setQuestion] = useState("");
  const minute = useClientMinute();
  const today =
    minute === null
      ? null
      : `${t(greeting(new Date(minute)))} · ${new Intl.DateTimeFormat(dateLocale("en-GB"), DATE_OPTIONS).format(minute)}`;

  function ask(text: string) {
    if (writeAskHandoff(text)) router.push("/ask");
  }

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    ask(question);
  }

  return (
    <section
      aria-labelledby="overview-title"
      className="relative overflow-hidden rounded-3xl bg-hero px-6 py-8 text-white sm:px-10 sm:py-9"
    >
      <div
        aria-hidden="true"
        className="pointer-events-none absolute -end-20 -top-32 size-[26rem] rounded-full bg-[radial-gradient(circle,rgb(22_119_242_/_0.55),rgb(22_119_242_/_0)_70%)]"
      />
      <div className="relative flex flex-col gap-5">
        <div className="flex flex-col gap-1.5">
          <p className="min-h-5 text-sm font-medium text-hero-muted">{today}</p>
          <h1
            id="overview-title"
            className="text-balance text-[1.75rem] font-bold tracking-[-0.02em] sm:text-[2.125rem]"
          >
            {t("What are we studying today?")}
          </h1>
        </div>

        <form
          onSubmit={onSubmit}
          className="flex items-center gap-2 rounded-2xl bg-card p-2 shadow-[0_18px_40px_rgb(5_14_38_/_0.25)]"
        >
          <label htmlFor="overview-ask" className="sr-only">
            {t("Ask AAOS")}
          </label>
          <input
            id="overview-ask"
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            maxLength={4096}
            placeholder={t("Ask about your courses, files, deadlines or Moodle changes…")}
            className="h-11 min-w-0 flex-1 bg-transparent px-3 text-base text-foreground outline-none placeholder:text-text-subtle"
          />
          <button
            type="submit"
            className="inline-flex h-11 shrink-0 items-center gap-2 rounded-xl bg-primary px-5 text-[15px] font-bold text-white transition-colors hover:bg-primary-strong focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
          >
            {t("Ask")}
            <ArrowUp aria-hidden="true" className="size-4" strokeWidth={2.4} />
          </button>
        </form>

        <div className="flex flex-wrap gap-2" aria-label={t("Suggested questions")} role="group">
          {SUGGESTIONS.map((suggestion) => (
            <button
              key={suggestion}
              type="button"
              onClick={() => ask(t(suggestion))}
              className="min-h-9 rounded-full border border-white/25 bg-white/10 px-4 py-1.5 text-start text-[13px] font-medium text-white transition-colors hover:bg-white/20 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white"
            >
              {t(suggestion)}
            </button>
          ))}
        </div>
      </div>
    </section>
  );
}
