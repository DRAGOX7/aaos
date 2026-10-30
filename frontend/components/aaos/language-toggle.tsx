"use client";

import { useI18n } from "@/lib/i18n";
import { setLocale } from "@/lib/i18n/locale";
import { cn } from "@/lib/utils";

/** Switches the dashboard between English and Arabic. */
export function LanguageToggle({ quiet = false }: { quiet?: boolean }) {
  const { locale, t } = useI18n();
  const arabic = locale === "ar";
  return (
    <button
      aria-label={t("Language: English. Switch to Arabic.")}
      className={cn("flex items-center justify-center text-sm text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        quiet ? "min-h-11 rounded-md px-1 font-medium" : "size-10 rounded-full border border-border bg-card font-bold hover:border-border-hover")}
      onClick={() => setLocale(arabic ? "en" : "ar")}
      title={arabic ? "English" : "العربية"}
      type="button"
    >
      <span aria-hidden="true" lang={quiet ? locale : arabic ? "en" : "ar"}>
        {quiet ? arabic ? "العربية" : "English" : arabic ? "EN" : "ع"}
      </span>
    </button>
  );
}
