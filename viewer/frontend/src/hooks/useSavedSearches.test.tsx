import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import { api } from "../api/client";
import type { SavedSearch } from "../types";
import { useMergedSearchJobs } from "./useSavedSearches";

vi.mock("../api/client", () => ({ api: { get: vi.fn() } }));

const get = vi.mocked(api.get);

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

async function paramsFor(search: SavedSearch) {
  get.mockReset();
  get.mockResolvedValue({ data: { jobs: [], total: 0, page: 1, limit: 500 } });
  const { result } = renderHook(() => useMergedSearchJobs([search]), { wrapper });
  await waitFor(() => expect(result.current.isLoading).toBe(false));
  return (get.mock.calls[0][1] as { params: Record<string, unknown> }).params;
}

describe("saved search locations", () => {
  it("still loads an entry that only has sources", async () => {
    const params = await paramsFor({ id: "a", label: "A", title: "pm", sources: ["lever"] });
    expect(params.sources).toBe("lever");
    expect(params.location).toBeUndefined();
  });

  it("sends optional locations as repeated location params", async () => {
    const params = await paramsFor({ id: "b", label: "B", locations: ["Costa Rica", "Latam"] });
    expect(params.location).toEqual(["Costa Rica", "Latam"]);
  });
});
