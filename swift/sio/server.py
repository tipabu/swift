import errno
import json
import math
import mimetypes
import os
from six.moves.urllib.parse import unquote

from swift.common import swob
from swift.common.constraints import valid_api_version
from swift.common.exceptions import APIVersionError
from swift.common.request_helpers import is_sys_or_user_meta
from swift.common.utils import split_path, public, Timestamp
from swift.proxy.server import Application
from swift.proxy.controllers.info import InfoController

def mkdirp(path):
    try:
        os.makedirs(path, mode=0700)
    except OSError as e:
        if e.errno != errno.EEXIST:
            raise

class Controller(object):
    server_type = 'swift-in-one'

    def __init__(self, app, **kwargs):
        self.app = app
        self.allowed_methods = ['PUT', 'GET', 'HEAD', 'DELETE', 'POST']

class AccountController(Controller):
    def __init__(self, app, account_name, **kwargs):
        super(AccountController, self).__init__(app)
        self.account_name = unquote(account_name)
        if not self.app.allow_account_management:
            self.allowed_methods.remove('PUT')
            self.allowed_methods.remove('DELETE')

    def GET(self, req):
        try:
            containers = os.listdir(self.app.datapath(self.account_name))
            meta = self.app.read_meta(self.account_name)
        except OSError as e:
            if e.errno == errno.EACCES:
                return swob.HTTPForbidden(request=req)
            if e.errno == errno.ENOENT:
                return swob.HTTPNotFound(request=req)
            raise
        container_list = []
        for c in sorted(containers):
             cm = self.app.read_meta(self.account_name, c)
             container_list.append({
                'name': c,
                'count': cm['X-Container-Object-Count'],
                'bytes': cm['X-Container-Bytes-Used'],
                'last_modified': Timestamp(cm['X-Backend-Put-Timestamp']).isoformat,
            })

        resp = swob.HTTPOk(
            request=req, headers=meta, body=json.dumps(container_list),
            content_type='application/json', charset='utf-8')
        return resp

    def HEAD(self, req):
        resp = self.GET(req)
        resp.body = ''
        return resp

    def PUT(self, req):
        asdf

    def POST(self, req):
        asdf

    def DELETE(self, req):
        asdf

class ContainerController(Controller):
    # Must be lower-case; screw container sync
    save_headers = ['x-container-read', 'x-container-write']

    def __init__(self, app, account_name, container_name, **kwargs):
        super(ContainerController, self).__init__(app)
        self.account_name = unquote(account_name)
        self.container_name = unquote(container_name)

    def GET(self, req):
        base = self.app.datapath(self.account_name, self.container_name)
        try:
            objects = []
            for path, dirs, files in os.walk(base):
                for f in files:
                    f = os.path.join(path, f)[len(base) + 1:]
                    fm = self.app.read_meta(self.account_name, self.container_name, f)
                    self.app.logger.info(fm)
                    objects.append({
                        'name': f,
                        'hash': fm['ETag'],
                        'bytes': fm['Content-Length'],
                        'content_type': fm['Content-Type'],
                        'last_modified': Timestamp(fm['X-Timestamp']).isoformat,
                    })
            meta = self.app.read_meta(self.account_name, self.container_name)
        except OSError as e:
            if e.errno == errno.EACCES:
                return swob.HTTPForbidden(request=req)
            if e.errno == errno.ENOENT:
                return swob.HTTPNotFound(request=req)
            raise
        meta['X-Backend-Timestamp'] = meta['X-Timestamp'] = req.headers.get(
            'X-Timestamp', Timestamp.now().internal)
        resp = swob.HTTPOk(
            request=req, headers=meta, body=json.dumps(objects),
            content_type='application/json', charset='utf-8')
        resp.last_modified = math.ceil(float(meta['X-Backend-Put-Timestamp']))
        return resp

    def HEAD(self, req):
        resp = self.GET(req)
        resp.body = ''
        return resp

    def PUT(self, req):
        created = True
        try:
            os.mkdir(self.app.datapath(self.account_name, self.container_name))
        except OSError as exc:
            if exc.errno != errno.EEXIST:
                raise
            created = False
        return self.POST(req, created)

    def POST(self, req, created=None):
        meta = self.app.read_meta(self.account_name, self.container_name)
        meta.update({
            key: value for key, value in req.headers.items()
            if key.lower() in self.save_headers
            or is_sys_or_user_meta('container', key)
        })
        meta.setdefault('X-Container-Object-Count', '0')
        meta.setdefault('X-Container-Bytes-Used', '0')
        self.app.write_meta(meta, self.account_name, self.container_name)
        if created is None:
            return swob.HTTPNoContent(request=req)
        elif created:
            return swob.HTTPCreated(request=req)
        else:
            return swob.HTTPAccepted(request=req)

    def DELETE(self, req):
        try:
            os.rmdir(self.app.datapath(self.account_name, self.container_name))
        except OSError as e:
            if e.errno in (errno.ENOTEMPTY, errno.ENOTDIR):
                resp = swob.HTTPConflict(request=req)
            elif e.errno == errno.ENOENT:
                resp = swob.HTTPNotFound(request=req)
            elif e.errno == errno.EACCES:
                resp = swob.HTTPForbidden(request=req)
            else:
                raise
        else:
            resp = swob.HTTPNoContent(request=req)
        self.app.logger.info('%r', resp.status_int)
        if resp.status_int in (204, 404):
            # Clean up metadata
            container_metapath = self.app.metapath(self.account_name, self.container_name)
            object_metadir = os.path.dirname(self.app.metapath(self.account_name, self.container_name, 'dummy'))
            for op, p in (
                    (os.unlink, container_metapath),
                    (os.rmdir, object_metadir)):
                try:
                    op(p)
                except OSError:
                    pass
        return resp

class SIOApplication(Application):
    def __init__(self, conf, *args, **kwargs):
        super(SIOApplication, self).__init__(conf, *args, **kwargs)
        self.root_dir = conf['root_dir']
        if not self.root_dir.startswith('/'):
            raise ValueError('root_dir must be an absolute path, not %r' %
                             self.root_dir)

        os.stat(self.root_dir)  # check that it exists
        mkdirp(self._path('.__swift_metadata__'))

    def read_meta(self, account, container=None, obj=None):
        p = self.metapath(account, container, obj)
        try:
            fp = open(p, 'rb')
        except IOError as e:
            if e.errno == errno.ENOENT:
                p = self.datapath(account, container, obj)
                s = os.stat(p)
                # do we need x-backend-recheck-*-existence headers?
                if not container:
                    return {
                        'X-Account-Bytes-Used': '0',
                        'X-Account-Object-Count': '0',
                        'X-Account-Container-Count': len(os.listdir(p)),
                        # *Maybe* we fake out per-policy stats for the default policy?
                        'X-Timestamp': '',
                    }
                if not obj:
                    return {
                        'X-Backend-Put-Timestamp': Timestamp(s.st_ctime).internal,
                        'X-Backend-Delete-Timestamp': '',
                        'X-Backend-Status-Changed-At': '',
                        'X-Container-Bytes-Used': '0',
                        'X-Container-Object-Count': '0',
                        # *Maybe* we fake out x-storage-policy/x-backend-storage-policy-index to be the default?
                    }
                guessed_type, _junk = mimetypes.guess_type(obj)
                return {
                    'ETag': '',  # We're not opening this up just for meta
                    'Content-Length': str(s.st_size),
                    'X-Timestamp': Timestamp(s.st_mtime).internal,
                    'Content-Type': guessed_type or 'application/octet-stream',
                }
            raise
        with fp:
            return json.load(fp)

    def write_meta(self, meta, account, container=None, obj=None):
        p = self.metapath(account, container, obj)
        mkdirp(os.path.dirname(p))
        with open(p, 'wb') as fp:
            json.dump(meta, fp)

    def metapath(self, account, container=None, obj=None):
        if container is None and obj is None:
            args = ('.__swift_metadata__', 'account', account)
        elif obj is None:
            args = ('.__swift_metadata__', 'container', account, container)
        elif container is not None:
            args = ('.__swift_metadata__', 'object', account, container, obj)
        else:
            raise ValueError('cannot specify obj without container')
        return self._path(*args)

    def datapath(self, account, container=None, obj=None):
        if container is None and obj is None:
            args = (account, )
        elif obj is None:
            args = (account, container)
        elif container is not None:
            args = (account, container, obj)
        else:
            raise ValueError('cannot specify obj without container')
        return self._path(*args)

    def _path(self, *args):
        p = os.path.join(self.root_dir, *args)
        if any(x in ('.', '..', '') for x in p[1:].split('/')):
            raise ValueError('%r has invalid path components' % p)
        return p

    def load_rings(self, *args):
        pass  # filesystem-only -> no rings necessary!

    def get_controller(self, req):
        if req.path == '/info':
            d = dict(version=None,
                     expose_info=self.expose_info,
                     disallowed_sections=self.disallowed_sections,
                     admin_key=self.admin_key)
            return InfoController, d

        version, account, container, obj = split_path(req.path, 1, 4, True)
        d = dict(version=version,
                 account_name=account,
                 container_name=container,
                 object_name=obj)
        if account and not valid_api_version(version):
            raise APIVersionError('Invalid path')

        if obj and container and account:
            pass
        elif container and account:
            return ContainerController, d
        elif account and not container and not obj:
            return AccountController, d
        return None, d

def app_factory(global_conf, **local_conf):
	conf = global_conf.copy()
	conf.update(local_conf)
	return SIOApplication(conf)
