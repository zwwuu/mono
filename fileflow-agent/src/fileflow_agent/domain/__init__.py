"""Domain layer -- pure business objects and rules.

No I/O lives here: no SQLAlchemy, no FTP, no HTTP, no filesystem
writes. Everything in this package can be imported and unit-tested
without a database, a network, or a disk. The rules that make the
pipeline correct (manifest format, idempotency key, run states)
live here, so they cannot be broken by changing a transport or a
database.
"""
