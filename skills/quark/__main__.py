"""`python -m skills.quark` 入口 → cli.main()。"""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
