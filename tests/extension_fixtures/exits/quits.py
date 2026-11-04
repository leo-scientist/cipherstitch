"""Parses arguments it was never given when imported, as a script turned extension would."""

import argparse

argparse.ArgumentParser(prog="quits").parse_args(["--bogus"])
