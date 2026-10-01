"""Create a fresh, temporary test baseline; never load local business data."""

import atexit
import os
from pathlib import Path
import tempfile


_baseline = tempfile.TemporaryDirectory(prefix='engineering_oss_tests_')
atexit.register(_baseline.cleanup)
os.environ['SUPPLY_CHAIN_DB_PATH'] = str(Path(_baseline.name) / 'baseline.db')
os.environ['SUPPLY_CHAIN_ATTACHMENTS_PATH'] = str(Path(_baseline.name) / 'attachments')

from db.schema import init_db

init_db()
