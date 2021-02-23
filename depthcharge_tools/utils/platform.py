#! /usr/bin/env python3

import collections
import platform
import re
import shlex

from depthcharge_tools.utils.pathlib import Path
from depthcharge_tools.utils.subprocess import crossystem


def dt_compatibles():
    dt_model = Path("/proc/device-tree/compatible")
    if dt_model.exists():
        return dt_model.read_text().strip("\x00").split("\x00")


def dt_model():
    dt_model = Path("/proc/device-tree/model")
    if dt_model.exists():
        return dt_model.read_text().strip("\x00")


def cros_hwid():
    hwid_file = Path("/proc/device-tree/firmware/chromeos/hardware-id")
    if hwid_file.exists():
        return hwid_file.read_text().strip("\x00")

    # If we booted with e.g. u-boot, we don't have dt/firmware/chromeos
    proc = crossystem("hwid")
    if proc.returncode == 0:
        return proc.stdout.strip("\x00")


def os_release():
    os_release = {}

    os_release_f = Path("/etc/os-release")
    if os_release_f.exists():
        for line in os_release_f.read_text().splitlines():
            lhs, _, rhs = line.partition("=")
            os_release[lhs] = rhs.strip('\'"')

    return os_release


def kernel_cmdline():
    cmdline = ""

    cmdline_f = Path("/proc/cmdline")
    if cmdline_f.exists():
        cmdline = cmdline_f.read_text().rstrip("\n")

    return shlex.split(cmdline)


def is_cros_board(vboot=True):
    cmd = kernel_cmdline()

    # ChromeOS firmware injects one of these values into the cmdline
    # based on which boot mechanism is used.
    if "cros_secure" in cmd:
        return True
    elif "cros_efi" in cmd:
        return not vboot
    elif "cros_legacy" in cmd:
        return not vboot

    return False


def root_requires_initramfs(root):
    x = "[0-9a-fA-F]"
    uuid = "{x}{{8}}-{x}{{4}}-{x}{{4}}-{x}{{4}}-{x}{{12}}".format(x=x)
    ntsig = "{x}{{8}}-{x}{{2}}".format(x=x)

    # Tries to validate the root=* kernel cmdline parameter.
    # See init/do_mounts.c in Linux tree.
    for pat in (
        "[0-9a-fA-F]{4}",
        "/dev/nfs",
        "/dev/[0-9a-zA-Z]+",
        "/dev/[0-9a-zA-Z]+[0-9]+",
        "/dev/[0-9a-zA-Z]+p[0-9]+",
        "PARTUUID=({uuid}|{ntsig})".format(uuid=uuid, ntsig=ntsig),
        "PARTUUID=({uuid}|{ntsig})/PARTNROFF=[0-9]+".format(
            uuid=uuid, ntsig=ntsig,
        ),
        "[0-9]+:[0-9]+",
        "PARTLABEL=.+",
        "/dev/cifs",
    ):
        if re.fullmatch(pat, root):
            return False

    return True


def vboot_keys(*keydirs, system=True):
    if len(keydirs) == 0 or system:
        keydirs = (
            *keydirs,
            "/usr/share/vboot/devkeys",
            "/usr/local/share/vboot/devkeys",
        )

    for keydir in keydirs:
        keydir = Path(keydir)
        if not keydir.is_dir():
            continue

        keyblock = keydir / "kernel.keyblock"
        signprivate = keydir / "kernel_data_key.vbprivk"
        signpubkey = keydir / "kernel_subkey.vbpubk"

        if not keyblock.exists():
            keyblock = None
        if not signprivate.exists():
            signprivate = None
        if not signpubkey.exists():
            signpubkey = None

        if keyblock or signprivate or signpubkey:
            return keydir, keyblock, signprivate, signpubkey

    return None, None, None, None


def installed_kernels():
    kernels = {}
    initrds = {}
    fdtdirs = {}

    for f in Path("/boot").iterdir():
        f = f.resolve()
        if not f.is_file():
            continue

        if (
            f.name.startswith("vmlinuz-")
            or f.name.startswith("vmlinux-")
        ):
            _, _, release = f.name.partition("-")
            kernels[release] = f

        if f.name in (
            "vmlinux", "vmlinuz",
            "Image", "zImage", "bzImage",
        ):
            kernels[None] = f

        if (
            f.name.startswith("initrd-")
            or f.name.startswith("initrd.img-")
        ):
            _, _, release = f.name.partition("-")
            initrds[release] = f

        if f.name in ("initrd", "initrd.img"):
            initrds[None] = f

    for d in Path("/usr/lib").iterdir():
        if not d.is_dir():
            continue

        if d.name.startswith("linux-image-"):
            _, _, release = d.name.partition("linux-image-")
            fdtdirs[release] = d

    for d in Path("/boot/dtbs").iterdir():
        if d.name in kernels:
            fdtdirs[d.name] = d
        else:
            fdtdirs[None] = Path("/boot/dtbs")

    return [
        Kernel(
            release,
            kernel=kernels[release],
            initrd=initrds.get(release, None),
            fdtdir=fdtdirs.get(release, None),
        ) for release in kernels.keys()
    ]


class Kernel:
    def __init__(self, release, kernel, initrd=None, fdtdir=None):
        self.release = release
        self.kernel = kernel
        self.initrd = initrd
        self.fdtdir = fdtdir

    @property
    def description(self):
        os_name = os_release().get("NAME", None)

        if os_name is None:
            return "Linux {}".format(self.release)
        else:
            return "{}, with Linux {}".format(os_name, self.release)

    def _release_parts(self):
        return [
            [
                (
                    not (dot.startswith("rc") or dot.startswith("trunk")),
                    int(dot) if dot.isnumeric() else -1,
                    str(dot),
                )
                for dot in dash.split(".")
            ]
            for dash in self.release.split("-")
        ]

    def _comparable_parts(self, other):
        end = (True, -1, "")

        s, o = self._release_parts(), other._release_parts()
        for si, oi in zip(s, o):
            if len(si) > len(oi):
                oi += [end] * (len(si) - len(oi))
            if len(oi) > len(si):
                si += [end] * (len(oi) - len(si))

        if len(s) > len(o):
            o += [[end]] * (len(s) - len(o))
        if len(o) > len(s):
            s += [[end]] * (len(o) - len(s))

        return s, o

    def __lt__(self, other):
        if not isinstance(other, Kernel):
            return NotImplemented

        s, o = self._comparable_parts(other)
        return s < o

    def __gt__(self, other):
        if not isinstance(other, Kernel):
            return NotImplemented

        s, o = self._comparable_parts(other)
        return s > o


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


class SysDevTree(collections.defaultdict):
    def __init__(self, sys=None, dev=None):
        super().__init__(set)

        sys = Path(sys or "/sys")
        dev = Path(dev or "/dev")

        for sysdir in (sys / "class" / "block").iterdir():
            for device in (sysdir / "dm" / "name").read_lines():
                self.add(dev / "mapper" / device, dev / sysdir.name)

            for device in (sysdir / "slaves").iterdir():
                self.add(dev / sysdir.name, dev / device.name)

            for device in (sysdir / "holders").iterdir():
                self.add(dev / device.name, dev / sysdir.name)

            for device in sysdir.iterdir():
                if device.name.startswith(sysdir.name):
                    self.add(dev / device.name, dev / sysdir.name)

        self.sys = sys
        self.dev = dev

    def add(self, child, parent):
        if child.exists() and parent.exists():
            if child != parent:
                self[child].add(parent)

    def leaves(self, *children):
        ls = set()

        if not children:
            ls.update(*self.values())
            ls.difference_update(self.keys())
            return ls

        children = [Path(c).resolve() for c in children]
        while children:
            c = children.pop(0)
            if c in self:
                for p in self[c]:
                    children.append(p)
            else:
                ls.add(c)

        return ls
