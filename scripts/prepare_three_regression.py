#!/usr/bin/env python3
"""Compatibility entrypoint for the renamed regression state preparer."""

from prepare_regression import main


if __name__ == "__main__":
    raise SystemExit(main())
