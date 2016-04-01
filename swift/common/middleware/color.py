# Copyright (c) 2016 OpenStack Foundation
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
from swift.common.swob import wsgify
from swift.common.utils import register_swift_info


def colorize(line, delimiter, ls_colors):
    if delimiter and ls_colors.get('di') and line.endswith(delimiter):
        return '\x1b[{color}m{line}\x1b[{reset}m{delim}'.format(
            color=ls_colors['di'], reset=ls_colors.get('rs') or '0',
            line=line[:-len(delimiter)], delim=delimiter)
    for key, color in ls_colors.items():
        if not key.startswith('*'):
            continue
        if line.endswith(key[1:]):
            return '\x1b[{color}m{line}\x1b[{reset}m'.format(
                color=color, reset=ls_colors.get('rs') or '0', line=line)
    return line


class ColorFilter(object):
    def __init__(self, app, default_ls_colors):
        self.app = app
        self.default_ls_colors = default_ls_colors

    @wsgify
    def __call__(self, req):
        ls_colors = req.headers.get('Ls-Colors', self.default_ls_colors)
        delim = req.params.get('delimiter')

        if not ls_colors:
            return self.app
        try:
            # account or container only
            req.split_path(2, 3)
        except ValueError:
            return self.app

        resp = req.get_response(self.app)
        if resp.headers['Content-Type'].startswith('text/plain'):
            ls_colors = dict(item.partition('=')[::2]
                             for item in ls_colors.split(':'))
            resp.body = '\n'.join(colorize(line, delim, ls_colors)
                                  for line in resp.body.split('\n'))
        return resp


def filter_factory(global_conf, **local_conf):
    conf = dict(global_conf, **local_conf)
    default_ls_colors = conf.get('default_ls_colors')
    register_swift_info('color', default_ls_colors=default_ls_colors)

    def color_filter(app):
        return ColorFilter(app, default_ls_colors)
    return color_filter
