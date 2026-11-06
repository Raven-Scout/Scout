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

Exports run only on demand: Sam turned off the nightly PDF job on {{d-1}}, and PROJ-1240 is done.

## Limits

An export is capped at 200,000 rows. The weekly sync raised the cap from 50,000 on {{d-1}}.

## The streaming change

The export streaming PR (#57) writes the file in chunks instead of building it in memory. It merged
on {{d-1}}.

## History
- {{d-8}}: CSV became the only first-release format ([[report-builder-decisions]]).
