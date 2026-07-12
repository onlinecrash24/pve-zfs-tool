#!/usr/bin/env python3
"""Build both PDF guides in one go: python tools/docgen/build.py

Regenerates docs/PVE-ZFS-Tool_Administratorhandbuch.pdf and
docs/PVE-ZFS-Tool_Benutzerhandbuch.pdf from the content in build_admin_guide.py
/ build_user_guide.py. See README.md in this directory for details.
"""
import os
import runpy

HERE = os.path.dirname(os.path.abspath(__file__))

for script in ("build_admin_guide.py", "build_user_guide.py"):
    runpy.run_path(os.path.join(HERE, script), run_name="__main__")
