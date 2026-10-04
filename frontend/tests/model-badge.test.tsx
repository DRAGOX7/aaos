import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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

/**
 * Route requests by method: reads return the current status (opening the panel
 * refreshes it, so the number of reads is not part of the contract), and each
 * switch request takes the next scripted response.
 */
function mockApi(initial: ModelStatus, ...switches: Response[]) {
  let current = initial;
  let models: string[] | null = ["gemini-3.1-flash-lite", "gemini-3.7-flash", "gemini-test-flash"];
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url.endsWith("/gemini-models")) {
      return models === null ? new Response("", { status: 503 }) : json({ models });
    }
    if (init?.method === "POST") {
      const next = switches.shift();
      if (!next) throw new Error("unexpected switch request");
      if (next.ok) current = (await next.clone().json()) as ModelStatus;
      return next;
    }
    return json(current);
  });
  vi.stubGlobal("fetch", fetchMock);
  const switchRequests = () =>
    fetchMock.mock.calls.filter(([, init]) => init?.method === "POST");
  return {
    fetchMock,
    switchRequests,
    setModels: (next: string[] | null) => {
      models = next;
    },
  };
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
    mockApi(LOCAL);
    renderBadge();

    expect(await screen.findByText("Local · qwen/qwen3.5-4b")).toBeInTheDocument();
  });

  it("says plainly when the cloud model is answering", async () => {
    mockApi(CLOUD);
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
    mockApi({ ...LOCAL, gemini_ready: false, gemini_blocker: "GEMINI_API_KEY_MISSING" });
    renderBadge();
    fireEvent.click(await screen.findByRole("button", { name: /Change model/ }));

    expect(await screen.findByText(/set GEMINI_API_KEY in the terminal/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Gemini/ })).toBeDisabled();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(document.querySelector("input")).toBeNull();
  });

  it("needs an explicit confirmation before anything goes to Google", async () => {
    const { switchRequests } = mockApi(LOCAL, json(CLOUD));
    renderBadge();
    fireEvent.click(await screen.findByRole("button", { name: /Change model/ }));

    fireEvent.click(await screen.findByRole("button", { name: /^Gemini/ }));
    expect(await screen.findByText("Send your data to Google?")).toBeInTheDocument();
    expect(switchRequests()).toHaveLength(0); // choosing Gemini alone sends nothing

    fireEvent.click(screen.getByRole("button", { name: "Send my data to Google" }));

    await waitFor(() => expect(switchRequests()).toHaveLength(1));
    expect(JSON.parse(String(switchRequests()[0][1]?.body))).toEqual({
      provider: "gemini",
      confirm_cloud: true,
      gemini_model: "gemini-test-flash", // the configured model, named explicitly
    });
    expect(await screen.findByText("Cloud · Gemini · gemini-test-flash")).toBeInTheDocument();
  });

  it("cancelling the confirmation changes nothing", async () => {
    const { switchRequests } = mockApi(LOCAL);
    renderBadge();
    fireEvent.click(await screen.findByRole("button", { name: /Change model/ }));
    fireEvent.click(await screen.findByRole("button", { name: /^Gemini/ }));

    fireEvent.click(await screen.findByRole("button", { name: "Cancel" }));

    expect(screen.queryByText("Send your data to Google?")).not.toBeInTheDocument();
    expect(switchRequests()).toHaveLength(0);
  });

  it("keeps the active choice enabled, and choosing it again changes nothing", async () => {
    const { switchRequests } = mockApi(LOCAL);
    renderBadge();
    fireEvent.click(await screen.findByRole("button", { name: /Change model/ }));

    const active = await screen.findByRole("button", { name: /^Local model/ });
    expect(active).toBeEnabled();
    expect(active).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(active);

    expect(switchRequests()).toHaveLength(0);
  });

  it("offers the models the key can use, with the configured one selected", async () => {
    mockApi(LOCAL);
    renderBadge();
    fireEvent.click(await screen.findByRole("button", { name: /Change model/ }));

    const picker = await screen.findByRole("combobox", { name: "Gemini model" });
    await waitFor(() => expect(picker).toBeEnabled());
    expect(within(picker).getAllByRole("option").map((option) => option.textContent)).toEqual([
      "gemini-3.1-flash-lite",
      "gemini-3.7-flash",
      "gemini-test-flash",
    ]);
    expect(picker).toHaveValue("gemini-test-flash");
  });

  it("has no picker until Gemini is set up in the backend", async () => {
    mockApi({ ...LOCAL, gemini_ready: false, gemini_blocker: "GEMINI_API_KEY_MISSING" });
    renderBadge();
    fireEvent.click(await screen.findByRole("button", { name: /Change model/ }));

    await screen.findByText("Answering model");
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
  });

  it("sends the picked model with the confirmed switch from local", async () => {
    const { switchRequests } = mockApi(LOCAL, json({ ...CLOUD, model: "gemini-3.7-flash" }));
    renderBadge();
    fireEvent.click(await screen.findByRole("button", { name: /Change model/ }));
    const picker = await screen.findByRole("combobox", { name: "Gemini model" });
    await waitFor(() => expect(picker).toBeEnabled());

    fireEvent.change(picker, { target: { value: "gemini-3.7-flash" } });
    expect(switchRequests()).toHaveLength(0); // picking alone changes nothing from local
    fireEvent.click(await screen.findByRole("button", { name: /^Gemini · gemini-3.7-flash/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Send my data to Google" }));

    await waitFor(() => expect(switchRequests()).toHaveLength(1));
    expect(JSON.parse(String(switchRequests()[0][1]?.body))).toEqual({
      provider: "gemini",
      confirm_cloud: true,
      gemini_model: "gemini-3.7-flash",
    });
  });

  it("changes the model at once while already in the cloud", async () => {
    const { switchRequests } = mockApi(CLOUD, json({ ...CLOUD, model: "gemini-3.1-flash-lite" }));
    renderBadge();
    fireEvent.click(await screen.findByRole("button", { name: /Change model/ }));
    const picker = await screen.findByRole("combobox", { name: "Gemini model" });
    await waitFor(() => expect(picker).toBeEnabled());

    fireEvent.change(picker, { target: { value: "gemini-3.1-flash-lite" } });

    await waitFor(() => expect(switchRequests()).toHaveLength(1));
    expect(JSON.parse(String(switchRequests()[0][1]?.body))).toEqual({
      provider: "gemini",
      confirm_cloud: true,
      gemini_model: "gemini-3.1-flash-lite",
    });
    expect(await screen.findByText("Cloud · Gemini · gemini-3.1-flash-lite")).toBeInTheDocument();
  });

  it("falls back to the configured model when the list cannot be loaded", async () => {
    const { setModels } = mockApi(LOCAL);
    setModels(null);
    renderBadge();
    fireEvent.click(await screen.findByRole("button", { name: /Change model/ }));

    expect(await screen.findByText(/model list could not be loaded/i)).toBeInTheDocument();
    const picker = screen.getByRole("combobox", { name: "Gemini model" });
    expect(within(picker).getAllByRole("option")).toHaveLength(1);
  });

  it("shows the last problem the model reported, in the panel", async () => {
    mockApi({ ...CLOUD, last_failure: "Gemini generation failed: http_429_RESOURCE_EXHAUSTED" });
    renderBadge();
    fireEvent.click(await screen.findByRole("button", { name: /Change model/ }));

    expect(
      await screen.findByText(/Last problem: Gemini generation failed: http_429_RESOURCE_EXHAUSTED/),
    ).toBeInTheDocument();
  });

  it("shows no problem line when nothing has gone wrong", async () => {
    mockApi(CLOUD);
    renderBadge();
    fireEvent.click(await screen.findByRole("button", { name: /Change model/ }));

    await screen.findByText("Answering model");
    expect(screen.queryByText(/Last problem/)).not.toBeInTheDocument();
  });

  it("goes back to the local model without a confirmation", async () => {
    const { switchRequests } = mockApi(CLOUD, json(LOCAL));
    renderBadge();
    fireEvent.click(await screen.findByRole("button", { name: /Change model/ }));

    fireEvent.click(await screen.findByRole("button", { name: /^Local model/ }));

    await waitFor(() => expect(switchRequests()).toHaveLength(1));
    expect(JSON.parse(String(switchRequests()[0][1]?.body))).toEqual({
      provider: "lm_studio",
      confirm_cloud: false,
    });
    expect(await screen.findByText("Local · qwen/qwen3.5-4b")).toBeInTheDocument();
  });

  it("tells the owner when a question is running and keeps the current model", async () => {
    mockApi(LOCAL, json({ error: { code: "MODEL_BUSY", message: "busy" } }, 409));
    renderBadge();
    fireEvent.click(await screen.findByRole("button", { name: /Change model/ }));
    fireEvent.click(await screen.findByRole("button", { name: /^Gemini/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Send my data to Google" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("A question is running");
    expect(screen.getByText("Local · qwen/qwen3.5-4b")).toBeInTheDocument();
  });
});
