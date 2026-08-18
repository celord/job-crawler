import { useJobs } from "./api/hooks";
import { AnalysisPanel } from "./components/AnalysisPanel";
import { FilterBar } from "./components/FilterBar";
import { Header } from "./components/Header";
import { JobList } from "./components/JobList";
import { Pagination } from "./components/Pagination";
import { SavedSearchStrip } from "./components/SavedSearchStrip";
import { useActiveSearchIds, useMergedSearchJobs, useSavedSearches } from "./hooks/useSavedSearches";
import { useFilterStore } from "./stores/filterStore";

const PAGE_SIZE = 50;

function App() {
  const filters = useFilterStore();
  const setFilter = useFilterStore((s) => s.setFilter);

  const savedSearches = useSavedSearches();
  const { activeSearchIds, toggleSearch } = useActiveSearchIds();
  const activeSearches = savedSearches.filter((s) => activeSearchIds.includes(s.id));
  const searchesActive = activeSearches.length > 0;

  const normalJobsQuery = useJobs({
    title: filters.title,
    myLoc: filters.myLoc,
    remote: filters.remote ? undefined : "0",
    days: filters.days || undefined,
    company: filters.company,
    sources: filters.sources.join(",") || undefined,
    tiers: filters.tiers.join(",") || undefined,
    types: filters.types.join(",") || undefined,
    score: filters.score || undefined,
    evaluated: filters.evaluated ? "1" : undefined,
    inc: filters.inc,
    exc: filters.exc,
    favCompanies: filters.favCompanies.join(",") || undefined,
    page: filters.page,
    limit: PAGE_SIZE,
  });
  const mergedSearchJobs = useMergedSearchJobs(activeSearches);

  const jobs = searchesActive ? mergedSearchJobs.jobs : (normalJobsQuery.data?.jobs ?? []);
  const isLoading = searchesActive ? mergedSearchJobs.isLoading : normalJobsQuery.isLoading;
  const isError = searchesActive ? false : normalJobsQuery.isError;
  const total = searchesActive ? mergedSearchJobs.total : (normalJobsQuery.data?.total ?? 0);

  return (
    <div className="app">
      <Header />
      <SavedSearchStrip activeSearchIds={activeSearchIds} onToggle={toggleSearch} />
      <FilterBar disabled={searchesActive} />
      <JobList jobs={jobs} isLoading={isLoading} isError={isError} />
      {!searchesActive && (
        <Pagination
          page={filters.page}
          total={total}
          limit={PAGE_SIZE}
          onPageChange={(page) => setFilter("page", page)}
        />
      )}
      <AnalysisPanel />
    </div>
  );
}

export default App;
