"""Provider registry. Ported from crawler/src/providers/index.ts.

Only the 4 simple single-page JSON providers exist so far (Story 5.1);
smartrecruiters/teamtailor/workable/workday/icims are added in Stories
5.2-5.6.
"""

from providers import ashby, bamboohr, greenhouse, lever

CRAWLER_BY_PROVIDER = {
    "ashby": ashby,
    "bamboohr": bamboohr,
    "greenhouse": greenhouse,
    "lever": lever,
}
