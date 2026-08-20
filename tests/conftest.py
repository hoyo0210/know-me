import os
from pathlib import Path
from unittest.mock import patch

_root = Path(__file__).resolve().parent.parent
os.environ["KNOW_ME_PERSONA_DIR"] = str(_root / "tests" / "fixtures" / "persona")

# Parent workspace .env must not re-inject personal resume URL during app import/reload.
patch("dotenv.load_dotenv", lambda *args, **kwargs: False).start()
