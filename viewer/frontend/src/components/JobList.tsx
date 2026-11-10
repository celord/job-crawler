import type { Job } from "../types";
import { useDelayedFlag } from "../hooks/useDelayedFlag";
import { usePrefersReducedMotion } from "../hooks/usePrefersReducedMotion";
import { JobCard } from "./JobCard";

interface Props {
  jobs: Job[];
  isLoading: boolean;
  isFetching: boolean;
  isError: boolean;
  total: number;
}

function SkeletonList() {
  return (
    <div className="job-list" aria-hidden="true">
      {Array.from({ length: 4 }, (_, index) => (
        <div className="job-card job-skeleton" data-testid="job-skeleton" key={index}>
          <div className="job-skeleton-line job-skeleton-line-title" />
          <div className="job-skeleton-line" />
          <div className="job-skeleton-line job-skeleton-line-short" />
        </div>
      ))}
    </div>
  );
}

// Server-side pagination already caps each page at a small, bounded item
// count (<=500), so plain rendering is used instead of @tanstack/react-virtual
// — the plan explicitly allows this as a simpler alternative.
export function JobList({ jobs, isLoading, isFetching, isError, total }: Props) {
  const showProgress = useDelayedFlag(isFetching && jobs.length > 0);
  const reducedMotion = usePrefersReducedMotion();
  const showSkeleton = isLoading || (isFetching && jobs.length === 0);
  const showError = isError && !isFetching;

  return (
    <section className="results" data-testid="results">
      <div className="results-progress">
        {showProgress &&
          (reducedMotion ? (
            <span className="search-progress-label" aria-hidden="true">
              Searching…
            </span>
          ) : (
            <div className="search-progress" data-testid="search-progress" aria-hidden="true" />
          ))}
      </div>
      <p className="results-status" role="status" aria-live="polite">
        {isFetching ? "Searching…" : showError ? "Failed to load jobs." : `${total} jobs found`}
      </p>

      <div className="results-list" data-testid="results-list" aria-busy={isFetching}>
        {showError ? (
          <p>Failed to load jobs.</p>
        ) : showSkeleton ? (
          <SkeletonList />
        ) : jobs.length === 0 ? (
          <p>No jobs match the current filters.</p>
        ) : (
          <div className="job-list">
            {jobs.map((job) => (
              <JobCard key={`${job.provider}|${job.source_key}|${job.job_id}`} job={job} />
            ))}
          </div>
        )}
      </div>
    </section>
  );
}
