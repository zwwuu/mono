"""FileFlow Watcher -- runs on the FTP server, the only machine that
can see the share drive.

Claims incoming files, re-verifies the SHA-256 manifest, routes by
job, runs the business procedure, and publishes to the share drive.
It also stages files from the share drive into outgoing/ for the
agent to pick up and upload to the website.

Clean architecture, four layers (dependency rule: outer layers depend
on inner ones, never the reverse):
  domain/         entities, ports, procedures
  application/    use cases: process-incoming, stage-outbound, scan,
                  reconcile
  infrastructure/ filesystem, SQLAlchemy ledger
  adapters/       CLI + composition root (builds use cases from config)

Shares one library with the agent project: fileflow-contract
(the manifest format, run identity, and SHA-256 verification).
Everything else is this project's own; the two machines still
deploy independently apart from that one install. Change the
manifest format in the contract package and bump MANIFEST_VERSION.
"""

__version__ = "2.5.0"
