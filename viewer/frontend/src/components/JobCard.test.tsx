import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { JobCard } from "./JobCard";
import type { Job } from "../types";

vi.mock("../api/client", () => ({ api: { get: vi.fn().mockResolvedValue({ data: {} }), post: vi.fn() } }));

function job(extra: Partial<Job>): Job {
  return {
    provider: "greenhouse",
    source_key: "acme",
    job_id: "1",
    title: "Engineer",
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
    ...extra,
  };
}

function renderCard(j: Job) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <JobCard job={j} />
    </QueryClientProvider>,
  );
}

describe("JobCard location label", () => {
  afterEach(cleanup);

  it("shows country for a country-level remote job", () => {
    renderCard(job({ region: "latam", country: "CR" }));
    expect(screen.getByText("Costa Rica · Remote")).toBeTruthy();
  });

  it("shows region for a region-level remote job", () => {
    renderCard(job({ region: "latam", country: null }));
    expect(screen.getByText("Latam · Remote")).toBeTruthy();
  });

  it("shows unspecified for remote_unspecified", () => {
    renderCard(job({ region: "remote_unspecified", country: null }));
    expect(screen.getByText("Remote (location not specified)")).toBeTruthy();
  });
});
