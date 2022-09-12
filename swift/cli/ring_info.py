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
from swift.common.ring import RingData
from swift.common.ring.utils import none_dev_id


def main(args=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('ring_file')
    parser.add_argument('--verbose', '-v', action='store_true')
    parsed = parser.parse_args(args)
    try:
        rd = RingData.load(parsed.ring_file, metadata_only=not parsed.verbose)
    except Exception as e:
        print('Error reading ring file: %s' % e)
        return 1

    none_dev = none_dev_id(rd.dev_id_bytes)
    history_assignments = 0 if rd._history_count == 0 else sum(
        dev != none_dev
        for part2dev in rd._past2part2dev_id
        for dev in part2dev)

    print('Ring        ' + parsed.ring_file)

    # Old rings may have a version of None
    line = 'Version: ' + str(rd.version).rjust(8)
    line += ' (format v%d)' % rd.format_version
    print(line)

    line = 'Replicas:   '
    if int(rd.replica_count) == rd.replica_count:
        line += format(int(rd.replica_count), "5d")
    else:
        line += format(rd.replica_count, "5.3f")
    print(line)

    line = 'History:    %5d' % rd._history_count
    if parsed.verbose:
        line += ' (%d assignments)' % history_assignments
    print(line)

    line = 'Part Power: %5d (%d partitions)' % (
        32 - rd._part_shift, 2 ** (32 - rd._part_shift))
    print(line)

    line = 'Next PP:    ' + str(rd.next_part_power).rjust(5)
    print(line)

    line = 'Devices:    %5d (%d slots)' % (rd._num_devs, len(rd.devs))
    print(line)

    line = 'Dev ID Width: %3d' % rd.dev_id_bytes
    print(line)

    line = 'Size: %11d (%d uncompressed)' % (rd.size, rd.raw_size)
    print(line)


if __name__ == '__main__':
    main()
