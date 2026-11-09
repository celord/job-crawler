import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, screen } from "@testing-library/react";
import type { ReactElement, ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { JobList } from "./JobList";
import type { Job } from "../types";

function makeJob(id: string, title = `Job ${id}`): Job {
  return {
    provider: "greenhouse",
    source_key: "acme",
    job_id: id,
    title,
    location: "Remote",
    employment_type: null,
    compensation: null,
    department: null,
    job_url: null,
    updated_at: null,
    posted_at: null,
    first_seen_at: null,
    last_seen_at: null,
    analysis: null,
    pipelines: {},
  };
}

const baseProps = {
  jobs: [makeJob("1"), makeJob("2")],
  isLoading: false,
  isFetching: false,
  isError: false,
  total: 2,
};

function renderList(ui: ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const Wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return render(ui, { wrapper: Wrapper });
}

describe("JobList", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("shows a skeleton list on first load", () => {
    renderList(<JobList {...baseProps} jobs={[]} isLoading isFetching total={0} />);

    expect(screen.getAllByTestId("job-skeleton")).toHaveLength(4);
    expect(screen.getByTestId("results-list").getAttribute("aria-busy")).toBe("true");
    expect(screen.queryByText("No jobs match the current filters.")).toBeNull();
  });

  it("shows a delayed progress bar while keeping the old list visible", () => {
    const { rerender } = renderList(<JobList {...baseProps} isFetching />);

    expect(screen.queryByTestId("search-progress")).toBeNull();
    act(() => vi.advanceTimersByTime(149));
    expect(screen.queryByTestId("search-progress")).toBeNull();

    act(() => vi.advanceTimersByTime(1));
    const progress = screen.getByTestId("search-progress");
    expect(progress.getAttribute("aria-hidden")).toBe("true");
    expect(screen.queryByRole("progressbar")).toBeNull();
    expect(screen.getByText("Job 1")).toBeTruthy();
    expect(screen.getByTestId("results-list").getAttribute("aria-busy")).toBe("true");
    expect(screen.getByRole("status").textContent).toBe("Searching…");

    rerender(<JobList {...baseProps} isFetching={false} />);
    expect(screen.queryByTestId("search-progress")).toBeNull();
    expect(screen.getByTestId("results-list").getAttribute("aria-busy")).toBe("false");
    expect(screen.getByText("2 jobs found")).toBeTruthy();
  });

  it("keeps the live status line outside any aria-busy subtree", () => {
    renderList(<JobList {...baseProps} isFetching />);

    const status = screen.getByRole("status");
    expect(status.textContent).toBe("Searching…");
    expect(status.closest('[aria-busy="true"]')).toBeNull();
  });

  it("does not flash the progress bar for fast responses", () => {
    const { rerender } = renderList(<JobList {...baseProps} isFetching />);
    act(() => vi.advanceTimersByTime(100));
    rerender(<JobList {...baseProps} isFetching={false} />);
    act(() => vi.advanceTimersByTime(100));

    expect(screen.queryByRole("progressbar")).toBeNull();
    expect(screen.queryByText("Searching…")).toBeNull();
  });

  it("announces searching once when reduced motion replaces the bar with a label", () => {
    vi.stubGlobal(
      "matchMedia",
      vi.fn(() => ({
        matches: true,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      })),
    );

    const { container } = renderList(<JobList {...baseProps} isFetching />);
    act(() => vi.advanceTimersByTime(150));

    expect(screen.queryByTestId("search-progress")).toBeNull();
    const statuses = screen.getAllByRole("status");
    expect(statuses).toHaveLength(1);
    expect(statuses[0].textContent).toBe("Searching…");

    const label = container.querySelector(".search-progress-label");
    expect(label?.textContent).toBe("Searching…");
    expect(label?.getAttribute("aria-hidden")).toBe("true");
  });
});
