"use client";

import type { components } from "@/types/api.generated";
import { useI18n } from "@/lib/i18n";
import { formatDeadline } from "@/lib/time/deadline";

/** Read-only preview of the server's proposed action, separate from approval expiry. */
export function CalendarActionPreview({ action }: { action: components["schemas"]["SafeActionSummary"] }) {
  const { t, dateLocale } = useI18n();
  return <dl className="mt-3 grid min-w-0 grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1 text-sm">
    <dt className="text-muted-foreground">{t("Starts")}</dt>
    <dd><time dateTime={action.start}>{formatDeadline(action.start, dateLocale("en-GB"), action.timezone)}</time></dd>
    <dt className="text-muted-foreground">{t("Ends")}</dt>
    <dd><time dateTime={action.end}>{formatDeadline(action.end, dateLocale("en-GB"), action.timezone)}</time></dd>
    <dt className="text-muted-foreground">{t("Time zone")}</dt><dd className="break-words"><bdi>{action.timezone}</bdi></dd>
    {action.location ? <><dt className="text-muted-foreground">{t("Location")}</dt><dd dir="auto" className="break-words">{action.location}</dd></> : null}
    {action.description ? <><dt className="text-muted-foreground">{t("Description")}</dt><dd dir="auto" className="whitespace-pre-wrap break-words">{action.description}</dd></> : null}
  </dl>;
}
