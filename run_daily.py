"""
Daily wrapper: update cache + run alert checks.
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

import update_cache
import check_alerts

print(f"\n{'=' * 60}\nDaily run starting\n{'=' * 60}")
update_cache.main()
print(f"\n{'=' * 60}\nAlert check\n{'=' * 60}")
check_alerts.main()
print(f"\n{'=' * 60}\nDone\n{'=' * 60}")
