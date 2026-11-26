#!/bin/bash
# Spike (runtime-agnostic Phase 2A.4): does agy's Google sign-in work from a launchd job, the way a scheduled
# Scout run would start it? It PRINTS a one-shot LaunchAgent plist and the launchctl lines to load, check and
# remove it. It never loads, starts or removes a job itself: the owner runs the printed lines by hand.
#
# The job runs `agy -p "/usage"` once (no model turn: agy answers /usage itself, findings §1.7) with a scheduled
# run's environment (HOME, a fixed PATH, SCOUT_MODE; no Gemini key), and writes agy's version, stdout, stderr and
# exit code to the report file. It uses the owner's real agy login, so run it only if you mean to.
#
# usage: agy-launchd-login.sh [--write]
#   --write   also write the plist to $REPORT_DIR (never into ~/Library/LaunchAgents)
# env:     AGY_BIN (default: agy on PATH, else ~/.local/bin/agy), AGY_SPIKE_PATH (the job's PATH),
#          REPORT_DIR (default ~/scout-reports)
set -euo pipefail

label="com.scout.spike.agy-launchd-login"
report_dir="${REPORT_DIR:-$HOME/scout-reports}"
report="$report_dir/agy-launchd-login.txt"
plist="$report_dir/$label.plist"
agy="${AGY_BIN:-$(command -v agy 2>/dev/null || echo "$HOME/.local/bin/agy")}"
job_path="${AGY_SPIKE_PATH:-$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin}"

xml_escape() {
  local s=$1
  s=${s//&/&amp;}
  s=${s//</&lt;}
  s=${s//>/&gt;}
  printf '%s' "$s"
}

# What the job runs: one shell line, its output to the report. `exec </dev/null` so agy can't wait on a terminal.
job=$(cat <<EOF
exec </dev/null
{
  echo "# agy under launchd, \$(date '+%Y-%m-%d %H:%M:%S %z')"
  echo "## agy --version"
  "$agy" --version 2>&1 || echo "(--version failed: \$?)"
  echo "## agy -p /usage: stdout"
  "$agy" -p "/usage" > "$report.out" 2> "$report.err"; rc=\$?
  cat "$report.out"
  echo "## stderr"
  cat "$report.err"
  echo "## exit=\$rc"
} > "$report" 2>&1
rm -f "$report.out" "$report.err"
EOF
)

render_plist() {
  cat <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$label</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>-c</string>
    <string>$(xml_escape "$job")</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>HOME</key><string>$(xml_escape "$HOME")</string>
    <key>PATH</key><string>$(xml_escape "$job_path")</string>
    <key>SCOUT_MODE</key><string>dreaming</string>
    <key>SCOUT_READER</key><string>autonomous</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>LaunchOnlyOnce</key><true/>
  <key>StandardOutPath</key><string>$(xml_escape "$report_dir/$label.launchd.log")</string>
  <key>StandardErrorPath</key><string>$(xml_escape "$report_dir/$label.launchd.log")</string>
</dict>
</plist>
EOF
}

if [ "${1:-}" = "--write" ]; then
  mkdir -p "$report_dir"
  render_plist > "$plist"
  plutil -lint "$plist" >/dev/null
  echo "# wrote $plist (not loaded)"
else
  echo "# --- the plist (save it as $plist, or rerun with --write) ---"
  render_plist
fi

cat <<EOF

# --- run these yourself, one at a time ---
launchctl bootstrap gui/\$(id -u) "$plist"
# wait a few seconds (RunAtLoad starts it once), watch for a Keychain prompt, then:
launchctl print gui/\$(id -u)/$label | grep -E 'state|last exit code'
cat "$report"
launchctl bootout gui/\$(id -u)/$label
rm -f "$plist"
EOF
