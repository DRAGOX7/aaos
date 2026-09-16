import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetTrigger,
} from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";

describe("AAOS design system foundation", () => {
  it("provides the shared button primitive", () => {
    render(<Button>Continue</Button>);

    expect(
      screen.getByRole("button", { name: "Continue" }),
    ).toBeEnabled();
  });

  it("provides the shared loading primitive", () => {
    render(<Skeleton data-testid="loading-skeleton" />);

    expect(
      screen.getByTestId("loading-skeleton"),
    ).toBeInTheDocument();
  });

  it("provides the shared sheet primitive", () => {
    render(
      <Sheet>
        <SheetTrigger>Open navigation</SheetTrigger>
        <SheetContent>Navigation content</SheetContent>
      </Sheet>,
    );

    fireEvent.click(
      screen.getByRole("button", { name: "Open navigation" }),
    );

    expect(
      screen.getByText("Navigation content"),
    ).toBeInTheDocument();
  });

  it("provides the shared tooltip primitive", () => {
    render(
      <TooltipProvider delayDuration={0}>
        <Tooltip open>
          <TooltipTrigger>Help</TooltipTrigger>
          <TooltipContent>Helpful context</TooltipContent>
        </Tooltip>
      </TooltipProvider>,
    );

    expect(
      screen.getByRole("button", { name: "Help" }),
    ).toBeInTheDocument();

    expect(
      screen.getByText("Helpful context"),
    ).toBeInTheDocument();
  });
});
