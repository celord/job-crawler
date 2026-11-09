import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "./client";
import { useJobs, useStartMatchRun, useStartMatchRunWithJD } from "./hooks";
import type { JobsFilters, JobsResponse } from "../types";

vi.mock("./client", () => ({
  api: { get: vi.fn(), post: vi.fn() },
}));

function response(title: string): JobsResponse {
  return { jobs: [], total: 0, page: 1, limit: 50, marker: title } as JobsResponse;
}

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

const get = vi.mocked(api.get);
const post = vi.mocked(api.post);

describe("analysis mutations", () => {
  beforeEach(() => {
    post.mockReset();
  });

  it("invalidates cached job results when a run starts", async () => {
    post.mockResolvedValue({ data: { run_id: "run-1", manifest: {} } });
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: Infinity } },
    });
    const Wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );

    const { result } = renderHook(
      () => ({ start: useStartMatchRun(), startWithJD: useStartMatchRunWithJD() }),
      { wrapper: Wrapper },
    );

    client.setQueryData(["jobs", { title: "full" }], response("stale"));
    await act(async () => {
      await result.current.start.mutateAsync({ job_keys: ["gh|acme|1"], mode: "full" });
    });
    expect(client.getQueryState(["jobs", { title: "full" }])?.isInvalidated).toBe(true);

    client.setQueryData(["jobs", { title: "paste" }], response("stale"));
    await act(async () => {
      await result.current.startWithJD.mutateAsync({
        provider: "gh",
        source_key: "acme",
        job_id: "1",
        jd_text: "JD",
        mode: "full",
      });
    });
    expect(client.getQueryState(["jobs", { title: "paste" }])?.isInvalidated).toBe(true);
  });
});

describe("useJobs", () => {
  beforeEach(() => {
    get.mockReset();
  });

  it("keeps previous results while the next query is in flight", async () => {
    const first = response("first");
    const second = response("second");
    get.mockResolvedValueOnce({ data: first });

    const { result, rerender } = renderHook(({ filters }: { filters: JobsFilters }) => useJobs(filters), {
      wrapper,
      initialProps: { filters: { title: "first" } },
    });

    await waitFor(() => expect(result.current.data).toEqual(first));

    let resolveSecond: ((value: { data: JobsResponse }) => void) | undefined;
    get.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveSecond = resolve;
        }),
    );

    rerender({ filters: { title: "second" } });

    await waitFor(() => expect(result.current.isFetching).toBe(true));
    expect(result.current.data).toEqual(first);

    await act(async () => {
      resolveSecond?.({ data: second });
    });
    await waitFor(() => expect(result.current.data).toEqual(second));
  });

  it("aborts the previous request and ignores a late stale response", async () => {
    const first = response("first");
    const second = response("second");
    const signals: AbortSignal[] = [];
    let resolveFirst: ((value: { data: JobsResponse }) => void) | undefined;
    let resolveSecond: ((value: { data: JobsResponse }) => void) | undefined;

    get.mockImplementation((_url, config) => {
      signals.push(config!.signal as AbortSignal);
      const title = (config!.params as JobsFilters).title;
      return new Promise((resolve) => {
        if (title === "first") resolveFirst = resolve;
        else resolveSecond = resolve;
      });
    });

    const { result, rerender } = renderHook(({ filters }: { filters: JobsFilters }) => useJobs(filters), {
      wrapper,
      initialProps: { filters: { title: "first" } },
    });

    await waitFor(() => expect(get).toHaveBeenCalledTimes(1));

    rerender({ filters: { title: "second" } });
    await waitFor(() => expect(get).toHaveBeenCalledTimes(2));
    expect(signals[0].aborted).toBe(true);

    await act(async () => {
      resolveSecond?.({ data: second });
    });
    await waitFor(() => expect(result.current.data).toEqual(second));

    await act(async () => {
      resolveFirst?.({ data: first });
    });
    expect(result.current.data).toEqual(second);
  });
});
