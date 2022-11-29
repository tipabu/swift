# Copyright (c) 2022 NVIDIA
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import argparse
import datetime
import errno
import os
import sys
import time

from swift.common.ring.io import RingReader, RingWriter


def unpack_handler(args):
    if args.destination is None:
        if args.ring_gz.endswith(".gz"):
            args.destination = args.ring_gz[:-3]
        else:
            args.destination = args.ring_gz + ".d"

    with RingReader(args.ring_gz) as reader:
        if reader.version != 2:
            # TODO: well, I mean... we *could* load up a v1/v0 ring, then
            # write out the v2 sections from it...
            print("Only v2 rings can be unpacked; %r seems to be v%d" % (
                args.ring_gz, reader.version))
            return 1
        for section in reader.index:
            if section == "swift/index" and not args.unpack_index:
                continue
            file_path = os.path.join(args.destination, section)
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            with reader.open_section(section) as src, \
                    open(file_path, "wb") as dst:
                for chunk in iter(lambda: src.read(2 ** 18), b""):
                    dst.write(chunk)
                dst.flush()
                os.fsync(dst.fileno())


def pack_handler(args):
    args.ring_dir = args.ring_dir.rstrip("/")

    if args.destination is None:
        if args.ring_dir.endswith(".d"):
            args.destination = args.ring_dir[:-2]
        else:
            args.destination = args.ring_dir + ".gz"

    with RingWriter(args.destination, args.mtime) as writer:
        # can only pack v2 rings
        writer.write_magic(2)

        written = set()
        for expected in (
            "swift/ring/metadata",
            "swift/ring/devices",
            "swift/ring/assignments",
        ):
            file_path = os.path.join(args.ring_dir, expected)
            try:
                src = open(file_path, "rb")
            except (OSError, IOError) as e:
                if e.errno == errno.ENOENT:
                    # TODO: print a warning about the missing section?
                    continue
                raise
            with src, writer.section(expected) as dst:
                dst.write_size(os.fstat(src.fileno()).st_size)
                for chunk in iter(lambda: src.read(2 ** 18), b""):
                    dst.write(chunk)
            written.add(expected)

        # walk the tree and pick up anything else in there
        for path, _, files in os.walk(args.ring_dir):
            for f in files:
                file_path = os.path.join(path, f)
                section = file_path[len(args.ring_dir) + 1:]
                if section in written:
                    continue
                with open(file_path, "rb") as src, \
                        writer.section(section) as dst:
                    dst.write_size(os.fstat(src.fileno()).st_size)
                    for chunk in iter(lambda: src.read(2 ** 18), b""):
                        dst.write(chunk)
                written.add(section)


def list_handler(args):
    with RingReader(args.ring_gz) as reader:
        if reader.version != 2:
            # TODO: well, I mean... we *could* load up a v1/v0 ring...
            print("Only v2 rings can be listed; %r seems to be v%d" % (
                args.ring_gz, reader.version))
            return 1
        print('Archive:  %s' % args.ring_gz)
        print(' Length     Size   Cmpr  Name')
        print('--------  -------- ----  ----------')
        totals = {
            'uncompressed': 0,
            'compressed': 0,
            'sections': 0,
        }
        for section, info in reader.index.items():
            print('%8d  %8d %3d%%  %s' % (
                info.uncompressed_length,
                info.compressed_length,
                int(info.compression_ratio * 100),
                section,
            ))
            totals['uncompressed'] += info.uncompressed_length
            totals['compressed'] += info.compressed_length
            totals['sections'] += 1
        print('--------  -------- ----  ----------')
        print('%8d  %8d %3d%%  %d sections' % (
            totals['uncompressed'],
            totals['compressed'],
            int(100 * (1 - totals['compressed'] /
                       totals['uncompressed'])),
            totals['sections'],
        ))


def date_or_int(value):
    if value.isdigit():
        return int(value)
    for fmt in (
        "%Y-%m-%d",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
    ):
        try:
            d = datetime.datetime.strptime(value, fmt)
        except ValueError:
            continue
        else:
            return int(d.timestamp())
    raise ValueError("Could not parse date %s" % value)


def main(argv=None):
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers()

    unpack = subparsers.add_parser("unpack")
    unpack.add_argument("ring_gz")
    unpack.add_argument("-d", "--destination")
    # Not much point in extracting index (at least, not enough to do
    # it by default)
    unpack.add_argument("--unpack-index", action="store_true")
    unpack.set_defaults(handler=unpack_handler)

    pack = subparsers.add_parser("pack")
    pack.add_argument("ring_dir")
    pack.add_argument("-d", "--destination")
    pack.add_argument("-m", "--mtime",
                      type=date_or_int, default=int(time.time()))
    pack.set_defaults(handler=pack_handler)

    list = subparsers.add_parser("list")
    list.add_argument("ring_gz")
    list.set_defaults(handler=list_handler)

    args = parser.parse_args(argv)
    exit(args.handler(args))


if __name__ == "__main__":
    exit(main(sys.argv[1:]))
