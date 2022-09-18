#! /usr/bin/env python3

import argparse
import collections
import configparser
import copy
import glob
import logging
import os
import platform
import re
import shlex
import tempfile

from pathlib import Path

from depthcharge_tools import (
    __version__,
    config_ini,
    boards_ini,
    config_files,
)
from depthcharge_tools.utils.argparse import (
    Command,
    Argument,
    Group,
    Subparsers,
)
from depthcharge_tools.utils.collections import (
    ConfigDict,
)
from depthcharge_tools.utils.os import (
    system_disks,
    Disks,
)
from depthcharge_tools.utils.platform import (
    Architecture,
    vboot_keys,
    cros_hwid,
    dt_compatibles,
    is_cros_boot,
)


class Board:
    def __init__(self, config):
        self._config = config

    @property
    def name(self):
        name = self._config.get("name")
        if name is None:
            name = "Unnamed {} board".format(self.codename or 'unknown')
        return name

    @property
    def codename(self):
        return self._config.get("codename")

    @property
    def arch(self):
        return Architecture(self._config.get("arch"))

    @property
    def dt_compatible(self):
        pattern = self._config.get("dt-compatible")

        # Try to detect non-regex values and extend them to match any
        # rev/sku, but if a rev/sku is given match only the given one.
        if pattern and re.fullmatch("[\w,-]+", pattern):
            prefix, rev, sku = re.fullmatch(
                "(.*?)(-rev\d+)?(-sku\d+)?",
                pattern,
            ).groups()

            pattern = "{}{}{}".format(
                prefix,
                rev or "(-rev\d+)?",
                sku or "(-sku\d+)?",
            )

        if pattern:
            return re.compile(pattern)

    @property
    def hwid_match(self):
        pattern = self._config.get("hwid-match")
        if pattern:
            return re.compile(pattern)

    @property
    def boots_lz4_kernel(self):
        return self._config.getboolean("boots-lz4-kernel", False)

    @property
    def boots_lzma_kernel(self):
        return self._config.getboolean("boots-lzma-kernel", False)

    @property
    def image_max_size(self):
        max_size = self._config.get("image-max-size")
        if max_size in (None, "None", "none"):
            return float("inf")
        elif max_size.startswith("0x"):
            return int(max_size, 16)
        elif max_size.startswith("0o"):
            return int(max_size, 8)
        elif max_size.startswith("0b"):
            return int(max_size, 2)
        else:
            return int(max_size)

    @property
    def image_format(self):
        return self._config.get("image-format")


class depthchargectl(
    Command,
    prog="depthchargectl",
    usage="%(prog)s [options] command ...",
    add_help=False,
):
    """Manage Chrome OS kernel partitions."""

    logger = logging.getLogger(__name__)
    config_section = "depthchargectl"

    @Group
    def global_options(self):
        """Global options"""

    @global_options.add
    @Argument("-h", "--help", action="help")
    def print_help(self):
        """Show this help message."""
        # type(self).parser.print_help()

    @global_options.add
    @Argument(
        "-V", "--version",
        action="version",
        version="depthcharge-tools %(prog)s {}".format(__version__),
    )
    def version(self):
        """Print program version."""
        return type(self).version.version % {"prog": type(self).prog}

    @global_options.add
    @Argument("-v", "--verbose", count=True)
    def verbosity(self, verbosity=0):
        """Print more detailed output."""
        level = logging.WARNING - int(verbosity) * 10
        self.logger.setLevel(level)
        return verbosity

    @global_options.add
    @Argument("--tmpdir", nargs=1)
    def tmpdir(self, dir_=None):
        """Directory to keep temporary files."""
        if dir_ is None:
            dir_ = tempfile.TemporaryDirectory(
                prefix="depthchargectl-",
            )
            dir_ = self.exitstack.enter_context(dir_)

        dir_ = Path(dir_)
        os.makedirs(dir_, exist_ok=True)

        self.logger.debug("Working in temp dir '{}'.".format(dir_))

        return dir_

    @global_options.add
    @Argument("--root", nargs=1)
    def root(self, root=None):
        """Root device or mountpoint of the system to work on."""
        if root is None:
            self.logger.info(
                "Defaulting to current system root '/'."
            )
            return None

        if os.path.ismount(Path(root).resolve()):
            self.logger.info(
                "Using root argument '{}' as the system to work on."
                .format(root)
            )
            return Path(root).resolve()

        self.logger.info(
            "Using root argument '{}' as a device description."
            .format(root)
        )

        return str(root)

    @global_options.add
    @Argument("--root-mountpoint", nargs=1, help=argparse.SUPPRESS)
    def root_mountpoint(self, mnt=None):
        """Root mountpoint of the system to work on."""
        if mnt:
            mnt = Path(mnt).resolve()
            self.logger.info(
                "Using root mountpoint '{}' from given argument."
                .format(mnt)
            )
            return mnt

        if self.root in ("", "None", "none", None):
            return Path("/").resolve()

        if isinstance(self.root, Path):
            return self.root

        disk = system_disks.evaluate(self.root)
        mountpoints = sorted(
            system_disks.mountpoints(disk),
            key=lambda p: len(p.parents),
        )

        if len(mountpoints) > 1:
            self.logger.warning(
                "Choosing '{}' from multiple root mountpoints: {}."
                .format(mountpoints[0], mountpoints)
            )

        if mountpoints:
            return mountpoints[0]

        self.logger.warning(
            "Couldn't find root mountpoint, falling back to '/'."
        )

        return Path("/").resolve()

    @global_options.add
    @Argument("--boot-mountpoint", nargs=1, help=argparse.SUPPRESS)
    def boot_mountpoint(self, boot=None):
        """Boot mountpoint of the system to work on."""
        if boot:
            boot = Path(boot).resolve()
            self.logger.info(
                "Using boot mountpoint '{}' from given argument."
                .format(root)
            )
            return boot

        root = self.root_mountpoint
        disks = Disks(
            fstab=(root / "etc" / "fstab"),
            crypttab=(root / "etc" / "crypttab"),
        )
        boot_str = disks.by_mountpoint("/boot", fstab_only=True)
        device = system_disks.evaluate(boot_str)
        mountpoints = sorted(
            disks.mountpoints(device),
            key=lambda p: len(p.parents),
        )

        if device and not mountpoints:
            self.logger.warning(
                "Boot partition '{}' for specified root is not mounted."
                .format(device)
            )

        if len(mountpoints) > 1:
            self.logger.warning(
                "Choosing '{}' from multiple /boot mountpoints: {}."
                .format(mountpoints[0], mountpoints)
            )

        if mountpoints:
            return mountpoints[0]

        boot = (root / "boot").resolve()
        self.logger.info(
            "Couldn't find /boot in fstab, falling back to '{}'."
            .format(boot)
        )

        if root != Path("/").resolve() and not boot.is_dir():
            self.logger.warning(
                "Boot mountpoint '{}' does not exist for custom root."
                .format(boot)
            )
            self.logger.warning(
                "Not falling back to the running system for boot mountpoint."
            )

        return boot

    @Group
    def config_options(self):
        """Configuration options"""

        # Autodetect OS-distributed keys if custom values not given.
        keydir, keyblock, signprivate, signpubkey = vboot_keys()
        if self.vboot_keyblock is None:
            self.vboot_keyblock = keyblock
        if self.vboot_private_key is None:
            self.vboot_private_key = signprivate
        if self.vboot_public_key is None:
            self.vboot_public_key = signpubkey

        # Update the values in the configparser object so that the
        # config subcommand can query e.g. the autodetected board.
        self.config.update({
            'board': self.board.codename if self.board else "none",
            'images-dir': str(self.images_dir),
            'vboot-keyblock': str(self.vboot_keyblock),
            'vboot-public-key': str(self.vboot_public_key),
            'vboot-private-key': str(self.vboot_private_key),
            'kernel-cmdline': " ".join(self.kernel_cmdline),
            'ignore-initramfs': str(self.ignore_initramfs),
        })

    @config_options.add
    @Argument("--config", nargs=1)
    def config(self, file_=None):
        """Additional configuration file to read"""
        if isinstance(file_, configparser.SectionProxy):
            parser = file_.parser

        elif isinstance(file_, configparser.ConfigParser):
            parser = file_
            file_ = None

        else:
            parser = configparser.ConfigParser(
                default_section="depthcharge-tools",
                dict_type=ConfigDict,
            )

            parser.read_string(config_ini, source="config.ini")
            parser.read_string(boards_ini, source="boards.ini")

            try:
                for p in parser.read(config_files):
                    self.logger.debug("Read config file '{}'.".format(p))

            except configparser.ParsingError as err:
                self.logger.warning(
                    "Config file '{}' could not be parsed."
                    .format(err.filename)
                )

        root = self.root_mountpoint
        boot = self.boot_mountpoint

        def fixup_path(f):
            p = Path(f).resolve()
            if f.startswith("/boot"):
                return str(boot / p.relative_to("/boot"))
            elif f.startswith("/"):
                return str(root / p.relative_to("/"))

        if root != Path("/").resolve():
            extra_parser = configparser.ConfigParser()
            extra_files = [
                *root.glob("etc/depthcharge-tools/config"),
                *root.glob("etc/depthcharge-tools/config.d/*"),
            ]

            try:
                for p in extra_parser.read(extra_files):
                    self.logger.debug("Read config file '{}'.".format(p))

            except configparser.ParsingError as err:
                self.logger.warning(
                    "Config file '{}' could not be parsed."
                    .format(err.filename)
                )

            file_configs = [
                "images-dir",
                "vboot-keyblock",
                "vboot-public-key",
                "vboot-private-key",
            ]

            for sect, conf in extra_parser.items():
                for key, value in conf.items():
                    if key in file_configs:
                        conf[key] = fixup_path(value)

            parser.read_dict(extra_parser)

        if self.config_section not in parser.sections():
            if self.config_section != parser.default_section:
                parser.add_section(self.config_section)

        if isinstance(file_, collections.abc.Mapping):
            parser[self.config_section].update(file_)

        elif file_ is not None:
            try:
                read = parser.read([file_])

            except configparser.ParsingError as err:
                raise ValueError(
                    "Config file '{}' could not be parsed."
                    .format(err.filename)
                )

            if file_ not in read:
                raise ValueError(
                    "Config file '{}' could not be read."
                    .format(file_)
                )

        return parser[self.config_section]

    @config_options.add
    @Argument("--board", nargs=1)
    def board(self, codename=""):
        """Assume we're running on the specified board"""
        if isinstance(codename, Board):
            return codename

        elif isinstance(codename, configparser.SectionProxy):
            return Board(codename)

        elif codename is None:
            return None

        boards = {
            sectname: Board(section)
            for sectname, section in self.config.parser.items()
            if sectname.startswith("boards/")
        }

        if not codename:
            codename = self.config.get("board", "")

        if codename in ("None", "none"):
            return None

        elif codename:
            parts = str(codename).lower().replace('-', '_').split('_')

            def codename_match(item):
                if item is None:
                    return (len(parts) - 1, 0)

                sectname, board = item
                matchparts = sectname.split("/") + (
                    [] if board.codename is None else
                    board.codename.lower().replace('-', '_').split('_')
                )

                # Don't match sections without explicit codenames
                parent, _, _ = sectname.rpartition('/')
                if parent in boards and boards[parent].codename == board.codename:
                    return (len(parts) - 1, float("inf"))

                # Some kind of a fuzzy match, how many parts of the
                # given codename exist in the parts of this config
                idx = len(parts) - 1
                while parts and matchparts and idx >= 0:
                    if parts[idx] == matchparts[-1]:
                        idx -= 1
                    # Oldest boards have x86-alex_he etc.
                    elif (parts[idx], matchparts[-1]) == ("x86", "amd64"):
                        idx -= 1
                    matchparts.pop()

                return (idx, len(sectname.split("/")))

            match_groups = collections.defaultdict(list)
            for item in (None, *boards.items()):
                match_groups[codename_match(item)].append(item)

            score, matches = min(match_groups.items())
            if not matches or None in matches:
                raise ValueError(
                    "Unknown board codename '{}'."
                    .format(codename)
                )

            elif len(matches) > 1:
                raise ValueError(
                    "Ambiguous board codename '{}' matches {}."
                    .format(codename, [b.codename for s, b in matches])
                )

            sectname, board = matches[0]
            self.logger.info(
                "Assuming board '{}' ('{}') by codename argument or config."
                .format(board.name, board.codename)
            )

            return board

        hwid = cros_hwid()
        def hwid_match(item):
            sectname, board = item
            try:
                return bool(re.match(board.hwid_match, hwid))
            except:
                return False

        if hwid is not None:
            matches = tuple(filter(hwid_match, boards.items()))
        else:
            matches = ()

        if matches:
            sectname, board = matches[0]
            self.logger.info(
                "Detected board '{}' ('{}') by HWID."
                .format(board.name, board.codename)
            )
            return board

        compatibles = dt_compatibles()
        def compat_preference(item):
            if item is None:
                return (len(compatibles), 0)

            sectname, board = item
            if board.dt_compatible is None:
                return (float("inf"), 0)

            for i, c in enumerate(compatibles):
                if board.dt_compatible.fullmatch(c):
                    return (i, -len(sectname.split("/")))
            else:
                return (float("inf"), -1)

        if compatibles is not None:
            match = min((None, *boards.items()), key=compat_preference)
        else:
            match = None

        if match is not None:
            sectname, board = match
            self.logger.info(
                "Detected board '{}' ('{}') by device-tree compatibles."
                .format(board.name, board.codename)
            )
            return board

        # This might actually be running on non-ChromeOS hardware.
        # Check this after the board detection code, because we might
        # also be running on e.g. RW_LEGACY but still with depthcharge.
        if not is_cros_boot():
            return None

        # Use generic boards per cpu architecture, since we couldn't
        # detect this system as a proper board
        arch = platform.machine()
        if arch in Architecture.arm_32:
            sectname = "boards/arm"
        elif arch in Architecture.arm_64:
            sectname = "boards/arm64"
        elif arch in Architecture.x86_32:
            sectname = "boards/x86"
        elif arch in Architecture.x86_64:
            sectname = "boards/amd64"
        board = boards.get(sectname, None)
        if board is not None:
            self.logger.warning(
                "Assuming a generic board of architecture '{}'."
                .format(board.arch)
            )
            return board

        raise ValueError(
            "Could not detect which board this is running on."
        )

    @config_options.add
    @Argument("--images-dir", nargs=1)
    def images_dir(self, dir_=None):
        """Directory to store built images"""
        if dir_ is not None:
            return Path(dir_).resolve()

        dir_ = self.config.get("images-dir")
        if dir_ is None:
            raise ValueError(
                "Images directory is not specified"
            )

        dir_ = Path(dir_).resolve()

        root = self.root_mountpoint
        if root != Path("/").resolve() and not dir_.is_relative_to(root):
            return root / dir_.relative_to("/")

        return dir_

    @config_options.add
    @Argument("--vboot-keyblock", nargs=1)
    def vboot_keyblock(self, keyblock=None):
        """Keyblock file to include in images"""
        if keyblock is None:
            keyblock = self.config.get("vboot-keyblock")

        return keyblock

    @config_options.add
    @Argument("--vboot-public-key", nargs=1)
    def vboot_public_key(self, signpubkey=None):
        """Public key file to verify images with"""
        if signpubkey is None:
            signpubkey = self.config.get("vboot-public-key")

        return signpubkey

    @config_options.add
    @Argument("--vboot-private-key", nargs=1)
    def vboot_private_key(self, signprivate=None):
        """Private key file to sign images with"""
        if signprivate is None:
            signprivate = self.config.get("vboot-private-key")

        return signprivate

    @config_options.add
    @Argument("--kernel-cmdline", nargs="+", metavar="CMD")
    def kernel_cmdline(self, *cmds):
        """Command line options for the kernel"""
        if len(cmds) == 0:
            cmdline = self.config.get("kernel-cmdline")
            if cmdline is not None:
                cmds = shlex.split(cmdline)

        flat_cmds = []
        for cmd in cmds:
            flat_cmds.extend(shlex.split(cmd))

        return flat_cmds

    @config_options.add
    @Argument("--ignore-initramfs", ignore=True)
    def ignore_initramfs(self, ignore=None):
        """Do not include initramfs in images"""
        if ignore is None:
            ignore = self.config.getboolean("ignore-initramfs", False)

        return ignore

    @Subparsers()
    def command(self, cmd):
        """Supported subcommands"""

    def __call__(self):
        if hasattr(type(self), "list"):
            self.logger.info("No subcommand given, defaulting to list")
            return type(self).list()
        else:
            raise ValueError("No subcommand given")


import depthcharge_tools.depthchargectl._bless
import depthcharge_tools.depthchargectl._build
import depthcharge_tools.depthchargectl._check
import depthcharge_tools.depthchargectl._config
import depthcharge_tools.depthchargectl._list
import depthcharge_tools.depthchargectl._remove
import depthcharge_tools.depthchargectl._target
import depthcharge_tools.depthchargectl._write
