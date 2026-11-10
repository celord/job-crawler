import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { QueueItem } from "./QueueItem";
import type { QueueItem as QueueItemType } from "../types";

vi.mock("../api/client", () => ({
  api: { get: vi.fn(), post: vi.fn(), delete: vi.fn() },
}));

function makeQueueItem(id: string, overrides: Partial<QueueItemType> = {}): QueueItemType {
  return {
    id,
    job_key: `gh|acme|${id}`,
    title: `Job ${id}`,
    company: "Acme",
    mode: "quick",
    status: "error",
    subtasks: [],
    attempt: 1,
    max_attempts: 3,
    next_retry_at: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    error: null,
    score: null,
    ...overrides,
  };
}

function renderItem(item: QueueItemType) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const Wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return render(<QueueItem item={item} />, { wrapper: Wrapper });
}

describe("QueueItem", () => {
  const writeText = vi.fn().mockResolvedValue(undefined);

  beforeEach(() => {
    writeText.mockClear();
    vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText } });
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("shows the first line of the error when expanded", () => {
    renderItem(
      makeQueueItem("1", { error: "HTTP 429: rate limited\nsecond line\nthird line" }),
    );

    expect(screen.queryByText(/HTTP 429: rate limited/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Show error" }));
    expect(screen.getByText(/HTTP 429: rate limited/)).toBeTruthy();
    expect(screen.queryByText(/second line/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Hide error" }));
    expect(screen.queryByText(/HTTP 429: rate limited/)).toBeNull();
  });

  it("copies the error JSON to the clipboard", async () => {
    const { findByRole } = renderItem(makeQueueItem("1", { error: "HTTP 429: rate limited" }));

    fireEvent.click(screen.getByRole("button", { name: "Show error" }));
    fireEvent.click(screen.getByRole("button", { name: "Copy error JSON" }));

    const payload = JSON.parse(writeText.mock.calls[0][0] as string);
    expect(payload).toMatchObject({
      job_key: "gh|acme|1",
      mode: "quick",
      status: "error",
      error: "HTTP 429: rate limited",
    });
    expect(await findByRole("button", { name: "Copied" })).toBeTruthy();
  });

  it("hides the error toggle when there is no error", () => {
    renderItem(makeQueueItem("1", { status: "done", error: null }));

    expect(screen.queryByRole("button", { name: "Show error" })).toBeNull();
  });

  it("disables Dismiss for running and queued items with an explanation", () => {
    renderItem(makeQueueItem("1", { status: "running" }));
    const runningDismiss = screen.getByRole("button", { name: "Dismiss" });
    expect(runningDismiss.hasAttribute("disabled")).toBe(true);
    expect(runningDismiss.getAttribute("title")).toContain("Stop");

    cleanup();
    renderItem(makeQueueItem("2", { status: "todo" }));
    expect(screen.getByRole("button", { name: "Dismiss" }).hasAttribute("disabled")).toBe(true);

    cleanup();
    renderItem(makeQueueItem("3", { status: "done" }));
    expect(screen.getByRole("button", { name: "Dismiss" }).hasAttribute("disabled")).toBe(false);
  });
});
