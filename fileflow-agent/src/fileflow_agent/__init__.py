"""FileFlow Agent -- runs on the dedicated Ethernet PC.

Downloads from the website, does the first rename/format, seals the
file with a SHA-256 manifest, and uploads it to the FTP server.
The forward flow runs in two stages: ``pull`` fetches and stages a
batch (recorded STAGED), ``push`` uploads staged batches (recorded
UPLOADED); ``full`` runs both back to back.

Clean architecture, four layers (dependency rule: outer layers depend
on inner ones, never the reverse):
  domain/         entities, ports, pure integrity rules, procedures
  application/    use cases: pull, push, dry-run
  infrastructure/ filesystem, SQLAlchemy ledger, transports, sources
  adapters/       CLI + composition root (builds use cases from config)

Shares one library with the watcher project: fileflow-contract
(the manifest format, run identity, and SHA-256 verification).
Everything else is this project's own; the two machines still
deploy independently apart from that one install. Change the
manifest format in the contract package and bump MANIFEST_VERSION.
"""

__version__ = "2.21.0"
