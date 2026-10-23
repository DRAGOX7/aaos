"use client";

import { useI18n, type MessageKey } from "@/lib/i18n";

/** A fixed interface string inside a server page, in the chosen language. */
export function LocalizedText({ text }: Readonly<{ text: MessageKey }>) {
  const { t } = useI18n();
  return t(text);
}
