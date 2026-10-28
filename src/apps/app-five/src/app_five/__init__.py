"""app-five: index a folder of Markdown notes into a JSON index.

Run from source:  python -m app_five [--folder DIR] [--pattern GLOB]
As an exe:        app-five

The multi-module sample: the pipeline is split by stage, one module
per stage — scanner.py finds the notes, parser.py reads one note
into a record, index.py aggregates the records. __main__.py only
wires the stages together (arguments, config, logging, output).
"""

from app_five.meta import APP_NAME, __version__

__all__ = ["APP_NAME", "__version__"]
