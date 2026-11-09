import os
import tempfile

# Keep MNE's user config/cache out of the real home directory in tests.
os.environ.setdefault("_MNE_FAKE_HOME_DIR", tempfile.mkdtemp(prefix="eeg-repair-mne-"))
