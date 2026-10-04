import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ModelBadge } from "@/components/aaos/model-badge";
import { fetchModelStatus, selectModel, type ModelStatus } from "@/lib/api/model";

const LOCAL: ModelStatus = {
  provider: "lm_studio",
  model: "qwen/qwen3.5-4b",
  cloud: false,
  local_model: "qwen/qwen3.5-4b",
  gemini_model: "gemini-test-flash",
  gemini_ready: true,
  gemini_blocker: null,
};
const CLOUD: ModelStatus = { ...LOCAL, provider: "gemini", model: "gemini-test-flash", cloud: true };

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function renderBadge() {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <ModelBadge />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("model transport", () => {
  it("reads with GET and switches with a POST of provider and confirmation only", async () => {
    const fetchMock = vi.fn().mockImplementation(async () => json(CLOUD));
    vi.stubGlobal("fetch", fetchMock);

    await fetchModelStatus();
    await selectModel("gemini", true);

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      "/api/v1/model",
      expect.objectContaining({ method: "GET" }),
    );
    const [, init] = fetchMock.mock.calls[1];
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ provider: "gemini", confirm_cloud: true });
    expect(JSON.stringify(fetchMock.mock.calls)).not.toMatch(/key|token|secret|AIza/i);
  });

  it("turns a refusal into a known failure, and anything else into UNKNOWN", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValueOnce(json({ error: { code: "MODEL_BUSY", message: "x" } }, 409))
        .mockResolvedValueOnce(json({ error: { code: "SOMETHING_NEW" } }, 500))
        .mockResolvedValueOnce(new Response("not json", { status: 502 })),
    );

    await expect(selectModel("lm_studio", false)).rejects.toMatchObject({ failure: "MODEL_BUSY" });
    await expect(selectModel("lm_studio", false)).rejects.toMatchObject({ failure: "UNKNOWN" });
    await expect(selectModel("lm_studio", false)).rejects.toMatchObject({ failure: "UNKNOWN" });
  });
});

describe("model badge", () => {
  it("says the local model is answering", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json(LOCAL)));
    renderBadge();

    expect(await screen.findByText("Local · qwen/qwen3.5-4b")).toBeInTheDocument();
  });

  it("says plainly when the cloud model is answering", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json(CLOUD)));
    renderBadge();

    expect(await screen.findByText("Cloud · Gemini · gemini-test-flash")).toBeInTheDocument();
  });

  it("shows nothing when the model cannot be read", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("", { status: 500 })));
    const { container } = renderBadge();

    await waitFor(() => expect(fetch).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it("explains how to enable Gemini, and never asks for the key in the page", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        json({ ...LOCAL, gemini_ready: false, gemini_blocker: "GEMINI_API_KEY_MISSING" }),
      ),
    );
    renderBadge();
    fireEvent.click(await screen.findByRole("button", { name: /Change model/ }));

    expect(await screen.findByText(/set GEMINI_API_KEY in the terminal/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Gemini/ })).toBeDisabled();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(document.querySelector("input")).toBeNull();
  });

  it("needs an explicit confirmation before anything goes to Google", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(json(LOCAL))
      .mockResolvedValueOnce(json(CLOUD));
    vi.stubGlobal("fetch", fetchMock);
    renderBadge();
    fireEvent.click(await screen.findByRole("button", { name: /Change model/ }));

    fireEvent.click(await screen.findByRole("button", { name: /^Gemini/ }));
    expect(await screen.findByText("Send your data to Google?")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(1); // choosing Gemini alone sends nothing

    fireEvent.click(screen.getByRole("button", { name: "Send my data to Google" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    const [, init] = fetchMock.mock.calls[1];
    expect(JSON.parse(init.body)).toEqual({ provider: "gemini", confirm_cloud: true });
    expect(await screen.findByText("Cloud · Gemini · gemini-test-flash")).toBeInTheDocument();
  });

  it("cancelling the confirmation changes nothing", async () => {
    const fetchMock = vi.fn().mockResolvedValue(json(LOCAL));
    vi.stubGlobal("fetch", fetchMock);
    renderBadge();
    fireEvent.click(await screen.findByRole("button", { name: /Change model/ }));
    fireEvent.click(await screen.findByRole("button", { name: /^Gemini/ }));

    fireEvent.click(await screen.findByRole("button", { name: "Cancel" }));

    expect(screen.queryByText("Send your data to Google?")).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("keeps the active choice enabled, and choosing it again changes nothing", async () => {
    const fetchMock = vi.fn().mockResolvedValue(json(LOCAL));
    vi.stubGlobal("fetch", fetchMock);
    renderBadge();
    fireEvent.click(await screen.findByRole("button", { name: /Change model/ }));

    const active = await screen.findByRole("button", { name: /^Local model/ });
    expect(active).toBeEnabled();
    expect(active).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(active);

    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("goes back to the local model without a confirmation", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(json(CLOUD))
      .mockResolvedValueOnce(json(LOCAL));
    vi.stubGlobal("fetch", fetchMock);
    renderBadge();
    fireEvent.click(await screen.findByRole("button", { name: /Change model/ }));

    fireEvent.click(await screen.findByRole("button", { name: /^Local model/ }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({
      provider: "lm_studio",
      confirm_cloud: false,
    });
    expect(await screen.findByText("Local · qwen/qwen3.5-4b")).toBeInTheDocument();
  });

  it("tells the owner when a question is running and keeps the current model", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValueOnce(json(LOCAL))
        .mockResolvedValueOnce(json({ error: { code: "MODEL_BUSY", message: "busy" } }, 409)),
    );
    renderBadge();
    fireEvent.click(await screen.findByRole("button", { name: /Change model/ }));
    fireEvent.click(await screen.findByRole("button", { name: /^Gemini/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Send my data to Google" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("A question is running");
    expect(screen.getByText("Local · qwen/qwen3.5-4b")).toBeInTheDocument();
  });
});
