"""Infrastructure layer -- the outside world, behind ports.

Filesystem, SQLite (SQLAlchemy), FTP/SFTP/local transports, and
sources. Nothing here is imported by the domain or application
layers; the adapters wire it in.
"""
