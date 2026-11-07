---
domain: report-builder
project: "[[projects/report-builder/report-builder]]"
last_updated: {{today}}T07:10:00+00:00
as_of: {{today}}
---
# How a report builder export is built

**In short:** an admin picks a report and a date range, and the API service streams a CSV file and
returns a download link. Exports run only on demand, and are capped at 200,000 rows.

## How an export runs

The export endpoint reads the report's rows from the warehouse and writes them to a CSV file in
chunks of 5,000 rows, then uploads it and returns a signed download link
([example-org/api#41](https://github.com/example-org/api/pull/41)).

Exports run only on demand: Sam has not turned off the nightly PDF job, and PROJ-1240 isn't done yet
([PROJ-1240](https://linear.app/example-org/issue/PROJ-1240)).

## Limits

An export is capped at 200,000 rows. The weekly sync raised the cap from 50,000 on {{d-1}}, because
streaming removed the memory limit that set it ([[sources/{{month}}/{{d-1}}-report-builder-sync]]).

## The streaming change

The export streaming PR writes the file in chunks instead of building it in memory, so memory use
stays flat at about 40 MB per export. It is not merged yet
([example-org/api#57](https://github.com/example-org/api/pull/57)).

## History
- {{d-1}}: the row cap rose from 50,000 to 200,000 rows ([[report-builder-decisions]]).
- {{d-8}}: CSV became the only first-release format ([[report-builder-decisions]]).
