"""FileFlow Watcher -- runs on the FTP server, the only machine that
can see the share drive.

Claims incoming files, re-verifies the SHA-256 manifest, routes by
job, runs the business procedure, and publishes to the share drive.
It also stages files from the share drive into outgoing/ for the
agent to pick up and upload to the website.

Clean architecture, four layers (dependency rule: outer layers depend
on inner ones, never the reverse):
  domain/         entities, ports, pure integrity rules, procedures
  application/    use cases: process-incoming, stage-outbound, scan,
                  reconcile
  infrastructure/ filesystem, SQLAlchemy ledger
  adapters/       CLI + composition root (builds use cases from config)

Self-contained: it shares no code with the agent project. The only
contract between the two projects is the manifest format
(domain/entities.py: MANIFEST_VERSION) and the idempotency key
(job|run_id|input-sha256). Change either in one project, change it
in the other, and bump MANIFEST_VERSION.
"""

__version__ = "2.0.0"
