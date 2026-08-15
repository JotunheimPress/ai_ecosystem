#!/usr/bin/env python3
"""Launcher. Binds 127.0.0.1 deliberately — see CLAUDE.md.

This app can (from phase 1) stop your backends and write into your models dirs, so it
does not get to listen on the network. Companion publishes on all interfaces; that
pattern must not be copied here. If remote access is ever wanted it needs auth in front
first, not a different bind address.
"""
import os

import uvicorn

HOST = os.environ.get("STACK_UI_HOST", "127.0.0.1")
PORT = int(os.environ.get("STACK_UI_PORT", "7863"))

if __name__ == "__main__":
    if HOST not in ("127.0.0.1", "localhost", "::1"):
        print(f"!! refusing to bind {HOST}: stack-ui is loopback-only until it has auth")
        raise SystemExit(1)
    uvicorn.run("server:app", host=HOST, port=PORT, reload=bool(os.environ.get("STACK_UI_RELOAD")))
