..
      Copyright 2010-2012 OpenStack Foundation
      All Rights Reserved.

      Licensed under the Apache License, Version 2.0 (the "License"); you may
      not use this file except in compliance with the License. You may obtain
      a copy of the License at

          http://www.apache.org/licenses/LICENSE-2.0

      Unless required by applicable law or agreed to in writing, software
      distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
      WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
      License for the specific language governing permissions and limitations
      under the License.

=================================
Welcome to Swift's documentation!
=================================

Swift is a highly available, distributed, eventually consistent object/blob
store. Organizations can use Swift to store lots of data efficiently, safely, and cheaply.

This documentation is generated by the Sphinx toolkit and lives in the source
tree.  Additional documentation on Swift and other components of OpenStack can
be found on the `OpenStack wiki`_ and at http://docs.openstack.org.

.. _`OpenStack wiki`: http://wiki.openstack.org

.. note::

    If you're looking for associated projects that enhance or use Swift, please see the :ref:`associated_projects` page.


.. toctree::
    :maxdepth: 2

    getting_started

Overview and Concepts
=====================

.. toctree::
    :maxdepth: 1

    api/object_api_v1_overview
    overview_architecture
    overview_wsgi_reload
    overview_ring
    overview_ring_format
    overview_policies
    overview_reaper
    overview_auth
    overview_acl
    overview_replication
    ratelimit
    overview_large_objects
    overview_global_cluster
    overview_container_sync
    overview_expiring_objects
    cors
    crossdomain
    overview_erasure_code
    overview_encryption
    overview_backing_store
    overview_container_sharding
    ring_background
    ring_partpower
    associated_projects

Contributor Documentation
=========================

.. toctree::
    :maxdepth: 2

    contributor/contributing
    contributor/review_guidelines

Developer Documentation
=======================

.. toctree::
    :maxdepth: 1

    development_guidelines
    development_saio
    first_contribution_swift
    policies_saio
    development_auth
    development_middleware
    development_ondisk_backends
    development_watchers

Administrator Documentation
===========================

.. toctree::
    :maxdepth: 1

    deployment_guide
    apache_deployment_guide
    admin_guide
    replication_network
    logs
    ops_runbook/index
    admin/index
    install/index
    config/index


Object Storage v1 REST API Documentation
========================================

See `Complete Reference for the Object Storage REST API <https://docs.openstack.org/api-ref/object-store/>`_

The following provides supporting information for the REST API:

.. toctree::
    :maxdepth: 1

    api/object_api_v1_overview.rst
    api/discoverability.rst
    api/authentication.rst
    api/container_quotas.rst
    api/object_versioning.rst
    api/large_objects.rst
    api/temporary_url_middleware.rst
    api/form_post_middleware.rst
    api/use_content-encoding_metadata.rst
    api/use_the_content-disposition_metadata.rst
    api/pseudo-hierarchical-folders-directories.rst
    api/pagination.rst
    api/serialized-response-formats.rst
    api/static-website.rst
    api/object-expiration.rst
    api/bulk-delete.rst

S3 Compatibility Info
=====================

.. toctree::
    :maxdepth: 1

    s3_compat

OpenStack End User Guide
========================

The `OpenStack End User Guide <http://docs.openstack.org/user-guide>`_
has additional information on using Swift.
See the `Manage objects and containers <http://docs.openstack.org/user-guide/managing-openstack-object-storage-with-swift-cli.html>`_
section.


Source Documentation
====================

.. toctree::
    :maxdepth: 2

    ring
    proxy
    account
    container
    db
    object
    misc
    middleware
    audit_watchers


Indices and tables
==================

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`
