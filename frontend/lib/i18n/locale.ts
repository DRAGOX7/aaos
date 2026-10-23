export const LOCALE_STORAGE_KEY = "aaos-lang";

export const LOCALES = ["en", "ar"] as const;

export type Locale = (typeof LOCALES)[number];

export type Direction = "ltr" | "rtl";

// Set on <html> while an Arabic page waits for its first Arabic render, so the
// server's English text is never shown; globals.css hides the page meanwhile.
export const LOCALE_PENDING_ATTRIBUTE = "data-aaos-locale-pending";

// Used when localStorage is blocked, so a choice still lasts for this page.
let memoryLocale: Locale = "en";
const listeners = new Set<() => void>();

export function isLocale(value: unknown): value is Locale {
  return typeof value === "string" && (LOCALES as readonly string[]).includes(value);
}

export function directionOf(locale: Locale): Direction {
  return locale === "ar" ? "rtl" : "ltr";
}

export function readLocale(): Locale {
  try {
    const stored = window.localStorage.getItem(LOCALE_STORAGE_KEY);
    return isLocale(stored) ? stored : "en";
  } catch {
    return memoryLocale;
  }
}

function writeLocale(locale: Locale): void {
  memoryLocale = locale;
  try {
    window.localStorage.setItem(LOCALE_STORAGE_KEY, locale);
  } catch {
    // Storage can be blocked; the choice then lasts for this page only.
  }
}

/** Sets lang and dir on <html> so the browser lays the page out for the language. */
export function applyLocale(locale: Locale): void {
  const root = document.documentElement;
  root.lang = locale;
  root.dir = directionOf(locale);
}

/** Saves and applies a choice, then tells every subscriber (useSyncExternalStore). */
export function setLocale(locale: Locale): void {
  writeLocale(locale);
  applyLocale(locale);
  listeners.forEach((listener) => listener());
}

/** Subscribes to language changes from this page and from other tabs. */
export function subscribeLocale(listener: () => void): () => void {
  listeners.add(listener);
  const onStorage = (event: StorageEvent) => {
    if (event.key === LOCALE_STORAGE_KEY) {
      applyLocale(readLocale());
      listener();
    }
  };
  window.addEventListener("storage", onStorage);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("storage", onStorage);
  };
}

// Runs in <head> before first paint (see app/layout.tsx), so it cannot import
// anything; it mirrors readLocale + applyLocale above. English needs nothing:
// the server already renders English, left to right.
export const LOCALE_INIT_SCRIPT = `(function(){try{if(localStorage.getItem(${JSON.stringify(
  LOCALE_STORAGE_KEY,
)})==="ar"){var r=document.documentElement;r.lang="ar";r.dir="rtl";r.setAttribute(${JSON.stringify(
  LOCALE_PENDING_ATTRIBUTE,
)},"")}}catch(e){}})();`;
