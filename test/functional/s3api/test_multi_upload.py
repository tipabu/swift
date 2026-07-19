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

import binascii
import calendar
import unittest
from datetime import datetime, timezone
from email.utils import parsedate

import botocore

import test.functional as tf
from swift.common.middleware.s3api.utils import MULTIUPLOAD_SUFFIX
from swift.common.utils import md5

from test.functional.s3api import SigV4Mixin, S3ApiBaseBoto3, get_boto3_conn
from test.functional.swift_test_client import Connection as SwiftConnection


# Matches the literal Expires header the boto2-based tests used to send.
EXPIRES = datetime(1994, 12, 1, 16, 0, 0, tzinfo=timezone.utc)
EXPIRES_STR = 'Thu, 01 Dec 1994 16:00:00 GMT'


def setUpModule():
    tf.setup_package()


def tearDownModule():
    tf.teardown_package()


class TestS3ApiMultiUpload(S3ApiBaseBoto3):
    def setUp(self):
        super(TestS3ApiMultiUpload, self).setUp()
        if not tf.cluster_info['s3api'].get('allow_multipart_uploads', False):
            self.skipTest('multipart upload is not enebled')

        self.min_segment_size = int(tf.cluster_info['s3api'].get(
            'min_segment_size', 5242880))

    def _gen_parts(self, etags, step=1):
        return [{'ETag': etag, 'PartNumber': i * step + 1}
                for i, etag in enumerate(etags)]

    def _create_bucket(self, bucket):
        try:
            resp = self.conn.create_bucket(Bucket=bucket)
        except botocore.exceptions.ClientError as e:
            if e.response['Error']['Code'] != 'BucketAlreadyOwnedByYou':
                raise
            resp = e.response
        else:
            self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])
        return resp

    def _upload_part(self, bucket, key, upload_id, content=None, part_num=1):
        content = content if content is not None else \
            b'a' * self.min_segment_size
        return self.conn.upload_part(
            Bucket=bucket, Key=key, PartNumber=part_num,
            UploadId=upload_id, Body=content)

    def _upload_part_copy(self, src_bucket, src_obj, dst_bucket, dst_key,
                          upload_id, part_num=1, src_range=None,
                          src_version_id=None):
        copy_source = {'Bucket': src_bucket, 'Key': src_obj}
        if src_version_id:
            copy_source['VersionId'] = src_version_id
        kwargs = dict(Bucket=dst_bucket, Key=dst_key, PartNumber=part_num,
                      UploadId=upload_id, CopySource=copy_source)
        if src_range:
            kwargs['CopySourceRange'] = src_range
        resp = self.conn.upload_part_copy(**kwargs)
        etag = resp['CopyPartResult']['ETag'].strip('"')
        return resp, etag

    def _complete_multi_upload(self, bucket, key, upload_id, parts):
        return self.conn.complete_multipart_upload(
            Bucket=bucket, Key=key, UploadId=upload_id,
            MultipartUpload={'Parts': parts})

    def _complete_with_headers(self, bucket, key, upload_id, parts,
                               extra_headers):
        # complete_multipart_upload has no boto3 params for If-Modified-Since /
        # If-Unmodified-Since, so inject the conditional headers directly.
        def add_headers(request, **kwargs):
            for header, value in extra_headers.items():
                request.headers[header] = value

        self.conn.meta.events.register(
            'before-send.s3.CompleteMultipartUpload', add_headers)
        try:
            return self.conn.complete_multipart_upload(
                Bucket=bucket, Key=key, UploadId=upload_id,
                MultipartUpload={'Parts': parts})
        finally:
            self.conn.meta.events.unregister(
                'before-send.s3.CompleteMultipartUpload', add_headers)

    def test_object_multi_upload(self):
        bucket = 'bucket'
        keys = [u'obj1\N{SNOWMAN}', u'obj2\N{SNOWMAN}', 'obj3']
        mpu_params = [
            {'ContentType': 'foo/bar', 'Metadata': {'baz': 'quux'},
             'ContentEncoding': 'gzip', 'ContentLanguage': 'en-US',
             'Expires': EXPIRES, 'CacheControl': 'no-cache',
             'ContentDisposition': 'attachment'},
            {},
            {},
        ]
        uploads = []

        self._create_bucket(bucket)

        # Initiate Multipart Upload
        for expected_key, params in zip(keys, mpu_params):
            resp = self.conn.create_multipart_upload(
                Bucket=bucket, Key=expected_key, **params)
            self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])
            headers = resp['ResponseMetadata']['HTTPHeaders']
            self.assertCommonResponseHeaders(headers)
            self.assertIn('content-type', headers)
            self.assertEqual(headers['content-type'], 'application/xml')
            self.assertEqual(resp['Bucket'], bucket)
            key = resp['Key']
            self.assertEqual(expected_key, key)
            upload_id = resp['UploadId']
            self.assertIsNotNone(upload_id)
            self.assertNotIn((key, upload_id), uploads)
            uploads.append((key, upload_id))

        self.assertEqual(len(uploads), len(keys))  # sanity

        # List Multipart Uploads
        expected_uploads_list = [uploads]
        for upload in uploads:
            expected_uploads_list.append([upload])
        for expected_uploads in expected_uploads_list:
            if len(expected_uploads) == 1:
                resp = self.conn.list_multipart_uploads(
                    Bucket=bucket, Prefix=expected_uploads[0][0])
            else:
                resp = self.conn.list_multipart_uploads(Bucket=bucket)
            self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])
            headers = resp['ResponseMetadata']['HTTPHeaders']
            self.assertCommonResponseHeaders(headers)
            self.assertIn('content-type', headers)
            self.assertEqual(headers['content-type'], 'application/xml')
            self.assertEqual(resp['Bucket'], bucket)
            self.assertEqual(resp.get('KeyMarker', ''), '')
            if len(expected_uploads) > 1:
                self.assertEqual(resp['NextKeyMarker'],
                                 expected_uploads[-1][0])
                self.assertEqual(resp['NextUploadIdMarker'],
                                 expected_uploads[-1][1])
            else:
                self.assertEqual(resp.get('NextKeyMarker', ''), '')
                self.assertEqual(resp.get('NextUploadIdMarker', ''), '')
            self.assertEqual(resp.get('UploadIdMarker', ''), '')
            self.assertEqual(resp['MaxUploads'], 1000)
            self.assertNotIn('EncodingType', resp)
            self.assertFalse(resp['IsTruncated'])
            self.assertEqual(len(resp['Uploads']), len(expected_uploads))
            for (expected_key, expected_upload_id), u in \
                    zip(expected_uploads, resp['Uploads']):
                self.assertEqual(expected_key, u['Key'])
                self.assertEqual(expected_upload_id, u['UploadId'])
                self.assertEqual(u['Initiator']['ID'], self.access_key)
                self.assertEqual(u['Initiator']['DisplayName'],
                                 self.access_key)
                self.assertEqual(u['Owner']['ID'], self.access_key)
                self.assertEqual(u['Owner']['DisplayName'], self.access_key)
                self.assertEqual(u['StorageClass'], 'STANDARD')
                self.assertIsNotNone(u['Initiated'])

        # Upload Part
        key, upload_id = uploads[0]
        content = b'a' * self.min_segment_size
        etag = md5(content, usedforsecurity=False).hexdigest()
        resp = self._upload_part(bucket, key, upload_id, content)
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])
        headers = resp['ResponseMetadata']['HTTPHeaders']
        self.assertCommonResponseHeaders(headers, etag)
        self.assertIn('content-type', headers)
        self.assertEqual(headers['content-type'], 'text/html; charset=UTF-8')
        self.assertIn('content-length', headers)
        self.assertEqual(headers['content-length'], '0')
        expected_parts_list = [(headers['etag'],
                                calendar.timegm(
                                    parsedate(headers['last-modified'])))]

        # Upload Part Copy
        key, upload_id = uploads[1]
        src_bucket = 'bucket2'
        src_obj = 'obj3'
        src_content = b'b' * self.min_segment_size
        etag = md5(src_content, usedforsecurity=False).hexdigest()

        # prepare src obj
        self._create_bucket(src_bucket)
        self.conn.put_object(Bucket=src_bucket, Key=src_obj, Body=src_content)
        resp = self.conn.head_object(Bucket=src_bucket, Key=src_obj)
        self.assertCommonResponseHeaders(
            resp['ResponseMetadata']['HTTPHeaders'])

        resp, resp_etag = self._upload_part_copy(
            src_bucket, src_obj, bucket, key, upload_id)
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])
        headers = resp['ResponseMetadata']['HTTPHeaders']
        self.assertCommonResponseHeaders(headers)
        self.assertIn('content-type', headers)
        self.assertEqual(headers['content-type'], 'application/xml')
        self.assertNotIn('etag', headers)
        copy_resp_last_modified = resp['CopyPartResult']['LastModified']
        self.assertIsNotNone(copy_resp_last_modified)
        self.assertEqual(resp_etag, etag)

        # Check last-modified timestamp
        key, upload_id = uploads[1]
        resp = self.conn.list_parts(
            Bucket=bucket, Key=key, UploadId=upload_id)
        listing_last_modified = [p['LastModified'] for p in resp['Parts']]
        # There should be *exactly* one part in the result
        self.assertEqual(listing_last_modified, [copy_resp_last_modified])

        # List Parts
        key, upload_id = uploads[0]
        resp = self.conn.list_parts(
            Bucket=bucket, Key=key, UploadId=upload_id)
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])
        headers = resp['ResponseMetadata']['HTTPHeaders']
        self.assertCommonResponseHeaders(headers)
        self.assertIn('content-type', headers)
        self.assertEqual(headers['content-type'], 'application/xml')
        self.assertEqual(resp['Bucket'], bucket)
        self.assertEqual(resp['Key'], key)
        self.assertEqual(resp['UploadId'], upload_id)
        self.assertEqual(resp['Initiator']['ID'], self.access_key)
        self.assertEqual(resp['Initiator']['DisplayName'], self.access_key)
        self.assertEqual(resp['Owner']['ID'], self.access_key)
        self.assertEqual(resp['Owner']['DisplayName'], self.access_key)
        self.assertEqual(resp['StorageClass'], 'STANDARD')
        self.assertEqual(resp['PartNumberMarker'], 0)
        self.assertEqual(resp['NextPartNumberMarker'], 1)
        self.assertEqual(resp['MaxParts'], 1000)
        self.assertFalse(resp['IsTruncated'])
        self.assertEqual(len(resp['Parts']), 1)

        # etags will be used to complete the multipart upload
        etags = []
        parts = []
        for (expected_etag, expected_date), p in \
                zip(expected_parts_list, resp['Parts']):
            self.assertIsNotNone(p['LastModified'])
            self.assertEqual(expected_date, int(p['LastModified'].timestamp()))
            self.assertEqual(expected_etag, p['ETag'])
            self.assertEqual(self.min_segment_size, p['Size'])
            etags.append(p['ETag'])
            parts.append({'ETag': p['ETag'], 'PartNumber': p['PartNumber']})

        # Complete Multipart Upload
        key, upload_id = uploads[0]
        resp = self._complete_multi_upload(bucket, key, upload_id, parts)
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])
        headers = resp['ResponseMetadata']['HTTPHeaders']
        self.assertCommonResponseHeaders(headers)
        self.assertIn('content-type', headers)
        self.assertEqual(headers['content-type'], 'application/xml')
        self.assertEqual(
            '%s/bucket/obj1%%E2%%98%%83' %
            tf.config['s3_storage_url'].rstrip('/'),
            resp['Location'])
        self.assertEqual(resp['Bucket'], bucket)
        self.assertEqual(resp['Key'], key)
        concatted_etags = b''.join(
            etag.strip('"').encode('ascii') for etag in etags)
        exp_etag = '"%s-%s"' % (
            md5(binascii.unhexlify(concatted_etags),
                usedforsecurity=False).hexdigest(), len(etags))
        self.assertEqual(resp['ETag'], exp_etag)

        exp_size = self.min_segment_size * len(etags)
        resp = self.conn.head_object(Bucket=bucket, Key=key)
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])
        headers = resp['ResponseMetadata']['HTTPHeaders']
        self.assertEqual(headers['content-length'], str(exp_size))
        self.assertEqual(headers['content-type'], 'foo/bar')
        self.assertEqual(headers['content-encoding'], 'gzip')
        self.assertEqual(headers['content-language'], 'en-US')
        self.assertEqual(headers['content-disposition'], 'attachment')
        self.assertEqual(headers['expires'], EXPIRES_STR)
        self.assertEqual(headers['cache-control'], 'no-cache')
        self.assertEqual(headers['x-amz-meta-baz'], 'quux')

        swift_etag = '"%s"' % md5(
            concatted_etags, usedforsecurity=False).hexdigest()
        # TODO: GET via swift api, check against swift_etag

        # Should be safe to retry
        resp = self._complete_multi_upload(bucket, key, upload_id, parts)
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])
        headers = resp['ResponseMetadata']['HTTPHeaders']
        self.assertCommonResponseHeaders(headers)
        self.assertIn('content-type', headers)
        self.assertEqual(headers['content-type'], 'application/xml')
        self.assertEqual(
            '%s/bucket/obj1%%E2%%98%%83' %
            tf.config['s3_storage_url'].rstrip('/'),
            resp['Location'])
        self.assertEqual(resp['Bucket'], bucket)
        self.assertEqual(resp['Key'], key)
        self.assertEqual(resp['ETag'], exp_etag)

        resp = self.conn.head_object(Bucket=bucket, Key=key)
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])
        headers = resp['ResponseMetadata']['HTTPHeaders']
        self.assertEqual(headers['content-length'], str(exp_size))
        self.assertEqual(headers['content-type'], 'foo/bar')
        self.assertEqual(headers['x-amz-meta-baz'], 'quux')

        # Upload Part Copy -- MU as source
        key, upload_id = uploads[1]
        resp, resp_etag = self._upload_part_copy(
            bucket, keys[0], bucket, key, upload_id, part_num=2)
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])
        headers = resp['ResponseMetadata']['HTTPHeaders']
        self.assertCommonResponseHeaders(headers)
        self.assertIn('content-type', headers)
        self.assertEqual(headers['content-type'], 'application/xml')
        self.assertNotIn('etag', headers)
        last_modified = resp['CopyPartResult']['LastModified']
        self.assertIsNotNone(last_modified)

        exp_content = b'a' * self.min_segment_size
        etag = md5(exp_content, usedforsecurity=False).hexdigest()
        self.assertEqual(resp_etag, etag)

        # Also check that the etag is correct in part listings
        resp = self.conn.list_parts(
            Bucket=bucket, Key=key, UploadId=upload_id)
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])
        headers = resp['ResponseMetadata']['HTTPHeaders']
        self.assertCommonResponseHeaders(headers)
        self.assertIn('content-type', headers)
        self.assertEqual(headers['content-type'], 'application/xml')
        self.assertEqual(len(resp['Parts']), 2)
        self.assertEqual(resp['Parts'][1]['PartNumber'], 2)
        self.assertEqual(resp['Parts'][1]['ETag'], '"%s"' % etag)

        # Abort Multipart Uploads
        # note that uploads[1] has part data while uploads[2] does not
        sw_conn = SwiftConnection(tf.config)
        sw_conn.authenticate()
        for key, upload_id in uploads[1:]:
            resp = self.conn.abort_multipart_upload(
                Bucket=bucket, Key=key, UploadId=upload_id)
            self.assertEqual(204, resp['ResponseMetadata']['HTTPStatusCode'])
            headers = resp['ResponseMetadata']['HTTPHeaders']
            self.assertCommonResponseHeaders(headers)
            self.assertIn('content-type', headers)
            self.assertEqual(headers['content-type'],
                             'text/html; charset=UTF-8')
            self.assertIn('content-length', headers)
            self.assertEqual(headers['content-length'], '0')
            # Check if all parts have been deleted
            segments = sw_conn.get_account().container(
                bucket + MULTIUPLOAD_SUFFIX).files(
                    parms={'prefix': '%s/%s' % (key, upload_id)})
            self.assertFalse(segments)

        # Check object
        def check_obj(req_headers, exp_status):
            try:
                resp = self.conn.head_object(
                    Bucket=bucket, Key=keys[0], **req_headers)
                status = resp['ResponseMetadata']['HTTPStatusCode']
                headers = resp['ResponseMetadata']['HTTPHeaders']
            except botocore.exceptions.ClientError as e:
                status = e.response['ResponseMetadata']['HTTPStatusCode']
                headers = e.response['ResponseMetadata']['HTTPHeaders']
            self.assertEqual(status, exp_status)
            self.assertCommonResponseHeaders(headers)
            self.assertIn('content-length', headers)
            if exp_status == 412:
                self.assertNotIn('etag', headers)
                self.assertEqual(headers['content-length'], '0')
            else:
                self.assertIn('etag', headers)
                self.assertEqual(headers['etag'], exp_etag)
                if exp_status == 304:
                    self.assertEqual(headers['content-length'], '0')
                else:
                    self.assertEqual(headers['content-length'], str(exp_size))

        check_obj({}, 200)

        # Sanity check conditionals
        check_obj({'IfMatch': 'some other thing'}, 412)
        check_obj({'IfNoneMatch': 'some other thing'}, 200)

        # More interesting conditional cases
        check_obj({'IfMatch': exp_etag}, 200)
        check_obj({'IfMatch': swift_etag}, 412)
        check_obj({'IfNoneMatch': swift_etag}, 200)
        check_obj({'IfNoneMatch': exp_etag}, 304)

        # Check listings
        resp = self.conn.list_objects(Bucket=bucket)
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])
        resp_objects = resp['Contents']
        self.assertEqual(len(resp_objects), 1)
        o = resp_objects[0]
        self.assertEqual(o['Key'], keys[0])
        self.assertIsNotNone(o['LastModified'])
        self.assertEqual(o['LastModified'].microsecond, 0)
        self.assertEqual(o['ETag'], exp_etag)
        self.assertEqual(o['Size'], exp_size)
        self.assertIsNotNone(o['StorageClass'])
        self.assertEqual(o['Owner']['ID'], self.access_key)
        self.assertEqual(o['Owner']['DisplayName'], self.access_key)

    def test_initiate_multi_upload_error(self):
        bucket = 'bucket'
        key = 'obj'
        self._create_bucket(bucket)

        auth_error_conn = get_boto3_conn(tf.config['s3_access_key'], 'invalid')
        with self.assertRaises(botocore.exceptions.ClientError) as ctx:
            auth_error_conn.create_multipart_upload(Bucket=bucket, Key=key)
        self.assertEqual(
            ctx.exception.response['Error']['Code'], 'SignatureDoesNotMatch')

        with self.assertRaises(botocore.exceptions.ClientError) as ctx:
            self.conn.create_multipart_upload(Bucket='nothing', Key=key)
        self.assertEqual(
            ctx.exception.response['Error']['Code'], 'NoSuchBucket')

        with self.assertRaises(botocore.exceptions.ClientError) as ctx:
            self.conn.create_multipart_upload(
                Bucket=bucket,
                Key='x' * (
                    tf.cluster_info['swift']['max_object_name_length'] + 1))
        self.assertEqual(
            ctx.exception.response['Error']['Code'], 'KeyTooLongError')

    def test_list_multi_uploads_error(self):
        bucket = 'bucket'
        self._create_bucket(bucket)

        auth_error_conn = get_boto3_conn(tf.config['s3_access_key'], 'invalid')
        with self.assertRaises(botocore.exceptions.ClientError) as ctx:
            auth_error_conn.list_multipart_uploads(Bucket=bucket)
        self.assertEqual(
            ctx.exception.response['Error']['Code'], 'SignatureDoesNotMatch')

        with self.assertRaises(botocore.exceptions.ClientError) as ctx:
            self.conn.list_multipart_uploads(Bucket='nothing')
        self.assertEqual(
            ctx.exception.response['Error']['Code'], 'NoSuchBucket')

    def test_upload_part_error(self):
        bucket = 'bucket'
        self._create_bucket(bucket)
        key = 'obj'
        resp = self.conn.create_multipart_upload(Bucket=bucket, Key=key)
        upload_id = resp['UploadId']

        auth_error_conn = get_boto3_conn(tf.config['s3_access_key'], 'invalid')
        with self.assertRaises(botocore.exceptions.ClientError) as ctx:
            auth_error_conn.upload_part(
                Bucket=bucket, Key=key, PartNumber=1, UploadId=upload_id,
                Body=b'')
        self.assertEqual(
            ctx.exception.response['Error']['Code'], 'SignatureDoesNotMatch')

        with self.assertRaises(botocore.exceptions.ClientError) as ctx:
            self.conn.upload_part(
                Bucket='nothing', Key=key, PartNumber=1, UploadId=upload_id,
                Body=b'')
        self.assertEqual(
            ctx.exception.response['Error']['Code'], 'NoSuchBucket')

        with self.assertRaises(botocore.exceptions.ClientError) as ctx:
            self.conn.upload_part(
                Bucket=bucket, Key=key, PartNumber=1, UploadId='nothing',
                Body=b'')
        self.assertEqual(
            ctx.exception.response['Error']['Code'], 'NoSuchUpload')

        with self.assertRaises(botocore.exceptions.ClientError) as ctx:
            self.conn.upload_part(
                Bucket=bucket, Key=key, PartNumber=0, UploadId=upload_id,
                Body=b'')
        self.assertEqual(
            ctx.exception.response['Error']['Code'], 'InvalidArgument')
        err_msg = 'Part number must be an integer between 1 and'
        self.assertIn(err_msg, ctx.exception.response['Error']['Message'])

    def test_upload_part_copy_error(self):
        src_bucket = 'src'
        src_obj = 'src'
        self._create_bucket(src_bucket)
        self.conn.put_object(Bucket=src_bucket, Key=src_obj, Body=b'')

        bucket = 'bucket'
        self._create_bucket(bucket)
        key = 'obj'
        resp = self.conn.create_multipart_upload(Bucket=bucket, Key=key)
        upload_id = resp['UploadId']

        auth_error_conn = get_boto3_conn(tf.config['s3_access_key'], 'invalid')
        with self.assertRaises(botocore.exceptions.ClientError) as ctx:
            auth_error_conn.upload_part_copy(
                Bucket=bucket, Key=key, PartNumber=1, UploadId=upload_id,
                CopySource={'Bucket': src_bucket, 'Key': src_obj})
        self.assertEqual(
            ctx.exception.response['Error']['Code'], 'SignatureDoesNotMatch')

        with self.assertRaises(botocore.exceptions.ClientError) as ctx:
            self.conn.upload_part_copy(
                Bucket='nothing', Key=key, PartNumber=1, UploadId=upload_id,
                CopySource={'Bucket': src_bucket, 'Key': src_obj})
        self.assertEqual(
            ctx.exception.response['Error']['Code'], 'NoSuchBucket')

        with self.assertRaises(botocore.exceptions.ClientError) as ctx:
            self.conn.upload_part_copy(
                Bucket=bucket, Key=key, PartNumber=1, UploadId='nothing',
                CopySource={'Bucket': src_bucket, 'Key': src_obj})
        self.assertEqual(
            ctx.exception.response['Error']['Code'], 'NoSuchUpload')

        with self.assertRaises(botocore.exceptions.ClientError) as ctx:
            self.conn.upload_part_copy(
                Bucket=bucket, Key=key, PartNumber=1, UploadId=upload_id,
                CopySource={'Bucket': src_bucket, 'Key': 'nothing'})
        self.assertEqual(
            ctx.exception.response['Error']['Code'], 'NoSuchKey')

    def test_list_parts_error(self):
        bucket = 'bucket'
        self._create_bucket(bucket)
        key = 'obj'
        resp = self.conn.create_multipart_upload(Bucket=bucket, Key=key)
        upload_id = resp['UploadId']

        auth_error_conn = get_boto3_conn(tf.config['s3_access_key'], 'invalid')
        with self.assertRaises(botocore.exceptions.ClientError) as ctx:
            auth_error_conn.list_parts(
                Bucket=bucket, Key=key, UploadId=upload_id)
        self.assertEqual(
            ctx.exception.response['Error']['Code'], 'SignatureDoesNotMatch')

        with self.assertRaises(botocore.exceptions.ClientError) as ctx:
            self.conn.list_parts(
                Bucket='nothing', Key=key, UploadId=upload_id)
        self.assertEqual(
            ctx.exception.response['Error']['Code'], 'NoSuchBucket')

        with self.assertRaises(botocore.exceptions.ClientError) as ctx:
            self.conn.list_parts(Bucket=bucket, Key=key, UploadId='nothing')
        self.assertEqual(
            ctx.exception.response['Error']['Code'], 'NoSuchUpload')

    def test_abort_multi_upload_error(self):
        bucket = 'bucket'
        self._create_bucket(bucket)
        key = 'obj'
        resp = self.conn.create_multipart_upload(Bucket=bucket, Key=key)
        upload_id = resp['UploadId']
        self._upload_part(bucket, key, upload_id)

        auth_error_conn = get_boto3_conn(tf.config['s3_access_key'], 'invalid')
        with self.assertRaises(botocore.exceptions.ClientError) as ctx:
            auth_error_conn.abort_multipart_upload(
                Bucket=bucket, Key=key, UploadId=upload_id)
        self.assertEqual(
            ctx.exception.response['Error']['Code'], 'SignatureDoesNotMatch')

        with self.assertRaises(botocore.exceptions.ClientError) as ctx:
            self.conn.abort_multipart_upload(
                Bucket='nothing', Key=key, UploadId=upload_id)
        self.assertEqual(
            ctx.exception.response['Error']['Code'], 'NoSuchBucket')

        with self.assertRaises(botocore.exceptions.ClientError) as ctx:
            self.conn.abort_multipart_upload(
                Bucket=bucket, Key='nothing', UploadId=upload_id)
        self.assertEqual(
            ctx.exception.response['Error']['Code'], 'NoSuchUpload')

        with self.assertRaises(botocore.exceptions.ClientError) as ctx:
            self.conn.abort_multipart_upload(
                Bucket=bucket, Key=key, UploadId='nothing')
        self.assertEqual(
            ctx.exception.response['Error']['Code'], 'NoSuchUpload')

    def test_complete_multi_upload_error(self):
        bucket = 'bucket'
        keys = ['obj', 'obj2']
        self._create_bucket(bucket)
        resp = self.conn.create_multipart_upload(Bucket=bucket, Key=keys[0])
        upload_id = resp['UploadId']

        parts = []
        for i in range(1, 3):
            resp = self.conn.upload_part(
                Bucket=bucket, Key=keys[0], PartNumber=i, UploadId=upload_id,
                Body=b'')
            parts.append({'ETag': resp['ETag'], 'PartNumber': i})

        # part 1 too small
        with self.assertRaises(botocore.exceptions.ClientError) as ctx:
            self._complete_multi_upload(bucket, keys[0], upload_id, parts)
        self.assertEqual(
            ctx.exception.response['Error']['Code'], 'EntityTooSmall')

        # invalid credentials
        auth_error_conn = get_boto3_conn(tf.config['s3_access_key'], 'invalid')
        with self.assertRaises(botocore.exceptions.ClientError) as ctx:
            auth_error_conn.complete_multipart_upload(
                Bucket=bucket, Key=keys[0], UploadId=upload_id,
                MultipartUpload={'Parts': parts})
        self.assertEqual(
            ctx.exception.response['Error']['Code'], 'SignatureDoesNotMatch')

        # wrong/missing bucket
        with self.assertRaises(botocore.exceptions.ClientError) as ctx:
            self.conn.complete_multipart_upload(
                Bucket='nothing', Key=keys[0], UploadId=upload_id,
                MultipartUpload={'Parts': parts})
        self.assertEqual(
            ctx.exception.response['Error']['Code'], 'NoSuchBucket')

        # wrong upload ID
        with self.assertRaises(botocore.exceptions.ClientError) as ctx:
            self.conn.complete_multipart_upload(
                Bucket=bucket, Key=keys[0], UploadId='nothing',
                MultipartUpload={'Parts': parts})
        self.assertEqual(
            ctx.exception.response['Error']['Code'], 'NoSuchUpload')

        # without Part in xml
        with self.assertRaises(botocore.exceptions.ClientError) as ctx:
            self.conn.complete_multipart_upload(
                Bucket=bucket, Key=keys[0], UploadId=upload_id,
                MultipartUpload={'Parts': []})
        self.assertEqual(
            ctx.exception.response['Error']['Code'], 'MalformedXML')

        # with invalid etag in xml
        with self.assertRaises(botocore.exceptions.ClientError) as ctx:
            self.conn.complete_multipart_upload(
                Bucket=bucket, Key=keys[0], UploadId=upload_id,
                MultipartUpload={'Parts': [
                    {'ETag': 'invalid', 'PartNumber': 1}]})
        self.assertEqual(
            ctx.exception.response['Error']['Code'], 'InvalidPart')

        # without part in Swift
        resp = self.conn.create_multipart_upload(Bucket=bucket, Key=keys[1])
        upload_id = resp['UploadId']
        with self.assertRaises(botocore.exceptions.ClientError) as ctx:
            self.conn.complete_multipart_upload(
                Bucket=bucket, Key=keys[1], UploadId=upload_id,
                MultipartUpload={'Parts': [
                    {'ETag': parts[0]['ETag'], 'PartNumber': 1}]})
        self.assertEqual(
            ctx.exception.response['Error']['Code'], 'InvalidPart')

    def test_complete_multi_upload_conditional(self):
        bucket = 'bucket'
        key = 'obj'
        self._create_bucket(bucket)
        resp = self.conn.create_multipart_upload(Bucket=bucket, Key=key)
        upload_id = resp['UploadId']

        resp = self.conn.upload_part(
            Bucket=bucket, Key=key, PartNumber=1, UploadId=upload_id, Body=b'')
        part_etag = resp['ETag']
        parts = [{'ETag': part_etag, 'PartNumber': 1}]

        for headers in [
            {'If-Match': part_etag},
            {'If-Match': '*'},
            {'If-None-Match': part_etag},
            {'If-Modified-Since': 'Wed, 21 Oct 2015 07:28:00 GMT'},
            {'If-Unmodified-Since': 'Wed, 21 Oct 2015 07:28:00 GMT'},
        ]:
            with self.subTest(headers=headers):
                with self.assertRaises(
                        botocore.exceptions.ClientError) as ctx:
                    self._complete_with_headers(
                        bucket, key, upload_id, parts, headers)
                self.assertEqual(
                    ctx.exception.response[
                        'ResponseMetadata']['HTTPStatusCode'], 501)
                self.assertEqual(
                    ctx.exception.response['Error']['Code'], 'NotImplemented')

        # Can do basic existence checks, though
        resp = self._complete_with_headers(
            bucket, key, upload_id, parts, {'If-None-Match': '*'})
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])

        # And it'll prevent overwrites
        resp = self.conn.create_multipart_upload(Bucket=bucket, Key=key)
        upload_id = resp['UploadId']

        resp = self.conn.upload_part(
            Bucket=bucket, Key=key, PartNumber=1, UploadId=upload_id, Body=b'')
        part_etag = resp['ETag']
        parts = [{'ETag': part_etag, 'PartNumber': 1}]

        with self.assertRaises(botocore.exceptions.ClientError) as ctx:
            self._complete_with_headers(
                bucket, key, upload_id, parts, {'If-None-Match': '*'})
        self.assertEqual(
            ctx.exception.response['ResponseMetadata']['HTTPStatusCode'], 412)
        self.assertEqual(
            ctx.exception.response['Error']['Code'], 'PreconditionFailed')

    def test_complete_upload_min_segment_size(self):
        bucket = 'bucket'
        key = 'obj'
        self._create_bucket(bucket)
        resp = self.conn.create_multipart_upload(Bucket=bucket, Key=key)
        upload_id = resp['UploadId']

        # multi parts with no body
        parts = []
        for i in range(1, 3):
            resp = self.conn.upload_part(
                Bucket=bucket, Key=key, PartNumber=i, UploadId=upload_id,
                Body=b'')
            parts.append({'ETag': resp['ETag'], 'PartNumber': i})

        with self.assertRaises(botocore.exceptions.ClientError) as ctx:
            self._complete_multi_upload(bucket, key, upload_id, parts)
        self.assertEqual(
            ctx.exception.response['Error']['Code'], 'EntityTooSmall')

        # multi parts with all parts less than min segment size
        parts = []
        for i in range(1, 3):
            resp = self.conn.upload_part(
                Bucket=bucket, Key=key, PartNumber=i, UploadId=upload_id,
                Body=b'AA')
            parts.append({'ETag': resp['ETag'], 'PartNumber': i})

        with self.assertRaises(botocore.exceptions.ClientError) as ctx:
            self._complete_multi_upload(bucket, key, upload_id, parts)
        self.assertEqual(
            ctx.exception.response['Error']['Code'], 'EntityTooSmall')

        # one part and less than min segment size
        resp = self.conn.upload_part(
            Bucket=bucket, Key=key, PartNumber=1, UploadId=upload_id,
            Body=b'AA')
        parts = [{'ETag': resp['ETag'], 'PartNumber': 1}]

        resp = self._complete_multi_upload(bucket, key, upload_id, parts)
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])

        # multi parts with all parts except the first part less than min
        # segment size
        resp = self.conn.create_multipart_upload(Bucket=bucket, Key=key)
        upload_id = resp['UploadId']

        parts = []
        body_size = [self.min_segment_size, self.min_segment_size - 1, 2]
        for i in range(1, 3):
            resp = self.conn.upload_part(
                Bucket=bucket, Key=key, PartNumber=i, UploadId=upload_id,
                Body=b'A' * body_size[i])
            parts.append({'ETag': resp['ETag'], 'PartNumber': i})

        with self.assertRaises(botocore.exceptions.ClientError) as ctx:
            self._complete_multi_upload(bucket, key, upload_id, parts)
        self.assertEqual(
            ctx.exception.response['Error']['Code'], 'EntityTooSmall')

        # multi parts with all parts except last part more than min segment
        # size
        resp = self.conn.create_multipart_upload(Bucket=bucket, Key=key)
        upload_id = resp['UploadId']

        parts = []
        body_size = [self.min_segment_size, self.min_segment_size, 2]
        for i in range(1, 3):
            resp = self.conn.upload_part(
                Bucket=bucket, Key=key, PartNumber=i, UploadId=upload_id,
                Body=b'A' * body_size[i])
            parts.append({'ETag': resp['ETag'], 'PartNumber': i})

        resp = self._complete_multi_upload(bucket, key, upload_id, parts)
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])

    def test_complete_upload_with_fewer_etags(self):
        bucket = 'bucket'
        key = 'obj'
        self._create_bucket(bucket)
        resp = self.conn.create_multipart_upload(Bucket=bucket, Key=key)
        upload_id = resp['UploadId']

        parts = []
        for i in range(1, 4):
            part_num = 2 * i - 1
            resp = self.conn.upload_part(
                Bucket=bucket, Key=key, PartNumber=part_num,
                UploadId=upload_id, Body=b'A' * 1024 * 1024 * 5)
            parts.append({'ETag': resp['ETag'], 'PartNumber': part_num})
        resp = self._complete_multi_upload(
            bucket, key, upload_id, parts[:-1])
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])

    def _initiate_mpu_upload(self, bucket, key):
        self._create_bucket(bucket)
        resp = self.conn.create_multipart_upload(Bucket=bucket, Key=key)
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])
        self.assertEqual(resp['Bucket'], bucket)
        self.assertEqual(resp['Key'], key)
        upload_id = resp['UploadId']
        self.assertIsNotNone(upload_id)
        return upload_id

    def _copy_part_from_new_src_range(self, bucket, key, upload_id):
        src_bucket = 'bucket2'
        src_obj = 'obj4'
        src_content = b'y' * (self.min_segment_size // 2) + b'z' * \
            self.min_segment_size
        src_range = 'bytes=0-%d' % (self.min_segment_size - 1)
        etag = md5(
            src_content[:self.min_segment_size],
            usedforsecurity=False).hexdigest()

        # prepare src obj
        self._create_bucket(src_bucket)
        self.conn.put_object(Bucket=src_bucket, Key=src_obj, Body=src_content)
        resp = self.conn.head_object(Bucket=src_bucket, Key=src_obj)
        self.assertCommonResponseHeaders(
            resp['ResponseMetadata']['HTTPHeaders'])

        resp, resp_etag = self._upload_part_copy(
            src_bucket, src_obj, bucket, key, upload_id, 1, src_range)
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])
        headers = resp['ResponseMetadata']['HTTPHeaders']
        self.assertCommonResponseHeaders(headers)
        self.assertIn('content-type', headers)
        self.assertEqual(headers['content-type'], 'application/xml')
        self.assertNotIn('etag', headers)
        copy_resp_last_modified = resp['CopyPartResult']['LastModified']
        self.assertIsNotNone(copy_resp_last_modified)
        self.assertEqual(resp_etag, etag)

        # Check last-modified timestamp
        resp = self.conn.list_parts(
            Bucket=bucket, Key=key, UploadId=upload_id)
        listing_last_modified = [p['LastModified'] for p in resp['Parts']]
        # There should be *exactly* one part in the result
        self.assertEqual(listing_last_modified, [copy_resp_last_modified])

        return '"%s"' % resp_etag

    def _complete_mpu_upload(self, bucket, key, upload_id, etags):
        parts = self._gen_parts(etags)
        resp = self._complete_multi_upload(bucket, key, upload_id, parts)
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])
        headers = resp['ResponseMetadata']['HTTPHeaders']
        self.assertCommonResponseHeaders(headers)
        self.assertIn('content-type', headers)
        self.assertEqual(headers['content-type'], 'application/xml')
        self.assertEqual(
            '%s/%s/%s' %
            (tf.config['s3_storage_url'].rstrip('/'), bucket, key),
            resp['Location'])
        self.assertEqual(resp['Bucket'], bucket)
        self.assertEqual(resp['Key'], key)
        concatted_etags = b''.join(
            etag.strip('"').encode('ascii') for etag in etags)
        exp_etag = '"%s-%s"' % (
            md5(binascii.unhexlify(concatted_etags),
                usedforsecurity=False).hexdigest(), len(etags))
        self.assertEqual(resp['ETag'], exp_etag)

    def test_mpu_copy_part_from_range_then_complete(self):
        bucket = 'mpu-copy-range'
        key = 'obj-complete'
        upload_id = self._initiate_mpu_upload(bucket, key)
        etag = self._copy_part_from_new_src_range(bucket, key, upload_id)
        self._complete_mpu_upload(bucket, key, upload_id, [etag])

    def test_mpu_copy_part_from_range_then_abort(self):
        bucket = 'mpu-copy-range'
        key = 'obj-abort'
        upload_id = self._initiate_mpu_upload(bucket, key)
        self._copy_part_from_new_src_range(bucket, key, upload_id)

        # Abort Multipart Upload
        resp = self.conn.abort_multipart_upload(
            Bucket=bucket, Key=key, UploadId=upload_id)

        # sanity checks
        self.assertEqual(204, resp['ResponseMetadata']['HTTPStatusCode'])
        headers = resp['ResponseMetadata']['HTTPHeaders']
        self.assertCommonResponseHeaders(headers)
        self.assertIn('content-type', headers)
        self.assertEqual(headers['content-type'], 'text/html; charset=UTF-8')
        self.assertIn('content-length', headers)
        self.assertEqual(headers['content-length'], '0')

    def _copy_part_from_new_mpu_range(self, bucket, key, upload_id):
        src_bucket = 'bucket2'
        src_obj = 'mpu2'
        src_upload_id = self._initiate_mpu_upload(src_bucket, src_obj)
        # upload parts
        etags = []
        for part_num in range(2):
            # Upload Part
            content = (chr(97 + part_num) * self.min_segment_size).encode()
            etag = md5(content, usedforsecurity=False).hexdigest()
            resp = self._upload_part(
                src_bucket, src_obj, src_upload_id, content,
                part_num=part_num + 1)
            self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])
            headers = resp['ResponseMetadata']['HTTPHeaders']
            self.assertCommonResponseHeaders(headers, etag)
            self.assertIn('content-type', headers)
            self.assertEqual(headers['content-type'],
                             'text/html; charset=UTF-8')
            self.assertIn('content-length', headers)
            self.assertEqual(headers['content-length'], '0')
            self.assertEqual(headers['etag'], '"%s"' % etag)
            etags.append('"%s"' % etag)
        self._complete_mpu_upload(src_bucket, src_obj, src_upload_id, etags)

        # Upload Part Copy -- MPU as source
        src_range = 'bytes=0-%d' % (self.min_segment_size - 1)
        resp, resp_etag = self._upload_part_copy(
            src_bucket, src_obj, bucket, key, upload_id, part_num=1,
            src_range=src_range)
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])
        headers = resp['ResponseMetadata']['HTTPHeaders']
        self.assertCommonResponseHeaders(headers)
        self.assertIn('content-type', headers)
        self.assertEqual(headers['content-type'], 'application/xml')
        self.assertNotIn('etag', headers)
        last_modified = resp['CopyPartResult']['LastModified']
        self.assertIsNotNone(last_modified)
        # use copied with src_range from src_obj?part-number=1
        self.assertEqual('"%s"' % resp_etag, etags[0])

        return '"%s"' % resp_etag

    def test_mpu_copy_part_from_mpu_part_number_then_complete(self):
        bucket = 'mpu-copy-range'
        key = 'obj-complete'
        upload_id = self._initiate_mpu_upload(bucket, key)
        etag = self._copy_part_from_new_mpu_range(bucket, key, upload_id)
        self._complete_mpu_upload(bucket, key, upload_id, [etag])

    def test_mpu_copy_part_from_mpu_part_number_then_abort(self):
        bucket = 'mpu-copy-range'
        key = 'obj-abort'
        upload_id = self._initiate_mpu_upload(bucket, key)
        self._copy_part_from_new_mpu_range(bucket, key, upload_id)

        # Abort Multipart Upload
        resp = self.conn.abort_multipart_upload(
            Bucket=bucket, Key=key, UploadId=upload_id)

        # sanity checks
        self.assertEqual(204, resp['ResponseMetadata']['HTTPStatusCode'])
        headers = resp['ResponseMetadata']['HTTPHeaders']
        self.assertCommonResponseHeaders(headers)
        self.assertIn('content-type', headers)
        self.assertEqual(headers['content-type'], 'text/html; charset=UTF-8')
        self.assertIn('content-length', headers)
        self.assertEqual(headers['content-length'], '0')

    def test_object_multi_upload_part_copy_version(self):
        if 'object_versioning' not in tf.cluster_info:
            self.skipTest('Object Versioning not enabled')
        bucket = 'bucket'
        key = 'obj1'
        self._create_bucket(bucket)
        resp = self.conn.create_multipart_upload(Bucket=bucket, Key=key)
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])
        self.assertEqual(resp['Bucket'], bucket)
        self.assertEqual(resp['Key'], key)
        upload_id = resp['UploadId']
        self.assertIsNotNone(upload_id)

        src_bucket = 'bucket2'
        src_obj = 'obj4'
        src_content = b'y' * (self.min_segment_size // 2) + b'z' * \
            self.min_segment_size
        etags = [md5(src_content, usedforsecurity=False).hexdigest()]

        # prepare null-version src obj
        self._create_bucket(src_bucket)
        self.conn.put_object(Bucket=src_bucket, Key=src_obj, Body=src_content)
        resp = self.conn.head_object(Bucket=src_bucket, Key=src_obj)
        self.assertCommonResponseHeaders(
            resp['ResponseMetadata']['HTTPHeaders'])

        # Turn on versioning
        self.conn.put_bucket_versioning(
            Bucket=src_bucket,
            VersioningConfiguration={'Status': 'Enabled'})

        src_obj2 = 'obj5'
        src_content2 = b'stub'
        etags.append(md5(src_content2, usedforsecurity=False).hexdigest())

        # prepare src obj w/ real version
        self.conn.put_object(Bucket=src_bucket, Key=src_obj2,
                             Body=src_content2)
        resp = self.conn.head_object(Bucket=src_bucket, Key=src_obj2)
        self.assertCommonResponseHeaders(
            resp['ResponseMetadata']['HTTPHeaders'])
        version_id2 = resp['VersionId']

        resp, resp_etag = self._upload_part_copy(
            src_bucket, src_obj, bucket, key, upload_id, 1,
            src_version_id='null')
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])
        headers = resp['ResponseMetadata']['HTTPHeaders']
        self.assertCommonResponseHeaders(headers)
        self.assertIn('content-type', headers)
        self.assertEqual(headers['content-type'], 'application/xml')
        self.assertNotIn('etag', headers)
        copy_resp_last_modifieds = [resp['CopyPartResult']['LastModified']]
        self.assertIsNotNone(copy_resp_last_modifieds[0])
        self.assertEqual(resp_etag, etags[0])

        resp, resp_etag = self._upload_part_copy(
            src_bucket, src_obj2, bucket, key, upload_id, 2,
            src_version_id=version_id2)
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])
        headers = resp['ResponseMetadata']['HTTPHeaders']
        self.assertCommonResponseHeaders(headers)
        self.assertIn('content-type', headers)
        self.assertEqual(headers['content-type'], 'application/xml')
        self.assertNotIn('etag', headers)
        copy_resp_last_modifieds.append(resp['CopyPartResult']['LastModified'])
        self.assertIsNotNone(copy_resp_last_modifieds[1])
        self.assertEqual(resp_etag, etags[1])

        # Check last-modified timestamp
        resp = self.conn.list_parts(
            Bucket=bucket, Key=key, UploadId=upload_id)
        listing_last_modified = [p['LastModified'] for p in resp['Parts']]
        self.assertEqual(listing_last_modified, copy_resp_last_modifieds)

        # Abort Multipart Upload
        resp = self.conn.abort_multipart_upload(
            Bucket=bucket, Key=key, UploadId=upload_id)

        # sanity checks
        self.assertEqual(204, resp['ResponseMetadata']['HTTPStatusCode'])
        headers = resp['ResponseMetadata']['HTTPHeaders']
        self.assertCommonResponseHeaders(headers)
        self.assertIn('content-type', headers)
        self.assertEqual(headers['content-type'], 'text/html; charset=UTF-8')
        self.assertIn('content-length', headers)
        self.assertEqual(headers['content-length'], '0')

    def test_delete_bucket_multi_upload_object_exisiting(self):
        bucket = 'bucket'
        key = 'obj1'
        self._create_bucket(bucket)
        resp = self.conn.create_multipart_upload(Bucket=bucket, Key=key)
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])
        self.assertEqual(resp['Key'], key)  # sanity
        upload_id = resp['UploadId']
        self.assertIsNotNone(upload_id)  # sanity

        # Upload Part
        content = b'a' * self.min_segment_size
        resp = self._upload_part(bucket, key, upload_id, content)
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])

        # Complete Multipart Upload
        etags = ['"%s"' % md5(content, usedforsecurity=False).hexdigest()]
        resp = self._complete_multi_upload(
            bucket, key, upload_id, self._gen_parts(etags))
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])

        # GET multipart object
        resp = self.conn.get_object(Bucket=bucket, Key=key)
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])
        self.assertEqual(content, resp['Body'].read())  # sanity

        # DELETE bucket while the object existing
        with self.assertRaises(botocore.exceptions.ClientError) as ctx:
            self.conn.delete_bucket(Bucket=bucket)
        self.assertEqual(
            ctx.exception.response['ResponseMetadata']['HTTPStatusCode'], 409)

        # The object must still be there.
        resp = self.conn.get_object(Bucket=bucket, Key=key)
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])
        self.assertEqual(content, resp['Body'].read())  # sanity

        # Can delete it with DeleteMultipleObjects request
        resp = self.conn.delete_objects(
            Bucket=bucket,
            Delete={'Objects': [{'Key': key}], 'Quiet': True})
        self.assertEqual(200, resp['ResponseMetadata']['HTTPStatusCode'])
        self.assertCommonResponseHeaders(
            resp['ResponseMetadata']['HTTPHeaders'])

        with self.assertRaises(botocore.exceptions.ClientError) as ctx:
            self.conn.get_object(Bucket=bucket, Key=key)
        self.assertEqual(
            ctx.exception.response['ResponseMetadata']['HTTPStatusCode'], 404)

        # Now we can delete
        resp = self.conn.delete_bucket(Bucket=bucket)
        self.assertEqual(204, resp['ResponseMetadata']['HTTPStatusCode'])


class TestS3ApiMultiUploadSigV4(TestS3ApiMultiUpload, SigV4Mixin):
    pass


if __name__ == '__main__':
    unittest.main()
