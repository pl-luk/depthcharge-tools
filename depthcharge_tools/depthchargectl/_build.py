#! /usr/bin/env python3

import argparse
import logging
import os
import shlex
import textwrap

from pathlib import Path

from depthcharge_tools import __version__
from depthcharge_tools.mkdepthcharge import mkdepthcharge
from depthcharge_tools.utils.argparse import (
    Command,
    Argument,
    Group,
    CommandExit,
)
from depthcharge_tools.utils.os import (
    system_disks,
    Partition,
)
from depthcharge_tools.utils.pathlib import (
    copy,
)
from depthcharge_tools.utils.platform import (
    installed_kernels,
    root_requires_initramfs,
)
from depthcharge_tools.utils.subprocess import (
    fdtget,
)

from depthcharge_tools.depthchargectl import depthchargectl

logger = logging.getLogger(__name__)


class SizeTooBigError(CommandExit):
    def __init__(self):
        super().__init__(
            "Couldn't build a small enough image for this board.",
        )


class InitramfsSizeTooBigError(SizeTooBigError):
    def __init__(self):
        super(SizeTooBigError, self).__init__(
            "Couldn't build a small enough image for this board. "
            "This is usually solvable by making the initramfs smaller, "
            "check your OS's documentation on how to do so."
        )


@depthchargectl.subcommand("build")
class depthchargectl_build(
    depthchargectl,
    prog="depthchargectl build",
    usage="%(prog)s [options] [KERNEL_VERSION]",
    add_help=False,
):
    """Buld a depthcharge image for the running system."""

    config_section = "depthchargectl/build"

    @Group
    def positionals(self):
        """Positional arguments"""

    @positionals.add
    @Argument
    def kernel_version(self, kernel_version=None):
        """Installed kernel version to build an image for."""
        kernels = installed_kernels()

        if isinstance(kernel_version, str):
            kernels = [
                k for k in kernels
                if k.release == kernel_version
            ]
            if not kernels:
                raise ValueError(
                    "Could not find an installed kernel for version '{}'."
                    .format(kernel_version)
                )
            kernel = kernels[0]

        elif kernels:
            kernel = max(kernels)

        return kernel

    @Group
    def options(self):
        """Options"""

    @Group
    def custom_kernel_options(self):
        """Custom kernel specification"""

    @custom_kernel_options.add
    @Argument("--kernel-release", nargs=1)
    def kernel_release(self, name=None):
        """Release name for the kernel used in image name"""
        if name is None:
            name = self.kernel_version.release

        return name

    @custom_kernel_options.add
    @Argument("--kernel", nargs=1)
    def kernel(self, file_=None):
        """Kernel executable"""
        if file_ is None:
            file_ = self.kernel_version.kernel

        # vmlinuz is always mandatory
        if file_ is None:
            raise ValueError(
                "No vmlinuz file found for version '{}'."
                .format(self.kernel_release)
            )

        return file_

    @custom_kernel_options.add
    @Argument("--initramfs", nargs=1)
    def initrd(self, file_=None):
        """Ramdisk image"""
        if file_ is None:
            file_ = self.kernel_version.initrd

        if self.ignore_initramfs:
            logger.warn(
                "Ignoring initramfs '{}' as configured."
                .format(file_)
            )
            return None

        # Initramfs is optional.
        if file_ is None:
            logger.info(
                "No initramfs file found for version '{}'."
                .format(self.kernel_release)
            )

        return file_

    @custom_kernel_options.add
    @Argument("--fdtdir", nargs=1)
    def fdtdir(self, dir_=None):
        """Directory to search device-tree binaries for the board"""
        if dir_ is None:
            dir_ = self.kernel_version.fdtdir

        return dir_

    @custom_kernel_options.add
    @Argument("--dtbs", nargs="+", metavar="FILE")
    def dtbs(self, *files):
        """Device-tree binary files to use instead of searching fdtdir"""

        # Device trees are optional based on board configuration.
        if self.board.dt_compatible and len(files) == 0:
            if self.fdtdir is None:
                raise ValueError(
                    "No dtb directory found for version '{}', "
                    "but this machine needs a dtb."
                    .format(self.kernel_release)
                )

            def is_compatible(dt_file):
                if self.board.dt_compatible == True:
                    return True
                proc = fdtget(dt_file, "/", "compatible", check=False)
                return self.board.dt_compatible in proc.stdout.split()

            files = list(filter(
                is_compatible,
                self.fdtdir.glob("**/*.dtb"),
            ))

            if len(files) == 0:
                raise ValueError(
                    "No dtb file compatible with '{}' found in '{}'."
                    .format(self.board.dt_compatible, self.fdtdir)
                )

        if self.board.image_format == "zimage" and len(files) != 0:
            raise ValueError(
                "Image format '{}' doesn't support dtb files."
                .format(self.board.image_format)
            )

        return files

    @options.add
    @Argument("--description", nargs=1)
    def description(self, desc=None):
        """Human-readable description for the image"""
        if desc is None:
            desc = self.kernel_version.description

        return desc

    @options.add
    @Argument("--root", nargs=1)
    def root(self, root=None):
        """Root device to add to kernel cmdline"""
        if root is None:
            cmdline = self.kernel_cmdline or []
            for c in cmdline:
                lhs, _, rhs = c.partition("=")
                if lhs.lower() == "root":
                    root = rhs
                    logger.info(
                        "Using root as set in user configured cmdline."
                    )

        if root is None:
            logger.info("Trying to figure out a root for cmdline.")
            root = system_disks.by_mountpoint("/", fstab_only=True)

            if root:
                logger.info("Using root as set in /etc/fstab.")
            else:
                logger.warn(
                    "Couldn't figure out a root cmdline parameter from "
                    "/etc/fstab. Will use currently mounted '{}'."
                    .format(root)
                )
                root = system_disks.by_mountpoint("/")

        if not root:
            raise ValueError(
                "Couldn't figure out a root cmdline parameter."
            )

        return root

    # This should be overriding kernel_cmdline from the parent instead...
    @property
    def cmdline(self):
        cmdline = self.kernel_cmdline or []

        # On at least Debian, the root the system should boot from
        # is included in the initramfs. Custom kernels might still
        # be able to boot without an initramfs, but we need to
        # inject a root= parameter for that.
        if 'root={}'.format(self.root) not in cmdline:
            logger.info(
                "Prepending 'root={}' to kernel cmdline."
                .format(self.root)
            )
            cmdline.insert(0, "root={}".format(self.root))

        if self.ignore_initramfs:
            logger.warn(
                "Ignoring initramfs as configured, "
                "appending 'noinitrd' to the kernel cmdline."
                .format(self.initrd)
            )
            cmdline.append("noinitrd")

        # Linux kernel without an initramfs only supports certain
        # types of root parameters, check for them.
        if self.initrd is None and root_requires_initramfs(self.root):
            raise ValueError(
                "An initramfs is required for root '{}'."
                .format(self.root)
            )

        return cmdline

    @options.add
    @Argument("--compress", nargs="+", metavar="TYPE")
    def compress(self, *compress):
        """Compression types to attempt."""

        # Allowed compression levels. We will call mkdepthcharge by
        # hand multiple times for these.
        for c in compress:
            if c not in ("none", "lz4", "lzma"):
                raise ValueError(
                    "Unsupported compression type '{}'."
                    .format(t)
                )

        if len(compress) == 0:
            compress = ["none"]
            if self.board.boots_lz4_kernel:
                compress += ["lz4"]
            if self.board.boots_lzma_kernel:
                compress += ["lzma"]

            # zimage doesn't support compression
            if self.board.image_format == "zimage":
                compress = ["none"]

        compress = sorted(set(compress), key=compress.index)

        # Skip compress="none" if inputs wouldn't fit max image size
        if "none" in compress:
            inputs_size = sum([
                self.kernel.stat().st_size,
                self.initrd.stat().st_size if self.initrd is not None else 0,
                *(dtb.stat().st_size for dtb in self.dtbs),
            ])

            if inputs_size > self.board.image_max_size:
                logger.warn(
                    "Inputs are too big, skipping uncompressed build."
                )
                compress = [c for c in compress if c != "none"]

        return compress

    @options.add
    @Argument("--timestamp", nargs=1)
    def timestamp(self, seconds=None):
        """Build timestamp for the image"""
        if seconds is None:
            if "SOURCE_DATE_EPOCH" in os.environ:
                seconds = os.environ["SOURCE_DATE_EPOCH"]

        # Initramfs date is bound to be later than vmlinuz date, so
        # prefer that if possible.
        if seconds is None:
            if self.initrd is not None:
                seconds = int(self.initrd.stat().st_mtime)
            else:
                seconds = int(self.kernel.stat().st_mtime)

        if seconds is None:
            logger.error(
                "Couldn't determine a timestamp from initramfs "
                "nor vmlinuz."
            )

        return seconds

    @options.add
    @Argument("--output", nargs=1)
    def output(self, path=None):
        """Output image to path instead of storing in images-dir"""
        if path is None:
            path = self.images_dir / "{}.img".format(self.kernel_release)

        return Path(path)

    def __call__(self):
        try:
            logger.info(
                "Building images for board '{}' ('{}')."
                .format(self.board.name, self.board.codename)
            )
        except KeyError:
            raise ValueError(
                "Cannot build images for unsupported board '{}'."
                .format(self.board)
            )

        logger.info(
            "Building for kernel version '{}'."
            .format(self.kernel_release)
        )

        # Images dir might not have been created at install-time
        os.makedirs(self.output.parent, exist_ok=True)

        # Build to a temporary file so we do not overwrite existing
        # images with an unbootable image.
        outtmp = self.tmpdir / "{}.tmp".format(self.output.name)

        # Try to keep output reproducible.
        if self.timestamp is not None:
            os.environ["SOURCE_DATE_EPOCH"] = str(self.timestamp)

        for compress in self.compress:
            logger.info("Trying with compression '{}'.".format(compress))
            tmpdir = self.tmpdir / "mkdepthcharge-{}".format(compress)

            try:
                mkdepthcharge(
                    arch=self.board.arch,
                    cmdline=self.cmdline,
                    compress=compress,
                    dtbs=self.dtbs,
                    image_format=self.board.image_format,
                    initramfs=self.initrd,
                    keyblock=self.vboot_keyblock,
                    name=self.description,
                    output=outtmp,
                    signprivate=self.vboot_private_key,
                    vmlinuz=self.kernel,
                    tmpdir=tmpdir,
                )

            except Exception as err:
                raise CommandExit(
                    "Failed while creating depthcharge image.",
                ) from err

            if outtmp.stat().st_size < self.board.image_max_size:
                break

            logger.warn(
                "Image with compression '{}' is too big for this board."
                .format(compress)
            )

        else:
            if self.initrd is not None:
                raise InitramfsSizeTooBigError()
            else:
                raise SizeTooBigError()

        logger.info("Copying newly built image to output.")
        copy(outtmp, self.output)

        logger.info(
            "Built image for kernel version '{}'."
            .format(self.kernel_release)
        )
        return self.output

    global_options = depthchargectl.global_options
    config_options = depthchargectl.config_options

