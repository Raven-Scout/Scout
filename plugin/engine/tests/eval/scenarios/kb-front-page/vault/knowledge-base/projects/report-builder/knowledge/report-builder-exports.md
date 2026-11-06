---
domain: report-builder
project: "[[projects/report-builder/report-builder]]"
last_updated: {{d-1}}T16:00:00+00:00
as_of: {{d-1}}
---
# How a report builder export is built

**In short:** an admin picks a report and a date range, and the API service builds a CSV file and
returns a download link. Today the whole file is built in memory, which is why exports are capped.

## How an export runs

The export endpoint reads the report's rows from the warehouse and writes them to one CSV file in
memory, then uploads it and returns a signed download link
([example-org/api#41](https://github.com/example-org/api/pull/41)).

## Limits

An export is capped at 50,000 rows, because a larger file ran the export worker out of memory in
testing ([PROJ-1236](https://linear.app/example-org/issue/PROJ-1236)).

## The streaming change

The export streaming PR writes the file in chunks of 5,000 rows instead of building it in memory.
It is in review ([example-org/api#57](https://github.com/example-org/api/pull/57)). Whether the row
cap can go up once it ships is an open question for the weekly sync.

## History
- {{d-8}}: CSV became the only first-release format ([[report-builder-decisions]]).
