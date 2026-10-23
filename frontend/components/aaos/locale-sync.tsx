"use client";

import { useLayoutEffect } from "react";

import { useLocale } from "@/lib/i18n";
import { applyLocale, LOCALE_PENDING_ATTRIBUTE, readLocale } from "@/lib/i18n/locale";

/**
 * Keeps <html lang dir> on the chosen language, and reveals an Arabic page once
 * its first Arabic render is committed (before that paint), so the server's
 * English text never shows.
 */
export function LocaleSync() {
  const locale = useLocale();

  useLayoutEffect(() => {
    // The hydration pass still uses the server's English; wait for the real one.
    if (locale !== readLocale()) return;
    applyLocale(locale);
    document.documentElement.removeAttribute(LOCALE_PENDING_ATTRIBUTE);
  }, [locale]);

  return null;
}
