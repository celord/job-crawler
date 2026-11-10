import { beforeEach, describe, expect, it, vi } from "vitest";

async function loadStore(search: string) {
  window.history.replaceState(null, "", `/${search}`);
  vi.resetModules();
  return (await import("./filterStore")).useFilterStore;
}

describe("filterStore location persistence", () => {
  beforeEach(() => window.history.replaceState(null, "", "/"));

  it("writes locations and the toggle to the query string", async () => {
    const store = await loadStore("");
    store.getState().setFilter("locations", ["Costa Rica", "USA"]);
    store.getState().setFilter("includeUnspecified", false);
    const params = new URLSearchParams(window.location.search);
    expect(params.getAll("location")).toEqual(["Costa Rica", "USA"]);
    expect(params.get("includeUnspecified")).toBe("0");
  });

  it("reads them back from the URL", async () => {
    const store = await loadStore("?location=Costa+Rica&location=Latam&includeUnspecified=0");
    expect(store.getState().locations).toEqual(["Costa Rica", "Latam"]);
    expect(store.getState().includeUnspecified).toBe(false);
  });

  it("defaults to no locations and include-unspecified on", async () => {
    const store = await loadStore("");
    expect(store.getState().locations).toEqual([]);
    expect(store.getState().includeUnspecified).toBe(true);
  });
});
