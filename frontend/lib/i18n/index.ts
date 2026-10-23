import { useSyncExternalStore } from "react";

import { AR, AR_PLURALS, AR_STATUS, type MessageKey, type PluralKey } from "@/lib/i18n/messages-ar";
import { directionOf, readLocale, subscribeLocale, type Direction, type Locale } from "@/lib/i18n/locale";

export type { MessageKey, PluralKey } from "@/lib/i18n/messages-ar";

type Vars = Readonly<Record<string, string | number>>;

const EN_PLURALS: Record<PluralKey, Readonly<{ one: string; other: string }>> = {
  file: { one: "{count} file", other: "{count} files" },
  page: { one: "{count} page", other: "{count} pages" },
  pastCourse: { one: "{count} past", other: "{count} past" },
  minute: { one: "{count} minute", other: "{count} minutes" },
};

// Arabic dates keep Western digits so they read alongside course codes like AI380.
const ARABIC_DATE_LOCALE = "ar-JO-u-nu-latn";

function fill(template: string, vars?: Vars): string {
  if (!vars) return template;
  return template.replace(/\{(\w+)\}/g, (match, name: string) =>
    name in vars ? String(vars[name]) : match,
  );
}

export type Translator = Readonly<{
  locale: Locale;
  dir: Direction;
  /** A fixed interface string; the English text is the key. */
  t: (key: MessageKey, vars?: Vars) => string;
  /** A counted phrase with the language's plural forms. */
  tn: (key: PluralKey, count: number) => string;
  /** Text known only at run time (an API message); unknown text is shown as is. */
  tx: (text: string) => string;
  /** A backend status code such as AWAITING_APPROVAL, in words; English uses `english` if given. */
  status: (code: string, english?: (code: string) => string) => string;
  /** The Intl locale for dates: the component's English choice, or Arabic. */
  dateLocale: (english: string | undefined) => string | undefined;
}>;

const arabicPlural = new Intl.PluralRules("ar");

export function createTranslator(locale: Locale): Translator {
  const arabic = locale === "ar";
  return {
    locale,
    dir: directionOf(locale),
    t: (key, vars) => fill(arabic ? AR[key] : key, vars),
    tn: (key, count) => {
      if (!arabic) {
        const forms = EN_PLURALS[key];
        return fill(count === 1 ? forms.one : forms.other, { count });
      }
      const forms = AR_PLURALS[key];
      return fill(forms[arabicPlural.select(count) as keyof typeof forms] ?? forms.other, { count });
    },
    tx: (text) => (arabic && text in AR ? AR[text as MessageKey] : text),
    status: (code, english) => {
      if (arabic && code in AR_STATUS) return AR_STATUS[code];
      return english ? english(code) : code.replaceAll("_", " ");
    },
    dateLocale: (english) => (arabic ? ARABIC_DATE_LOCALE : english),
  };
}

const TRANSLATORS: Record<Locale, Translator> = {
  en: createTranslator("en"),
  ar: createTranslator("ar"),
};

// The server cannot read the saved choice, so it renders English; an Arabic
// page stays hidden until its first Arabic render (see LocaleSync).
const serverLocale = (): Locale => "en";

export function useLocale(): Locale {
  return useSyncExternalStore(subscribeLocale, readLocale, serverLocale);
}

export function useI18n(): Translator {
  return TRANSLATORS[useLocale()];
}
