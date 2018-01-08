import errno
import json
import math
import os
from six.moves.urllib.parse import unquote

from swift.common.constraints import valid_api_version
from swift.common.exceptions import APIVersionError
from swift.common.swob import HTTPOk, HTTPForbidden, HTTPNotFound
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
            containers = os.listdir(self.app.path(self.account_name))
            meta = self.app.read_meta(self.account_name)
        except OSError as e:
            self.app.logger.warning(e)
            if e.errno == errno.EACCES:
                return HTTPForbidden(request=req)
            if e.errno == errno.ENOENT:
                return HTTPNotFound(request=req)
            raise
        resp = HTTPOk(
            request=req, headers=meta, body=json.dumps([
                {'name': c} for c in containers]),
            content_type='application/json', charset='utf-8')
        resp.last_modified = math.ceil(float(meta['X-PUT-Timestamp']))
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

class SIOApplication(Application):
    def __init__(self, conf, *args, **kwargs):
        super(SIOApplication, self).__init__(conf, *args, **kwargs)
        self.root_dir = conf['root_dir']
        if not self.root_dir.startswith('/'):
            raise ValueError('root_dir must be an absolute path, not %r' %
                             self.root_dir)

        os.stat(self.root_dir)  # ensure it exists
        mkdirp(self.path('.__swift_metadata__'))

    def read_meta(self, account, container=None, obj=None):
        p = self.metapath(account, container, obj)
        try:
            fp = open(p, 'rb')
        except IOError as e:
            if e.errno == errno.ENOENT:
                s = os.stat(self.datapath(account, container, obj))
                if not obj:
                    return {
                        'X-PUT-Timestamp': Timestamp(s.st_ctime).internal,
                    }
                # TODO: if obj, at least try to guess content type from name
                return {}
        with fp:
            return json.load(fp)

    def write_meta(self, meta, account, container=None, obj=None):
        p = self.metapath(account, container, obj)
        mkdirp(os.dirname(p))
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
        return self.path(*args)

    def datapath(self, account, container=None, obj=None):
        if container is None and obj is None:
            args = (account, )
        elif obj is None:
            args = (account, container)
        elif container is not None:
            args = (account, container, obj)
        else:
            raise ValueError('cannot specify obj without container')
        return self.path(*args)

    def path(self, *args):
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
            pass
        elif account and not container and not obj:
            return AccountController, d
        return None, d

def app_factory(global_conf, **local_conf):
	conf = global_conf.copy()
	conf.update(local_conf)
	return SIOApplication(conf)
