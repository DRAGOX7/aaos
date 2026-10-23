export const THEME_STORAGE_KEY = "aaos-theme";

export const THEME_CHOICES = ["light", "dark", "system"] as const;

export type ThemeChoice = (typeof THEME_CHOICES)[number];

export type ResolvedTheme = "light" | "dark";

const DARK_QUERY = "(prefers-color-scheme: dark)";

// Used when localStorage is blocked, so a choice still lasts for this page.
let memoryChoice: ThemeChoice = "system";
const listeners = new Set<() => void>();

export function isThemeChoice(value: unknown): value is ThemeChoice {
  return (
    typeof value === "string" &&
    (THEME_CHOICES as readonly string[]).includes(value)
  );
}

export function readThemeChoice(): ThemeChoice {
  try {
    const stored = window.localStorage.getItem(THEME_STORAGE_KEY);
    return isThemeChoice(stored) ? stored : "system";
  } catch {
    return memoryChoice;
  }
}

export function writeThemeChoice(choice: ThemeChoice): void {
  memoryChoice = choice;
  try {
    window.localStorage.setItem(THEME_STORAGE_KEY, choice);
  } catch {
    // Storage can be blocked; the choice then lasts for this page only.
  }
}

/** Saves and applies a choice, then tells every subscriber (useSyncExternalStore). */
export function setThemeChoice(choice: ThemeChoice): void {
  writeThemeChoice(choice);
  applyTheme(choice);
  listeners.forEach((listener) => listener());
}

/** Subscribes to choice changes from this page and from other tabs. */
export function subscribeThemeChoice(listener: () => void): () => void {
  listeners.add(listener);
  const onStorage = (event: StorageEvent) => {
    if (event.key === THEME_STORAGE_KEY) {
      applyTheme(readThemeChoice());
      listener();
    }
  };
  window.addEventListener("storage", onStorage);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("storage", onStorage);
  };
}

export function systemPrefersDark(): boolean {
  return (
    typeof window.matchMedia === "function" &&
    window.matchMedia(DARK_QUERY).matches
  );
}

export function resolveTheme(choice: ThemeChoice): ResolvedTheme {
  if (choice === "system") {
    return systemPrefersDark() ? "dark" : "light";
  }
  return choice;
}

export function applyTheme(choice: ThemeChoice): ResolvedTheme {
  const resolved = resolveTheme(choice);
  const root = document.documentElement;
  root.classList.toggle("dark", resolved === "dark");
  root.style.colorScheme = resolved;
  return resolved;
}

export function watchSystemTheme(onChange: () => void): () => void {
  if (typeof window.matchMedia !== "function") {
    return () => {};
  }
  const query = window.matchMedia(DARK_QUERY);
  query.addEventListener("change", onChange);
  return () => query.removeEventListener("change", onChange);
}

// Runs in <head> before first paint (see app/layout.tsx), so it cannot import
// anything; it mirrors readThemeChoice + applyTheme above.
export const THEME_INIT_SCRIPT = `(function(){try{var c=localStorage.getItem(${JSON.stringify(
  THEME_STORAGE_KEY,
)});if(c!=="light"&&c!=="dark")c=window.matchMedia&&window.matchMedia(${JSON.stringify(
  DARK_QUERY,
)}).matches?"dark":"light";var r=document.documentElement;r.classList.toggle("dark",c==="dark");r.style.colorScheme=c}catch(e){}})();`;
