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

import array
import collections
import io
import json
import mock
import os.path
import pickle
import unittest
import zlib

from swift.common.ring.io import IndexEntry, RingReader, RingWriter

from test.unit import with_tempdir


class TestRoundTrip(unittest.TestCase):
    def assertRepeats(self, data, pattern, n):
        l = len(pattern)
        self.assertEqual(len(data), n * l)
        actual = collections.Counter(
            data[x * l:(x + 1) * l]
            for x in range(n))
        self.assertEqual(actual, {pattern: n})

    @with_tempdir
    def test_write_failure(self, tempd):
        tempf = os.path.join(tempd, 'not-persisted')
        try:
            with RingWriter.open(tempf):
                self.assertEqual(1, len(os.listdir(tempd)))
                raise ValueError
        except ValueError:
            pass
        self.assertEqual(0, len(os.listdir(tempd)))

    def test_arbitrary_bytes(self):
        buf = io.BytesIO()
        with RingWriter(buf) as writer:
            writer.write(b'\xde\xad\xbe\xef' * 10240)
            writer.write(b'\xda\x7a\xda\x7a' * 10240)
            good_pos = writer.tell()

            self.assertTrue(writer.flushed)
            pos = writer.raw_fp.tell()
            writer.write(b'')
            self.assertTrue(writer.flushed)
            self.assertEqual(pos, writer.raw_fp.tell())

            writer.write(b'more' * 10240)
            self.assertFalse(writer.flushed)

        buf.seek(0)
        reader = RingReader(buf)
        self.assertEqual(reader.version, 0)
        self.assertEqual(reader.raw_size, 12 * 10240)
        self.assertRepeats(reader.read(40960), b'\xde\xad\xbe\xef', 10240)
        self.assertRepeats(reader.read(40960), b'\xda\x7a\xda\x7a', 10240)
        self.assertRepeats(reader.read(40960), b'more', 10240)
        # Can seek backwards
        reader.seek(good_pos)
        self.assertRepeats(reader.read(40960), b'more', 10240)
        # Even all the way to the beginning
        reader.seek(0)
        self.assertRepeats(reader.read(40960), b'\xde\xad\xbe\xef', 10240)
        # but not arbitrarily
        reader.seek(good_pos - 100)
        with self.assertRaises(zlib.error):
            reader.read(1)

    def test_pickle(self):
        data = {
            "foo": "bar",
            "baz": [1, 2, 3],
            "quux": array.array('I', range(4)),
            "quuux": bytearray(b"\xde\xad\xbe\xef"),
        }
        buf = io.BytesIO()
        with RingWriter(buf) as writer:
            writer.write(pickle.dumps(data, protocol=2))

        buf.seek(0)
        reader = RingReader(buf)
        self.assertEqual(reader.version, 0)
        self.assertEqual(data, pickle.loads(reader.read(2 ** 10)))

        # section-based access only works for v2
        self.assertFalse('foo' in reader)
        with self.assertRaises(ValueError) as caught:
            with reader.open_section('foo'):
                pass
        self.assertEqual('No index loaded', str(caught.exception))

        buf = io.BytesIO()
        # they also work as writable/readable for pickle
        with RingWriter(buf) as writer:
            pickle.dump(data, writer, protocol=2)

        buf.seek(0)
        reader = RingReader(buf)
        self.assertEqual(reader.version, 0)
        self.assertEqual(data, pickle.load(reader))

    def test_sections(self):
        buf = io.BytesIO()
        with RingWriter(buf) as writer:
            writer.write_magic(2)
            with writer.section('foo'):
                writer.write_blob(b'\xde\xad\xbe\xef' * 10240)

            with writer.section('bar'):
                # Sometimes you might not want to get the whole section into
                # memory as a byte-string all at once (eg, when writing ring
                # assignments)
                writer.write_size(40960)
                for _ in range(10):
                    writer.write(b'\xda\x7a\xda\x7a' * 1024)

            with writer.section('baz'):
                writer.write_blob(b'more' * 10240)

                # Can't nest sections
                with self.assertRaises(ValueError):
                    with writer.section('inner'):
                        pass
                self.assertNotIn('inner', writer.index)

            writer.write(b'can add arbitrary bytes')
            # ...though accessing them on read may be difficult; see below.
            # This *is not* a recommended pattern -- write proper length-value
            # blobs instead (even if you don't include them as sections in the
            # index).

            with writer.section('quux'):
                writer.write_blob(b'data' * 10240)

            # Gotta do this at the start
            with self.assertRaises(IOError):
                writer.write_magic(2)

            # Can't write duplicate sections
            with self.assertRaises(ValueError):
                with writer.section('foo'):
                    pass

            # We're reserving globs, so we can later support something like
            # reader.load_sections('swift/ring/*')
            with self.assertRaises(ValueError):
                with writer.section('foo*'):
                    pass

        buf.seek(0)
        reader = RingReader(buf)
        self.assertEqual(reader.version, 2)
        # Order matters!
        self.assertEqual(list(reader.index), [
            'foo', 'bar', 'baz', 'quux', 'swift/index'])
        self.assertEqual({
            k: (uncomp_start, uncomp_end, algo)
            for k, (_, uncomp_start, _, uncomp_end, algo, _)
            in reader.index.items()
        }, {
            'foo': (6, 40974, 'sha256'),
            'bar': (40974, 81942, 'sha256'),
            'baz': (81942, 122910, 'sha256'),
            # note the gap between baz and quux for the raw bytes
            'quux': (122933, 163901, 'sha256'),
            'swift/index': (163901, None, None),
        })

        self.assertIn('foo', reader)
        self.assertNotIn('inner', reader)

        self.assertRepeats(reader.read_section('foo'),
                           b'\xde\xad\xbe\xef', 10240)
        with reader.open_section('bar') as s:
            for _ in range(10):
                self.assertEqual(s.read(4), b'\xda\x7a\xda\x7a')
            self.assertRepeats(s.read(), b'\xda\x7a\xda\x7a', 10230)
        # If you know that one section follows another, you don't *have*
        # to "open" the next one
        self.assertRepeats(reader.read_blob(), b'more', 10240)
        self.assertRepeats(reader.read_section('quux'),
                           b'data', 10240)
        index_dict = json.loads(reader.read_section('swift/index'))
        self.assertEqual(reader.index, {
            section: IndexEntry(*entry)
            for section, entry in index_dict.items()})

        # Missing section
        with self.assertRaises(KeyError) as caught:
            with reader.open_section('foobar'):
                pass
        self.assertEqual("'foobar'", str(caught.exception))

        # seek to the end of baz
        reader.seek(reader.index['baz'].compressed_end)
        # so we can read the raw bytes we stuffed in
        gap_length = (reader.index['quux'].uncompressed_start -
                      reader.index['baz'].uncompressed_end)
        self.assertGreater(gap_length, 0)
        self.assertEqual(b'can add arbitrary bytes',
                         reader.read(gap_length))

    def test_sections_with_corruption(self):
        buf = io.BytesIO()
        with RingWriter(buf) as writer:
            writer.write_magic(2)
            with writer.section('foo'):
                writer.write_blob(b'\xde\xad\xbe\xef' * 10240)

        buf.seek(0)
        reader = RingReader(buf)
        # if you open a section, you better read it all!
        read_bytes = b''
        with self.assertRaises(ValueError) as caught:
            with reader.open_section('foo') as s:
                read_bytes = s.read(4)
        self.assertEqual(
            'Incomplete read; expected 40956 more bytes to be read',
            str(caught.exception))
        self.assertEqual(b'\xde\xad\xbe\xef', read_bytes)

        # if there's a digest mismatch, you can read data, but it'll
        # throw an error on close
        self.assertEqual('sha256', reader.index['foo'].checksum_method)
        self.assertEqual(
            'c51d6703d54cd7cf57b4d4b7ecfcca60'
            '56dbd41ebf1c1e83c0e8e48baeff629a',
            reader.index['foo'].checksum_value)
        reader.index['foo'] = IndexEntry(*(
            reader.index['foo'][:-1] + ('not-the-sha',)))
        read_bytes = b''
        with self.assertRaises(ValueError) as caught:
            with reader.open_section('foo') as s:
                read_bytes = s.read()
        self.assertIn('Hash mismatch in block: ', str(caught.exception))
        self.assertRepeats(read_bytes, b'\xde\xad\xbe\xef', 10240)

    @mock.patch('logging.getLogger')
    def test_sections_with_unsupported_checksum(self, mock_logging):
        buf = io.BytesIO()
        with RingWriter(buf) as writer:
            writer.write_magic(2)
            with writer.section('foo'):
                writer.write_blob(b'\xde\xad\xbe\xef')
            writer.index['foo'] = IndexEntry(*(
                writer.index['foo'][:-2] + ('not_a_digest', 'do not care')))

        buf.seek(0)
        reader = RingReader(buf)
        with reader.open_section('foo') as s:
            read_bytes = s.read(4)
        self.assertEqual(b'\xde\xad\xbe\xef', read_bytes)
        self.assertEqual(mock_logging.mock_calls, [
            mock.call('swift.ring'),
            mock.call('swift.ring').warning(
                'Ignoring unsupported checksum %s:%s for section %s',
                'not_a_digest', mock.ANY, 'foo'),
        ])

    def test_recompressed(self):
        buf = io.BytesIO()
        with RingWriter(buf) as writer:
            writer.write_magic(2)
            with writer.section('foo'):
                writer.write_blob(b'\xde\xad\xbe\xef' * 10240)

        buf.seek(0)
        reader = RingReader(buf)
        with self.assertRaises(IOError):
            reader.read(-1)  # don't be greedy
        uncompressed_bytes = reader.read(2 ** 20)

        buf = io.BytesIO()
        with RingWriter(buf) as writer:
            writer.write(uncompressed_bytes)

        buf.seek(0)
        with self.assertRaises(IOError):
            # ...but we can't read it
            RingReader(buf)

    def test_version_too_high(self):
        buf = io.BytesIO()
        with RingWriter(buf) as writer:
            # you can write it...
            writer.write_magic(3)
            with writer.section('foo'):
                writer.write_blob(b'\xde\xad\xbe\xef' * 10240)

        buf.seek(0)
        with self.assertRaises(ValueError):
            # ...but we can't read it
            RingReader(buf)
