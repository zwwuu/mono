"""FileFlow Watcher -- runs on the FTP server, the only machine that
can see the share drive.

Claims incoming files, re-verifies the SHA-256 manifest, routes by
job, runs the business procedure, and publishes to the share drive.
It also stages files from the share drive into outgoing/ for the
agent to pick up and upload to the website.

Self-contained: it shares no code with the agent project. The only
contract between the two projects is the manifest format
(common.py: MANIFEST_VERSION) and the idempotency key
(job|run_id|input-sha256). Change either in one project, change it
in the other, and bump MANIFEST_VERSION.
"""
__version__ = "1.0.0"
