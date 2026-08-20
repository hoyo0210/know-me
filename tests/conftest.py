import os
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
os.environ["KNOW_ME_PERSONA_DIR"] = str(_root / "tests" / "fixtures" / "sample_persona")
