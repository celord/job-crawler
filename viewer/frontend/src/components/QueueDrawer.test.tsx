import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { QueueDrawer } from "./QueueDrawer";
import { api } from "../api/client";
import { useUiStore } from "../stores/uiStore";
import type { QueueItem as QueueItemType } from "../types";

vi.mock("../api/client", () => ({
  api: { get: vi.fn(), post: vi.fn(), delete: vi.fn() },
}));

const get = vi.mocked(api.get);
const post = vi.mocked(api.post);

function makeQueueItem(id: string, overrides: Partial<QueueItemType> = {}): QueueItemType {
  return {
    id,
    job_key: `gh|acme|${id}`,
    title: `Job ${id}`,
    company: "Acme",
    mode: "quick",
    status: "done",
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

function renderDrawer() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const Wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return render(<QueueDrawer />, { wrapper: Wrapper });
}

describe("QueueDrawer", () => {
  beforeEach(() => {
    get.mockReset();
    post.mockReset();
    get.mockImplementation((url: string) => {
      if (url === "/queue") return Promise.resolve({ data: [makeQueueItem("1")] });
      if (url === "/config")
        return Promise.resolve({ data: { scoreNotifyMinScore: 4 } });
      return Promise.resolve({
        data: new Blob(["{}"], { type: "application/x-ndjson" }),
        headers: { "content-disposition": 'attachment; filename="queue-errors-2026-11-09.jsonl"' },
      });
    });
    useUiStore.setState({ isQueueDrawerOpen: true });
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    useUiStore.setState({ isQueueDrawerOpen: false });
  });

  it("asks for confirmation before clearing finished items", async () => {
    renderDrawer();

    fireEvent.click(await screen.findByRole("button", { name: "Clear finished" }));
    expect(screen.getByRole("dialog", { name: "Clear finished items?" })).toBeTruthy();
    expect(post).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Clear finished items" }));
    await waitFor(() =>
      expect(post).toHaveBeenCalledWith("/queue/clear", { scope: "finished" }),
    );
    expect(screen.queryByRole("dialog", { name: /clear/i })).toBeNull();
  });

  it("cancelling clear all leaves the queue unchanged", async () => {
    renderDrawer();

    fireEvent.click(await screen.findByRole("button", { name: "Clear all" }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    expect(post).not.toHaveBeenCalled();
    expect(screen.queryByText(/Clear all items/i)).toBeNull();
    expect(await screen.findByText("Job 1")).toBeTruthy();
  });

  it("clears everything but running items after confirming", async () => {
    renderDrawer();

    fireEvent.click(await screen.findByRole("button", { name: "Clear all" }));
    fireEvent.click(screen.getByRole("button", { name: "Clear all items" }));
    await waitFor(() => expect(post).toHaveBeenCalledWith("/queue/clear", { scope: "all" }));
  });

  it("downloads the error log as a blob", async () => {
    const createObjectURL = vi.fn(() => "blob:mock");
    const revokeObjectURL = vi.fn();
    Object.defineProperty(URL, "createObjectURL", { value: createObjectURL, configurable: true });
    Object.defineProperty(URL, "revokeObjectURL", { value: revokeObjectURL, configurable: true });

    renderDrawer();

    fireEvent.click(await screen.findByRole("button", { name: "Download error log" }));
    await waitFor(() => expect(createObjectURL).toHaveBeenCalled());
    expect(get).toHaveBeenCalledWith(
      "/queue/errors",
      expect.objectContaining({ responseType: "blob" }),
    );
  });

  it("asks for confirmation before clearing the error log", async () => {
    renderDrawer();

    fireEvent.click(await screen.findByRole("button", { name: "Clear error log" }));
    expect(post).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Clear log" }));
    await waitFor(() => expect(post).toHaveBeenCalledWith("/queue/errors/clear"));
  });
});
