"""Run the local archive service with ``python -m twitter_x_archiver``."""
from .server import cli

if __name__ == "__main__":
    raise SystemExit(cli())
