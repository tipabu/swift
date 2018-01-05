from swift.common.exceptions import APIVersionError
from swift.common.utils import split_path
from swift.proxy.server import Application
from swift.proxy.controllers.info import InfoController

class SIOApplication(Application):
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
            pass
        return None, d

def app_factory(global_conf, **local_conf):
	conf = global_conf.copy()
	conf.update(local_conf)
	return SIOApplication(conf)
