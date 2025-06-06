.. -*- rst -*-

==========
Containers
==========

Lists objects in a container. Creates, shows details for, and
deletes containers. Creates, updates, shows, and deletes container
metadata. For more information and concepts about
containers see `Object Storage API overview
<https://docs.openstack.org/swift/latest/api/object_api_v1_overview.html>`_.


Show container details and list objects
=======================================

.. rest_method::  GET /v1/{account}/{container}

Shows details for a container and lists objects, sorted by name, in the container.

Specify query parameters in the request to filter the list and
return a subset of objects. Omit query parameters to return
a list of objects that are stored in the container,
up to 10,000 names. The 10,000 maximum value is configurable. To
view the value for the cluster, issue a GET ``/info`` request.

Example requests and responses:

- ``OK (200)``. Success. The response body lists the objects.

- ``No Content (204)``. Success. The response body shows no objects.
  Either the container has no objects or you are paging through a
  long list of objects by using the ``marker``, ``limit``, or
  ``end_marker`` query parameter and you have reached the end of
  the list.

If the container does not exist, the call returns the ``Not Found
(404)`` response code.

Normal response codes: 200, 204

Error response codes: 404


Request
-------

.. rest_parameters:: parameters.yaml

   - account: account
   - container: container
   - limit: limit
   - marker: marker
   - end_marker: end_marker
   - prefix: prefix
   - format: format
   - delimiter: delimiter
   - delimiter-depth: delimiter-depth
   - path: path
   - reverse: reverse
   - X-Auth-Token: X-Auth-Token
   - X-Service-Token: X-Service-Token
   - X-Newest: X-Newest
   - Accept: Accept
   - X-Container-Meta-Temp-URL-Key: X-Container-Meta-Temp-URL-Key_req
   - X-Container-Meta-Temp-URL-Key-2: X-Container-Meta-Temp-URL-Key-2_req
   - X-Trans-Id-Extra: X-Trans-Id-Extra
   - X-Storage-Policy: X-Storage-Policy


Response Parameters
-------------------

.. rest_parameters:: parameters.yaml

   - X-Container-Meta-name: X-Container-Meta-name
   - Content-Length: Content-Length_listing_resp
   - X-Container-Object-Count: X-Container-Object-Count
   - X-Container-Bytes-Used: X-Container-Bytes-Used
   - Accept-Ranges: Accept-Ranges
   - X-Container-Meta-Temp-URL-Key: X-Container-Meta-Temp-URL-Key_resp
   - X-Container-Meta-Temp-URL-Key-2: X-Container-Meta-Temp-URL-Key-2_resp
   - X-Container-Meta-Quota-Count: X-Container-Meta-Quota-Count_resp
   - X-Container-Meta-Quota-Bytes: X-Container-Meta-Quota-Bytes_resp
   - X-Storage-Policy: X-Storage-Policy
   - X-Container-Read: X-Container-Read_resp
   - X-Container-Write: X-Container-Write_resp
   - X-Container-Sync-Key: X-Container-Sync-Key_resp
   - X-Container-Sync-To: X-Container-Sync-To_resp
   - X-Versions-Location: X-Versions-Location_resp
   - X-History-Location: X-History-Location_resp
   - X-Timestamp: X-Timestamp
   - X-Trans-Id: X-Trans-Id
   - X-Openstack-Request-Id: X-Openstack-Request-Id
   - Content-Type: Content-Type_listing_resp
   - Date: Date
   - hash: hash
   - last_modified: last_modified
   - content_type: content_type
   - bytes: bytes_in_container_get
   - name: name_in_container_get
   - symlink_path: symlink_path


Response Example format=json
----------------------------

.. literalinclude:: samples/objects-list-http-response-json.txt
.. literalinclude:: samples/objects-list-response.json


Response Example format=xml
---------------------------

.. literalinclude:: samples/objects-list-http-response-xml.txt
.. literalinclude:: samples/objects-list-response.xml

Create container
================

.. rest_method::  PUT /v1/{account}/{container}

Creates a container.

You do not need to check whether a container already exists before
issuing a PUT operation because the operation is idempotent: It
creates a container or updates an existing container, as
appropriate.

To create, update, or delete a custom metadata item, use the ``X
-Container-Meta-{name}`` header, where ``{name}`` is the name of
the metadata item.

.. include:: metadata_header_syntax.inc
.. include:: metadata_header_encoding.inc

Example requests and responses:

- Create a container with no metadata:

  ::

     curl -i $publicURL/steven -X PUT -H "Content-Length: 0" -H "X-Auth-Token: $token"




  ::

     HTTP/1.1 201 Created
     Content-Length: 0
     Content-Type: text/html; charset=UTF-8
     X-Trans-Id: tx7f6b7fa09bc2443a94df0-0052d58b56
     X-Openstack-Request-Id: tx7f6b7fa09bc2443a94df0-0052d58b56
     Date: Tue, 14 Jan 2014 19:09:10 GMT


- Create a container with metadata:

  ::

     curl -i $publicURL/marktwain -X PUT -H "X-Auth-Token: $token" -H "X-Container-Meta-Book: TomSawyer"




  ::

     HTTP/1.1 201 Created
     Content-Length: 0
     Content-Type: text/html; charset=UTF-8
     X-Trans-Id: tx06021f10fc8642b2901e7-0052d58f37
     X-Openstack-Request-Id: tx06021f10fc8642b2901e7-0052d58f37
     Date: Tue, 14 Jan 2014 19:25:43 GMT

- Create a container with an ACL to allow anybody to get an object in the
  marktwain container:
  ::

     curl -i $publicURL/marktwain -X PUT -H "X-Auth-Token: $token" -H "X-Container-Read: .r:*"



  ::

     HTTP/1.1 201 Created
     Content-Length: 0
     Content-Type: text/html; charset=UTF-8
     X-Trans-Id: tx06021f10fc8642b2901e7-0052d58f37
     X-Openstack-Request-Id: tx06021f10fc8642b2901e7-0052d58f37
     Date: Tue, 14 Jan 2014 19:25:43 GMT

Normal response codes: 201, 202

Error response codes: 400, 404, 507

Request
-------

.. rest_parameters:: parameters.yaml

   - account: account
   - container: container
   - X-Auth-Token: X-Auth-Token
   - X-Service-Token: X-Service-Token
   - X-Container-Read: X-Container-Read
   - X-Container-Write: X-Container-Write
   - X-Container-Sync-To: X-Container-Sync-To
   - X-Container-Sync-Key: X-Container-Sync-Key
   - X-Versions-Location: X-Versions-Location
   - X-History-Location: X-History-Location
   - X-Container-Meta-name: X-Container-Meta-name_req
   - X-Container-Meta-Access-Control-Allow-Origin: X-Container-Meta-Access-Control-Allow-Origin
   - X-Container-Meta-Access-Control-Max-Age: X-Container-Meta-Access-Control-Max-Age
   - X-Container-Meta-Access-Control-Expose-Headers: X-Container-Meta-Access-Control-Expose-Headers
   - X-Container-Meta-Quota-Bytes: X-Container-Meta-Quota-Bytes
   - X-Container-Meta-Quota-Count: X-Container-Meta-Quota-Count
   - X-Container-Meta-Temp-URL-Key: X-Container-Meta-Temp-URL-Key_req
   - X-Container-Meta-Temp-URL-Key-2: X-Container-Meta-Temp-URL-Key-2_req
   - X-Trans-Id-Extra: X-Trans-Id-Extra
   - X-Storage-Policy: X-Storage-Policy


Response Parameters
-------------------

.. rest_parameters:: parameters.yaml

   - Date: Date
   - X-Timestamp: X-Timestamp
   - Content-Length: Content-Length_cud_resp
   - Content-Type: Content-Type_cud_resp
   - X-Trans-Id: X-Trans-Id
   - X-Openstack-Request-Id: X-Openstack-Request-Id






Create, update, or delete container metadata
============================================

.. rest_method::  POST /v1/{account}/{container}

Creates, updates, or deletes custom metadata for a container.

To create, update, or delete a custom metadata item, use the ``X
-Container-Meta-{name}`` header, where ``{name}`` is the name of
the metadata item.

.. include:: metadata_header_syntax.inc
.. include:: metadata_header_encoding.inc

Subsequent requests for the same key and value pair overwrite the
previous value.

To delete container metadata, send an empty value for that header,
such as for the ``X-Container-Meta-Book`` header. If the tool you
use to communicate with Object Storage, such as an older version of
cURL, does not support empty headers, send the ``X-Remove-
Container-Meta-{name}`` header with an arbitrary value. For
example, ``X-Remove-Container-Meta-Book: x``. The operation ignores
the arbitrary value.

If the container already has other custom metadata items, a request
to create, update, or delete metadata does not affect those items.

Example requests and responses:

- Create container metadata:

  ::

     curl -i $publicURL/marktwain -X POST -H "X-Auth-Token: $token" -H "X-Container-Meta-Author: MarkTwain" -H "X-Container-Meta-Web-Directory-Type: text/directory" -H "X-Container-Meta-Century: Nineteenth"




  ::

     HTTP/1.1 204 No Content
     Content-Length: 0
     Content-Type: text/html; charset=UTF-8
     X-Trans-Id: tx05dbd434c651429193139-0052d82635
     X-Openstack-Request-Id: tx05dbd434c651429193139-0052d82635
     Date: Thu, 16 Jan 2014 18:34:29 GMT


- Update container metadata:

  ::

     curl -i $publicURL/marktwain -X POST -H "X-Auth-Token: $token" -H "X-Container-Meta-Author: SamuelClemens"




  ::

     HTTP/1.1 204 No Content
     Content-Length: 0
     Content-Type: text/html; charset=UTF-8
     X-Trans-Id: txe60c7314bf614bb39dfe4-0052d82653
     X-Openstack-Request-Id: txe60c7314bf614bb39dfe4-0052d82653
     Date: Thu, 16 Jan 2014 18:34:59 GMT


- Delete container metadata:

  ::

     curl -i $publicURL/marktwain -X POST -H "X-Auth-Token: $token" -H "X-Remove-Container-Meta-Century: x"




  ::

     HTTP/1.1 204 No Content
     Content-Length: 0
     Content-Type: text/html; charset=UTF-8
     X-Trans-Id: tx7997e18da2a34a9e84ceb-0052d826d0
     X-Openstack-Request-Id: tx7997e18da2a34a9e84ceb-0052d826d0
     Date: Thu, 16 Jan 2014 18:37:04 GMT


If the request succeeds, the operation returns the ``No Content
(204)`` response code.

To confirm your changes, issue a show container metadata request.

Normal response codes: 204

Error response codes: 404


Request
-------

.. rest_parameters:: parameters.yaml

   - account: account
   - container: container
   - X-Auth-Token: X-Auth-Token
   - X-Service-Token: X-Service-Token
   - X-Container-Read: X-Container-Read
   - X-Remove-Container-name: X-Remove-Container-name
   - X-Container-Write: X-Container-Write
   - X-Container-Sync-To: X-Container-Sync-To
   - X-Container-Sync-Key: X-Container-Sync-Key
   - X-Versions-Location: X-Versions-Location
   - X-History-Location: X-History-Location
   - X-Remove-Versions-Location: X-Remove-Versions-Location
   - X-Remove-History-Location: X-Remove-History-Location
   - X-Container-Meta-name: X-Container-Meta-name_req
   - X-Container-Meta-Access-Control-Allow-Origin: X-Container-Meta-Access-Control-Allow-Origin
   - X-Container-Meta-Access-Control-Max-Age: X-Container-Meta-Access-Control-Max-Age
   - X-Container-Meta-Access-Control-Expose-Headers: X-Container-Meta-Access-Control-Expose-Headers
   - X-Container-Meta-Quota-Bytes: X-Container-Meta-Quota-Bytes
   - X-Container-Meta-Quota-Count: X-Container-Meta-Quota-Count
   - X-Container-Meta-Web-Directory-Type: X-Container-Meta-Web-Directory-Type
   - X-Container-Meta-Temp-URL-Key: X-Container-Meta-Temp-URL-Key_req
   - X-Container-Meta-Temp-URL-Key-2: X-Container-Meta-Temp-URL-Key-2_req
   - X-Trans-Id-Extra: X-Trans-Id-Extra


Response Parameters
-------------------

.. rest_parameters:: parameters.yaml

   - Date: Date
   - X-Timestamp: X-Timestamp
   - Content-Length: Content-Length_cud_resp
   - Content-Type: Content-Type_cud_resp
   - X-Trans-Id: X-Trans-Id
   - X-Openstack-Request-Id: X-Openstack-Request-Id





Show container metadata
=======================

.. rest_method::  HEAD /v1/{account}/{container}

Shows container metadata, including the number of objects and the total bytes of all objects stored in the container.

Show container metadata request:

::

   curl -i $publicURL/marktwain -X HEAD -H "X-Auth-Token: $token"




::

   HTTP/1.1 204 No Content
   Content-Length: 0
   X-Container-Object-Count: 1
   Accept-Ranges: bytes
   X-Container-Meta-Book: TomSawyer
   X-Timestamp: 1389727543.65372
   X-Container-Meta-Author: SamuelClemens
   X-Container-Bytes-Used: 14
   Content-Type: text/plain; charset=utf-8
   X-Trans-Id: tx0287b982a268461b9ec14-0052d826e2
   X-Openstack-Request-Id: tx0287b982a268461b9ec14-0052d826e2
   Date: Thu, 16 Jan 2014 18:37:22 GMT


If the request succeeds, the operation returns the ``No Content
(204)`` response code.

Normal response codes: 204


Request
-------

.. rest_parameters:: parameters.yaml

   - account: account
   - container: container
   - X-Auth-Token: X-Auth-Token
   - X-Service-Token: X-Service-Token
   - X-Newest: X-Newest
   - X-Trans-Id-Extra: X-Trans-Id-Extra


Response Parameters
-------------------

.. rest_parameters:: parameters.yaml

   - X-Container-Meta-name: X-Container-Meta-name
   - Content-Length: Content-Length_cud_resp
   - X-Container-Object-Count: X-Container-Object-Count
   - X-Container-Bytes-Used: X-Container-Bytes-Used
   - X-Container-Write: X-Container-Write_resp
   - X-Container-Meta-Quota-Bytes: X-Container-Meta-Quota-Bytes_resp
   - X-Container-Meta-Quota-Count: X-Container-Meta-Quota-Count_resp
   - Accept-Ranges: Accept-Ranges
   - X-Container-Read: X-Container-Read_resp
   - X-Container-Meta-Access-Control-Expose-Headers: X-Container-Meta-Access-Control-Expose-Headers
   - X-Container-Meta-Temp-URL-Key: X-Container-Meta-Temp-URL-Key_resp
   - X-Container-Meta-Temp-URL-Key-2: X-Container-Meta-Temp-URL-Key-2_resp
   - X-Timestamp: X-Timestamp
   - X-Container-Meta-Access-Control-Allow-Origin: X-Container-Meta-Access-Control-Allow-Origin
   - X-Container-Meta-Access-Control-Max-Age: X-Container-Meta-Access-Control-Max-Age
   - X-Container-Sync-Key: X-Container-Sync-Key_resp
   - X-Container-Sync-To: X-Container-Sync-To_resp
   - Date: Date
   - X-Trans-Id: X-Trans-Id
   - X-Openstack-Request-Id: X-Openstack-Request-Id
   - Content-Type: Content-Type_cud_resp
   - X-Versions-Location: X-Versions-Location_resp
   - X-History-Location: X-History-Location_resp
   - X-Storage-Policy: X-Storage-Policy





Delete container
================

.. rest_method::  DELETE /v1/{account}/{container}

Deletes an empty container.

This operation fails unless the container is empty. An empty
container has no objects.

Delete the ``steven`` container:

::

   curl -i $publicURL/steven -X DELETE -H "X-Auth-Token: $token"


If the container does not exist, the response is:

::

   HTTP/1.1 404 Not Found
   Content-Length: 70
   Content-Type: text/html; charset=UTF-8
   X-Trans-Id: tx4d728126b17b43b598bf7-0052d81e34
   X-Openstack-Request-Id: tx4d728126b17b43b598bf7-0052d81e34
   Date: Thu, 16 Jan 2014 18:00:20 GMT


If the container exists and the deletion succeeds, the response is:

::

   HTTP/1.1 204 No Content
   Content-Length: 0
   Content-Type: text/html; charset=UTF-8
   X-Trans-Id: txf76c375ebece4df19c84c-0052d81f14
   X-Openstack-Request-Id: txf76c375ebece4df19c84c-0052d81f14
   Date: Thu, 16 Jan 2014 18:04:04 GMT


If the container exists but is not empty, the response is:

::

   HTTP/1.1 409 Conflict
   Content-Length: 95
   Content-Type: text/html; charset=UTF-8
   X-Trans-Id: tx7782dc6a97b94a46956b5-0052d81f6b
   X-Openstack-Request-Id: tx7782dc6a97b94a46956b5-0052d81f6b
   Date: Thu, 16 Jan 2014 18:05:31 GMT
   <html>
   <h1>Conflict
   </h1>
   <p>There was a conflict when trying to complete your request.
   </p>
   </html>

Normal response codes: 204

Error response codes: 404, 409


Request
-------

.. rest_parameters:: parameters.yaml

   - account: account
   - container: container
   - X-Auth-Token: X-Auth-Token
   - X-Service-Token: X-Service-Token
   - X-Trans-Id-Extra: X-Trans-Id-Extra


Response Parameters
-------------------

.. rest_parameters:: parameters.yaml

   - Date: Date
   - X-Timestamp: X-Timestamp
   - Content-Length: Content-Length_cud_resp
   - Content-Type: Content-Type_cud_resp
   - X-Trans-Id: X-Trans-Id
   - X-Openstack-Request-Id: X-Openstack-Request-Id






