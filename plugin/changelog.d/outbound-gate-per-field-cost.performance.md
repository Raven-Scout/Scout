- **The outbound privacy gate builds the private terms once per tool call, not once per field**
  (`engine/scout/hooks/outbound_gate.py`, `engine/scout/kb/outbound.py`). Each build walks the vault, so the
  check cost about a second per string in the tool's input: a call with eleven strings ran past the gate's
  10 s deadline, which denies the call in a scheduled run and lets it through unchecked for the owner.
  The gate now asks `outbound_terms` once and matches every field against the result.
