#! /usr/bin/env python3

import argparse
import collections
import logging
import os
import pathlib
import re
import shutil
import tempfile

from depthcharge_tools import __version__
from depthcharge_tools.process import (
    gzip,
    lz4,
    lzma,
    cgpt,
    findmnt,
)

logger = logging.getLogger(__name__)


def depthcharge_partitions(*disks):
    parts = []
    for disk in disks:
        proc = cgpt("find", "-n", "-t", "kernel", disk.path)
        parts += [disk.partition(int(n)) for n in proc.stdout.splitlines()]

    output = []
    for part in parts:
        proc = cgpt("show", "-A", "-i", str(part.partno), part.disk.path)
        attr = int(proc.stdout, 16)
        priority = (attr) & 0xF
        tries = (attr >> 4) & 0xF
        successful = (attr >> 8) & 0x1
        output += [(part, priority, tries, successful)]

    return output


class Path(pathlib.PosixPath):
    def copy_to(self, dest):
        dest = shutil.copy2(self, dest)
        return Path(dest)

    def is_gzip(self):
        proc = gzip.test(self)
        return proc.returncode == 0

    def gunzip(self, dest=None):
        if dest is None:
            if self.name.endswith(".gz"):
                dest = self.parent / self.name[:-3]
            else:
                dest = self.parent / (self.name + ".gunzip")
        gzip.decompress(self, dest)
        return Path(dest)

    def lz4(self, dest=None):
        if dest is None:
            dest = self.parent / (self.name + ".lz4")
        lz4.compress(self, dest)
        return Path(dest)

    def lzma(self, dest=None):
        if dest is None:
            dest = self.parent / (self.name + ".lzma")
        lzma.compress(self, dest)
        return Path(dest)

    def is_vmlinuz(self):
        return any((
            "vmlinuz" in self.name,
            "vmlinux" in self.name,
            "linux" in self.name,
            "Image" in self.name,
            "kernel" in self.name,
        ))

    def is_initramfs(self):
        return any((
            "initrd" in self.name,
            "initramfs" in self.name,
            "cpio" in self.name,
        ))

    def is_dtb(self):
        return any((
            "dtb" in self.name,
        ))


class BlockDevice:
    _parents = collections.defaultdict(set)
    _physicals = set()

    def __init__(self, path):
        path = Path(path).resolve()

        if Path("/dev") not in path.parents:
            fmt = "Path '{}' is not in /dev."
            msg = fmt.format(dev)
            raise ValueError(msg)

        if not path.is_block_device():
            fmt = "Path '{}' is not a block device."
            msg = fmt.format(path)
            raise ValueError(msg)

        self.path = path

    @classmethod
    def scan_devices(cls, force=False):
        if cls._parents and cls._physicals and not force:
            return

        for dev in Path("/sys/class/block").iterdir():
            dm_name_path = dev / "dm" / "name"
            if dm_name_path.is_file():
                dm_name = dm_name_path.read_text()
                for name in dm_name.splitlines():
                    cls._parents[name].add(dev.name)

            slaves_path = dev / "slaves"
            if slaves_path.is_dir():
                for slave in slaves_path.iterdir():
                    cls._parents[dev.name].add(slave.name)

            parent = dev.resolve().parent
            if parent.parent.name == "block":
                cls._parents[dev.name].add(parent.name)

            if parent.name == "block" and parent.parent.name != "virtual":
                cls._physicals.add(dev.name)

    @classmethod
    def from_mountpoint(cls, mnt):
        proc = findmnt.find(mnt, fstab=True)
        if proc.returncode == 0:
            return cls(proc.stdout.strip())

        proc = findmnt.find(mnt, fstab=False)
        if proc.returncode == 0:
            return cls(proc.stdout.strip())

    @classmethod
    def bootable_physical_disks(cls):
        boot = cls.from_mountpoint("/boot")
        root = cls.from_mountpoint("/")

        disks = []
        if boot is not None:
            disks += boot.physical_parents()
        if root is not None:
            for disk in root.physical_parents():
                if disk not in disks:
                    disks.append(disk)

        return disks

    @classmethod
    def all_physical_disks(cls):
        disks = []
        for p in sorted(cls._physicals):
            try:
                dev = DiskDevice(Path("/dev") / p)
                disks.append(dev)
            except:
                pass

        return disks

    def physical_parents(self):
        if self.path.name in self._physicals:
            return [self]

        parents = set(self._parents[self.path.name])
        while parents - self._physicals:
            for p in parents - self._physicals:
                parents.remove(p)
                parents.update(self._parents.get(p, set()))

        parent_devs = []
        for p in sorted(parents):
            try:
                dev = DiskDevice(Path("/dev") / p)
                parent_devs.append(dev)
            except:
                pass

        return parent_devs

    def is_physical_disk(self):
        return self.name in self._physicals

    def __repr__(self):
        cls = self.__class__.__name__
        return "{}('{}')".format(cls, self.path)


class Disk:
    def __init__(self, path):
        path = Path(path).resolve()

        if not (path.is_file() or path.is_block_device()):
            fmt = "Disk '{}' is not a file or block device."
            msg = fmt.format(str(path))
            raise ValueError(msg)

        self.path = path

    def partition(self, partno):
        return Partition(self.path, partno)

    def __repr__(self):
        cls = self.__class__.__name__
        return "{}('{}')".format(cls, self.path)


class Partition:
    def __init__(self, path, partno):
        disk = path if isinstance(path, Disk) else Disk(path)

        if partno is None:
            fmt = "Partition number not given for disk '{}'."
            msg = fmt.format(str(disk))
            raise ValueError(msg)

        elif not (isinstance(partno, int) and partno > 0):
            fmt = "Partition number '{}' must be a positive integer."
            msg = fmt.format(partno)
            raise ValueError(msg)

        self.disk = disk
        self.partno = partno

    def __repr__(self):
        cls = self.__class__.__name__
        return "{}('{}', {})".format(cls, self.disk.path, self.partno)


class DiskDevice(BlockDevice, Disk):
    def __init__(self, path, partno=None):
        path = Path(path).resolve()

        BlockDevice.__init__(self, path)
        Disk.__init__(self, path)

        self.path = path

    def partition(self, partno):
        return PartitionDevice(self.path, partno)


class PartitionDevice(BlockDevice, Partition):
    def __init__(self, path, partno=None):
        path = Path(path).resolve()

        if partno is not None:
            disk = path
            fmt = "{}p{}" if disk.name[-1].isnumeric() else "{}{}"
            name = fmt.format(disk.name, partno)
            path = disk.parent / name

        else:
            match = re.fullmatch("(.*[0-9])p([0-9]+)", path.name)
            if match is None:
                match = re.fullmatch("(.*[^0-9])([0-9]+)", path.name)
            if match:
                diskname, partno = match.groups()
                partno = int(partno)
                disk = path.parent / diskname

        BlockDevice.__init__(self, path)
        Partition.__init__(self, disk, partno)

        self.path = path
        self.disk = DiskDevice(disk)
        self.partno = partno


class Architecture(str):
    arm_32 = ["arm"]
    arm_64 = ["arm64", "aarch64"]
    arm = arm_32 + arm_64
    x86_32 = ["i386", "x86"]
    x86_64 = ["x86_64", "amd64"]
    x86 = x86_32 + x86_64
    all = arm + x86
    groups = (arm_32, arm_64, x86_32, x86_64)

    def __eq__(self, other):
        if isinstance(other, Architecture):
            for group in self.groups:
                if self in group and other in group:
                    return True
        return str(self) == str(other)

    def __ne__(self, other):
        if isinstance(other, Architecture):
            for group in self.groups:
                if self in group and other not in group:
                    return True
        return str(self) != str(other)

    @property
    def mkimage(self):
        if self in self.arm_32:
            return "arm"
        if self in self.arm_64:
            return "arm64"
        if self in self.x86_32:
            return "x86"
        if self in self.x86_64:
            return "x86_64"

    @property
    def vboot(self):
        if self in self.arm_32:
            return "arm"
        if self in self.arm_64:
            return "aarch64"
        if self in self.x86_32:
            return "x86"
        if self in self.x86_64:
            return "amd64"


class TemporaryDirectory(tempfile.TemporaryDirectory):
    def __enter__(self):
        return Path(super().__enter__())


class MixedArgumentsAction(argparse.Action):
    def __init_subclass__(cls, *args, **kwargs):
        super().__init_subclass__(*args, **kwargs)
        cls._selectors = {}
        cls._nargs = {}

    def __init__(self, option_strings, dest, select=None, **kwargs):
        super().__init__(option_strings, dest, **kwargs)
        self._selectors[select] = self.dest
        self._nargs[dest] = self.nargs

    def __call__(self, parser, namespace, values, option_string=None):
        if values is None:
            return
        elif values is getattr(namespace, self.dest):
            return
        elif not isinstance(values, list):
            values = [values]

        for value in values:
            chosen_dest = None
            for select, dest in self._selectors.items():
                if callable(select) and select(value):
                    chosen_dest = dest

            if chosen_dest is not None:
                try:
                    self._set_value(namespace, chosen_dest, value)
                    continue
                except argparse.ArgumentError as err:
                    parser.error(err.message)

            for dest, nargs in self._nargs.items():
                try:
                    self._set_value(namespace, dest, value)
                    break
                except argparse.ArgumentError as err:
                    continue
            else:
                parser.error(err.message)

    def _set_value(self, namespace, dest, value):
        nargs = self._nargs[dest]
        current = getattr(namespace, dest)

        if nargs is None or nargs == "?":
            if current is not None:
                fmt = "Cannot have multiple {} args '{}' and '{}'."
                msg = fmt.format(dest, current, value)
                raise argparse.ArgumentError(self, msg)
            else:
                setattr(namespace, dest, value)

        elif isinstance(nargs, int) and len(current) > nargs:
            fmt = "Cannot have more than {} {} args {}."
            msg = fmt.format(nargs, dest, current + value)
            raise argparse.ArgumentError(self, msg)

        elif current is None:
            setattr(namespace, dest, [value])

        else:
            current.append(value)

