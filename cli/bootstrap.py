"""Bootstrap module for CLI path setup.

This module handles sys.path manipulation before app imports,
allowing clean import structure in cli.py without E402 warnings.
"""

import sys
from pathlib import Path

# Automatically set up path when module is imported
_project_root = Path(__file__).parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))
