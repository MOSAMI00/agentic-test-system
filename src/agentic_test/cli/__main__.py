"""
Package entry point for `python -m agentic_test.cli`.
Adheres to WBS 1.6.4A.
"""

from agentic_test.cli.app import main

__all__ = ["main"]

if __name__ == "__main__":
    main()

