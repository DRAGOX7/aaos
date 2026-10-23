"use client";

import { useEffect, useLayoutEffect, useSyncExternalStore } from "react";
import { Monitor, Moon, Sun } from "lucide-react";

import {
  applyTheme,
  readThemeChoice,
  setThemeChoice,
  subscribeThemeChoice,
  watchSystemTheme,
  type ThemeChoice,
} from "@/lib/theme";

const NEXT_CHOICE: Record<ThemeChoice, ThemeChoice> = {
  light: "dark",
  dark: "system",
  system: "light",
};

const CHOICE_LABEL: Record<ThemeChoice, string> = {
  light: "Light",
  dark: "Dark",
  system: "System",
};

const CHOICE_ICON = { light: Sun, dark: Moon, system: Monitor } as const;

// The server cannot read the saved choice, so it renders "system"; the inline
// script in app/layout.tsx has already applied the real theme before paint.
const serverChoice = (): ThemeChoice => "system";

/** Cycles the dashboard theme: Light, then Dark, then System (follow the OS). */
export function ThemeToggle() {
  const choice = useSyncExternalStore(subscribeThemeChoice, readThemeChoice, serverChoice);

  // Re-apply on mount too: a development remount can drop the <html> class.
  useLayoutEffect(() => {
    applyTheme(choice);
  }, [choice]);

  useEffect(() => {
    if (choice !== "system") return;
    return watchSystemTheme(() => applyTheme("system"));
  }, [choice]);

  const next = NEXT_CHOICE[choice];
  const Icon = CHOICE_ICON[choice];
  return (
    <button
      aria-label={`Theme: ${CHOICE_LABEL[choice]}. Switch to ${CHOICE_LABEL[next]}.`}
      className="flex size-10 items-center justify-center rounded-full border border-border bg-card text-muted-foreground transition-colors hover:border-border-hover hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      onClick={() => setThemeChoice(next)}
      title={`Theme: ${CHOICE_LABEL[choice]}`}
      type="button"
    >
      <Icon aria-hidden="true" className="size-[18px]" strokeWidth={1.9} />
    </button>
  );
}
