#! /usr/bin/env python3

import logging
import pathlib

from depthcharge_tools.utils import (
    Config,
    BoardInfo,
)


logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())

__version__ = 'v0.5.0-dev'

DATADIR = pathlib.Path("conf")
SYSCONFDIR = pathlib.Path("conf")
LOCALSTATEDIR = pathlib.Path("var")

config = Config(SYSCONFDIR / "config")
boards = BoardInfo(DATADIR / "db", DATADIR / "userdb")
