"use client";

import { type FormEvent, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowUp } from "lucide-react";

import { writeAskHandoff } from "@/lib/ask/handoff";
import { useClientMinute } from "@/lib/time/use-client-minute";

const SUGGESTIONS = [
  "What's due this week?",
  "What changed in Moodle since yesterday?",
  "Plan my study week",
] as const;

function greeting(now: Date): string {
  const hour = now.getHours();
  if (hour < 12) return "Good morning";
  if (hour < 18) return "Good afternoon";
  return "Good evening";
}

const dateFormat = new Intl.DateTimeFormat("en-GB", {
  weekday: "long",
  day: "numeric",
  month: "long",
});

export function OverviewHero() {
  const router = useRouter();
  const [question, setQuestion] = useState("");
  const minute = useClientMinute();
  const today =
    minute === null ? null : `${greeting(new Date(minute))} · ${dateFormat.format(minute)}`;

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
        className="pointer-events-none absolute -right-20 -top-32 size-[26rem] rounded-full bg-[radial-gradient(circle,rgb(22_119_242_/_0.55),rgb(22_119_242_/_0)_70%)]"
      />
      <div className="relative flex flex-col gap-5">
        <div className="flex flex-col gap-1.5">
          <p className="min-h-5 text-sm font-medium text-hero-muted">{today}</p>
          <h1
            id="overview-title"
            className="text-balance text-[1.75rem] font-bold tracking-[-0.02em] sm:text-[2.125rem]"
          >
            What are we studying today?
          </h1>
        </div>

        <form
          onSubmit={onSubmit}
          className="flex items-center gap-2 rounded-2xl bg-card p-2 shadow-[0_18px_40px_rgb(5_14_38_/_0.25)]"
        >
          <label htmlFor="overview-ask" className="sr-only">
            Ask AAOS
          </label>
          <input
            id="overview-ask"
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            maxLength={4096}
            placeholder="Ask about your courses, files, deadlines or Moodle changes…"
            className="h-11 min-w-0 flex-1 bg-transparent px-3 text-base text-foreground outline-none placeholder:text-text-subtle"
          />
          <button
            type="submit"
            className="inline-flex h-11 shrink-0 items-center gap-2 rounded-xl bg-primary px-5 text-[15px] font-bold text-white transition-colors hover:bg-primary-strong focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
          >
            Ask
            <ArrowUp aria-hidden="true" className="size-4" strokeWidth={2.4} />
          </button>
        </form>

        <div className="flex flex-wrap gap-2" aria-label="Suggested questions" role="group">
          {SUGGESTIONS.map((suggestion) => (
            <button
              key={suggestion}
              type="button"
              onClick={() => ask(suggestion)}
              className="min-h-9 rounded-full border border-white/25 bg-white/10 px-4 py-1.5 text-left text-[13px] font-medium text-white transition-colors hover:bg-white/20 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white"
            >
              {suggestion}
            </button>
          ))}
        </div>
      </div>
    </section>
  );
}
