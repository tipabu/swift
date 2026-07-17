# Copyright (c) 2015 OpenStack Foundation
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
import os

import botocore

import test.functional as tf

from test.functional.s3api import S3ApiBaseBoto3
from test.functional.s3api.s3_test_client import get_boto3_conn


def setUpModule():
    tf.setup_package()


def tearDownModule():
    tf.teardown_package()


class TestS3ApiService(S3ApiBaseBoto3):
    def test_service(self):
        # GET Service(without bucket)
        resp = self.conn.list_buckets()
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])
        headers = resp['ResponseMetadata']['HTTPHeaders']

        self.assertCommonResponseHeaders(headers)
        self.assertIsNotNone(headers['content-type'])
        # TODO; requires consideration
        # self.assertEqual(headers['transfer-encoding'], 'chunked')

        self.assertEqual(resp['Buckets'], [])
        if tf.cluster_info['s3api'].get('s3_acl'):
            self.assertEqual(resp['Owner']['ID'], self.access_key)
            self.assertEqual(resp['Owner']['DisplayName'], self.access_key)
        else:
            self.assertIn('ID', resp['Owner'])
            self.assertIn('DisplayName', resp['Owner'])

        # GET Service(with Bucket)
        req_buckets = ('bucket', 'bucket2')
        for bucket in req_buckets:
            self.conn.create_bucket(Bucket=bucket)
        resp = self.conn.list_buckets()
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])

        resp_buckets = resp['Buckets']
        self.assertEqual(len(resp_buckets), 2)
        for b in resp_buckets:
            self.assertIn(b['Name'], req_buckets)
            self.assertIn('CreationDate', b)

    def test_service_error_signature_not_match(self):
        auth_error_conn = get_boto3_conn(tf.config['s3_access_key'], 'invalid')
        with self.assertRaises(botocore.exceptions.ClientError) as ctx:
            auth_error_conn.list_buckets()
        self.assertEqual(
            ctx.exception.response['Error']['Code'], 'SignatureDoesNotMatch')
        self.assertEqual(
            ctx.exception.response['ResponseMetadata']['HTTPHeaders'][
                'content-type'], 'application/xml')

    def test_service_error_no_date_header(self):
        # Without x-amz-date/Date header, that makes 403 forbidden
        def remove_date_header(request, **kwargs):
            request.headers['Date'] = ''
            for hdr in ('X-Amz-Date', 'x-amz-date'):
                if hdr in request.headers:
                    del request.headers[hdr]

        self.conn.meta.events.register(
            'before-send.s3.ListBuckets', remove_date_header)
        try:
            with self.assertRaises(botocore.exceptions.ClientError) as ctx:
                self.conn.list_buckets()
        finally:
            self.conn.meta.events.unregister(
                'before-send.s3.ListBuckets', remove_date_header)
        self.assertEqual(
            ctx.exception.response['ResponseMetadata']['HTTPStatusCode'], 403)
        self.assertEqual(
            ctx.exception.response['Error']['Code'], 'AccessDenied')
        self.assertIn(
            'AWS authentication requires a valid Date or x-amz-date header',
            ctx.exception.response['Error']['Message'])


class TestS3ApiServiceSigV4(TestS3ApiService):
    @classmethod
    def setUpClass(cls):
        os.environ['S3_USE_SIGV4'] = "True"

    @classmethod
    def tearDownClass(cls):
        del os.environ['S3_USE_SIGV4']

    def setUp(self):
        super(TestS3ApiServiceSigV4, self).setUp()


if __name__ == '__main__':
    unittest.main()
