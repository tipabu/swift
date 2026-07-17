# -*- coding: utf-8 -*-
# Copyright (c) 2015-2021 OpenStack Foundation
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

import base64
import email.parser
from datetime import timedelta
import os
import struct
from zlib import crc32

import boto3
import botocore
import requests

import test.functional as tf
from swift.common import utils, swob

from swift.common.utils import md5

from test.functional.s3api import SigV4Mixin, S3ApiBaseBoto3, get_boto3_conn
from test.functional.s3api.utils import get_error_code, calculate_md5, \
    get_error_msg


def setUpModule():
    tf.setup_package()


def tearDownModule():
    tf.teardown_package()


class ShortFileLike:
    """
    Act like fully compatible seekable/readable, but after some captured
    sequence of read calls on one specific "magic" invocation of read return a
    little bit of garbage.
    """

    # Found through trial and error, the magic number for the start of the full
    # read which happens to be the actual upload.
    MAGIC_NUMBER = 4

    def __init__(self, size):
        self.calls = []
        self._size = size
        self._pos = 0

    def tell(self, *args, **kwargs):
        self.calls.append(('tell', args, kwargs))
        return self._pos

    def seek(self, pos=0, whence=os.SEEK_SET):
        self.calls.append(('seek', (pos, whence)))
        if whence == os.SEEK_END:
            self._pos = self._size - pos
        else:
            self._pos = pos

    def read(self, *args, **kwargs):
        self.calls.append(('read', args, kwargs))
        buff = b'a' * (self._size - self._pos)
        self._pos += len(buff)
        num_reads = len([c for c in self.calls if c[0] == 'read'])
        if num_reads == self.MAGIC_NUMBER:
            return b'bbb'
        return buff


class TestS3ApiObjectBoto3(S3ApiBaseBoto3):
    def setUp(self):
        super().setUp()
        self.conn = get_boto3_conn(tf.config['s3_access_key'],
                                   tf.config['s3_secret_key'])
        self.bucket = 'test-bucket'
        resp = self.conn.create_bucket(Bucket=self.bucket)
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])

    def test_put(self):
        body = b'abcd' * 8192
        resp = self.conn.put_object(Bucket=self.bucket, Key='obj', Body=body)
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])
        resp = self.conn.get_object(Bucket=self.bucket, Key='obj')
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])
        self.assertEqual(body, resp['Body'].read())

    def test_put_chunked(self):
        body = b'abcd' * 8192
        resp = self.conn.put_object(Bucket=self.bucket, Key='obj', Body=body,
                                    ContentEncoding='aws-chunked')
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])
        resp = self.conn.get_object(Bucket=self.bucket, Key='obj')
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])
        self.assertEqual(body, resp['Body'].read())

    def test_put_chunked_sha256(self):
        body = b'abcd' * 8192
        resp = self.conn.put_object(Bucket=self.bucket, Key='obj', Body=body,
                                    ContentEncoding='aws-chunked',
                                    ChecksumAlgorithm='SHA256')
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])
        resp = self.conn.get_object(Bucket=self.bucket, Key='obj')
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])
        self.assertEqual(body, resp['Body'].read())

    def test_put_object_request_timeout(self):
        if not tf.in_process:
            raise unittest.SkipTest(
                'in-process functests required for '
                'predictable client_timeout')

        obj = 'object'
        body = ShortFileLike(10)
        crc_int = crc32(body.read())
        body.seek()
        digest = struct.pack('!I', crc_int)
        b64_bytes = base64.b64encode(digest)
        config = boto3.session.Config(
            s3={'addressing_style': 'path'},
            retries={
                'mode': 'standard',
                'total_max_attempts': 1,
            },
        )
        endpoint_url = tf.config['s3_storage_url']
        conn = boto3.client(
            's3',
            aws_access_key_id=tf.config['s3_access_key'],
            aws_secret_access_key=tf.config['s3_secret_key'],
            config=config,
            region_name=tf.config.get('s3_region', 'us-east-1'),
            use_ssl=endpoint_url.startswith('https:'),
            endpoint_url=endpoint_url,
        )
        try:
            resp = conn.put_object(
                Bucket=self.bucket, Key=obj, Body=body,
                ChecksumCRC32=b64_bytes.decode('utf8'))
        except boto3.exceptions.botocore.exceptions.ClientError as e:
            resp = e.response
        else:
            self.fail('ShortFileLike upload worked!?', resp)
        self.assertEqual('RequestTimeout', resp['Error']['Code'])
        self.assertEqual(400, resp['ResponseMetadata']['HTTPStatusCode'])
        headers = resp['ResponseMetadata']['HTTPHeaders']
        self.assertEqual('close', headers['connection'])


class TestS3ApiObject(S3ApiBaseBoto3):
    def setUp(self):
        super(TestS3ApiObject, self).setUp()
        self.bucket = 'bucket'
        resp = self.conn.create_bucket(Bucket=self.bucket)
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])

    def _assertObjectEtag(self, bucket, obj, etag):
        resp = self.conn.head_object(Bucket=bucket, Key=obj)
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])
        self.assertCommonResponseHeaders(
            resp['ResponseMetadata']['HTTPHeaders'], etag)

    def _presigned_put(self, bucket, obj, body=b'', headers=None):
        # boto3 can't send arbitrary/unsupported request headers, so build a
        # presigned URL and PUT to it directly with the requests library.
        url = self.conn.generate_presigned_url(
            'put_object', Params={'Bucket': bucket, 'Key': obj},
            ExpiresIn=60)
        return requests.put(url, data=body, headers=headers or {})

    def test_object(self):
        obj = u'object name with %-sign 🙂'
        content = b'abc123'
        etag = md5(content, usedforsecurity=False).hexdigest()

        # PUT Object
        resp = self.conn.put_object(
            Bucket=self.bucket, Key=obj, Body=content)
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])

        headers = resp['ResponseMetadata']['HTTPHeaders']
        self.assertCommonResponseHeaders(headers)
        self.assertIn('content-length', headers)  # sanity
        self.assertEqual(headers['content-length'], '0')
        self._assertObjectEtag(self.bucket, obj, etag)

        # PUT Object Copy
        dst_bucket = 'dst-bucket'
        dst_obj = 'dst_obj'
        self.conn.create_bucket(Bucket=dst_bucket)
        resp = self.conn.copy_object(
            Bucket=dst_bucket, Key=dst_obj,
            CopySource={'Bucket': self.bucket, 'Key': obj})
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])

        # PUT Object Copy with a dict source (botocore URL-encodes the source)
        resp = self.conn.copy_object(
            Bucket=dst_bucket, Key=dst_obj,
            CopySource={'Bucket': self.bucket, 'Key': obj})
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])

        headers = resp['ResponseMetadata']['HTTPHeaders']
        self.assertCommonResponseHeaders(headers)

        copy_result = resp['CopyObjectResult']
        self.assertIsNotNone(copy_result['LastModified'])
        copy_resp_last_modified = copy_result['LastModified']
        self.assertIsNotNone(copy_result['ETag'])
        self.assertEqual(etag, copy_result['ETag'].strip('"'))
        self._assertObjectEtag(dst_bucket, dst_obj, etag)

        # Check timestamp on Copy in listing:
        resp = self.conn.list_objects(Bucket=dst_bucket)
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])
        self.assertEqual(
            resp['Contents'][0]['LastModified'], copy_resp_last_modified)

        # GET Object copy
        resp = self.conn.get_object(Bucket=dst_bucket, Key=dst_obj)
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])

        headers = resp['ResponseMetadata']['HTTPHeaders']
        self.assertCommonResponseHeaders(headers, etag)
        self.assertIsNotNone(headers['last-modified'])
        self.assertEqual(resp['LastModified'], copy_resp_last_modified)
        self.assertIsNotNone(headers['content-type'])
        self.assertEqual(resp['ContentLength'], len(content))

        # GET Object
        resp = self.conn.get_object(Bucket=self.bucket, Key=obj)
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])

        headers = resp['ResponseMetadata']['HTTPHeaders']
        self.assertCommonResponseHeaders(headers, etag)
        self.assertIsNotNone(headers['last-modified'])
        self.assertIsNotNone(headers['content-type'])
        self.assertEqual(resp['ContentLength'], len(content))
        self.assertEqual(headers['accept-ranges'], 'bytes')

        # HEAD Object
        resp = self.conn.head_object(Bucket=self.bucket, Key=obj)
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])

        headers = resp['ResponseMetadata']['HTTPHeaders']
        self.assertCommonResponseHeaders(headers, etag)
        self.assertIsNotNone(headers['last-modified'])
        self.assertIn('content-type', headers)
        self.assertEqual(resp['ContentLength'], len(content))
        self.assertEqual(headers['accept-ranges'], 'bytes')

        # DELETE Object
        resp = self.conn.delete_object(Bucket=self.bucket, Key=obj)
        self.assertEqual(204, resp['ResponseMetadata']['HTTPStatusCode'])
        self.assertCommonResponseHeaders(
            resp['ResponseMetadata']['HTTPHeaders'])

        # DELETE Non-Existent Object
        resp = self.conn.delete_object(
            Bucket=self.bucket, Key='does-not-exist')
        self.assertEqual(204, resp['ResponseMetadata']['HTTPStatusCode'])
        self.assertCommonResponseHeaders(
            resp['ResponseMetadata']['HTTPHeaders'])

    def test_put_object_error(self):
        auth_error_conn = get_boto3_conn(tf.config['s3_access_key'], 'invalid')
        with self.assertRaises(botocore.exceptions.ClientError) as ctx:
            auth_error_conn.put_object(
                Bucket=self.bucket, Key='object', Body=b'')
        self.assertEqual(
            ctx.exception.response['Error']['Code'], 'SignatureDoesNotMatch')
        self.assertEqual(
            ctx.exception.response['ResponseMetadata']['HTTPHeaders'][
                'content-type'], 'application/xml')

        with self.assertRaises(botocore.exceptions.ClientError) as ctx:
            self.conn.put_object(Bucket='bucket2', Key='object', Body=b'')
        self.assertEqual(
            ctx.exception.response['Error']['Code'], 'NoSuchBucket')
        self.assertEqual(
            ctx.exception.response['ResponseMetadata']['HTTPHeaders'][
                'content-type'], 'application/xml')

    def test_put_object_name_too_long(self):
        with self.assertRaises(botocore.exceptions.ClientError) as ctx:
            self.conn.put_object(
                Bucket=self.bucket,
                Key='x' * (
                    tf.cluster_info['swift']['max_object_name_length'] + 1),
                Body=b'')
        self.assertEqual(
            ctx.exception.response['Error']['Code'], 'KeyTooLongError')
        self.assertEqual(
            ctx.exception.response['ResponseMetadata']['HTTPHeaders'][
                'content-type'], 'application/xml')

    def test_put_object_copy_error(self):
        obj = 'object'
        self.conn.put_object(Bucket=self.bucket, Key=obj, Body=b'')
        dst_bucket = 'dst-bucket'
        self.conn.create_bucket(Bucket=dst_bucket)
        dst_obj = 'dst_object'

        auth_error_conn = get_boto3_conn(tf.config['s3_access_key'], 'invalid')
        with self.assertRaises(botocore.exceptions.ClientError) as ctx:
            auth_error_conn.copy_object(
                Bucket=dst_bucket, Key=dst_obj,
                CopySource={'Bucket': self.bucket, 'Key': obj})
        self.assertEqual(
            ctx.exception.response['Error']['Code'], 'SignatureDoesNotMatch')
        self.assertEqual(
            ctx.exception.response['ResponseMetadata']['HTTPHeaders'][
                'content-type'], 'application/xml')

        # /src/nothing -> /dst/dst
        with self.assertRaises(botocore.exceptions.ClientError) as ctx:
            self.conn.copy_object(
                Bucket=dst_bucket, Key=dst_obj,
                CopySource={'Bucket': self.bucket, 'Key': 'nothing'})
        self.assertEqual(
            ctx.exception.response['Error']['Code'], 'NoSuchKey')
        self.assertEqual(
            ctx.exception.response['ResponseMetadata']['HTTPHeaders'][
                'content-type'], 'application/xml')

        # /nothing/src -> /dst/dst
        # TODO: source bucket is not checked.
        try:
            self.conn.copy_object(
                Bucket=dst_bucket, Key=dst_obj,
                CopySource={'Bucket': 'nothing', 'Key': obj})
        except botocore.exceptions.ClientError:
            pass

        # /src/src -> /nothing/dst
        with self.assertRaises(botocore.exceptions.ClientError) as ctx:
            self.conn.copy_object(
                Bucket='nothing', Key=dst_obj,
                CopySource={'Bucket': self.bucket, 'Key': obj})
        self.assertEqual(
            ctx.exception.response['Error']['Code'], 'NoSuchBucket')
        self.assertEqual(
            ctx.exception.response['ResponseMetadata']['HTTPHeaders'][
                'content-type'], 'application/xml')

    def test_get_object_error(self):
        obj = 'object'
        self.conn.put_object(Bucket=self.bucket, Key=obj, Body=b'')

        auth_error_conn = get_boto3_conn(tf.config['s3_access_key'], 'invalid')
        with self.assertRaises(botocore.exceptions.ClientError) as ctx:
            auth_error_conn.get_object(Bucket=self.bucket, Key=obj)
        self.assertEqual(
            ctx.exception.response['Error']['Code'], 'SignatureDoesNotMatch')
        self.assertEqual(
            ctx.exception.response['ResponseMetadata']['HTTPHeaders'][
                'content-type'], 'application/xml')

        with self.assertRaises(botocore.exceptions.ClientError) as ctx:
            self.conn.get_object(Bucket=self.bucket, Key='invalid')
        self.assertEqual(
            ctx.exception.response['Error']['Code'], 'NoSuchKey')
        self.assertEqual(
            ctx.exception.response['ResponseMetadata']['HTTPHeaders'][
                'content-type'], 'application/xml')

        with self.assertRaises(botocore.exceptions.ClientError) as ctx:
            self.conn.get_object(Bucket='invalid', Key=obj)
        self.assertEqual(
            ctx.exception.response['Error']['Code'], 'NoSuchBucket')
        self.assertEqual(
            ctx.exception.response['ResponseMetadata']['HTTPHeaders'][
                'content-type'], 'application/xml')

    def test_head_object_error(self):
        obj = 'object'
        self.conn.put_object(Bucket=self.bucket, Key=obj, Body=b'')

        auth_error_conn = get_boto3_conn(tf.config['s3_access_key'], 'invalid')
        with self.assertRaises(botocore.exceptions.ClientError) as ctx:
            auth_error_conn.head_object(Bucket=self.bucket, Key=obj)
        self.assertEqual(
            ctx.exception.response['ResponseMetadata']['HTTPStatusCode'], 403)
        self.assertEqual(
            ctx.exception.response['ResponseMetadata']['HTTPHeaders'][
                'content-type'], 'application/xml')

        with self.assertRaises(botocore.exceptions.ClientError) as ctx:
            self.conn.head_object(Bucket=self.bucket, Key='invalid')
        self.assertEqual(
            ctx.exception.response['ResponseMetadata']['HTTPStatusCode'], 404)
        self.assertEqual(
            ctx.exception.response['ResponseMetadata']['HTTPHeaders'][
                'content-type'], 'application/xml')

        with self.assertRaises(botocore.exceptions.ClientError) as ctx:
            self.conn.head_object(Bucket='invalid', Key=obj)
        self.assertEqual(
            ctx.exception.response['ResponseMetadata']['HTTPStatusCode'], 404)
        self.assertEqual(
            ctx.exception.response['ResponseMetadata']['HTTPHeaders'][
                'content-type'], 'application/xml')

    def test_delete_object_error(self):
        obj = 'object'
        self.conn.put_object(Bucket=self.bucket, Key=obj, Body=b'')

        auth_error_conn = get_boto3_conn(tf.config['s3_access_key'], 'invalid')
        with self.assertRaises(botocore.exceptions.ClientError) as ctx:
            auth_error_conn.delete_object(Bucket=self.bucket, Key=obj)
        self.assertEqual(
            ctx.exception.response['Error']['Code'], 'SignatureDoesNotMatch')
        self.assertEqual(
            ctx.exception.response['ResponseMetadata']['HTTPHeaders'][
                'content-type'], 'application/xml')

        with self.assertRaises(botocore.exceptions.ClientError) as ctx:
            self.conn.delete_object(Bucket='invalid', Key=obj)
        self.assertEqual(
            ctx.exception.response['Error']['Code'], 'NoSuchBucket')
        self.assertEqual(
            ctx.exception.response['ResponseMetadata']['HTTPHeaders'][
                'content-type'], 'application/xml')

    def test_put_object_content_encoding(self):
        obj = 'object'
        etag = md5(usedforsecurity=False).hexdigest()
        resp = self.conn.put_object(
            Bucket=self.bucket, Key=obj, ContentEncoding='gzip')
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])
        resp = self.conn.head_object(Bucket=self.bucket, Key=obj)
        headers = resp['ResponseMetadata']['HTTPHeaders']
        self.assertIn('content-encoding', headers)  # sanity
        self.assertEqual(headers['content-encoding'], 'gzip')
        self.assertCommonResponseHeaders(headers)
        self._assertObjectEtag(self.bucket, obj, etag)

    def test_put_object_content_md5(self):
        obj = 'object'
        content = b'abcdefghij'
        etag = md5(content, usedforsecurity=False).hexdigest()
        resp = self.conn.put_object(
            Bucket=self.bucket, Key=obj, Body=content,
            ContentMD5=calculate_md5(content))
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])
        self.assertCommonResponseHeaders(
            resp['ResponseMetadata']['HTTPHeaders'])
        self._assertObjectEtag(self.bucket, obj, etag)

    def test_put_object_content_type(self):
        obj = 'object'
        content = b'abcdefghij'
        etag = md5(content, usedforsecurity=False).hexdigest()
        resp = self.conn.put_object(
            Bucket=self.bucket, Key=obj, Body=content,
            ContentType='text/plain')
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])
        resp = self.conn.head_object(Bucket=self.bucket, Key=obj)
        headers = resp['ResponseMetadata']['HTTPHeaders']
        self.assertEqual(headers['content-type'], 'text/plain')
        self.assertCommonResponseHeaders(headers)
        self._assertObjectEtag(self.bucket, obj, etag)

    def test_put_object_conditional_requests(self):
        obj = 'object'
        content = b'abcdefghij'
        resp = self._presigned_put(
            self.bucket, obj, content, {'If-None-Match': 'asdf'})
        self.assertEqual(resp.status_code, 501)

        resp = self._presigned_put(
            self.bucket, obj, content, {'If-Match': '*'})
        self.assertEqual(resp.status_code, 501)

        resp = self._presigned_put(
            self.bucket, obj, content,
            {'If-Modified-Since': 'Sat, 27 Jun 2015 00:00:00 GMT'})
        self.assertEqual(resp.status_code, 501)

        resp = self._presigned_put(
            self.bucket, obj, content,
            {'If-Unmodified-Since': 'Sat, 27 Jun 2015 00:00:00 GMT'})
        self.assertEqual(resp.status_code, 501)

        # None of the above should actually have created an object
        with self.assertRaises(botocore.exceptions.ClientError) as ctx:
            self.conn.head_object(Bucket=self.bucket, Key=obj)
        self.assertEqual(
            ctx.exception.response['ResponseMetadata']['HTTPStatusCode'], 404)

        # But this will
        resp = self._presigned_put(
            self.bucket, obj, content, {'If-None-Match': '*'})
        self.assertEqual(resp.status_code, 200)

        # And the if-none-match prevents overwrites
        resp = self._presigned_put(
            self.bucket, obj, content, {'If-None-Match': '*'})
        self.assertEqual(resp.status_code, 412)

    def test_put_object_expect(self):
        obj = 'object'
        content = b'abcdefghij'
        etag = md5(content, usedforsecurity=False).hexdigest()
        resp = self._presigned_put(
            self.bucket, obj, content, {'Expect': '100-continue'})
        self.assertEqual(resp.status_code, 200)
        self.assertCommonResponseHeaders(resp.headers)
        self._assertObjectEtag(self.bucket, obj, etag)

    def _test_put_object_headers(self, req_headers, expected_headers=None):
        if expected_headers is None:
            expected_headers = req_headers
        obj = 'object'
        content = b'abcdefghij'
        etag = md5(content, usedforsecurity=False).hexdigest()
        resp = self._presigned_put(self.bucket, obj, content, req_headers)
        self.assertEqual(resp.status_code, 200)
        resp = self.conn.head_object(Bucket=self.bucket, Key=obj)
        headers = resp['ResponseMetadata']['HTTPHeaders']
        for header, value in expected_headers.items():
            self.assertIn(header.lower(), headers)
            self.assertEqual(headers[header.lower()], value)
        self.assertCommonResponseHeaders(headers)
        self._assertObjectEtag(self.bucket, obj, etag)

    def test_put_object_metadata(self):
        self._test_put_object_headers({
            'X-Amz-Meta-Bar': 'foo',
            'X-Amz-Meta-Bar2': 'foo2'})

    def test_put_object_weird_metadata(self):
        req_headers = dict(
            ('x-amz-meta-' + c, c)
            for c in '!"#$%&\'()*+-./<=>?@[\\]^`{|}~')
        exp_headers = dict(
            ('x-amz-meta-' + c, c)
            for c in '!#$%&\'(*+-.^`|~')
        self._test_put_object_headers(req_headers, exp_headers)

    def test_put_object_underscore_in_metadata(self):
        # Break this out separately for ease of testing pre-0.19.0 eventlet
        self._test_put_object_headers({
            'X-Amz-Meta-Foo-Bar': 'baz',
            'X-Amz-Meta-Foo_Bar': 'also baz'})

    def test_put_object_content_headers(self):
        self._test_put_object_headers({
            'Content-Type': 'foo/bar',
            'Content-Encoding': 'baz',
            'Content-Disposition': 'attachment',
            'Content-Language': 'en'})

    def test_put_object_cache_control(self):
        self._test_put_object_headers({
            'Cache-Control': 'private, some-extension'})

    def test_put_object_expires(self):
        self._test_put_object_headers({
            # We don't validate that the Expires header is a valid date
            'Expires': 'a valid HTTP-date timestamp'})

    def test_put_object_robots_tag(self):
        self._test_put_object_headers({
            'X-Robots-Tag': 'googlebot: noarchive'})

    def test_put_object_storage_class(self):
        obj = 'object'
        content = b'abcdefghij'
        etag = md5(content, usedforsecurity=False).hexdigest()
        resp = self.conn.put_object(
            Bucket=self.bucket, Key=obj, Body=content,
            StorageClass='STANDARD')
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])
        self.assertCommonResponseHeaders(
            resp['ResponseMetadata']['HTTPHeaders'])
        self._assertObjectEtag(self.bucket, obj, etag)

    def test_put_object_valid_delete_headers(self):
        obj = 'object'
        content = b'abcdefghij'
        ts = utils.Timestamp.now()
        resp = self._presigned_put(
            self.bucket, obj, content,
            {'X-Delete-At': str(int(ts) + 70)})
        self.assertEqual(resp.status_code, 200)
        resp = self._presigned_put(
            self.bucket, obj, content,
            {'X-Delete-After': str(int(ts) + 130)})
        self.assertEqual(resp.status_code, 200)

    def test_object_expiration_header(self):
        # Test that X-Delete-At translates to x-amz-expiration.
        obj = 'expiring-object'
        content = b'test content'
        resp = self.conn.put_object(
            Bucket=self.bucket, Key=obj, Body=content)
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])

        resp = self.conn.head_object(Bucket=self.bucket, Key=obj)
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])
        self.assertNotIn(
            'x-amz-expiration', resp['ResponseMetadata']['HTTPHeaders'])
        resp = self.conn.get_object(Bucket=self.bucket, Key=obj)
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])
        self.assertNotIn(
            'x-amz-expiration', resp['ResponseMetadata']['HTTPHeaders'])

        # now set x-delete-at
        delete_at_ts = utils.Timestamp.now(delta=3600 * 1e5)
        resp = self._presigned_put(
            self.bucket, obj, content,
            {'X-Delete-At': str(delete_at_ts.ceil())})
        self.assertEqual(resp.status_code, 200)

        expected = ('expiry-date="%s", rule-id="swift-object-expiration"'
                    % swob.date_header_format(delete_at_ts))

        # HEAD should return x-amz-expiration
        resp = self.conn.head_object(Bucket=self.bucket, Key=obj)
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])
        headers = resp['ResponseMetadata']['HTTPHeaders']
        self.assertIn('x-amz-expiration', headers)
        self.assertEqual(expected, headers['x-amz-expiration'])

        # GET should also return x-amz-expiration
        resp = self.conn.get_object(Bucket=self.bucket, Key=obj)
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])
        headers = resp['ResponseMetadata']['HTTPHeaders']
        self.assertIn('x-amz-expiration', headers)
        self.assertEqual(expected, headers['x-amz-expiration'])

    def test_put_object_invalid_x_delete_at(self):
        obj = 'object'
        content = b'abcdefghij'
        ts = utils.Timestamp.now()
        resp = self._presigned_put(
            self.bucket, obj, content,
            {'X-Delete-At': str(int(ts) - 140)})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(get_error_code(resp.content), 'InvalidArgument')
        self.assertEqual(get_error_msg(resp.content), 'X-Delete-At in past')
        resp = self._presigned_put(
            self.bucket, obj, content, {'X-Delete-At': 'test'})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(get_error_code(resp.content), 'InvalidArgument')
        self.assertEqual(
            get_error_msg(resp.content), 'Non-integer X-Delete-At')

    def test_put_object_invalid_x_delete_after(self):
        obj = 'object'
        content = b'abcdefghij'
        resp = self._presigned_put(
            self.bucket, obj, content, {'X-Delete-After': 'test'})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(get_error_code(resp.content), 'InvalidArgument')
        self.assertEqual(
            get_error_msg(resp.content), 'Non-integer X-Delete-After')
        resp = self._presigned_put(
            self.bucket, obj, content, {'X-Delete-After': '-140'})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(get_error_code(resp.content), 'InvalidArgument')
        self.assertEqual(
            get_error_msg(resp.content), 'X-Delete-After in past')

    def _copy_object_raw(self, dst_bucket, dst_obj, copy_source,
                         extra_headers=None):
        # Copies with deliberately-malformed copy-source query strings can't be
        # expressed via boto3, so PUT to a presigned URL with the raw header.
        headers = {'X-Amz-Copy-Source': copy_source}
        if extra_headers:
            headers.update(extra_headers)
        url = self.conn.generate_presigned_url(
            'put_object', Params={'Bucket': dst_bucket, 'Key': dst_obj},
            ExpiresIn=60)
        return requests.put(url, headers=headers)

    def test_put_object_copy_source_params(self):
        obj = 'object'
        src_body = b'some content'
        dst_bucket = 'dst-bucket'
        dst_obj = 'dst_object'
        self.conn.put_object(
            Bucket=self.bucket, Key=obj, Body=src_body,
            Metadata={'test': 'src'})
        self.conn.create_bucket(Bucket=dst_bucket)

        resp = self._copy_object_raw(
            dst_bucket, dst_obj, '/%s/%s?nonsense' % (self.bucket, obj))
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(get_error_code(resp.content), 'InvalidArgument')

        resp = self._copy_object_raw(
            dst_bucket, dst_obj,
            '/%s/%s?versionId=null&nonsense' % (self.bucket, obj))
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(get_error_code(resp.content), 'InvalidArgument')

        resp = self._copy_object_raw(
            dst_bucket, dst_obj,
            '/%s/%s?versionId=null' % (self.bucket, obj))
        self.assertEqual(resp.status_code, 200)
        resp = self.conn.get_object(Bucket=dst_bucket, Key=dst_obj)
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])
        self.assertEqual(resp['Metadata']['test'], 'src')
        self.assertEqual(resp['Body'].read(), src_body)

    def test_put_object_copy_source(self):
        obj = 'object'
        content = b'abcdefghij'
        etag = md5(content, usedforsecurity=False).hexdigest()
        self.conn.put_object(Bucket=self.bucket, Key=obj, Body=content)

        dst_bucket = 'dst-bucket'
        dst_obj = 'dst_object'
        self.conn.create_bucket(Bucket=dst_bucket)

        # /src/src -> /dst/dst
        resp = self.conn.copy_object(
            Bucket=dst_bucket, Key=dst_obj,
            CopySource={'Bucket': self.bucket, 'Key': obj})
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])
        self.assertCommonResponseHeaders(
            resp['ResponseMetadata']['HTTPHeaders'])
        self._assertObjectEtag(dst_bucket, dst_obj, etag)

        # /src/src -> /src/dst
        resp = self.conn.copy_object(
            Bucket=self.bucket, Key=dst_obj,
            CopySource={'Bucket': self.bucket, 'Key': obj})
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])
        self.assertCommonResponseHeaders(
            resp['ResponseMetadata']['HTTPHeaders'])
        self._assertObjectEtag(self.bucket, dst_obj, etag)

        # /src/src -> /src/src
        # need changes to copy itself (e.g. metadata)
        resp = self.conn.copy_object(
            Bucket=self.bucket, Key=obj,
            CopySource={'Bucket': self.bucket, 'Key': obj},
            Metadata={'foo': 'bar'}, MetadataDirective='REPLACE')
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])
        self._assertObjectEtag(self.bucket, obj, etag)
        self.assertCommonResponseHeaders(
            resp['ResponseMetadata']['HTTPHeaders'])

    def test_put_object_copy_metadata_directive(self):
        obj = 'object'
        dst_bucket = 'dst-bucket'
        dst_obj = 'dst_object'
        self.conn.put_object(
            Bucket=self.bucket, Key=obj, Metadata={'test': 'src'})
        self.conn.create_bucket(Bucket=dst_bucket)

        resp = self.conn.copy_object(
            Bucket=dst_bucket, Key=dst_obj,
            CopySource={'Bucket': self.bucket, 'Key': obj},
            MetadataDirective='REPLACE', Metadata={'test': 'dst'})
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])
        self.assertCommonResponseHeaders(
            resp['ResponseMetadata']['HTTPHeaders'])
        resp = self.conn.head_object(Bucket=dst_bucket, Key=dst_obj)
        self.assertEqual(resp['Metadata']['test'], 'dst')

        resp = self.conn.copy_object(
            Bucket=dst_bucket, Key=dst_obj,
            CopySource={'Bucket': self.bucket, 'Key': obj},
            MetadataDirective='COPY', Metadata={'test': 'dst'})
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])
        self.assertCommonResponseHeaders(
            resp['ResponseMetadata']['HTTPHeaders'])
        resp = self.conn.head_object(Bucket=dst_bucket, Key=dst_obj)
        self.assertEqual(resp['Metadata']['test'], 'src')

        resp = self.conn.copy_object(
            Bucket=dst_bucket, Key=dst_obj,
            CopySource={'Bucket': self.bucket, 'Key': obj},
            MetadataDirective='REPLACE', Metadata={'test2': 'dst'})
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])
        self.assertCommonResponseHeaders(
            resp['ResponseMetadata']['HTTPHeaders'])
        resp = self.conn.head_object(Bucket=dst_bucket, Key=dst_obj)
        self.assertNotIn('test', resp['Metadata'])
        self.assertEqual(resp['Metadata']['test2'], 'dst')

        resp = self._copy_object_raw(
            dst_bucket, dst_obj, '/%s/%s' % (self.bucket, obj),
            extra_headers={'X-Amz-Metadata-Directive': 'BAD'})
        self.assertEqual(resp.status_code, 400)

    def test_put_object_copy_source_if_modified_since(self):
        obj = 'object'
        dst_bucket = 'dst-bucket'
        dst_obj = 'dst_object'
        etag = md5(usedforsecurity=False).hexdigest()
        self.conn.put_object(Bucket=self.bucket, Key=obj, Body=b'')
        self.conn.create_bucket(Bucket=dst_bucket)

        resp = self.conn.head_object(Bucket=self.bucket, Key=obj)
        src_datetime = resp['LastModified'] - timedelta(days=1)
        resp = self.conn.copy_object(
            Bucket=dst_bucket, Key=dst_obj,
            CopySource={'Bucket': self.bucket, 'Key': obj},
            CopySourceIfModifiedSince=src_datetime)
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])
        self.assertCommonResponseHeaders(
            resp['ResponseMetadata']['HTTPHeaders'])
        self._assertObjectEtag(self.bucket, obj, etag)

    def test_put_object_copy_source_if_unmodified_since(self):
        obj = 'object'
        dst_bucket = 'dst-bucket'
        dst_obj = 'dst_object'
        etag = md5(usedforsecurity=False).hexdigest()
        self.conn.put_object(Bucket=self.bucket, Key=obj, Body=b'')
        self.conn.create_bucket(Bucket=dst_bucket)

        resp = self.conn.head_object(Bucket=self.bucket, Key=obj)
        src_datetime = resp['LastModified'] + timedelta(days=1)
        resp = self.conn.copy_object(
            Bucket=dst_bucket, Key=dst_obj,
            CopySource={'Bucket': self.bucket, 'Key': obj},
            CopySourceIfUnmodifiedSince=src_datetime)
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])
        self.assertCommonResponseHeaders(
            resp['ResponseMetadata']['HTTPHeaders'])
        self._assertObjectEtag(self.bucket, obj, etag)

    def test_put_object_copy_source_if_match(self):
        obj = 'object'
        dst_bucket = 'dst-bucket'
        dst_obj = 'dst_object'
        etag = md5(usedforsecurity=False).hexdigest()
        self.conn.put_object(Bucket=self.bucket, Key=obj, Body=b'')
        self.conn.create_bucket(Bucket=dst_bucket)

        resp = self.conn.copy_object(
            Bucket=dst_bucket, Key=dst_obj,
            CopySource={'Bucket': self.bucket, 'Key': obj},
            CopySourceIfMatch=etag)
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])
        self.assertCommonResponseHeaders(
            resp['ResponseMetadata']['HTTPHeaders'])
        self._assertObjectEtag(self.bucket, obj, etag)

    def test_put_object_copy_source_if_none_match(self):
        obj = 'object'
        dst_bucket = 'dst-bucket'
        dst_obj = 'dst_object'
        etag = md5(usedforsecurity=False).hexdigest()
        self.conn.put_object(Bucket=self.bucket, Key=obj, Body=b'')
        self.conn.create_bucket(Bucket=dst_bucket)

        resp = self.conn.copy_object(
            Bucket=dst_bucket, Key=dst_obj,
            CopySource={'Bucket': self.bucket, 'Key': obj},
            CopySourceIfNoneMatch='none-match')
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])
        self.assertCommonResponseHeaders(
            resp['ResponseMetadata']['HTTPHeaders'])
        self._assertObjectEtag(self.bucket, obj, etag)

    def test_get_object_response_content_type(self):
        obj = 'obj'
        self.conn.put_object(Bucket=self.bucket, Key=obj, Body=b'')

        resp = self.conn.get_object(
            Bucket=self.bucket, Key=obj, ResponseContentType='text/plain')
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])
        headers = resp['ResponseMetadata']['HTTPHeaders']
        self.assertCommonResponseHeaders(headers)
        self.assertEqual(headers['content-type'], 'text/plain')
        self.assertIn('accept-ranges', headers)

    def test_get_object_response_content_language(self):
        obj = 'object'
        self.conn.put_object(Bucket=self.bucket, Key=obj, Body=b'')

        resp = self.conn.get_object(
            Bucket=self.bucket, Key=obj, ResponseContentLanguage='en')
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])
        headers = resp['ResponseMetadata']['HTTPHeaders']
        self.assertCommonResponseHeaders(headers)
        self.assertEqual(headers['content-language'], 'en')
        self.assertIn('accept-ranges', headers)

    def test_get_object_response_cache_control(self):
        obj = 'object'
        self.conn.put_object(Bucket=self.bucket, Key=obj, Body=b'')

        resp = self.conn.get_object(
            Bucket=self.bucket, Key=obj, ResponseCacheControl='private')
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])
        headers = resp['ResponseMetadata']['HTTPHeaders']
        self.assertCommonResponseHeaders(headers)
        self.assertIn('accept-ranges', headers)
        self.assertEqual(headers['cache-control'], 'private')

    def test_get_object_response_content_disposition(self):
        obj = 'object'
        self.conn.put_object(Bucket=self.bucket, Key=obj, Body=b'')

        resp = self.conn.get_object(
            Bucket=self.bucket, Key=obj,
            ResponseContentDisposition='inline')
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])
        headers = resp['ResponseMetadata']['HTTPHeaders']
        self.assertCommonResponseHeaders(headers)
        self.assertEqual(headers['content-disposition'], 'inline')
        self.assertIn('accept-ranges', headers)

    def test_get_object_response_content_encoding(self):
        obj = 'object'
        self.conn.put_object(Bucket=self.bucket, Key=obj, Body=b'')

        resp = self.conn.get_object(
            Bucket=self.bucket, Key=obj, ResponseContentEncoding='gzip')
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])
        headers = resp['ResponseMetadata']['HTTPHeaders']
        self.assertCommonResponseHeaders(headers)
        self.assertEqual(headers['content-encoding'], 'gzip')
        self.assertIn('accept-ranges', headers)

    def test_get_object_range(self):
        obj = 'object'
        content = b'abcdefghij'
        self.conn.put_object(
            Bucket=self.bucket, Key=obj, Body=content,
            Metadata={'test': 'swift'},
            ContentType='application/octet-stream')

        resp = self.conn.get_object(
            Bucket=self.bucket, Key=obj, Range='bytes=1-5')
        self.assertEqual(206, resp['ResponseMetadata']['HTTPStatusCode'])
        headers = resp['ResponseMetadata']['HTTPHeaders']
        self.assertCommonResponseHeaders(headers)
        self.assertIn('content-length', headers)
        self.assertIn('accept-ranges', headers)
        self.assertEqual(headers['content-length'], '5')
        self.assertEqual(resp['Metadata'].get('test'), 'swift')
        self.assertEqual(resp['Body'].read(), b'bcdef')
        self.assertEqual('application/octet-stream', headers['content-type'])

        resp = self.conn.get_object(
            Bucket=self.bucket, Key=obj, Range='bytes=5-')
        self.assertEqual(206, resp['ResponseMetadata']['HTTPStatusCode'])
        headers = resp['ResponseMetadata']['HTTPHeaders']
        self.assertCommonResponseHeaders(headers)
        self.assertIn('content-length', headers)
        self.assertIn('accept-ranges', headers)
        self.assertEqual(headers['content-length'], '5')
        self.assertEqual(resp['Metadata'].get('test'), 'swift')
        self.assertEqual(resp['Body'].read(), b'fghij')

        resp = self.conn.get_object(
            Bucket=self.bucket, Key=obj, Range='bytes=-5')
        self.assertEqual(206, resp['ResponseMetadata']['HTTPStatusCode'])
        headers = resp['ResponseMetadata']['HTTPHeaders']
        self.assertCommonResponseHeaders(headers)
        self.assertIn('accept-ranges', headers)
        self.assertIn('content-length', headers)
        self.assertEqual(headers['content-length'], '5')
        self.assertEqual(resp['Metadata'].get('test'), 'swift')
        self.assertEqual(resp['Body'].read(), b'fghij')

        ranges = ['1-2', '4-5']

        resp = self.conn.get_object(
            Bucket=self.bucket, Key=obj,
            Range='bytes=%s' % ','.join(ranges))
        self.assertEqual(206, resp['ResponseMetadata']['HTTPStatusCode'])
        headers = resp['ResponseMetadata']['HTTPHeaders']
        self.assertCommonResponseHeaders(headers)
        self.assertIn('accept-ranges', headers)
        self.assertIn('content-length', headers)

        self.assertIn('content-type', headers)  # sanity
        content_type, boundary = headers['content-type'].split(';')

        self.assertEqual('multipart/byteranges', content_type)
        self.assertTrue(boundary.strip().startswith('boundary='))  # sanity
        boundary_str = boundary.strip()[len('boundary='):]

        body = resp['Body'].read()
        # TODO: Using swift.common.utils.multipart_byteranges_to_document_iters
        #       could be easy enough.
        parser = email.parser.BytesFeedParser()
        parser.feed(
            b"Content-Type: multipart/byterange; boundary=%s\r\n\r\n" %
            boundary_str.encode('ascii'))
        parser.feed(body)
        message = parser.close()

        self.assertTrue(message.is_multipart())  # sanity check
        mime_parts = message.get_payload()
        self.assertEqual(len(mime_parts), len(ranges))  # sanity

        for index, range_value in enumerate(ranges):
            start, end = map(int, range_value.split('-'))
            # go to next section and check sanity
            self.assertTrue(mime_parts[index])

            part = mime_parts[index]
            self.assertEqual(
                'application/octet-stream', part.get_content_type())
            expected_range = 'bytes %s/%s' % (range_value, len(content))
            self.assertEqual(
                expected_range, part.get('Content-Range'))
            # rest
            payload = part.get_payload(decode=True).strip()
            self.assertEqual(content[start:end + 1], payload)

    def test_get_object_if_modified_since(self):
        obj = 'object'
        self.conn.put_object(Bucket=self.bucket, Key=obj, Body=b'')

        resp = self.conn.head_object(Bucket=self.bucket, Key=obj)
        src_datetime = resp['LastModified'] - timedelta(days=1)
        resp = self.conn.get_object(
            Bucket=self.bucket, Key=obj, IfModifiedSince=src_datetime)
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])
        headers = resp['ResponseMetadata']['HTTPHeaders']
        self.assertIn('accept-ranges', headers)
        self.assertCommonResponseHeaders(headers)

    def test_get_object_if_unmodified_since(self):
        obj = 'object'
        self.conn.put_object(Bucket=self.bucket, Key=obj, Body=b'')

        resp = self.conn.head_object(Bucket=self.bucket, Key=obj)
        src_datetime = resp['LastModified'] + timedelta(days=1)
        resp = self.conn.get_object(
            Bucket=self.bucket, Key=obj, IfUnmodifiedSince=src_datetime)
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])
        headers = resp['ResponseMetadata']['HTTPHeaders']
        self.assertCommonResponseHeaders(headers)
        self.assertIn('accept-ranges', headers)

        # check we can use the last modified time from the listing...
        resp = self.conn.list_objects(Bucket=self.bucket)
        listing_datetime = resp['Contents'][0]['LastModified']
        # Make sure there's no fractions of a second
        self.assertEqual(listing_datetime.microsecond, 0)

        resp = self.conn.get_object(
            Bucket=self.bucket, Key=obj, IfUnmodifiedSince=listing_datetime)
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])
        headers = resp['ResponseMetadata']['HTTPHeaders']
        self.assertCommonResponseHeaders(headers)
        self.assertIn('accept-ranges', headers)

        try:
            resp = self.conn.get_object(
                Bucket=self.bucket, Key=obj,
                IfModifiedSince=listing_datetime)
            status = resp['ResponseMetadata']['HTTPStatusCode']
            headers = resp['ResponseMetadata']['HTTPHeaders']
        except botocore.exceptions.ClientError as e:
            status = e.response['ResponseMetadata']['HTTPStatusCode']
            headers = e.response['ResponseMetadata']['HTTPHeaders']
        self.assertEqual(status, 304)
        self.assertIn('accept-ranges', headers)

    def test_get_object_if_match(self):
        obj = 'object'
        self.conn.put_object(Bucket=self.bucket, Key=obj, Body=b'')

        resp = self.conn.head_object(Bucket=self.bucket, Key=obj)
        etag = resp['ResponseMetadata']['HTTPHeaders']['etag']

        resp = self.conn.get_object(
            Bucket=self.bucket, Key=obj, IfMatch=etag)
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])
        headers = resp['ResponseMetadata']['HTTPHeaders']
        self.assertCommonResponseHeaders(headers)
        self.assertIn('accept-ranges', headers)

    def test_get_object_if_none_match(self):
        obj = 'object'
        self.conn.put_object(Bucket=self.bucket, Key=obj, Body=b'')

        resp = self.conn.get_object(
            Bucket=self.bucket, Key=obj, IfNoneMatch='none-match')
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])
        headers = resp['ResponseMetadata']['HTTPHeaders']
        self.assertIn('accept-ranges', headers)
        self.assertCommonResponseHeaders(headers)

    def test_head_object_range(self):
        obj = 'object'
        content = b'abcdefghij'
        self.conn.put_object(Bucket=self.bucket, Key=obj, Body=content)

        resp = self.conn.head_object(
            Bucket=self.bucket, Key=obj, Range='bytes=1-5')
        headers = resp['ResponseMetadata']['HTTPHeaders']
        self.assertEqual(headers['content-length'], '5')
        self.assertCommonResponseHeaders(headers)
        self.assertIn('accept-ranges', headers)

        resp = self.conn.head_object(
            Bucket=self.bucket, Key=obj, Range='bytes=5-')
        headers = resp['ResponseMetadata']['HTTPHeaders']
        self.assertEqual(headers['content-length'], '5')
        self.assertCommonResponseHeaders(headers)
        self.assertIn('accept-ranges', headers)

        resp = self.conn.head_object(
            Bucket=self.bucket, Key=obj, Range='bytes=-5')
        headers = resp['ResponseMetadata']['HTTPHeaders']
        self.assertEqual(headers['content-length'], '5')
        self.assertCommonResponseHeaders(headers)
        self.assertIn('accept-ranges', headers)

    def test_head_object_if_modified_since(self):
        obj = 'object'
        self.conn.put_object(Bucket=self.bucket, Key=obj, Body=b'')

        resp = self.conn.head_object(Bucket=self.bucket, Key=obj)
        dt = resp['LastModified'] - timedelta(days=1)

        resp = self.conn.head_object(
            Bucket=self.bucket, Key=obj, IfModifiedSince=dt)
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])
        headers = resp['ResponseMetadata']['HTTPHeaders']
        self.assertCommonResponseHeaders(headers)
        self.assertIn('accept-ranges', headers)

    def test_head_object_if_unmodified_since(self):
        obj = 'object'
        self.conn.put_object(Bucket=self.bucket, Key=obj, Body=b'')

        resp = self.conn.head_object(Bucket=self.bucket, Key=obj)
        dt = resp['LastModified'] + timedelta(days=1)

        resp = self.conn.head_object(
            Bucket=self.bucket, Key=obj, IfUnmodifiedSince=dt)
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])
        headers = resp['ResponseMetadata']['HTTPHeaders']
        self.assertCommonResponseHeaders(headers)
        self.assertIn('accept-ranges', headers)

    def test_head_object_if_match(self):
        obj = 'object'
        self.conn.put_object(Bucket=self.bucket, Key=obj, Body=b'')

        resp = self.conn.head_object(Bucket=self.bucket, Key=obj)
        etag = resp['ResponseMetadata']['HTTPHeaders']['etag']

        resp = self.conn.head_object(
            Bucket=self.bucket, Key=obj, IfMatch=etag)
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])
        headers = resp['ResponseMetadata']['HTTPHeaders']
        self.assertCommonResponseHeaders(headers)
        self.assertIn('accept-ranges', headers)

    def test_head_object_if_none_match(self):
        obj = 'object'
        self.conn.put_object(Bucket=self.bucket, Key=obj, Body=b'')

        resp = self.conn.head_object(
            Bucket=self.bucket, Key=obj, IfNoneMatch='none-match')
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])
        headers = resp['ResponseMetadata']['HTTPHeaders']
        self.assertCommonResponseHeaders(headers)
        self.assertIn('accept-ranges', headers)


class TestS3ApiObjectSigV4(TestS3ApiObject, SigV4Mixin):
    pass


if __name__ == '__main__':
    unittest.main()
