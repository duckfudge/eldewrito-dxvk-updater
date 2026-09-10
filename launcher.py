import os
import sys
from pathlib import Path

if not getattr(sys, 'frozen', False):
    root = Path(__file__).resolve().parent
    sys.path.insert(0, str(root / 'src'))
    tcl = Path(sys.base_prefix) / 'tcl'
    if (tcl / 'tcl8.6' / 'init.tcl').is_file():
        os.environ.setdefault('TCL_LIBRARY', str(tcl / 'tcl8.6'))
        os.environ.setdefault('TK_LIBRARY', str(tcl / 'tk8.6'))

from dxvk_updater.app import main

if __name__ == "__main__":
    main()
