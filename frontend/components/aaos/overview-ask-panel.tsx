import Link from "next/link";
import { MessageCircle } from "lucide-react";

import { SectionCard } from "@/components/aaos/section-card";

export function OverviewAskPanel() {
  return (
    <SectionCard
      title="Ask AAOS"
      className="border-primary/20 bg-[linear-gradient(145deg,rgba(255,255,255,0.97),rgba(239,248,255,0.88))]"
      action={
        <MessageCircle aria-hidden="true" className="size-5 text-primary" strokeWidth={1.8} />
      }
    >
      <div className="flex flex-col justify-between gap-3 sm:flex-row sm:items-start">
        <div className="max-w-2xl">
          <p className="text-lg font-semibold tracking-tight text-primary-deep sm:text-xl">
            Ask a question grounded in your academic knowledge base.
          </p>
          <p className="mt-1.5 text-sm leading-5 text-muted-foreground">
            Grounded answers keep citations visible and say when the evidence is not enough.
          </p>
        </div>
        <Link
          href="/ask"
          className="shrink-0 text-sm font-semibold text-primary underline-offset-4 transition-colors hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          Open full Ask AAOS
        </Link>
      </div>
    </SectionCard>
  );
}
