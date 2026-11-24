# FileFlow Agent sample: scrape a portal, rename PDFs, copy to FTP

Yes — this is exactly what the agent is for. The app you described
maps one-to-one onto the agent's download mode:

| Your app | The agent |
|---|---|
| Scrape a website | `playwright` source (or `selenium` if the portal needs the real installed Chrome): an action script of goto / fill / click / wait_for / download |
| Download files like PDFs to local | staging dir `<staging>/<run_id>/` — downloads land there via `.part` + rename, never a partial file |
| Rename them to business logic | a custom procedure (`procedures_local.py`) + `rename_pattern` |
| Copy to an FTP server at a specific path | `destination` with `transport: ftp` and a `directory` template — a direct drop, no watcher needed |

One gap was found while building this sample and fixed in the
agent itself (2.11.0): the agent could name a custom procedure in
the config, but had no way to load one — the parser rejected the
unknown name at startup. The agent now has `procedures_module`,
mirroring the watcher: set it to the module name of your
procedures file and the agent imports it before parsing, so the
custom procedure name validates.

## Layout

```
agent.json           the deploy shape: real portal URL, FTP destination
agent-demo.json      the runnable demo: local portal, local transport
                     standing in for the FTP server
procedures_local.py  rename-pdf-report: Monthly-Report-<VENDOR>-<YYYYMMDD>.pdf
                     -> <VENDOR>_<YYYYMMDD>_monthly-report.pdf
demo_portal.py       a tiny login page + one PDF download (127.0.0.1:8901)
run-demo.sh          starts the portal, runs the agent once, lists the result
```

## Install

```bash
pip install -e <path-to-fileflow-agent>        # the agent itself
pip install -e "<path-to-fileflow-agent>[playwright]"
playwright install chromium                     # once, on the PC
```

## Run the demo

```bash
./run-demo.sh
```

It logs into the demo portal, downloads
`Monthly-Report-ACME-20261122.pdf`, renames it to
`ACME_20261122_monthly-report.pdf`, and copies it to
`demo-out/ftp/reports/` — the `demo-out/ftp` directory is
the FTP server stand-in. A `<run_id>.manifest.json` lands next to
it: the batch receipt (SHA-256 of every delivered file). Run it a
second time and the run is SKIPPED — the ledger already recorded
that file as processed.

## Logs

Two logs land in `demo-out/` after a run: `agent.log` is the
agent's own log (the `log_file` in `agent-demo.json` — midnight
rotation, 7 backups on a real deployment), and `run.log` is the
whole demo script's output, teed by `run-demo.sh`. The demo has no
`notify` section, so a finished run is only logged; add one to
`agent.json` to get an email per run instead (see the agent README).

## Adapt it to the real portal

1. In `agent.json`, replace the portal URL, the login selectors,
   and the download selector with the real ones. Use
   `"headless": false` once to watch it run, then put it back.
   Secrets go in env vars, never in the config: the password fill
   reads `PORTAL_PASSWORD`, the FTP password reads
   `FILEFLOW_FTP_PASSWORD`.
2. In `procedures_local.py`, replace the filename regex and the
   output name with your business rule. The procedure can also
   read the PDF (page text, metadata) instead of trusting the
   filename — it gets the downloaded file's path and returns the
   renamed output path. Keep the two rules: write a new file,
   raise on bad input.
3. `rename_pattern` is the template layer on top of the
   procedure: `{name}{ext}` passes the procedure's name through
   unchanged. The date in the name comes from the procedure
   itself (it reads the date out of the input filename), not from
   a template token -- there are no built-in tokens.
4. `destination.directory` is the FTP path, a template: it can
   use the job's `variables`. Leave it out and uploads go to
   `incoming/<job>/` for the watcher flow instead.

## Running it for real

`fileflow-agent --config agent.json` runs once;
`--loop 3600` repeats every hour; `--check-config` validates the
config (including the procedure name) without running anything.
For a server, run it under systemd or cron instead of `--loop`.

What the agent adds over a plain script: every run is one batch
under one manifest (the FTP side can verify the files against
it); a re-run never re-uploads a file already recorded as
processed; a failed run keeps its staging dir for inspection and
retries cleanly; an empty result (portal has nothing new) is
SKIPPED, not a failure.

## Two things to know

- The manifest is uploaded with every batch, even on a direct
  drop with no watcher — one small `<run_id>.manifest.json` per
  run sits next to your PDFs on the FTP server. It is the receipt
  for the batch; if the receiving side must see only PDFs, have it
  ignore `*.manifest.json`.
- The optional file rule from 2.10.0 applies here too: if some
  reports are optional, list them with `"optional": true` in a
  `remote-files` source. For a browser source, a missing download
  link fails the run — there is no optional download action.
