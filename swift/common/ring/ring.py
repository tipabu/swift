# Copyright (c) 2010-2012 OpenStack Foundation
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

import array
import contextlib

import six.moves.cPickle as pickle
import json
from collections import defaultdict
from gzip import GzipFile
from os.path import getmtime
import struct
from time import time
import os
from itertools import chain, count
from tempfile import NamedTemporaryFile
import sys
import zlib

import six
from six.moves import range

from swift.common.exceptions import RingLoadError, DevIdBytesTooSmall
from swift.common.utils import hash_path, validate_configuration, md5
from swift.common.ring.utils import tiers_for_dev, BYTES_TO_TYPE_CODE, \
    calculate_minimum_dev_id_bytes, resized_array, none_dev_id


DEFAULT_RING_FORMAT_VERSION = 1
ALLOWED_RING_FORMAT_VERSIONS = (1, 2)
DEFAULT_RELOAD_TIME = 15


def calc_replica_count(replica2part2dev_id):
    if not replica2part2dev_id:
        return 0
    base = len(replica2part2dev_id) - 1
    extra = 1.0 * len(replica2part2dev_id[-1]) / len(replica2part2dev_id[0])
    return base + extra


@contextlib.contextmanager
def network_order_array(arr):
    if sys.byteorder == 'little':
        # Switch to network-order for serialization
        arr.byteswap()
    try:
        yield arr
    finally:
        if sys.byteorder == 'little':
            # Didn't make a copy; switch it back
            arr.byteswap()


def read_network_order_array(type_code, data):
    arr = array.array(type_code, data)
    if sys.byteorder == 'little':
        arr.byteswap()
    return arr


class FixedLengthReader(object):
    '''
    Wrap a file-like to cap how much can be read.

    This is useful for v2 ring formats which use a length-value encoding.
    On close, it verifies that the full length was read.

    :param fp: the file-like to wrap
    :param length: the maximum number of bytes to read
    '''
    def __init__(self, fp, length):
        self._fp = fp
        self._remaining = length

    def read(self, amt=None):
        if amt is None or amt < 0:
            amt = self._remaining
        amt = min(amt, self._remaining)
        data = self._fp.read(amt)
        self._remaining -= len(data)
        return data

    def close(self):
        if self._remaining:
            raise ValueError('Excess data in block: %d bytes remaining'
                             % self._remaining)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class RingReader(object):
    chunk_size = 2 ** 16

    def __init__(self, filename):
        self.fp = open(filename, 'rb')
        self._reset()

    def _reset(self):
        self._buffer = b''
        self.size = 0
        self.raw_size = 0
        self._md5 = md5(usedforsecurity=False)
        self._decomp = zlib.decompressobj(32 + zlib.MAX_WBITS)

    @property
    def close(self):
        return self.fp.close

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def seek(self, pos, ref=0):
        if (pos, ref) != (0, 0):
            raise NotImplementedError
        self._reset()
        return self.fp.seek(pos, ref)

    def _buffer_chunk(self):
        chunk = self.fp.read(self.chunk_size)
        if not chunk:
            return False
        self.size += len(chunk)
        self._md5.update(chunk)
        chunk = self._decomp.decompress(chunk)
        self.raw_size += len(chunk)
        self._buffer += chunk
        return True

    def read(self, amount=-1):
        if amount < 0:
            raise IOError("don't be greedy")

        while amount > len(self._buffer):
            if not self._buffer_chunk():
                break

        result, self._buffer = self._buffer[:amount], self._buffer[amount:]
        return result

    def readline(self):
        # apparently pickle needs this?
        while b'\n' not in self._buffer:
            if not self._buffer_chunk():
                break

        line, sep, self._buffer = self._buffer.partition(b'\n')
        return line + sep

    def readinto(self, buffer):
        chunk = self.read(len(buffer))
        buffer[:len(chunk)] = chunk
        return len(chunk)

    @property
    def md5(self):
        return self._md5.hexdigest()


class RingData(object):
    """Partitioned consistent hashing ring data (used for serialization)."""

    def __init__(self, replica2part2dev_id, devs, part_shift,
                 next_part_power=None, version=None, last_part2dev_id=None):
        self.devs = devs
        self._replica2part2dev_id = replica2part2dev_id
        self._part_shift = part_shift
        self.next_part_power = next_part_power
        self.version = version
        self._last_part2dev_id = last_part2dev_id or []
        self.md5 = self.size = self.raw_size = None

        for dev in self.devs:
            if dev is not None:
                dev.setdefault("region", 1)

    @property
    def replica_count(self):
        """Number of replicas (full or partial) used in the ring."""
        return calc_replica_count(self._replica2part2dev_id)

    @property
    def none_dev_id(self):
        return none_dev_id(self.dev_id_bytes)

    def set_dev_id_bytes(self, dev_id_bytes):
        if dev_id_bytes not in BYTES_TO_TYPE_CODE:
            raise ValueError('dev_id_bytes must be None or one of %r'
                             % (tuple(BYTES_TO_TYPE_CODE),))

        if dev_id_bytes == self._replica2part2dev_id[0].itemsize:
            return  # Already done!
        min_dev_id_bytes = calculate_minimum_dev_id_bytes(len(self.devs) - 1)
        if min_dev_id_bytes > dev_id_bytes:
            raise DevIdBytesTooSmall('Too many devices for %d-byte '
                                     'device ids' % dev_id_bytes)

        self._replica2part2dev_id = [
            resized_array(part2dev_id, dev_id_bytes)
            for part2dev_id in self._replica2part2dev_id]

        self._last_part2dev_id = [
            resize_array(part2dev_id, dev_id_bytes)
            for part2dev_id in self._last_part2dev_id]

    def calculate_and_update_dev_id_bytes(self):
        new_dev_id_bytes = calculate_minimum_dev_id_bytes(len(self.devs) - 1)
        self.set_dev_id_bytes(new_dev_id_bytes)
        return new_dev_id_bytes

    @classmethod
    def deserialize_v1(cls, gz_file, metadata_only=False):
        """
        Deserialize a v1 ring file into a dictionary with `devs`, `part_shift`,
        and `replica2part2dev_id` keys.

        If the optional kwarg `metadata_only` is True, then the
        `replica2part2dev_id` is not loaded and that key in the returned
        dictionary just has the value `[]`.

        :param file gz_file: An opened file-like object which has already
                             consumed the 6 bytes of magic and version.
        :param bool metadata_only: If True, only load `devs` and `part_shift`
        :returns: A dict containing `devs`, `part_shift`, and
                  `replica2part2dev_id`
        """

        json_len, = struct.unpack('!I', gz_file.read(4))
        ring_dict = json.loads(gz_file.read(json_len))
        ring_dict['replica2part2dev_id'] = []
        ring_dict['last_primary2dev_id'] = []

        if metadata_only:
            return ring_dict

        byteswap = (ring_dict.get('byteorder', sys.byteorder) != sys.byteorder)

        partition_count = 1 << (32 - ring_dict['part_shift'])
        for x in range(ring_dict['replica_count']):
            part2dev = array.array('H', gz_file.read(2 * partition_count))
            if byteswap:
                part2dev.byteswap()
            ring_dict['replica2part2dev_id'].append(part2dev)

        return ring_dict

    @classmethod
    def deserialize_v2(cls, gz_file, metadata_only=False):
        """
        Deserialize a v2 ring file into a dictionary with `devs`, `part_shift`,
        and `replica2part2dev_id` keys.

        If the optional kwarg `metadata_only` is True, then the
        `replica2part2dev_id` is not loaded and that key in the returned
        dictionary just has the value `[]`.

        :param file gz_file: An opened file-like object which has already
                             consumed the 6 bytes of magic and version.
        :param bool metadata_only: If True, only load `devs` and `part_shift`
        :returns: A dict containing `devs`, `part_shift`, and
                  `replica2part2dev_id`
        """

        json_len, = struct.unpack('!Q', gz_file.read(8))
        with FixedLengthReader(gz_file, json_len) as data:
            ring_dict = json.load(data)
        ring_dict['replica2part2dev_id'] = []
        ring_dict['last_primary2dev_id'] = []

        partition_count = 1 << (32 - ring_dict['part_shift'])
        max_row_len = ring_dict['dev_id_bytes'] * partition_count
        type_code = BYTES_TO_TYPE_CODE[ring_dict['dev_id_bytes']]
        if metadata_only:
            return ring_dict

        data_len, = struct.unpack('!Q', gz_file.read(8))
        with FixedLengthReader(gz_file, data_len) as data:
            for row in iter(lambda: data.read(max_row_len), b''):
                ring_dict['replica2part2dev_id'].append(
                    read_network_order_array(type_code, row))

        # This is the end of the required fields; anything after this point
        # needs to check that they actually read a length before trying to
        # unpack it

        # Attempt to load the last primary table
        packed_len = gz_file.read(8)
        if not packed_len:
            return ring_dict
        data_len, = struct.unpack('!Q', packed_len)
        with FixedLengthReader(gz_file, data_len) as data:
            for row in iter(lambda: data.read(max_row_len), b''):
                ring_dict['last_primary2dev_id'].append(
                    read_network_order_array(type_code, row))

        return ring_dict

    @classmethod
    def load(cls, filename, metadata_only=False):
        """
        Load ring data from a file.

        :param filename: Path to a file serialized by the save() method.
        :param bool metadata_only: If True, only load `devs` and `part_shift`.
        :returns: A RingData instance containing the loaded data.
        """
        with RingReader(filename) as gz_file:
            # See if the file is in the new format
            magic = gz_file.read(4)
            if magic == b'R1NG':
                format_version, = struct.unpack('!H', gz_file.read(2))
                if format_version == 1:
                    ring_data = cls.deserialize_v1(
                        gz_file, metadata_only=metadata_only)
                elif format_version == 2:
                    ring_data = cls.deserialize_v2(
                        gz_file, metadata_only=metadata_only)
                else:
                    raise Exception('Unknown ring format version %d' %
                                    format_version)
            else:
                # Assume old-style pickled ring
                gz_file.seek(0)
                ring_data = pickle.load(gz_file)

        if not hasattr(ring_data, 'devs'):
            ring_data = RingData(ring_data['replica2part2dev_id'],
                                 ring_data['devs'], ring_data['part_shift'],
                                 ring_data.get('next_part_power'),
                                 ring_data.get('version'),
                                 ring_data.get('last_primary2dev_id'))
        for attr in ('md5', 'size', 'raw_size'):
            setattr(ring_data, attr, getattr(gz_file, attr))
        return ring_data

    def serialize_v1(self, file_obj):
        # Write out new-style serialization magic and version:
        file_obj.write(struct.pack('!4sH', b'R1NG', 1))
        self.set_dev_id_bytes(2)
        ring = self.to_dict()

        # Only include next_part_power if it is set in the
        # builder, otherwise just ignore it
        _text = {'devs': ring['devs'], 'part_shift': ring['part_shift'],
                 'replica_count': len(ring['replica2part2dev_id']),
                 'byteorder': sys.byteorder}

        if ring['version'] is not None:
            _text['version'] = ring['version']

        next_part_power = ring.get('next_part_power')
        if next_part_power is not None:
            _text['next_part_power'] = next_part_power

        json_text = json.dumps(_text, sort_keys=True,
                               ensure_ascii=True).encode('ascii')
        json_len = len(json_text)
        file_obj.write(struct.pack('!I', json_len))
        file_obj.write(json_text)
        part2devs = list(ring['replica2part2dev_id'])
        for part2dev_id in part2devs:
            if six.PY2:
                # Can't just use tofile() because a GzipFile apparently
                # doesn't count as an 'open file'
                file_obj.write(part2dev_id.tostring())
            else:
                part2dev_id.tofile(file_obj)

    def serialize_v2(self, file_obj):
        # Write out new-style serialization magic and version:
        file_obj.write(struct.pack('!4sH', b'R1NG', 2))
        new_dev_id_bytes = self.calculate_and_update_dev_id_bytes()
        ring = self.to_dict()

        # Only include next_part_power if it is set in the
        # builder, otherwise just ignore it
        _text = {'devs': ring['devs'], 'part_shift': ring['part_shift'],
                 'dev_id_bytes': new_dev_id_bytes}

        if ring['version'] is not None:
            _text['version'] = ring['version']

        next_part_power = ring.get('next_part_power')
        if next_part_power is not None:
            _text['next_part_power'] = next_part_power

        json_text = json.dumps(_text, sort_keys=True,
                               ensure_ascii=True).encode('ascii')
        json_len = len(json_text)
        file_obj.write(struct.pack('!Q', json_len))
        file_obj.write(json_text)

        assignments = sum(len(a) for a in ring['replica2part2dev_id'])
        file_obj.write(struct.pack('!Q', new_dev_id_bytes * assignments))

        for part2dev_id in ring['replica2part2dev_id']:
            with network_order_array(part2dev_id):
                if six.PY2:
                    # Can't just use tofile() because a GzipFile apparently
                    # doesn't count as an 'open file'
                    file_obj.write(part2dev_id.tostring())
                else:
                    part2dev_id.tofile(file_obj)

        # Future code may put more data past the end. It should always be in
        #   [ length (8 bytes) ][ blob (<length> bytes) ]
        # format, and always optional (since old code won't attempt to parse
        # it). If we want to add a new *required* field, we need a v3 format.

        assignments = sum(len(a) for a in ring['last_part2dev_id'])
        file_obj.write(struct.pack('!Q', assignments * ring['dev_id_bytes']))

        for part2dev_id in ring['last_part2dev_id']:
            with network_order_array(part2dev_id):
                if six.PY2:
                    # Can't just use tofile() because a GzipFile apparently
                    # doesn't count as an 'open file'
                    file_obj.write(part2dev_id.tostring())
                else:
                    part2dev_id.tofile(file_obj)

    def save(self, filename, mtime=1300507380.0,
             format_version=DEFAULT_RING_FORMAT_VERSION):
        """
        Serialize this RingData instance to disk.

        :param filename: File into which this instance should be serialized.
        :param mtime: time used to override mtime for gzip, default or None
                      if the caller wants to include time
        :param format_version: Ring format version to use. May be 1 (default)
                               or 2. Older versions of Swift will refuse to
                               load rings with unrecognized versions.
        """
        if format_version == 1:
            serialize_f = self.serialize_v1
        elif format_version == 2:
            serialize_f = self.serialize_v2
        else:
            raise ValueError('format_version must be one of %r'
                             % (ALLOWED_RING_FORMAT_VERSIONS,))
        # Override the timestamp so that the same ring data creates
        # the same bytes on disk. This makes a checksum comparison a
        # good way to see if two rings are identical.
        tempf = NamedTemporaryFile(dir=".", prefix=filename, delete=False)
        gz_file = GzipFile(filename, mode='wb', fileobj=tempf, mtime=mtime)
        serialize_f(gz_file)
        # else, should have raised ValueError at the top
        gz_file.close()
        tempf.flush()
        os.fsync(tempf.fileno())
        tempf.close()
        os.chmod(tempf.name, 0o644)
        os.rename(tempf.name, filename)

    def to_dict(self):
        return {'devs': self.devs,
                'replica2part2dev_id': self._replica2part2dev_id,
                'last_part2dev_id': self._last_part2dev_id,
                'part_shift': self._part_shift,
                'next_part_power': self.next_part_power,
                'version': self.version}


class Ring(object):
    """
    Partitioned consistent hashing ring.

    :param serialized_path: path to serialized RingData instance
    :param reload_time: time interval in seconds to check for a ring change
    :param ring_name: ring name string (basically specified from policy)
    :param validation_hook: hook point to validate ring configuration ontime

    :raises RingLoadError: if the loaded ring data violates its constraint
    """

    def __init__(self, serialized_path, reload_time=None, ring_name=None,
                 validation_hook=lambda ring_data: None):
        # can't use the ring unless HASH_PATH_SUFFIX is set
        validate_configuration()
        if ring_name:
            self.serialized_path = os.path.join(serialized_path,
                                                ring_name + '.ring.gz')
        else:
            self.serialized_path = os.path.join(serialized_path)
        self.reload_time = (DEFAULT_RELOAD_TIME if reload_time is None
                            else reload_time)
        self._validation_hook = validation_hook
        self._reload(force=True)

    def _reload(self, force=False):
        self._rtime = time() + self.reload_time
        if force or self.has_changed():
            ring_data = RingData.load(self.serialized_path)

            try:
                self._validation_hook(ring_data)
            except RingLoadError:
                if force:
                    raise
                else:
                    # In runtime reload at working server, it's ok to use old
                    # ring data if the new ring data is invalid.
                    return

            self._mtime = getmtime(self.serialized_path)
            self._devs = ring_data.devs
            # NOTE(akscram): Replication parameters like replication_ip
            #                and replication_port are required for
            #                replication process. An old replication
            #                ring doesn't contain this parameters into
            #                device. Old-style pickled rings won't have
            #                region information.
            for dev in self._devs:
                if dev:
                    dev.setdefault('region', 1)
                    if 'ip' in dev:
                        dev.setdefault('replication_ip', dev['ip'])
                    if 'port' in dev:
                        dev.setdefault('replication_port', dev['port'])

            self._replica2part2dev_id = ring_data._replica2part2dev_id
            self._part_shift = ring_data._part_shift
            self._last_part2dev_id = ring_data._last_part2dev_id
            self._rebuild_tier_data()
            self._update_bookkeeping()
            self._next_part_power = ring_data.next_part_power
            self._version = ring_data.version
            self._md5 = ring_data.md5
            self._size = ring_data.size
            self._raw_size = ring_data.raw_size
            self.none_dev_id = ring_data.none_dev_id

    def _update_bookkeeping(self):
        # Do this now, when we know the data has changed, rather than
        # doing it on every call to get_more_nodes().
        #
        # Since this is to speed up the finding of handoffs, we only
        # consider devices with at least one partition assigned. This
        # way, a region, zone, or server with no partitions assigned
        # does not count toward our totals, thereby keeping the early
        # bailouts in get_more_nodes() working.
        dev_ids_with_parts = set()
        for part2dev_id in self._replica2part2dev_id:
            for dev_id in part2dev_id:
                dev_ids_with_parts.add(dev_id)
        regions = set()
        zones = set()
        ips = set()
        self._num_devs = 0
        self._num_assigned_devs = 0
        self._num_weighted_devs = 0
        for dev in self._devs:
            if dev is None:
                continue
            self._num_devs += 1
            if dev.get('weight', 0) > 0:
                self._num_weighted_devs += 1
            if dev['id'] in dev_ids_with_parts:
                regions.add(dev['region'])
                zones.add((dev['region'], dev['zone']))
                ips.add((dev['region'], dev['zone'], dev['ip']))
                self._num_assigned_devs += 1
        self._num_regions = len(regions)
        self._num_zones = len(zones)
        self._num_ips = len(ips)

    @property
    def next_part_power(self):
        if time() > self._rtime:
            self._reload()
        return self._next_part_power

    @property
    def part_power(self):
        return 32 - self._part_shift

    @property
    def version(self):
        return self._version

    @property
    def md5(self):
        return self._md5

    @property
    def size(self):
        return self._size

    @property
    def raw_size(self):
        return self._raw_size

    def _rebuild_tier_data(self):
        self.tier2devs = defaultdict(list)
        for dev in self._devs:
            if not dev:
                continue
            for tier in tiers_for_dev(dev):
                self.tier2devs[tier].append(dev)

        tiers_by_length = defaultdict(list)
        for tier in self.tier2devs:
            tiers_by_length[len(tier)].append(tier)
        self.tiers_by_length = sorted(tiers_by_length.values(),
                                      key=lambda x: len(x[0]))
        for tiers in self.tiers_by_length:
            tiers.sort()

    @property
    def replica_count(self):
        """Number of replicas (full or partial) used in the ring."""
        return calc_replica_count(self._replica2part2dev_id)

    @property
    def partition_count(self):
        """Number of partitions in the ring."""
        return len(self._replica2part2dev_id[0])

    @property
    def device_count(self):
        """Number of devices in the ring."""
        return self._num_devs

    @property
    def weighted_device_count(self):
        """Number of devices with weight in the ring."""
        return self._num_weighted_devs

    @property
    def assigned_device_count(self):
        """Number of devices with assignments in the ring."""
        return self._num_assigned_devs

    @property
    def devs(self):
        """devices in the ring"""
        if time() > self._rtime:
            self._reload()
        return self._devs

    def has_changed(self):
        """
        Check to see if the ring on disk is different than the current one in
        memory.

        :returns: True if the ring on disk has changed, False otherwise
        """
        return getmtime(self.serialized_path) != self._mtime

    def get_last_part_node(self, part):
        """
        Return the node that was a primary for the given part before
        the last rebalance, if there was a previous owner.

        This can only return nodes that were primaries in last partition.
        """
        if not (self._last_part2dev_id and self._last_part2dev_id[0] and
                part < len(self._last_part2dev_id[0])):
            return None

        dev_id = self._last_part2dev_id[0][part]
        if dev_id != self.none_dev_id:
            try:
                return self.devs[dev_id]
            except IndexError:
                pass
        return None

    def _get_part_nodes(self, part):
        part_nodes = []
        seen_ids = set()
        for r2p2d in self._replica2part2dev_id:
            if part < len(r2p2d):
                dev_id = r2p2d[part]
                if dev_id not in seen_ids:
                    part_nodes.append(self.devs[dev_id])
                    seen_ids.add(dev_id)
        return [dict(node, index=i) for i, node in enumerate(part_nodes)]

    def get_part(self, account, container=None, obj=None):
        """
        Get the partition for an account/container/object.

        :param account: account name
        :param container: container name
        :param obj: object name
        :returns: the partition number
        """
        key = hash_path(account, container, obj, raw_digest=True)
        if time() > self._rtime:
            self._reload()
        part = struct.unpack_from('>I', key)[0] >> self._part_shift
        return part

    def get_part_nodes(self, part):
        """
        Get the nodes that are responsible for the partition. If one
        node is responsible for more than one replica of the same
        partition, it will only appear in the output once.

        :param part: partition to get nodes for
        :returns: list of node dicts

        See :func:`get_nodes` for a description of the node dicts.
        """

        if time() > self._rtime:
            self._reload()
        return self._get_part_nodes(part)

    def get_nodes(self, account, container=None, obj=None):
        """
        Get the partition and nodes for an account/container/object.
        If a node is responsible for more than one replica, it will
        only appear in the output once.

        :param account: account name
        :param container: container name
        :param obj: object name
        :returns: a tuple of (partition, list of node dicts)

        Each node dict will have at least the following keys:

        ======  ===============================================================
        id      unique integer identifier amongst devices
        index   offset into the primary node list for the partition
        weight  a float of the relative weight of this device as compared to
                others; this indicates how many partitions the builder will try
                to assign to this device
        zone    integer indicating which zone the device is in; a given
                partition will not be assigned to multiple devices within the
                same zone
        ip      the ip address of the device
        port    the tcp port of the device
        device  the device's name on disk (sdb1, for example)
        meta    general use 'extra' field; for example: the online date, the
                hardware description
        ======  ===============================================================
        """
        part = self.get_part(account, container, obj)
        return part, self._get_part_nodes(part)

    def get_more_nodes(self, part, for_read=False):
        """
        Generator to get extra nodes for a partition for hinted handoff.

        The handoff nodes will try to be in zones other than the
        primary zones, will take into account the device weights, and
        will usually keep the same sequences of handoffs even with
        ring changes.

        :param part: partition to get handoff nodes for
        :param for_read: if true insert the last primary at the start of
            the generator, if one exists for the part.
        :returns: generator of node dicts

        See :func:`get_nodes` for a description of the node dicts.
        """
        if time() > self._rtime:
            self._reload()
        primary_nodes = self._get_part_nodes(part)
        used = set(d['id'] for d in primary_nodes)
        index = count()
        same_regions = set(d['region'] for d in primary_nodes)
        same_zones = set((d['region'], d['zone']) for d in primary_nodes)
        same_ips = set(
            (d['region'], d['zone'], d['ip']) for d in primary_nodes)

        # if for_read is used attempt to first push the last primary, if it
        # hesn't already be used.
        if for_read:
            last_primary = self.get_last_part_node(part)
            if last_primary and last_primary['id'] not in used:
                yield dict(last_primary, handoff_index=next(index))
                used.add(last_primary['id'])

        parts = len(self._replica2part2dev_id[0])
        part_hash = md5(str(part).encode('ascii'),
                        usedforsecurity=False).digest()
        start = struct.unpack_from('>I', part_hash)[0] >> self._part_shift
        inc = int(parts / 65536) or 1
        # Multiple loops for execution speed; the checks and bookkeeping get
        # simpler as you go along
        hit_all_regions = len(same_regions) == self._num_regions
        for handoff_part in chain(range(start, parts, inc),
                                  range(inc - ((parts - start) % inc),
                                        start, inc)):
            if hit_all_regions:
                # At this point, there are no regions left untouched, so we
                # can stop looking.
                break
            for part2dev_id in self._replica2part2dev_id:
                if handoff_part < len(part2dev_id):
                    dev_id = part2dev_id[handoff_part]
                    dev = self._devs[dev_id]
                    region = dev['region']
                    if dev_id not in used and region not in same_regions:
                        yield dict(dev, handoff_index=next(index))
                        used.add(dev_id)
                        same_regions.add(region)
                        zone = dev['zone']
                        ip = (region, zone, dev['ip'])
                        same_zones.add((region, zone))
                        same_ips.add(ip)
                        if len(same_regions) == self._num_regions:
                            hit_all_regions = True
                            break

        hit_all_zones = len(same_zones) == self._num_zones
        for handoff_part in chain(range(start, parts, inc),
                                  range(inc - ((parts - start) % inc),
                                        start, inc)):
            if hit_all_zones:
                # Much like we stopped looking for fresh regions before, we
                # can now stop looking for fresh zones; there are no more.
                break
            for part2dev_id in self._replica2part2dev_id:
                if handoff_part < len(part2dev_id):
                    dev_id = part2dev_id[handoff_part]
                    dev = self._devs[dev_id]
                    zone = (dev['region'], dev['zone'])
                    if dev_id not in used and zone not in same_zones:
                        yield dict(dev, handoff_index=next(index))
                        used.add(dev_id)
                        same_zones.add(zone)
                        ip = zone + (dev['ip'],)
                        same_ips.add(ip)
                        if len(same_zones) == self._num_zones:
                            hit_all_zones = True
                            break

        hit_all_ips = len(same_ips) == self._num_ips
        for handoff_part in chain(range(start, parts, inc),
                                  range(inc - ((parts - start) % inc),
                                        start, inc)):
            if hit_all_ips:
                # We've exhausted the pool of unused backends, so stop
                # looking.
                break
            for part2dev_id in self._replica2part2dev_id:
                if handoff_part < len(part2dev_id):
                    dev_id = part2dev_id[handoff_part]
                    dev = self._devs[dev_id]
                    ip = (dev['region'], dev['zone'], dev['ip'])
                    if dev_id not in used and ip not in same_ips:
                        yield dict(dev, handoff_index=next(index))
                        used.add(dev_id)
                        same_ips.add(ip)
                        if len(same_ips) == self._num_ips:
                            hit_all_ips = True
                            break

        hit_all_devs = len(used) == self._num_assigned_devs
        for handoff_part in chain(range(start, parts, inc),
                                  range(inc - ((parts - start) % inc),
                                        start, inc)):
            if hit_all_devs:
                # We've used every device we have, so let's stop looking for
                # unused devices now.
                break
            for part2dev_id in self._replica2part2dev_id:
                if handoff_part < len(part2dev_id):
                    dev_id = part2dev_id[handoff_part]
                    if dev_id not in used:
                        dev = self._devs[dev_id]
                        yield dict(dev, handoff_index=next(index))
                        used.add(dev_id)
                        if len(used) == self._num_assigned_devs:
                            hit_all_devs = True
                            break
