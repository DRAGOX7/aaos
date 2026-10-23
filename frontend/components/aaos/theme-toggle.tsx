"use client";

import { useEffect, useLayoutEffect, useSyncExternalStore } from "react";
import { Monitor, Moon, Sun } from "lucide-react";

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  applyTheme,
  isThemeChoice,
  readThemeChoice,
  setThemeChoice,
  subscribeThemeChoice,
  THEME_CHOICES,
  watchSystemTheme,
  type ThemeChoice,
} from "@/lib/theme";
import { useI18n, type MessageKey } from "@/lib/i18n";

const CHOICE_LABEL: Record<ThemeChoice, MessageKey> = {
  light: "Light",
  dark: "Dark",
  system: "System",
};

const CHOICE_ICON = { light: Sun, dark: Moon, system: Monitor } as const;

// The server cannot read the saved choice, so it renders "system"; the inline
// script in app/layout.tsx has already applied the real theme before paint.
const serverChoice = (): ThemeChoice => "system";

/** Picks the dashboard theme: Light, Dark, or System (follow the OS). */
export function ThemeToggle() {
  const { t, dir } = useI18n();
  const choice = useSyncExternalStore(subscribeThemeChoice, readThemeChoice, serverChoice);

  // Re-apply on mount too: a development remount can drop the <html> class.
  useLayoutEffect(() => {
    applyTheme(choice);
  }, [choice]);

  useEffect(() => {
    if (choice !== "system") return;
    return watchSystemTheme(() => applyTheme("system"));
  }, [choice]);

  const Icon = CHOICE_ICON[choice];
  const current = t(CHOICE_LABEL[choice]);
  return (
    // Radix menus are left to right unless told otherwise.
    <DropdownMenu dir={dir}>
      <DropdownMenuTrigger asChild>
        <button
          aria-label={t("Theme: {current}. Change theme", { current })}
          className="flex size-10 items-center justify-center rounded-full border border-border bg-card text-muted-foreground transition-colors hover:border-border-hover hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring data-[state=open]:border-border-hover data-[state=open]:text-foreground"
          title={t("Theme: {current}", { current })}
          type="button"
        >
          <Icon aria-hidden="true" className="size-[18px]" strokeWidth={1.9} />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-40">
        <DropdownMenuLabel>{t("Theme")}</DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuRadioGroup
          value={choice}
          onValueChange={(value) => {
            if (isThemeChoice(value)) setThemeChoice(value);
          }}
        >
          {THEME_CHOICES.map((option) => {
            const OptionIcon = CHOICE_ICON[option];
            return (
              <DropdownMenuRadioItem key={option} value={option}>
                <OptionIcon aria-hidden="true" strokeWidth={1.9} />
                {t(CHOICE_LABEL[option])}
              </DropdownMenuRadioItem>
            );
          })}
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
