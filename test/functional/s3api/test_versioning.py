# Copyright (c) 2017 OpenStack Foundation
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

import unittest

import botocore
import requests

import test.functional as tf
from swift.common.middleware.s3api.etree import tostring, Element, SubElement

from test.functional.s3api import S3ApiBaseBoto3
from test.functional.s3api.utils import get_error_code


def setUpModule():
    tf.setup_package()


def tearDownModule():
    tf.teardown_package()


class TestS3ApiVersioning(S3ApiBaseBoto3):
    def setUp(self):
        super(TestS3ApiVersioning, self).setUp()
        if 'object_versioning' not in tf.cluster_info:
            # Alternatively, maybe we should assert we get 501s...
            self.skipTest('S3 versioning requires that Swift object '
                          'versioning be enabled')
        self.bucket = 'bucket'
        resp = self.conn.create_bucket(Bucket=self.bucket)
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])

    def tearDown(self):
        # TODO: is this necessary on AWS? or can you delete buckets while
        # versioning is enabled?
        try:
            self.conn.put_bucket_versioning(
                Bucket=self.bucket,
                VersioningConfiguration={'Status': 'Suspended'})
        except botocore.exceptions.ClientError:
            pass
        super(TestS3ApiVersioning, self).tearDown()

    @staticmethod
    def _clear_data(request, **kwargs):
        request.data = b''

    def _presign_versioning_url(self):
        # boto3 validates the Status enum client-side, so to send deliberately
        # bad values we build a presigned URL and PUT arbitrary bytes to it.
        params = {'Bucket': self.bucket,
                  'VersioningConfiguration': {'Status': 'Enabled'}}
        try:
            # https://github.com/boto/boto3/issues/2192
            self.conn.meta.events.register(
                'before-sign.s3.*', self._clear_data)
            url = self.conn.generate_presigned_url(
                'put_bucket_versioning', Params=params, ExpiresIn=60)
        finally:
            self.conn.meta.events.unregister(
                'before-sign.s3.*', self._clear_data)
        if '/?' not in url:
            url = url.replace('?', '/?')
        return url

    def test_versioning_put(self):
        # Versioning not configured
        resp = self.conn.get_bucket_versioning(Bucket=self.bucket)
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])
        self.assertNotIn('Status', resp)

        # Enable versioning
        resp = self.conn.put_bucket_versioning(
            Bucket=self.bucket,
            VersioningConfiguration={'Status': 'Enabled'})
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])

        resp = self.conn.get_bucket_versioning(Bucket=self.bucket)
        self.assertEqual(resp['Status'], 'Enabled')

        # Suspend versioning
        resp = self.conn.put_bucket_versioning(
            Bucket=self.bucket,
            VersioningConfiguration={'Status': 'Suspended'})
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])

        resp = self.conn.get_bucket_versioning(Bucket=self.bucket)
        self.assertEqual(resp['Status'], 'Suspended')

        # Resume versioning
        resp = self.conn.put_bucket_versioning(
            Bucket=self.bucket,
            VersioningConfiguration={'Status': 'Enabled'})
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])

        resp = self.conn.get_bucket_versioning(Bucket=self.bucket)
        self.assertEqual(resp['Status'], 'Enabled')

    def test_versioning_immediately_suspend(self):
        # Versioning not configured
        resp = self.conn.get_bucket_versioning(Bucket=self.bucket)
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])
        self.assertNotIn('Status', resp)

        # Suspend versioning
        resp = self.conn.put_bucket_versioning(
            Bucket=self.bucket,
            VersioningConfiguration={'Status': 'Suspended'})
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])

        resp = self.conn.get_bucket_versioning(Bucket=self.bucket)
        self.assertEqual(resp['Status'], 'Suspended')

        # Enable versioning
        resp = self.conn.put_bucket_versioning(
            Bucket=self.bucket,
            VersioningConfiguration={'Status': 'Enabled'})
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])

        resp = self.conn.get_bucket_versioning(Bucket=self.bucket)
        self.assertEqual(resp['Status'], 'Enabled')

    def test_versioning_put_error(self):
        url = self._presign_versioning_url()

        # Root tag is not VersioningConfiguration
        elem = Element('foo')
        SubElement(elem, 'Status').text = 'Enabled'
        xml = tostring(elem)
        resp = requests.put(url, data=xml)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(get_error_code(resp.content), 'MalformedXML')

        # Status is not "Enabled" or "Suspended"
        elem = Element('VersioningConfiguration')
        SubElement(elem, 'Status').text = '...'
        xml = tostring(elem)
        resp = requests.put(url, data=xml)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(get_error_code(resp.content), 'MalformedXML')

        elem = Element('VersioningConfiguration')
        SubElement(elem, 'Status').text = ''
        xml = tostring(elem)
        resp = requests.put(url, data=xml)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(get_error_code(resp.content), 'MalformedXML')


if __name__ == '__main__':
    unittest.main()
