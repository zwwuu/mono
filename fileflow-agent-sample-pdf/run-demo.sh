#!/bin/bash
# Run the sample end to end against the demo portal.
# Starts demo_portal.py on 127.0.0.1:8901, runs the agent once with
# agent-demo.json, prints what landed in the demo FTP directory.
# The whole script's output is teed to demo-out/run.log; the agent's
# own log (agent-demo.json "log_file") lands in demo-out/agent.log.
set -e
set -o pipefail
cd "$(dirname "$0")"
mkdir -p demo-out
main() {
  export DEMO_PORTAL_PASSWORD=demo
  export PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}"
  python3 demo_portal.py &
  PORTAL_PID=$!
  trap 'kill $PORTAL_PID' EXIT
  sleep 1
  fileflow-agent --config agent-demo.json
  echo "--- delivered (demo FTP stand-in) ---"
  find demo-out/ftp -type f | sort
}
main 2>&1 | tee demo-out/run.log
