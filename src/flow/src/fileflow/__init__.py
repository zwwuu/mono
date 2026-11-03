"""FileFlow — generic file-processing task engine.

Each task is a pipeline declared in JSON (tasks.json): fetch a file,
checksum it, optionally approve it (once or several times, at any point),
rename it, and copy it to any number of destinations — each task with its
own source, rename schema and destinations. Sources can be a website, a
local file or directory, an FTP directory, a share, or a web page driven
through login clicks to a download button — with an extensible selector
model for elements CSS alone can't reach (table rows by cell value,
roles, labels, chained lookups, custom kinds). Add or remove tasks
without changing code. Notifications are conditional and templated per
task and per step. Runs can be started on demand, from the dashboard,
or on daily/weekly/monthly schedules.
"""

__version__ = "0.14.0"
__all__ = ["__version__"]
