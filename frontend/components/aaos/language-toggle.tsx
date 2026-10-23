"use client";

import { useI18n } from "@/lib/i18n";
import { setLocale } from "@/lib/i18n/locale";

/** Switches the dashboard between English and Arabic. */
export function LanguageToggle() {
  const { locale, t } = useI18n();
  const arabic = locale === "ar";
  return (
    <button
      aria-label={t("Language: English. Switch to Arabic.")}
      className="flex size-10 items-center justify-center rounded-full border border-border bg-card text-sm font-bold text-muted-foreground transition-colors hover:border-border-hover hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      onClick={() => setLocale(arabic ? "en" : "ar")}
      title={arabic ? "English" : "العربية"}
      type="button"
    >
      <span aria-hidden="true" lang={arabic ? "en" : "ar"}>
        {arabic ? "EN" : "ع"}
      </span>
    </button>
  );
}
