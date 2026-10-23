import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ThemeToggle } from "@/components/aaos/theme-toggle";
import { setLocale } from "@/lib/i18n/locale";
import { THEME_INIT_SCRIPT, THEME_STORAGE_KEY } from "@/lib/theme";

type ChangeListener = (event: { matches: boolean }) => void;

/** A controllable prefers-color-scheme media query. */
function mockSystemTheme(initiallyDark: boolean) {
  const listeners = new Set<ChangeListener>();
  const query = {
    matches: initiallyDark,
    addEventListener: (_: string, listener: ChangeListener) => listeners.add(listener),
    removeEventListener: (_: string, listener: ChangeListener) => listeners.delete(listener),
  };
  vi.stubGlobal("matchMedia", vi.fn(() => query));
  return {
    setDark(dark: boolean) {
      query.matches = dark;
      listeners.forEach((listener) => listener({ matches: dark }));
    },
    listenerCount: () => listeners.size,
  };
}

const root = () => document.documentElement;

beforeEach(() => {
  window.localStorage.clear();
  root().classList.remove("dark");
  root().style.colorScheme = "";
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

/** Opens the theme menu from the keyboard and picks one option. */
function choose(option: "Light" | "Dark" | "System") {
  fireEvent.keyDown(screen.getByRole("button"), { key: "Enter" });
  fireEvent.click(screen.getByRole("menuitemradio", { name: option }));
}

describe("ThemeToggle", () => {
  it("follows the system theme when nothing is saved", () => {
    mockSystemTheme(false);
    render(<ThemeToggle />);
    expect(screen.getByRole("button", { name: "Theme: System. Change theme" })).toBeInTheDocument();
    expect(root()).not.toHaveClass("dark");
    expect(root().style.colorScheme).toBe("light");
  });

  it("lists all three themes and marks the current one", () => {
    mockSystemTheme(false);
    window.localStorage.setItem(THEME_STORAGE_KEY, "dark");
    render(<ThemeToggle />);
    fireEvent.keyDown(screen.getByRole("button"), { key: "Enter" });
    const options = screen.getAllByRole("menuitemradio");
    expect(options.map((option) => option.textContent)).toEqual(["Light", "Dark", "System"]);
    expect(screen.getByRole("menuitemradio", { name: "Dark" })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByRole("menuitemradio", { name: "Light" })).toHaveAttribute("aria-checked", "false");
  });

  it("applies and saves each choice", () => {
    mockSystemTheme(false);
    render(<ThemeToggle />);

    choose("Dark");
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("dark");
    expect(root()).toHaveClass("dark");
    expect(root().style.colorScheme).toBe("dark");
    expect(screen.getByRole("button")).toHaveAccessibleName("Theme: Dark. Change theme");

    choose("Light");
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("light");
    expect(root()).not.toHaveClass("dark");

    choose("System");
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("system");
    expect(screen.getByRole("button")).toHaveAccessibleName("Theme: System. Change theme");
  });

  it("opens right to left, in Arabic, when the page is Arabic", () => {
    mockSystemTheme(false);
    act(() => setLocale("ar"));
    try {
      render(<ThemeToggle />);
      fireEvent.keyDown(screen.getByRole("button"), { key: "Enter" });
      expect(screen.getByRole("menu")).toHaveAttribute("dir", "rtl");
      expect(screen.getByRole("menuitemradio", { name: "داكن" })).toBeInTheDocument();
    } finally {
      act(() => setLocale("en"));
    }
  });

  it("applies a saved dark choice even when the system is light", () => {
    mockSystemTheme(false);
    window.localStorage.setItem(THEME_STORAGE_KEY, "dark");
    render(<ThemeToggle />);
    expect(root()).toHaveClass("dark");
    expect(screen.getByRole("button")).toHaveAccessibleName("Theme: Dark. Change theme");
  });

  it("tracks the system theme only while on System", () => {
    const system = mockSystemTheme(false);
    render(<ThemeToggle />);
    act(() => system.setDark(true));
    expect(root()).toHaveClass("dark");
    act(() => system.setDark(false));
    expect(root()).not.toHaveClass("dark");

    choose("Light");
    expect(system.listenerCount()).toBe(0);
    act(() => system.setDark(true));
    expect(root()).not.toHaveClass("dark");
  });

  it("still switches when storage is blocked", () => {
    mockSystemTheme(false);
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    render(<ThemeToggle />);
    choose("Dark");
    expect(root()).toHaveClass("dark");
    expect(screen.getByRole("button")).toHaveAccessibleName("Theme: Dark. Change theme");
    // Leave the in-memory fallback on the default for later tests.
    choose("System");
  });
});

describe("THEME_INIT_SCRIPT", () => {
  const run = () => new Function(THEME_INIT_SCRIPT)();

  it("applies a saved choice before React loads", () => {
    mockSystemTheme(false);
    window.localStorage.setItem(THEME_STORAGE_KEY, "dark");
    run();
    expect(root()).toHaveClass("dark");
    expect(root().style.colorScheme).toBe("dark");
  });

  it("falls back to the system theme for a missing or unknown value", () => {
    mockSystemTheme(true);
    window.localStorage.setItem(THEME_STORAGE_KEY, "sepia");
    run();
    expect(root()).toHaveClass("dark");

    mockSystemTheme(false);
    window.localStorage.removeItem(THEME_STORAGE_KEY);
    run();
    expect(root()).not.toHaveClass("dark");
  });

  it("never throws when storage is blocked", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    expect(run).not.toThrow();
  });
});
