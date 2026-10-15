"""Backward-compatible entry. The workspace lives in main.py."""

import os
import sys

project_directory = os.getcwd()
if project_directory not in sys.path:
    sys.path.insert(0, project_directory)

from pipeline.app.main import main

if __name__ == "__main__":
    main()
