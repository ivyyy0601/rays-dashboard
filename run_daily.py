"""
Daily wrapper: update cache + run alert checks (reliability review).
Called by launchd at 5:30 AM ET. Calling Python directly (not bash) avoids
macOS TCC restrictions on Desktop folder access for /bin/bash.
"""
import os
import sys
from pathlib import Path

# Anchor working dir + ensure we can find our modules even if launchd starts elsewhere
ROOT = Path(__file__).parent.resolve()
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from daily_pipeline import main

if __name__ == '__main__':
    raise SystemExit(main())
