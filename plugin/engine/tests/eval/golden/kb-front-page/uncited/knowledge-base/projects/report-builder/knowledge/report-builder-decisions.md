---
domain: report-builder
project: "[[projects/report-builder/report-builder]]"
last_updated: {{today}}T07:10:00+00:00
as_of: {{today}}
---
# Report builder decisions

| Date | Decision | Who decided | Why | Source |
|---|---|---|---|---|
| {{d-1}} | The export row cap goes from 50,000 to 200,000 rows | [[people/priya\|Priya]] | Streaming removed the memory limit that set the old cap | the weekly sync |
| {{d-8}} | CSV is the only export format in the first release | [[people/alex\|Alex]] | Admins asked for CSV first | [PROJ-1238](https://linear.app/example-org/issue/PROJ-1238) |
| {{d-15}} | Exports run on demand, not on a nightly schedule | [[people/priya\|Priya]] | Most nightly reports were never opened | [PROJ-1240](https://linear.app/example-org/issue/PROJ-1240) |
