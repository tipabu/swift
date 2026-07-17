# Copyright (c) 2016 SwiftStack, Inc.
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

import os

from urllib.parse import urlparse

import requests

from swift.common.bufferedhttp import http_connect_raw
from swift.common.middleware.s3api.etree import fromstring

import test.functional as tf

from test.functional.s3api import S3ApiBaseBoto3
from test.functional.s3api.s3_test_client import get_boto3_conn
from test.functional.s3api.utils import get_error_code, get_error_msg


def setUpModule():
    tf.setup_package()


def tearDownModule():
    tf.teardown_package()


class TestS3ApiPresignedUrls(S3ApiBaseBoto3):
    def setUp(self):
        super(TestS3ApiPresignedUrls, self).setUp()
        # Presigned URLs are signature-version specific, so build a client
        # that signs the way the boto2-based tests used to: SigV2 by default,
        # SigV4 for the SigV4 variant.
        if os.environ.get('S3_USE_SIGV4') == 'True':
            signature_version = 's3v4'
        else:
            signature_version = 's3'
        self.conn = get_boto3_conn(
            tf.config['s3_access_key'], tf.config['s3_secret_key'],
            signature_version=signature_version)
        parsed = urlparse(self.endpoint_url)
        self.host = parsed.hostname
        self.port = parsed.port

    def _presigned_url(self, client_method, bucket, key=None, expires_in=3600,
                       **params):
        params['Bucket'] = bucket
        if key is not None:
            params['Key'] = key
        return self.conn.generate_presigned_url(
            client_method, Params=params, ExpiresIn=expires_in)

    def test_bucket(self):
        bucket = 'test-bucket'
        req_objects = ('object', 'object2')
        max_bucket_listing = tf.cluster_info['s3api'].get(
            'max_bucket_listing', 1000)

        # GET Bucket (Without Object)
        self.conn.create_bucket(Bucket=bucket)

        url = self._presigned_url('list_objects', bucket)
        resp = requests.get(url)
        self.assertEqual(resp.status_code, 200,
                         'Got %d %s' % (resp.status_code, resp.content))
        self.assertCommonResponseHeaders(resp.headers)
        self.assertIsNotNone(resp.headers['content-type'])
        self.assertEqual(resp.headers['content-length'],
                         str(len(resp.content)))

        elem = fromstring(resp.content, 'ListBucketResult')
        self.assertEqual(elem.find('Name').text, bucket)
        self.assertIsNone(elem.find('Prefix').text)
        self.assertIsNone(elem.find('Marker').text)
        self.assertEqual(elem.find('MaxKeys').text,
                         str(max_bucket_listing))
        self.assertEqual(elem.find('IsTruncated').text, 'false')
        objects = elem.findall('./Contents')
        self.assertEqual(list(objects), [])

        # GET Bucket (With Object)
        for obj in req_objects:
            self.conn.put_object(Bucket=bucket, Key=obj, Body=b'')

        resp = requests.get(url)
        self.assertEqual(resp.status_code, 200,
                         'Got %d %s' % (resp.status_code, resp.content))
        self.assertCommonResponseHeaders(resp.headers)
        self.assertIsNotNone(resp.headers['content-type'])
        self.assertEqual(resp.headers['content-length'],
                         str(len(resp.content)))

        elem = fromstring(resp.content, 'ListBucketResult')
        self.assertEqual(elem.find('Name').text, bucket)
        self.assertIsNone(elem.find('Prefix').text)
        self.assertIsNone(elem.find('Marker').text)
        self.assertEqual(elem.find('MaxKeys').text,
                         str(max_bucket_listing))
        self.assertEqual(elem.find('IsTruncated').text, 'false')
        resp_objects = elem.findall('./Contents')
        self.assertEqual(len(list(resp_objects)), 2)
        for o in resp_objects:
            self.assertIn(o.find('Key').text, req_objects)
            self.assertIsNotNone(o.find('LastModified').text)
            self.assertRegex(
                o.find('LastModified').text,
                r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}.000Z$')
            self.assertIsNotNone(o.find('ETag').text)
            self.assertEqual(o.find('Size').text, '0')
            self.assertIsNotNone(o.find('StorageClass').text is not None)
            self.assertEqual(o.find('Owner/ID').text, self.access_key)
            self.assertEqual(o.find('Owner/DisplayName').text,
                             self.access_key)
        # DELETE Bucket
        for obj in req_objects:
            self.conn.delete_object(Bucket=bucket, Key=obj)
        url = self._presigned_url('delete_bucket', bucket)
        resp = requests.delete(url)
        self.assertEqual(resp.status_code, 204,
                         'Got %d %s' % (resp.status_code, resp.content))

    def test_expiration_limits(self):
        if os.environ.get('S3_USE_SIGV4'):
            self._test_expiration_limits_v4()
        else:
            self._test_expiration_limits_v2()

    def _test_expiration_limits_v2(self):
        bucket = 'test-bucket'

        # Expiration date is too far in the future
        url = self._presigned_url('list_objects', bucket, expires_in=2 ** 32)
        resp = requests.get(url)
        self.assertEqual(resp.status_code, 403,
                         'Got %d %s' % (resp.status_code, resp.content))
        self.assertEqual(get_error_code(resp.content),
                         'AccessDenied')
        self.assertIn('Invalid date (should be seconds since epoch)',
                      get_error_msg(resp.content))

    def _test_expiration_limits_v4(self):
        bucket = 'test-bucket'

        # Expiration is negative
        url = self._presigned_url('list_objects', bucket, expires_in=-1)
        resp = requests.get(url)
        self.assertEqual(resp.status_code, 400,
                         'Got %d %s' % (resp.status_code, resp.content))
        self.assertEqual(get_error_code(resp.content),
                         'AuthorizationQueryParametersError')
        self.assertIn('X-Amz-Expires must be non-negative',
                      get_error_msg(resp.content))

        # Expiration date is too far in the future
        for exp in (7 * 24 * 60 * 60 + 1,
                    2 ** 63 - 1):
            url = self._presigned_url('list_objects', bucket, expires_in=exp)
            resp = requests.get(url)
            self.assertEqual(resp.status_code, 400,
                             'Got %d %s' % (resp.status_code, resp.content))
            self.assertEqual(get_error_code(resp.content),
                             'AuthorizationQueryParametersError')
            self.assertIn('X-Amz-Expires must be less than 604800 seconds',
                          get_error_msg(resp.content))

        # Expiration date is *way* too far in the future, or isn't a number
        for exp in (2 ** 63, 'foo'):
            url = self._presigned_url(
                'list_objects', bucket, expires_in=2 ** 63)
            resp = requests.get(url)
            self.assertEqual(resp.status_code, 400,
                             'Got %d %s' % (resp.status_code, resp.content))
            self.assertEqual(get_error_code(resp.content),
                             'AuthorizationQueryParametersError')
            self.assertEqual('X-Amz-Expires should be a number',
                             get_error_msg(resp.content))

    def test_object(self):
        bucket = 'test-bucket'
        obj = 'object'

        self.conn.create_bucket(Bucket=bucket)

        # HEAD/missing object
        head_url = self._presigned_url('head_object', bucket, obj)
        resp = requests.head(head_url)
        self.assertEqual(resp.status_code, 404,
                         'Got %d %s' % (resp.status_code, resp.content))

        # Wrong verb
        resp = requests.get(head_url)
        self.assertEqual(resp.status_code, 403,
                         'Got %d %s' % (resp.status_code, resp.content))
        self.assertEqual(get_error_code(resp.content),
                         'SignatureDoesNotMatch')

        # PUT empty object
        put_url = self._presigned_url('put_object', bucket, obj)
        resp = requests.put(put_url, data=b'')
        self.assertEqual(resp.status_code, 200,
                         'Got %d %s' % (resp.status_code, resp.content))
        # GET empty object
        get_url = self._presigned_url('get_object', bucket, obj)
        resp = requests.get(get_url)
        self.assertEqual(resp.status_code, 200,
                         'Got %d %s' % (resp.status_code, resp.content))
        self.assertEqual(resp.content, b'')

        # PUT over object
        resp = requests.put(put_url, data=b'foobar')
        self.assertEqual(resp.status_code, 200,
                         'Got %d %s' % (resp.status_code, resp.content))

        # GET non-empty object
        resp = requests.get(get_url)
        self.assertEqual(resp.status_code, 200,
                         'Got %d %s' % (resp.status_code, resp.content))
        self.assertEqual(resp.content, b'foobar')

        # DELETE Object
        delete_url = self._presigned_url('delete_object', bucket, obj)
        resp = requests.delete(delete_url)
        self.assertEqual(resp.status_code, 204,
                         'Got %d %s' % (resp.status_code, resp.content))

        # Final cleanup
        self.conn.delete_bucket(Bucket=bucket)

    def test_absolute_form_request(self):
        bucket = 'test-bucket'

        put_url = self._presigned_url('create_bucket', bucket)
        resp = http_connect_raw(
            self.host,
            self.port,
            'PUT',
            put_url,  # whole URL, not just the path/query!
            ssl=put_url.startswith('https:'),
        ).getresponse()
        self.assertEqual(resp.status, 200,
                         'Got %d %s' % (resp.status, resp.read()))

        delete_url = self._presigned_url('delete_bucket', bucket)
        resp = http_connect_raw(
            self.host,
            self.port,
            'DELETE',
            delete_url,  # whole URL, not just the path/query!
            ssl=delete_url.startswith('https:'),
        ).getresponse()
        self.assertEqual(resp.status, 204,
                         'Got %d %s' % (resp.status, resp.read()))


class TestS3ApiPresignedUrlsSigV4(TestS3ApiPresignedUrls):
    @classmethod
    def setUpClass(cls):
        os.environ['S3_USE_SIGV4'] = "True"

    @classmethod
    def tearDownClass(cls):
        del os.environ['S3_USE_SIGV4']

    def setUp(self):
        super(TestS3ApiPresignedUrlsSigV4, self).setUp()
