"""Frozen launcher for the public ``python -m alice`` CLI."""
import multiprocessing
import runpy


if __name__ == "__main__":
    multiprocessing.freeze_support()
    runpy.run_module("alice", run_name="__main__")
