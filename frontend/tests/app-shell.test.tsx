import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { AppShell } from "@/components/aaos/app-shell";
import { PageHeader } from "@/components/aaos/page-header";

describe("AAOS application shell", () => {
  it("provides the required application landmarks", () => {
    render(
      <AppShell>
        <p>Workspace content</p>
      </AppShell>,
    );

    expect(
      screen.getByRole("navigation", { name: "AAOS" }),
    ).toBeInTheDocument();

    expect(
      screen.getByRole("banner"),
    ).toBeInTheDocument();

    expect(
      screen.getByRole("main"),
    ).toBeInTheDocument();
  });

  it("uses only the approved 7B overview header copy", () => {
    render(
      <AppShell>
        <p>Workspace content</p>
      </AppShell>,
    );

    expect(
      screen.getByRole("heading", {
        level: 1,
        name: "Overview",
      }),
    ).toBeInTheDocument();

    expect(
      screen.getByText("Your academic workspace"),
    ).toBeInTheDocument();
  });

  it("preserves PageHeader defaults when rendered directly", () => {
    render(<PageHeader />);

    expect(
      screen.getByRole("heading", {
        level: 1,
        name: "Overview",
      }),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Your academic workspace"),
    ).toBeInTheDocument();
  });

  it("keeps child content inside the main workspace", () => {
    render(
      <AppShell>
        <p>Workspace content</p>
      </AppShell>,
    );

    const main = screen.getByRole("main");

    expect(
      within(main).getByText("Workspace content"),
    ).toBeInTheDocument();
  });
});
