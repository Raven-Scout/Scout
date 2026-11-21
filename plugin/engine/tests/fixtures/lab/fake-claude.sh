#!/bin/sh
# Test double for `claude -p`. First argument is a log file; the rest is what the meter passed.
# Records argv, cwd and environment to the log, then answers like `claude --output-format json`.
log="$1"
shift
prompt="$(cat)"
{
  echo "ARGV:$*"
  echo "CWD:$(pwd -P)"
  env | sed 's/^/ENV:/'
  echo "END"
} >> "$log"
case "$prompt" in
  *BADJSON*) echo "not json at all"; exit 0 ;;
  *SLEEPCALL*) sleep 5; exit 0 ;;
  *NOCOST*) echo '{"result":"free"}'; exit 0 ;;
  *NEGCOST*) echo '{"result":"neg","total_cost_usd":-5}'; exit 0 ;;
  *FAILCALL*) echo "boom" >&2; exit 1 ;;
esac
esc="$(printf '%s' "$prompt" | sed 's/\\/\\\\/g; s/"/\\"/g')"
printf '{"result":"echo:%s","total_cost_usd":0.4}\n' "$esc"
