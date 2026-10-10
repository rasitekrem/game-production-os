"""Fixed real foundation-Job adversary: persistent Player boundary must refuse a batch caller."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from gpos.tools import player_process_win32 as native
try:
    native.parent_permission()
except native.LifecycleRefused:
    print('REFUSED_IN_BATCH_JOB')
else:
    raise AssertionError('a GPOS batch Job was allowed to break away')
