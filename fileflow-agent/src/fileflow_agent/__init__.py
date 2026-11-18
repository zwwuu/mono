"""FileFlow Agent -- runs on the dedicated Ethernet PC.

Downloads from the website, does the first rename/format, seals the
file with a SHA-256 manifest, and uploads it to the FTP server.
In pickup mode it also picks staged files up from the FTP server's
outgoing/ folder and uploads them to the website.

Clean architecture, four layers (dependency rule: outer layers depend
on inner ones, never the reverse):
  domain/         entities, ports, pure integrity rules, procedures
  application/    use cases: download+upload, pickup
  infrastructure/ filesystem, SQLAlchemy ledger, transports, sources,
                  website uploader
  adapters/       CLI + composition root (builds use cases from config)

Self-contained: it shares no code with the watcher project. The
only contract between the two projects is the manifest format
(domain/entities.py: MANIFEST_VERSION) and the idempotency key
(job|run_id|input-sha256). Change either in one project, change it
in the other, and bump MANIFEST_VERSION.
"""

__version__ = "2.0.0"
