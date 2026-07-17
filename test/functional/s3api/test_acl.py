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
from unittest import SkipTest
from test.functional.s3api import S3ApiBaseBoto3
from test.functional.s3api.s3_test_client import get_boto3_conn


def setUpModule():
    tf.setup_package()


def tearDownModule():
    tf.teardown_package()


class TestS3Acl(S3ApiBaseBoto3):
    def setUp(self):
        super(TestS3Acl, self).setUp()
        self.bucket = 'bucket'
        self.obj = 'object'
        if 's3_access_key3' not in tf.config or \
                's3_secret_key3' not in tf.config:
            raise SkipTest(
                'TestS3Acl requires s3_access_key3 and s3_secret_key3 '
                'configured for reduced-access user')
        resp = self.conn.create_bucket(Bucket=self.bucket)
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])
        self.conn3 = get_boto3_conn(
            tf.config['s3_access_key3'], tf.config['s3_secret_key3'])

    def test_acl(self):
        self.conn.put_object(Bucket=self.bucket, Key=self.obj, Body=b'')

        # PUT Bucket ACL
        resp = self.conn.put_bucket_acl(
            Bucket=self.bucket, ACL='public-read')
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])
        headers = resp['ResponseMetadata']['HTTPHeaders']
        self.assertCommonResponseHeaders(headers)
        self.assertEqual(headers['content-length'], '0')

        # GET Bucket ACL
        resp = self.conn.get_bucket_acl(Bucket=self.bucket)
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])
        headers = resp['ResponseMetadata']['HTTPHeaders']
        self.assertCommonResponseHeaders(headers)
        # TODO: Fix the response that last-modified must be in the response.
        # self.assertTrue(headers['last-modified'] is not None)
        self.assertIsNotNone(headers['content-type'])
        self.assertEqual(resp['Owner']['ID'], self.access_key)
        self.assertEqual(resp['Owner']['DisplayName'], self.access_key)
        self.assertTrue(len(resp['Grants']) > 0)

        # GET Object ACL
        resp = self.conn.get_object_acl(Bucket=self.bucket, Key=self.obj)
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])
        headers = resp['ResponseMetadata']['HTTPHeaders']
        self.assertCommonResponseHeaders(headers)
        # TODO: Fix the response that last-modified must be in the response.
        # self.assertTrue(headers['last-modified'] is not None)
        self.assertIsNotNone(headers['content-type'])
        self.assertEqual(resp['Owner']['ID'], self.access_key)
        self.assertEqual(resp['Owner']['DisplayName'], self.access_key)
        self.assertTrue(len(resp['Grants']) > 0)

    def test_put_bucket_acl_error(self):
        aws_error_conn = get_boto3_conn(tf.config['s3_access_key'], 'invalid')
        with self.assertRaises(botocore.exceptions.ClientError) as ctx:
            aws_error_conn.put_bucket_acl(
                Bucket=self.bucket, ACL='public-read')
        self.assertEqual(
            ctx.exception.response['Error']['Code'], 'SignatureDoesNotMatch')

        with self.assertRaises(botocore.exceptions.ClientError) as ctx:
            self.conn.put_bucket_acl(Bucket='nothing', ACL='public-read')
        self.assertEqual(
            ctx.exception.response['Error']['Code'], 'NoSuchBucket')

        with self.assertRaises(botocore.exceptions.ClientError) as ctx:
            self.conn3.put_bucket_acl(Bucket=self.bucket, ACL='public-read')
        self.assertEqual(
            ctx.exception.response['Error']['Code'], 'AccessDenied')

    def test_get_bucket_acl_error(self):
        aws_error_conn = get_boto3_conn(tf.config['s3_access_key'], 'invalid')
        with self.assertRaises(botocore.exceptions.ClientError) as ctx:
            aws_error_conn.get_bucket_acl(Bucket=self.bucket)
        self.assertEqual(
            ctx.exception.response['Error']['Code'], 'SignatureDoesNotMatch')

        with self.assertRaises(botocore.exceptions.ClientError) as ctx:
            self.conn.get_bucket_acl(Bucket='nothing')
        self.assertEqual(
            ctx.exception.response['Error']['Code'], 'NoSuchBucket')

        with self.assertRaises(botocore.exceptions.ClientError) as ctx:
            self.conn3.get_bucket_acl(Bucket=self.bucket)
        self.assertEqual(
            ctx.exception.response['Error']['Code'], 'AccessDenied')

    def test_get_object_acl_error(self):
        self.conn.put_object(Bucket=self.bucket, Key=self.obj, Body=b'')

        aws_error_conn = get_boto3_conn(tf.config['s3_access_key'], 'invalid')
        with self.assertRaises(botocore.exceptions.ClientError) as ctx:
            aws_error_conn.get_object_acl(Bucket=self.bucket, Key=self.obj)
        self.assertEqual(
            ctx.exception.response['Error']['Code'], 'SignatureDoesNotMatch')

        with self.assertRaises(botocore.exceptions.ClientError) as ctx:
            self.conn.get_object_acl(Bucket=self.bucket, Key='nothing')
        self.assertEqual(
            ctx.exception.response['Error']['Code'], 'NoSuchKey')

        with self.assertRaises(botocore.exceptions.ClientError) as ctx:
            self.conn3.get_object_acl(Bucket=self.bucket, Key=self.obj)
        self.assertEqual(
            ctx.exception.response['Error']['Code'], 'AccessDenied')


class TestS3AclSigV4(TestS3Acl):
    @classmethod
    def setUpClass(cls):
        os.environ['S3_USE_SIGV4'] = "True"

    @classmethod
    def tearDownClass(cls):
        del os.environ['S3_USE_SIGV4']

    def setUp(self):
        super(TestS3AclSigV4, self).setUp()


if __name__ == '__main__':
    unittest.main()
