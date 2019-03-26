#!/usr/bin/env python

from __future__ import print_function

try:
    from ConfigParser import RawConfigParser
except ImportError:
    from configparser import RawConfigParser
import contextlib
import json
import optparse
import os
import shutil
import subprocess
import sys
import tempfile
import time


def run(cmd, can_fail=False):
    """Run command and either report result or abort the script."""
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    (stdout, _) = proc.communicate()
    if proc.returncode == 0:
        return True
    if can_fail:
        return False
    print(
        "Executed command '%s' failed with following output:" % " ".join(cmd),
        file=sys.stderr,
    )
    print(stdout, file=sys.stderr)
    sys.exit(1)


@contextlib.contextmanager
def mount_iso(image):
    """Mount ISO into temporary directory and yield a path to it. When
    finished, it unmounts the image and deletes the mount point.
    """
    mount_point = tempfile.mkdtemp(suffix=".rhel-8-iso")

    run(["mount", "-o", "loop", image, mount_point])
    try:
        yield mount_point
    finally:
        for timeout in range(5):
            # Simple retry logic: it's possible the umount will fail with file
            # being still used.
            if run(["umount", mount_point], can_fail=True):
                break
            time.sleep(timeout)
        os.rmdir(mount_point)


def _tweak_paths(ti, variant, platforms=None):
    ti.set("general", "variants", variant)
    ti.set("general", "variant", variant)
    ti.set("general", "repository", ".")
    ti.set("general", "packagedir", "Packages")
    ti.set("tree", "variants", variant)

    ti.set("variant-%s" % variant, "packages", "Packages")
    ti.set("variant-%s" % variant, "repository", ".")

    if platforms:
        arch = ti.get("general", "arch")
        ti.set("general", "platforms", arch)
        ti.set("tree", "platforms", arch)

    ti.remove_section("media")


def tweak_baseos_treeinfo(file_path):
    ti = RawConfigParser()
    ti.read(file_path)

    _tweak_paths(ti, "BaseOS")

    ti.remove_section("variant-AppStream")

    with open(file_path, "w") as f:
        ti.write(f)


def tweak_appstream_treeinfo(file_path):
    ti = RawConfigParser()
    ti.read(file_path)

    _tweak_paths(ti, "AppStream", platforms=True)

    sections_to_remove = ["variant-BaseOS", "checksums", "stage2"]

    for section in ti.sections():
        if section.startswith("images-"):
            sections_to_remove.append(section)

    for section in sections_to_remove:
        ti.remove_section(section)

    with open(file_path, "w") as f:
        ti.write(f)


def copytree(src, dst):
    def filt(dir, files):
        return ["TRANS.TBL"]
    shutil.copytree(src, dst, ignore=filt)


def copy_boot_files(srcdir, destdir, ignore):
    # Copy everything that is not known already.
    IGNORE = ["AppStream", "BaseOS", ".treeinfo", ".discinfo", "media.repo"]
    IGNORE.extend(ignore)

    for fname in os.listdir(srcdir):
        if fname in IGNORE:
            continue
        src = os.path.join(srcdir, fname)
        if os.path.isdir(src):
            copytree(src, os.path.join(destdir, fname))
        else:
            shutil.copy(src, destdir)


def main():
    usage = "usage: %prog ISO_FILE DEST_DIR"
    parser = optparse.OptionParser(usage)
    opts, args = parser.parse_args()

    if len(args) != 2:
        parser.error(
            "Expected 2 arguments, path to ISO file and destination directory"
        )

    isofile, destdir = args

    if os.path.exists(destdir):
        parser.error("Destination directory already exists: %s" % destdir)

    join = os.path.join

    with mount_iso(isofile) as mount_dir:
        # Figure out what extra files are there, these go into both variants.
        with open(join(mount_dir, "extra_files.json")) as f:
            extra_files_data = json.load(f)
            extra_files = [item["file"] for item in extra_files_data["data"]]
        extra_files.insert(0, "extra_files.json")

        # Copy packages, repodata, metadata and extra_files for each variant.
        for variant in ("AppStream", "BaseOS"):
            variant_dir = join(destdir, variant.lower(), "kickstart")
            copytree(join(mount_dir, variant), variant_dir)

            for fname in ("discinfo", "treeinfo"):
                shutil.copy(join(mount_dir, "." + fname), join(variant_dir, fname))

            for fname in extra_files:
                shutil.copy(join(mount_dir, fname), variant_dir)

        # Copy everything that is left to BaseOS. It's boot images and
        # configuration.
        copy_boot_files(
            mount_dir, join(destdir, "baseos", "kickstart"), ignore=extra_files
        )

    # Update treeinfo files: fix paths to repos and packages, remove boot
    # references from AppStream, filter to only one variant in each file.
    tweak_baseos_treeinfo(join(destdir, "baseos", "kickstart", "treeinfo"))
    tweak_appstream_treeinfo(join(destdir, "appstream", "kickstart", "treeinfo"))

    run(["chmod", "u+w", "-R", destdir])


if __name__ == "__main__":
    main()
