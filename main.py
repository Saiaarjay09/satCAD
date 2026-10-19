"""Launch the satCAD desktop application:  python main.py [part.json] [--demo]"""
import sys

from satcad.gui.main import main

if __name__ == "__main__":
    sys.exit(main())
