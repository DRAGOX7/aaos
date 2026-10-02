import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { AaosMark } from "@/components/aaos/aaos-mark";

describe("AAOS mark", () => {
  it("is decorative by default, so the visible brand name is what is announced", () => {
    const { container } = render(<AaosMark />);

    expect(container.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
  });

  it("can be labelled when it stands alone", () => {
    render(<AaosMark title="AAOS" />);

    expect(screen.getByRole("img", { name: "AAOS" })).toBeInTheDocument();
  });

  it("gives each gradient instance its own id", () => {
    const { container } = render(
      <>
        <AaosMark />
        <AaosMark />
      </>,
    );

    const ids = [...container.querySelectorAll("linearGradient")].map((node) => node.id);
    expect(ids).toHaveLength(2);
    expect(new Set(ids).size).toBe(2);
  });

  it("follows the text colour in the mono variant", () => {
    const { container } = render(<AaosMark variant="mono" />);

    expect(container.querySelector("linearGradient")).toBeNull();
    expect(container.querySelector("g")).toHaveAttribute("stroke", "currentColor");
  });
});
