"""FileFlow Watcher -- handoff watcher between two FTP servers.

The agent uploads verified manifest batches to FTP 1; this
watcher copies them to FTP 2 for external processing, then acts
when the processor finishes each file: the original on FTP 1 is
archived to done/ or failed/, and one email is sent per finished
batch. It also stages files from the share drive into FTP 1
outgoing/ for the agent to pick up and upload to the website.

Clean architecture, four layers (dependency rule: outer layers depend
on inner ones, never the reverse):
  domain/         entities, ports
  application/    use cases: handoff, completion, stage-outbound,
                  scan, reconcile
  infrastructure/ filesystem, SQLAlchemy ledger, transports, notify
  adapters/       CLI + composition root (builds use cases from config)

Shares one library with the agent project: fileflow-contract
(the manifest format, run identity, and SHA-256 verification).
Everything else is this project's own; the two machines still
deploy independently apart from that one install. Change the
manifest format in the contract package and bump MANIFEST_VERSION.
"""

__version__ = "2.8.0"
