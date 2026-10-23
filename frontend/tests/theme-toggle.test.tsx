import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ThemeToggle } from "@/components/aaos/theme-toggle";
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

describe("ThemeToggle", () => {
  it("follows the system theme when nothing is saved", () => {
    mockSystemTheme(false);
    render(<ThemeToggle />);
    expect(screen.getByRole("button", { name: "Theme: System. Switch to Light." })).toBeInTheDocument();
    expect(root()).not.toHaveClass("dark");
    expect(root().style.colorScheme).toBe("light");
  });

  it("cycles Light, Dark, System and saves each choice", () => {
    mockSystemTheme(false);
    render(<ThemeToggle />);
    const button = screen.getByRole("button");

    fireEvent.click(button);
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("light");
    expect(root()).not.toHaveClass("dark");
    expect(button).toHaveAccessibleName("Theme: Light. Switch to Dark.");

    fireEvent.click(button);
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("dark");
    expect(root()).toHaveClass("dark");
    expect(root().style.colorScheme).toBe("dark");
    expect(button).toHaveAccessibleName("Theme: Dark. Switch to System.");

    fireEvent.click(button);
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("system");
    expect(root()).not.toHaveClass("dark");
    expect(button).toHaveAccessibleName("Theme: System. Switch to Light.");
  });

  it("applies a saved dark choice even when the system is light", () => {
    mockSystemTheme(false);
    window.localStorage.setItem(THEME_STORAGE_KEY, "dark");
    render(<ThemeToggle />);
    expect(root()).toHaveClass("dark");
    expect(screen.getByRole("button")).toHaveAccessibleName("Theme: Dark. Switch to System.");
  });

  it("tracks the system theme only while on System", () => {
    const system = mockSystemTheme(false);
    render(<ThemeToggle />);
    act(() => system.setDark(true));
    expect(root()).toHaveClass("dark");
    act(() => system.setDark(false));
    expect(root()).not.toHaveClass("dark");

    fireEvent.click(screen.getByRole("button")); // Light
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
    const button = screen.getByRole("button");
    // The in-memory fallback keeps the last choice, so the start depends on test order.
    const seen = new Set<string | null>();
    for (let i = 0; i < 3; i += 1) {
      fireEvent.click(button);
      seen.add(button.getAttribute("aria-label"));
      if (root().classList.contains("dark")) {
        expect(button).toHaveAccessibleName("Theme: Dark. Switch to System.");
      }
    }
    expect(seen).toEqual(
      new Set([
        "Theme: Light. Switch to Dark.",
        "Theme: Dark. Switch to System.",
        "Theme: System. Switch to Light.",
      ]),
    );
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
