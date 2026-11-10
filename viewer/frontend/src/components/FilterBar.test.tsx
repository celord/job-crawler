import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../api/client";
import { useFilterStore } from "../stores/filterStore";
import { FilterBar } from "./FilterBar";

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return { api: { ...actual.api, get: vi.fn() } };
});

const get = vi.mocked(api.get);

function renderBar() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const Wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return render(<FilterBar disabled={false} />, { wrapper: Wrapper });
}

describe("FilterBar", () => {
  beforeEach(() => {
    get.mockReset();
    get.mockImplementation(async (url: string) => {
      if (url === "/config") {
        return { data: { locationOptions: [{ token: "Brazil", label: "Brazil" }, { token: "EMEA", label: "Europe+" }] } };
      }
      return { data: {} };
    });
    useFilterStore.getState().resetFilters();
  });
  afterEach(cleanup);

  it("does not render a Providers group", () => {
    renderBar();
    expect(screen.queryByText("Providers")).toBeNull();
    expect(get).not.toHaveBeenCalledWith("/sources");
  });

  it("renders location options from the config response", async () => {
    renderBar();
    expect(await screen.findByLabelText("Europe+")).toBeTruthy();
    expect(screen.getByLabelText("Brazil")).toBeTruthy();
    expect(screen.queryByLabelText("Costa Rica")).toBeNull();
  });

  it("stores a selection and defaults include-unspecified on", async () => {
    renderBar();
    const unspecified = screen.getByLabelText("Include remote/unspecified") as HTMLInputElement;
    expect(unspecified.checked).toBe(true);
    fireEvent.click(await screen.findByLabelText("Brazil"));
    await waitFor(() => expect(useFilterStore.getState().locations).toEqual(["Brazil"]));
  });
});

describe("api client", () => {
  it("serializes repeated location params without brackets", async () => {
    const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
    expect(actual.api.getUri({ url: "/jobs", params: { location: ["Costa Rica", "USA"], includeUnspecified: "0" } })).toBe(
      "/api/jobs?location=Costa+Rica&location=USA&includeUnspecified=0",
    );
  });
});
