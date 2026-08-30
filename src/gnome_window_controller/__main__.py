"""
Created on 2026-07-19 08:03:12.

@author: eduardotc
@email: eduardotcampos@hotmail.com

Python file to handle main functions as a module.
"""

from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
