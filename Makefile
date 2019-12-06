#! /usr/bin/make -f

PACKAGENAME ?= depthcharge-tools
VERSION ?= 0.3.0-dev
DESTDIR ?=

PREFIX ?= /usr/local
BINDIR ?= $(PREFIX)/bin
SBINDIR ?= $(PREFIX)/sbin
DATADIR ?= $(PREFIX)/share
SYSCONFDIR ?= $(PREFIX)/etc
LOCALSTATEDIR ?= $(PREFIX)/var
LIBDIR ?= $(PREFIX)/lib
MANDIR ?= $(DATADIR)/man

vars := PACKAGENAME VERSION
vars += PREFIX BINDIR SBINDIR DATADIR SYSCONFDIR LOCALSTATEDIR LIBDIR

# Default values for depthchargectl configuration.
# These don't affect mkdepthcharge.
DEFAULT_FORMAT ?= fit
DEFAULT_CMDLINE ?= quiet splash
DEFAULT_COMPRESS ?= none lz4 lzma
DEFAULT_MAX_SIZE ?= 33554432
DEFAULT_DTB_NAME ?=
DEFAULT_NOINITRAMFS ?= no

# These are paths for Debian.
DEFAULT_VBOOT_DEVKEYS ?= /usr/share/vboot/devkeys
DEFAULT_VBOOT_KEYBLOCK ?= $${DEFAULT_VBOOT_DEVKEYS}/kernel.keyblock
DEFAULT_VBOOT_SIGNPUBKEY ?= $${DEFAULT_VBOOT_DEVKEYS}/kernel_subkey.vbpubk
DEFAULT_VBOOT_SIGNPRIVATE ?= $${DEFAULT_VBOOT_DEVKEYS}/kernel_data_key.vbprivk

d_vars := FORMAT CMDLINE COMPRESS MAX_SIZE DTB_NAME NOINITRAMFS
d_vars += VBOOT_DEVKEYS VBOOT_KEYBLOCK VBOOT_SIGNPUBKEY VBOOT_SIGNPRIVATE
vars += $(foreach var,$(d_vars),DEFAULT_$(var))

# Search lines of 'VAR="value"' and replace them with ours.
pattern = 's|^$(var)=".*"$$|$(var)="$(call $(var))"|1'
patterns := $(foreach var, $(dirs) $(vars),-e $(pattern))
substvars := sed $(patterns)

functions := $(foreach f, $(wildcard lib/*.sh), $(basename $(notdir $(f))))
pattern = '\|^\. "$${FUNCTIONS_DIR}/$(f)\.sh"$$| r lib/$(f).sh'
patterns := $(foreach f, $(functions),-e $(pattern))
includelibs := sed $(patterns) -e 's|^\. "$${FUNCTIONS_DIR}/.*\.sh"$$|\n|1'

all: bin/depthchargectl bin/depthchargectl.8
all: bin/mkdepthcharge bin/mkdepthcharge.1
all: bin/mkdepthcharge-standalone

bin/:
	mkdir -p bin

bin/depthchargectl: depthchargectl bin/
	$(substvars) <"$<" >"$@"

bin/mkdepthcharge: mkdepthcharge bin/
	$(substvars) <"$<" >"$@"

# This builds mkdepthcharge into a single file.
bin/mkdepthcharge-standalone: mkdepthcharge bin/
	$(substvars) <"$<" | $(includelibs) >"$@"

bin/depthchargectl.8: depthchargectl.rst bin/
	rst2man <"$<" >"$@"

bin/mkdepthcharge.1: mkdepthcharge.rst bin/
	rst2man <"$<" >"$@"

install: bin/depthchargectl bin/depthchargectl.8
install: bin/mkdepthcharge bin/mkdepthcharge.1
install:
	install -d '$(DESTDIR)$(BINDIR)'
	install -d '$(DESTDIR)$(SBINDIR)'
	install -d '$(DESTDIR)$(DATADIR)/$(PACKAGENAME)'
	install -d '$(DESTDIR)$(DATADIR)/$(PACKAGENAME)'/depthchargectl
	install -d '$(DESTDIR)$(SYSCONFDIR)/$(PACKAGENAME)'
	install -d '$(DESTDIR)$(SYSCONFDIR)/$(PACKAGENAME)'/config.d
	install -d '$(DESTDIR)$(SYSCONFDIR)/$(PACKAGENAME)'/userdb.d
	install -d '$(DESTDIR)$(LOCALSTATEDIR)/$(PACKAGENAME)'
	install -d '$(DESTDIR)$(LOCALSTATEDIR)/$(PACKAGENAME)'/images
	install -d '$(DESTDIR)$(LOCALSTATEDIR)/$(PACKAGENAME)'/images
	install -d '$(DESTDIR)$(MANDIR)'/man1
	install -d '$(DESTDIR)$(MANDIR)'/man8
	install -m 0755 bin/mkdepthcharge '$(DESTDIR)$(BINDIR)'
	install -m 0755 bin/depthchargectl '$(DESTDIR)$(SBINDIR)'
	install -m 0644 lib/*.sh '$(DESTDIR)$(DATADIR)/$(PACKAGENAME)'
	install -m 0644 lib/depthchargectl/*.sh '$(DESTDIR)$(DATADIR)/$(PACKAGENAME)'/depthchargectl
	install -m 0644 conf/db '$(DESTDIR)$(DATADIR)/$(PACKAGENAME)'
	install -m 0644 conf/userdb '$(DESTDIR)$(SYSCONFDIR)/$(PACKAGENAME)'
	install -m 0644 conf/config '$(DESTDIR)$(SYSCONFDIR)/$(PACKAGENAME)'
	install -m 0644 bin/mkdepthcharge.1 '$(DESTDIR)$(MANDIR)'/man1
	install -m 0644 bin/depthchargectl.8 '$(DESTDIR)$(MANDIR)'/man8

install-systemd: systemd/depthchargectl-set-good.service
	install -d '$(DESTDIR)$(LIBDIR)/systemd/system'
	install -m 0644 systemd/depthchargectl-set-good.service '$(DESTDIR)$(LIBDIR)/systemd/system'
	@echo "This target only installs the service, does not enable it."
	@echo "You might want to run:"
	@echo "  systemctl daemon-reload"
	@echo "  systemctl --enable depthchargectl-set-good"

uninstall:
	rm -f '$(DESTDIR)$(BINDIR)'/mkdepthcharge
	rm -f '$(DESTDIR)$(SBINDIR)'/depthchargectl
	rm -rf '$(DESTDIR)$(DATADIR)/$(PACKAGENAME)'
	rm -rf '$(DESTDIR)$(SYSCONFDIR)/$(PACKAGENAME)'
	rm -rf '$(DESTDIR)$(LOCALSTATEDIR)/$(PACKAGENAME)'
	rm -rf '$(DESTDIR)$(LIBDIR)'/systemd/system/depthchargectl-set-good.service

install-standalone: bin/mkdepthcharge-standalone
	install -d '$(DESTDIR)$(BINDIR)'
	install -m 0755 bin/mkdepthcharge-standalone -T '$(DESTDIR)$(BINDIR)'/mkdepthcharge

clean:
	rm -f bin/depthchargectl bin/depthchargectl.8
	rm -f bin/mkdepthcharge bin/mkdepthcharge.1
	rm -f bin/mkdepthcharge-standalone
	[ ! -d bin ] || rmdir bin

.PHONY: all install install-systemd install-standalone uninstall clean
