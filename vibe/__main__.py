import sys


if sys.version_info < (3, 9):
    sys.stderr.write("Vibe requires Python 3.9+. Please use python3 -m vibe.\n")
    sys.exit(1)

from vibe.cli import main


if __name__ == "__main__":
    main()
