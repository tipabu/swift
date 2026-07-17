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
import requests

import test.functional as tf
from swift.common.middleware.s3api.etree import tostring, Element, SubElement

from test.functional.s3api import S3ApiBaseBoto3
from test.functional.s3api.s3_test_client import get_boto3_conn
from test.functional.s3api.utils import get_error_code, calculate_md5


def setUpModule():
    tf.setup_package()


def tearDownModule():
    tf.teardown_package()


class TestS3ApiMultiDelete(S3ApiBaseBoto3):
    def _prepare_test_delete_multi_objects(self, bucket, objects):
        self.conn.create_bucket(Bucket=bucket)
        for obj in objects:
            self.conn.put_object(Bucket=bucket, Key=obj, Body=b'')

    def _gen_invalid_multi_delete_xml(self, hasObjectTag=False):
        elem = Element('Delete')
        if hasObjectTag:
            obj = SubElement(elem, 'Object')
            SubElement(obj, 'Key').text = ''

        return tostring(elem, use_s3ns=False)

    @staticmethod
    def _clear_data(request, **kwargs):
        request.data = b''

    def _presign_delete_url(self, bucket):
        # boto3 won't let us send deliberately-malformed XML, so build a
        # presigned URL that we can POST arbitrary bytes to.
        params = {'Bucket': bucket, 'Delete': {'Objects': [{'Key': 'x'}]}}
        try:
            # https://github.com/boto/boto3/issues/2192
            self.conn.meta.events.register(
                'before-sign.s3.*', self._clear_data)
            url = self.conn.generate_presigned_url(
                'delete_objects', Params=params, ExpiresIn=60)
        finally:
            self.conn.meta.events.unregister(
                'before-sign.s3.*', self._clear_data)
        if '/?' not in url:
            url = url.replace('?', '/?')
        return url

    def _test_delete_multi_objects(self, with_non_ascii=False):
        bucket = 'bucket'
        if with_non_ascii:
            put_objects = [u'\N{SNOWMAN}obj%s' % var for var in range(4)]
        else:
            put_objects = ['obj%s' % var for var in range(4)]
        self._prepare_test_delete_multi_objects(bucket, put_objects)

        # Delete an object via MultiDelete API
        req_objects = put_objects[:1]
        resp = self.conn.delete_objects(
            Bucket=bucket,
            Delete={'Objects': [{'Key': key} for key in req_objects]})
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])
        headers = resp['ResponseMetadata']['HTTPHeaders']
        self.assertCommonResponseHeaders(headers)
        self.assertIsNotNone(headers['content-type'])
        self.assertIn('content-length', headers)
        resp_objects = resp.get('Deleted', [])
        self.assertEqual(len(resp_objects), len(req_objects))
        for o in resp_objects:
            self.assertIn(o['Key'], req_objects)

        # Delete 2 objects via MultiDelete API
        req_objects = put_objects[1:3]
        resp = self.conn.delete_objects(
            Bucket=bucket,
            Delete={'Objects': [{'Key': key} for key in req_objects]})
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])
        resp_objects = resp.get('Deleted', [])
        self.assertEqual(len(resp_objects), len(req_objects))
        for o in resp_objects:
            self.assertIn(o['Key'], req_objects)

        if with_non_ascii:
            fake_objs = [u'\N{SNOWMAN}obj%s' % var for var in range(4, 6)]
        else:
            fake_objs = ['obj%s' % var for var in range(4, 6)]
        # Delete 2 objects via MultiDelete API but one (obj4) doesn't exist.
        req_objects = [put_objects[-1], fake_objs[0]]
        resp = self.conn.delete_objects(
            Bucket=bucket,
            Delete={'Objects': [{'Key': key} for key in req_objects]})
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])
        resp_objects = resp.get('Deleted', [])
        # S3 assumes a NoSuchKey object as deleted.
        self.assertEqual(len(resp_objects), len(req_objects))
        for o in resp_objects:
            self.assertIn(o['Key'], req_objects)

        # Delete 2 objects via MultiDelete API but no objects exist
        req_objects = fake_objs[:2]
        resp = self.conn.delete_objects(
            Bucket=bucket,
            Delete={'Objects': [{'Key': key} for key in req_objects]})
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])
        resp_objects = resp.get('Deleted', [])
        self.assertEqual(len(resp_objects), len(req_objects))
        for o in resp_objects:
            self.assertIn(o['Key'], req_objects)

    def test_delete_multi_objects(self):
        self._test_delete_multi_objects()

    def test_delete_multi_objects_with_non_ascii(self):
        self._test_delete_multi_objects(with_non_ascii=True)

    def test_delete_multi_objects_error(self):
        bucket = 'bucket'
        put_objects = ['obj']
        self._prepare_test_delete_multi_objects(bucket, put_objects)

        auth_error_conn = get_boto3_conn(tf.config['s3_access_key'], 'invalid')
        with self.assertRaises(botocore.exceptions.ClientError) as ctx:
            auth_error_conn.delete_objects(
                Bucket=bucket,
                Delete={'Objects': [{'Key': key} for key in put_objects]})
        self.assertEqual(
            ctx.exception.response['Error']['Code'], 'SignatureDoesNotMatch')

        with self.assertRaises(botocore.exceptions.ClientError) as ctx:
            self.conn.delete_objects(
                Bucket='nothing',
                Delete={'Objects': [{'Key': key} for key in put_objects]})
        self.assertEqual(
            ctx.exception.response['Error']['Code'], 'NoSuchBucket')

        url = self._presign_delete_url(bucket)

        # without Object tag
        xml = self._gen_invalid_multi_delete_xml()
        resp = requests.post(
            url, data=xml, headers={'Content-MD5': calculate_md5(xml)})
        self.assertEqual(get_error_code(resp.content), 'MalformedXML')

        # without value of Key tag
        xml = self._gen_invalid_multi_delete_xml(hasObjectTag=True)
        resp = requests.post(
            url, data=xml, headers={'Content-MD5': calculate_md5(xml)})
        self.assertEqual(
            get_error_code(resp.content), 'UserKeyMustBeSpecified')

        max_deletes = int(tf.cluster_info.get('s3api', {}).get(
            'max_multi_delete_objects', 1000))
        # specified number of objects are over max_multi_delete_objects
        # (Default 1000), but xml size is relatively small
        req_objects = ['obj%s' % var for var in range(max_deletes + 1)]
        with self.assertRaises(botocore.exceptions.ClientError) as ctx:
            self.conn.delete_objects(
                Bucket=bucket,
                Delete={'Objects': [{'Key': key} for key in req_objects]})
        self.assertEqual(
            ctx.exception.response['Error']['Code'], 'MalformedXML')

        # specified xml size is large, but number of objects are
        # smaller than max_multi_delete_objects.
        obj = 'a' * 102400
        req_objects = [obj + str(var) for var in range(max_deletes - 1)]
        with self.assertRaises(botocore.exceptions.ClientError) as ctx:
            self.conn.delete_objects(
                Bucket=bucket,
                Delete={'Objects': [{'Key': key} for key in req_objects]})
        self.assertEqual(
            ctx.exception.response['Error']['Code'], 'MalformedXML')

    def test_delete_multi_objects_with_quiet(self):
        bucket = 'bucket'
        put_objects = ['obj']

        # with Quiet true
        self._prepare_test_delete_multi_objects(bucket, put_objects)
        resp = self.conn.delete_objects(
            Bucket=bucket,
            Delete={'Objects': [{'Key': key} for key in put_objects],
                    'Quiet': True})
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])
        self.assertEqual(len(resp.get('Deleted', [])), 0)

        # with Quiet false
        self._prepare_test_delete_multi_objects(bucket, put_objects)
        resp = self.conn.delete_objects(
            Bucket=bucket,
            Delete={'Objects': [{'Key': key} for key in put_objects],
                    'Quiet': False})
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])
        self.assertEqual(len(resp.get('Deleted', [])), 1)


class TestS3ApiMultiDeleteSigV4(TestS3ApiMultiDelete):
    @classmethod
    def setUpClass(cls):
        os.environ['S3_USE_SIGV4'] = "True"

    @classmethod
    def tearDownClass(cls):
        del os.environ['S3_USE_SIGV4']

    def setUp(self):
        super(TestS3ApiMultiDeleteSigV4, self).setUp()


if __name__ == '__main__':
    unittest.main()
