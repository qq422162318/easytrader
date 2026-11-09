# -*- coding: utf-8 -*-
"""Standalone launcher for easytrader.server.

`easytrader/server.py` uses relative imports (`from . import api`), so it
cannot be started directly as a script or via `-m`. This launcher runs from
the repository root, where `easytrader` is importable as a package, and then
starts the Flask app.

Usage:
    python run_server.py [port]

In VSCode: select the "调试 easytrader.server (Flask 服务)" configuration and
press F5. Breakpoints in easytrader/*.py will hit normally.
"""
import os
import sys

# Make sure the repository root is importable regardless of how we are run.
_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from easytrader.server import app, run  # noqa: E402


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 1430
    print("easytrader server -> http://0.0.0.0:{}".format(port))
    app.run(host="0.0.0.0", port=port, use_reloader=False)