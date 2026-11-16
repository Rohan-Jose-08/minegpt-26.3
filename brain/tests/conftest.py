import sys
from pathlib import Path

# Lets tests import the fake mod server next to them.
sys.path.insert(0, str(Path(__file__).parent))
