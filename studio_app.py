"""Entry point for the packaged SatCAD Studio app (and `python studio_app.py`). Starts the local editor and opens your browser."""
import sys
import traceback
from pathlib import Path


def run() -> int:
    from satcad.studio import main
    return main()


if __name__ == "__main__":
    try:
        sys.exit(run())
    except SystemExit:
        raise
    except BaseException:                                   # a packaged app has no console, so leave a readable note
        note = Path.home() / "SatCAD-Studio-error.txt"
        try:
            note.write_text("SatCAD Studio could not start.\n\n" + traceback.format_exc())
        except OSError:
            pass
        raise
