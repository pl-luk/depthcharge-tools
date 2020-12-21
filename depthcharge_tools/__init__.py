#! /usr/bin/env python3

import logging
import pathlib

from depthcharge_tools.utils import (
    Config,
    BoardInfo,
)


logger = logging.getLogger(__name__)
log_handler = logging.StreamHandler()
logger.addHandler(log_handler)

__version__ = 'v0.5.0-dev'

DATADIR = pathlib.Path("conf").resolve()
SYSCONFDIR = pathlib.Path("conf").resolve()
LOCALSTATEDIR = pathlib.Path("var").resolve()

config = Config(SYSCONFDIR / "config")
boards = BoardInfo(DATADIR / "db", DATADIR / "userdb")
