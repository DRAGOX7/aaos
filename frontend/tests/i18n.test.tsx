import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { LanguageToggle } from "@/components/aaos/language-toggle";
import { LocaleSync } from "@/components/aaos/locale-sync";
import { createTranslator } from "@/lib/i18n";
import {
  LOCALE_INIT_SCRIPT,
  LOCALE_PENDING_ATTRIBUTE,
  LOCALE_STORAGE_KEY,
  setLocale,
} from "@/lib/i18n/locale";
import { AR, AR_PLURALS } from "@/lib/i18n/messages-ar";

const root = () => document.documentElement;

beforeEach(() => {
  window.localStorage.clear();
  root().lang = "en";
  root().dir = "ltr";
  root().removeAttribute(LOCALE_PENDING_ATTRIBUTE);
});

afterEach(() => {
  act(() => setLocale("en"));
  vi.restoreAllMocks();
});

describe("translator", () => {
  const en = createTranslator("en");
  const ar = createTranslator("ar");

  it("leaves English text exactly as written", () => {
    expect(en.t("Overview")).toBe("Overview");
    expect(en.t("Due: {date}", { date: "Oct 24, 2026" })).toBe("Due: Oct 24, 2026");
    expect(en.tn("file", 1)).toBe("1 file");
    expect(en.tn("file", 3)).toBe("3 files");
    expect(en.status("AWAITING_APPROVAL")).toBe("AWAITING APPROVAL");
    expect(en.dateLocale("en-GB")).toBe("en-GB");
  });

  it("translates fixed text and fills values", () => {
    expect(ar.t("Overview")).toBe("نظرة عامة");
    expect(ar.t("Due: {date}", { date: "24 Oct" })).toBe("موعد التسليم: 24 Oct");
    expect(ar.status("AWAITING_APPROVAL")).toBe("بانتظار الموافقة");
    expect(ar.status("SOMETHING_NEW")).toBe("SOMETHING NEW");
  });

  it("uses the Arabic plural forms", () => {
    expect(ar.tn("file", 1)).toBe("ملف واحد");
    expect(ar.tn("file", 2)).toBe("ملفان");
    expect(ar.tn("file", 5)).toBe("5 ملفات");
    expect(ar.tn("file", 11)).toBe("11 ملفًا");
    expect(ar.tn("file", 100)).toBe("100 ملف");
  });

  it("shows unknown runtime text unchanged", () => {
    expect(ar.tx("The requested course is not available.")).toBe("المقرر المطلوب غير متاح.");
    expect(ar.tx("AAOS courses request failed (502)")).toBe("AAOS courses request failed (502)");
  });

  it("keeps every placeholder of a key in its Arabic text", () => {
    for (const [english, arabic] of Object.entries(AR)) {
      const names = (text: string) => [...text.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort();
      expect(names(arabic), english).toEqual(names(english));
    }
    for (const forms of Object.values(AR_PLURALS)) {
      expect(forms.other).toContain("{count}");
    }
  });

  it("has Arabic for every message shown through tx()", () => {
    const source = (file: string) => readFileSync(resolve(process.cwd(), file), "utf8");
    const pick = (text: string, pattern: RegExp) => [...text.matchAll(pattern)].map((m) => m[1]);
    const runtime = [
      ...pick(source("components/aaos/ask-view.tsx"), /message: "([^"]+)"/g),
      ...pick(source("components/aaos/ask-result-cards.tsx"), /setError\("([^"]+)"\)/g),
      ...pick(source("components/aaos/ask-result-cards.tsx"), /return "([^"]+)" satisfies/g),
      ...pick(source("lib/api/errors.ts"), /^ {2}[A-Z_]+: "([^"]+)",$/gm),
    ];
    expect(runtime.length).toBeGreaterThan(15);
    for (const message of runtime) {
      expect(Object.keys(AR), message).toContain(message);
    }
  });
});

describe("LanguageToggle and LocaleSync", () => {
  it("switches to Arabic, right to left, and back", () => {
    render(
      <>
        <LanguageToggle />
        <LocaleSync />
      </>,
    );
    const button = screen.getByRole("button", { name: "Language: English. Switch to Arabic." });

    fireEvent.click(button);
    expect(window.localStorage.getItem(LOCALE_STORAGE_KEY)).toBe("ar");
    expect(root().lang).toBe("ar");
    expect(root().dir).toBe("rtl");
    expect(button).toHaveAccessibleName("اللغة: العربية. التبديل إلى الإنجليزية.");

    fireEvent.click(button);
    expect(window.localStorage.getItem(LOCALE_STORAGE_KEY)).toBe("en");
    expect(root().dir).toBe("ltr");
    expect(button).toHaveAccessibleName("Language: English. Switch to Arabic.");
  });

  it("reveals a pending Arabic page once Arabic is rendered", () => {
    window.localStorage.setItem(LOCALE_STORAGE_KEY, "ar");
    root().setAttribute(LOCALE_PENDING_ATTRIBUTE, "");
    render(<LocaleSync />);
    expect(root().hasAttribute(LOCALE_PENDING_ATTRIBUTE)).toBe(false);
    expect(root().dir).toBe("rtl");
  });
});

describe("LOCALE_INIT_SCRIPT", () => {
  const run = () => new Function(LOCALE_INIT_SCRIPT)();

  it("lays out a saved Arabic choice before React loads, hidden until rendered", () => {
    window.localStorage.setItem(LOCALE_STORAGE_KEY, "ar");
    run();
    expect(root().lang).toBe("ar");
    expect(root().dir).toBe("rtl");
    expect(root().hasAttribute(LOCALE_PENDING_ATTRIBUTE)).toBe(true);
  });

  it("changes nothing for English or an unknown value", () => {
    window.localStorage.setItem(LOCALE_STORAGE_KEY, "fr");
    run();
    expect(root().dir).toBe("ltr");
    expect(root().hasAttribute(LOCALE_PENDING_ATTRIBUTE)).toBe(false);
  });

  it("never throws when storage is blocked", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    expect(run).not.toThrow();
  });
});
