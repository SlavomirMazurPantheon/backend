# Copyright The IETF Trust 2020, All Rights Reserved
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

__author__ = "Miroslav Kovac"
__copyright__ = "Copyright The IETF Trust 2020, All Rights Reserved"
__license__ = "Apache License, Version 2.0"
__email__ = "miroslav.kovac@pantheon.tech"

import collections
import errno
import io
import json
import os
import re
import subprocess
import sys
from copy import deepcopy

import jinja2
import requests
from flask import Blueprint, make_response, jsonify, abort, request, Response
from pyang import plugin
from pyang.plugins.tree import emit_tree

import api.yangSearch.elasticsearchIndex as inde
from api.globalConfig import yc_gc
from utility.util import get_curr_dir
from utility.yangParser import create_context


class YcSearch(Blueprint):

    def __init__(self, name, import_name, static_folder=None, static_url_path=None, template_folder=None,
                 url_prefix=None, subdomain=None, url_defaults=None, root_path=None):
        super().__init__(name, import_name, static_folder, static_url_path, template_folder, url_prefix, subdomain,
                         url_defaults, root_path)


app = YcSearch('ycSearch', __name__)


### ROUTE ENDPOINT DEFINITIONS ###
@app.route('/fast', methods=['POST'])
def fast_search():
    """Search through the YANG keyword index for a given search pattern.
       The arguments are a payload specifying search options and filters.
    """
    if not request.json:
        abort(400, description='No input data')

    limit = 1000000
    payload = request.json
    yc_gc.LOGGER.info(payload)
    if 'search' not in payload:
        abort(400, description='You must specify a "search" argument')
    try:
        count = 0
        search_res, limit_reached = inde.do_search(payload, yc_gc.es_host,
                                    yc_gc.es_port, yc_gc.es_aws, yc_gc.elk_credentials,
                                    yc_gc.LOGGER)
        if search_res is None and limit_reached is None:
            return abort(400, description='Search is too broad. Please search for something more specific')
        res = []
        found_modules = {}
        rejects = []
        not_founds = []
        errors = []

        for row in search_res:
            res_row = {}
            res_row['node'] = row['node']
            m_name = row['module']['name']
            m_revision = row['module']['revision']
            m_organization = row['module']['organization']
            mod_sig = '{}@{}/{}'.format(m_name, m_revision, m_organization)
            if mod_sig in rejects:
                continue

            mod_meta = None
            try:
                if mod_sig not in not_founds:
                    if mod_sig in found_modules:
                        mod_meta = found_modules[mod_sig]
                    else:
                        mod_meta = search_module(m_name, m_revision, m_organization)
                        if mod_meta.status_code == 404 and m_revision.endswith('02-28'):
                            mod_meta = search_module(m_name, m_revision.replace('02-28', '02-29'), m_organization)
                        if mod_meta.status_code == 404:
                            not_founds.append(mod_sig)
                            yc_gc.LOGGER.error('index search module {}@{} not found but exist in elasticsearch'.format(m_name, m_revision))
                            res_row = {'module': {'error': 'no {}@{} in API'.format(m_name, m_revision)}}
                            res.append(res_row)
                            continue
                        else:
                            mod_meta = mod_meta.json['module'][0]
                            found_modules[mod_sig] = mod_meta

                if 'include-mibs' not in payload or payload['include-mibs'] is False:
                    if re.search('yang:smiv2:', mod_meta.get('namespace')):
                        rejects.append(mod_sig)
                        continue

                if mod_meta is not None and 'yang-versions' in payload and len(payload['yang-versions']) > 0:
                    if mod_meta.get('yang-version') not in payload['yang-versions']:
                        rejects.append(mod_sig)
                        continue

                if mod_meta is not None:
                    if 'filter' not in payload or 'module-metadata' not in payload['filter']:
                        # If the filter is not specified, return all
                        # fields.
                        res_row['module'] = mod_meta
                    elif 'module-metadata' in payload['filter']:
                        res_row['module'] = {}
                        for field in payload['filter']['module-metadata']:
                            if field in mod_meta:
                                res_row['module'][field] = mod_meta[field]
            except Exception as e:
                count -= 1
                if mod_sig not in errors:
                    res_row['module'] = {
                        'error': 'Search failed at {}: {}'.format(mod_sig, e)}
                    errors.append(mod_sig)

            if not filter_using_api(res_row, payload):
                count += 1
                res.append(res_row)
            else:
                rejects.append(mod_sig)
            if count >= limit:
                break
        return jsonify({'results': res, 'limit_reched': limit_reached})
    except Exception as e:
        return make_response(jsonify({'error': str(e)}), 500)


@app.route('/search/<path:value>', methods=['GET'])
def search(value):
    """Search for a specific leaf from yang-catalog.yang module in modules
    branch. The key searched is defined in @module_keys variable.
            Arguments:
                :param value: (str) path that contains one of the @module_keys and
                    ends with /value searched for
                :return response to the request.
    """
    path = value
    yc_gc.LOGGER.info('Searching for {}'.format(value))
    split = value.split('/')[:-1]
    key = '/'.join(value.split('/')[:-1])
    value = value.split('/')[-1]
    module_keys = ['ietf/ietf-wg', 'maturity-level', 'document-name', 'author-email', 'compilation-status', 'namespace',
                   'conformance-type', 'module-type', 'organization', 'yang-version', 'name', 'revision', 'tree-type',
                   'belongs-to', 'generated-from', 'expires', 'expired', 'prefix', 'reference']
    for module_key in module_keys:
        if key == module_key:
            data = modules_data().get('module', {})
            if data is None:
                return abort(404, description='No module found in confd database')
            passed_data = []
            for module in data:
                count = -1
                process(module, passed_data, value, module, split, count)

            if len(passed_data) > 0:
                modules = json.JSONDecoder(object_pairs_hook=collections.OrderedDict) \
                    .decode(json.dumps(passed_data))
                return Response(json.dumps({
                    'yang-catalog:modules': {
                        'module': modules
                    }
                }), mimetype='application/json')
            else:
                return abort(404, description='No module found using provided input data')
    return abort(400, description='Search on path {} is not supported'.format(path))


@app.route('/search-filter/<leaf>', methods=['POST'])
def rpc_search_get_one(leaf):
    rpc = request.json
    if rpc.get('input'):
        recursive = rpc['input'].get('recursive')
    else:
        return abort(404, description='Json must start with root element input')
    if recursive:
        rpc['input'].pop('recursive')
    response = rpc_search(rpc)
    modules = json.loads(response.get_data(as_text=True)).get('yang-catalog:modules')
    if modules is None:
        return abort(404, description='No module found in confd database')
    modules = modules.get('module')
    if modules is None:
        return abort(404, description='No module found in confd database')
    output = set()
    resolved = set()
    for module in modules:
        if recursive:
            search_recursive(output, module, leaf, resolved)
        meta_data = module.get(leaf)
        output.add(meta_data)
    if None in output:
        output.remove(None)
    if len(output) == 0:
        return abort(404, description='No module found using provided input data')
    else:
        return Response(json.dumps({'output': {leaf: list(output)}}),
                        mimetype='application/json', status=201)


@app.route('/search-filter', methods=['POST'])
def rpc_search(body=None):
    if body is None:
        body = request.json
    yc_gc.LOGGER.info('Searching and filtering modules based on RPC {}'
                .format(json.dumps(body)))
    data = modules_data().get('module', {})
    body = body.get('input')
    if body:
        partial = body.get('partial')
        if partial is None:
            partial = False
        passed_modules = []
        if partial:
            for module in data:
                passed = True
                if 'dependencies' in body:
                    submodules = deepcopy(module.get('dependencies'))
                    if submodules is None:
                        continue
                    for sub in body['dependencies']:
                        found = True
                        name = sub.get('name')
                        revision = sub.get('revision')
                        schema = sub.get('schema')
                        for submodule in submodules:
                            found = True
                            if name:
                                if name not in submodule['name']:
                                    found = False
                            if not found:
                                continue
                            if revision:
                                if revision not in submodule['revision']:
                                    found = False
                            if not found:
                                continue
                            if schema:
                                if schema not in submodule['schema']:
                                    found = False
                            if found:
                                break

                        if not found:
                            passed = False
                            break
                if not passed:
                    continue
                if 'dependents' in body:
                    submodules = deepcopy(module.get('dependents'))
                    if submodules is None:
                        continue
                    for sub in body['dependents']:
                        found = True
                        name = sub.get('name')
                        revision = sub.get('revision')
                        schema = sub.get('schema')
                        for submodule in submodules:
                            found = True
                            if name:
                                if name not in submodule['name']:
                                    found = False
                            if not found:
                                continue
                            if revision:
                                if revision not in submodule['revision']:
                                    found = False
                            if not found:
                                continue
                            if schema:
                                if schema not in submodule['schema']:
                                    found = False
                            if found:
                                break

                        if not found:
                            passed = False
                            break
                if not passed:
                    continue
                if 'submodule' in body:
                    submodules = deepcopy(module.get('submodule'))
                    if submodules is None:
                        continue
                    for sub in body['submodule']:
                        found = True
                        name = sub.get('name')
                        revision = sub.get('revision')
                        schema = sub.get('schema')
                        for submodule in submodules:
                            found = True
                            if name:
                                if name not in submodule['name']:
                                    found = False
                            if not found:
                                continue
                            if revision:
                                if revision not in submodule['revision']:
                                    found = False
                            if not found:
                                continue
                            if schema:
                                if schema not in submodule['schema']:
                                    found = False
                            if found:
                                break

                        if not found:
                            passed = False
                            break
                if not passed:
                    continue
                if 'implementations' in body:
                    implementations = deepcopy(module.get('implementations'))
                    if implementations is None:
                        continue
                    passed = True
                    for imp in body['implementations']['implementation']:
                        if not passed:
                            break
                        for leaf in imp:
                            found = False
                            impls = []
                            if leaf == 'deviation':
                                for implementation in implementations[
                                    'implementation']:
                                    deviations = implementation.get('deviation')
                                    if deviations is None:
                                        continue
                                    for dev in imp[leaf]:
                                        found = True
                                        name = dev.get('name')
                                        revision = dev.get('revision')
                                        for deviation in deviations:
                                            found = True
                                            if name:
                                                if name not in deviation['name']:
                                                    found = False
                                            if not found:
                                                continue
                                            if revision:
                                                if revision not in deviation['revision']:
                                                    found = False
                                            if found:
                                                break
                                        if not found:
                                            break
                                    if not found:
                                        continue
                                    else:
                                        impls.append(implementation)
                                if not found:
                                    passed = False
                                    break
                            elif leaf == 'feature':
                                for implementation in implementations['implementation']:
                                    if implementation.get(leaf) is None:
                                        continue
                                    if imp[leaf] in implementation[leaf]:
                                        found = True
                                        impls.append(implementation)
                                        continue
                                if not found:
                                    passed = False
                            else:
                                for implementation in implementations['implementation']:
                                    if implementation.get(leaf) is None:
                                        continue
                                    if imp[leaf] in implementation[leaf]:
                                        found = True
                                        impls.append(implementation)
                                        continue
                                if not found:
                                    passed = False
                            if not passed:
                                break
                            implementations['implementation'] = impls
                if not passed:
                    continue
                for leaf in body:
                    if leaf != 'implementations' and leaf != 'submodule':
                        module_leaf = module.get(leaf)
                        if module_leaf:
                            if body[leaf] not in module_leaf:
                                passed = False
                                break
                if passed:
                    passed_modules.append(module)
        else:
            for module in data:
                passed = True
                if 'dependencies' in body:
                    submodules = deepcopy(module.get('dependencies'))
                    if submodules is None:
                        continue
                    for sub in body['dependencies']:
                        found = True
                        name = sub.get('name')
                        revision = sub.get('revision')
                        schema = sub.get('schema')
                        for submodule in submodules:
                            found = True
                            if name:
                                if name != submodule['name']:
                                    found = False
                            if not found:
                                continue
                            if revision:
                                if revision != submodule['revision']:
                                    found = False
                            if not found:
                                continue
                            if schema:
                                if schema != submodule['schema']:
                                    found = False
                            if found:
                                break

                        if not found:
                            passed = False
                            break
                if not passed:
                    continue
                if 'dependents' in body:
                    submodules = deepcopy(module.get('dependents'))
                    if submodules is None:
                        continue
                    for sub in body['dependents']:
                        found = True
                        name = sub.get('name')
                        revision = sub.get('revision')
                        schema = sub.get('schema')
                        for submodule in submodules:
                            found = True
                            if name:
                                if name != submodule['name']:
                                    found = False
                            if not found:
                                continue
                            if revision:
                                if revision!= submodule['revision']:
                                    found = False
                            if not found:
                                continue
                            if schema:
                                if schema != submodule['schema']:
                                    found = False
                            if found:
                                break

                        if not found:
                            passed = False
                            break
                if not passed:
                    continue
                if 'submodule' in body:
                    submodules = deepcopy(module.get('submodule'))
                    if submodules is None:
                        continue
                    for sub in body['submodule']:
                        found = True
                        name = sub.get('name')
                        revision = sub.get('revision')
                        schema = sub.get('schema')
                        for submodule in submodules:
                            found = True
                            if name:
                                if name != submodule['name']:
                                    found = False
                            if not found:
                                continue
                            if revision:
                                if revision != submodule['revision']:
                                    found = False
                            if not found:
                                continue
                            if schema:
                                if schema != submodule['schema']:
                                    found = False
                            if found:
                                break

                        if not found:
                            passed = False
                            break
                if not passed:
                    continue
                if 'implementations' in body:
                    implementations = deepcopy(module.get('implementations'))
                    if implementations is None:
                        continue
                    passed = True
                    for imp in body['implementations']['implementation']:
                        if not passed:
                            break
                        for leaf in imp:
                            found = False
                            impls = []
                            if leaf == 'deviation':
                                for implementation in implementations[
                                    'implementation']:
                                    deviations = implementation.get('deviation')
                                    if deviations is None:
                                        continue
                                    for dev in imp[leaf]:
                                        found = True
                                        name = dev.get('name')
                                        revision = dev.get('revision')
                                        for deviation in deviations:
                                            found = True
                                            if name:
                                                if name != deviation['name']:
                                                    found = False
                                            if not found:
                                                continue
                                            if revision:
                                                if revision != deviation['revision']:
                                                    found = False
                                            if found:
                                                break
                                        if not found:
                                            break
                                    if not found:
                                        continue
                                    else:
                                        impls.append(implementation)
                                if not found:
                                    passed = False
                                    break
                            elif leaf == 'feature':
                                for implementation in implementations['implementation']:
                                    if implementation.get(leaf) is None:
                                        continue
                                    if imp[leaf] == implementation[leaf]:
                                        found = True
                                        impls.append(implementation)
                                        continue
                                if not found:
                                    passed = False
                            else:
                                for implementation in implementations['implementation']:
                                    if implementation.get(leaf) is None:
                                        continue
                                    if imp[leaf] == implementation[leaf]:
                                        found = True
                                        impls.append(implementation)
                                        continue
                                if not found:
                                    passed = False
                            if not passed:
                                break
                            implementations['implementation'] = impls
                if not passed:
                    continue
                for leaf in body:
                    if (leaf != 'implementations' and leaf != 'submodule'
                        and leaf != 'dependencies' and leaf != 'dependents'):
                        if body[leaf] != module.get(leaf):
                            passed = False
                            break
                if passed:
                    passed_modules.append(module)
        if len(passed_modules) > 0:
            modules = json.JSONDecoder(object_pairs_hook=collections.OrderedDict) \
                .decode(json.dumps(passed_modules))
            return Response(json.dumps({
                'yang-catalog:modules': {
                    'module': modules
                }
            }), mimetype='application/json')
        else:
            return abort(404, description='No modules found with provided input')
    else:
        return abort(400, description='body request has to start with "input" container')


@app.route('/contributors', methods=['GET'])
def get_organizations():
    orgs = set()
    data = modules_data().get('module', {})
    for mod in data:
        if mod['organization'] != 'example' and mod['organization'] != 'missing element':
            orgs.add(mod['organization'])
    orgs = list(orgs)
    resp = make_response(jsonify({'contributors': orgs}), 200)
    resp.headers['Access-Control-Allow-Origin'] = '*'
    return resp


@app.route('/services/file1=<f1>@<r1>/check-update-from/file2=<f2>@<r2>', methods=['GET'])
def create_update_from(f1, r1, f2, r2):
    try:
        os.makedirs(get_curr_dir(__file__) + '/temp')
    except OSError as e:
        # be happy if someone already created the path
        if e.errno != errno.EEXIST:
            return 'Server error - could not create directory'
    schema1 = '{}/{}@{}.yang'.format(yc_gc.save_file_dir, f1, r1)
    schema2 = '{}/{}@{}.yang'.format(yc_gc.save_file_dir, f2, r2)
    arguments = ['pyang', '-p',
                 yc_gc.yang_models,
                 schema1, '--check-update-from',
                 schema2]
    pyang = subprocess.Popen(arguments,
                             stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE)
    stdout, stderr = pyang.communicate()
    if sys.version_info >= (3, 4):
        stderr = stderr.decode(encoding='utf-8', errors='strict')
    return '<html><body><pre>{}</pre></body></html>'.format(stderr)


@app.route('/services/diff-file/file1=<f1>@<r1>/file2=<f2>@<r2>', methods=['GET'])
def create_diff_file(f1, r1, f2, r2):
    try:
        os.makedirs(get_curr_dir(__file__) + '/temp')
    except OSError as e:
        # be happy if someone already created the path
        if e.errno != errno.EEXIST:
            return 'Server error - could not create directory'
    schema1 = '{}/{}@{}.yang'.format(yc_gc.save_file_dir, f1, r1)
    schema2 = '{}/{}@{}.yang'.format(yc_gc.save_file_dir, f2, r2)

    arguments = ['cat', schema1]
    cat = subprocess.Popen(arguments, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE)
    stdout, stderr = cat.communicate()
    if sys.version_info >= (3, 4):
        stdout = stdout.decode(encoding='utf-8', errors='strict')
    file_name1 = 'schema1-file-diff.txt'
    with open('{}/{}'.format(yc_gc.diff_file_dir, file_name1), 'w+') as f:
        f.write('<pre>{}</pre>'.format(stdout))
    arguments = ['cat', schema2]
    cat = subprocess.Popen(arguments, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE)
    stdout, stderr = cat.communicate()
    if sys.version_info >= (3, 4):
        stdout = stdout.decode(encoding='utf-8', errors='strict')
    file_name2 = 'schema2-file-diff.txt'
    with open('{}/{}'.format(yc_gc.diff_file_dir, file_name2), 'w+') as f:
        f.write('<pre>{}</pre>'.format(stdout))
    tree1 = '{}/compatibility/{}'.format(yc_gc.my_uri, file_name1)
    tree2 = '{}/compatibility/{}'.format(yc_gc.my_uri, file_name2)
    diff_url = ('https://www.ietf.org/rfcdiff/rfcdiff.pyht?url1={}&url2={}'
                .format(tree1, tree2))
    response = requests.get(diff_url)
    os.remove(yc_gc.diff_file_dir + '/' + file_name1)
    os.remove(yc_gc.diff_file_dir + '/' + file_name2)
    return '<html><body>{}</body></html>'.format(response.text)


@app.route('/services/diff-tree/file1=<f1>@<r1>/file2=<f2>@<r2>', methods=['GET'])
def create_diff_tree(f1, r1, f2, r2):
    try:
        os.makedirs(get_curr_dir(__file__) + '/temp')
    except OSError as e:
        # be happy if someone already created the path
        if e.errno != errno.EEXIST:
            return 'Server error - could not create directory'
    schema1 = '{}/{}@{}.yang'.format(yc_gc.save_file_dir, f1, r1)
    schema2 = '{}/{}@{}.yang'.format(yc_gc.save_file_dir, f2, r2)
    plugin.plugins = []
    plugin.init([])
    ctx = create_context('{}:{}'.format(yc_gc.yang_models, yc_gc.save_file_dir))
    ctx.opts.lint_namespace_prefixes = []
    ctx.opts.lint_modulename_prefixes = []
    ctx.lax_quote_checks = True
    ctx.lax_xpath_checks = True
    for p in plugin.plugins:
        p.setup_ctx(ctx)

    with open(schema1, 'r') as ff:
        a = ctx.add_module(schema1, ff.read())
    ctx.errors = []
    if ctx.opts.tree_path is not None:
        path = ctx.opts.tree_path.split('/')
        if path[0] == '':
            path = path[1:]
    else:
        path = None

    ctx.validate()
    f = io.StringIO()
    emit_tree(ctx, [a], f, ctx.opts.tree_depth, ctx.opts.tree_line_length, path)
    stdout = f.getvalue()
    file_name1 = 'schema1-tree-diff.txt'
    full_path_file1 = '{}/{}'.format(yc_gc.diff_file_dir, file_name1)
    with open(full_path_file1, 'w+') as ff:
        ff.write('<pre>{}</pre>'.format(stdout))
    with open(schema2, 'r') as ff:
        a = ctx.add_module(schema2, ff.read())
    ctx.validate()
    f = io.StringIO()
    emit_tree(ctx, [a], f, ctx.opts.tree_depth, ctx.opts.tree_line_length, path)
    stdout = f.getvalue()
    file_name2 = 'schema2-tree-diff.txt'
    full_path_file2 = '{}/{}'.format(yc_gc.diff_file_dir, file_name2)
    with open(full_path_file2, 'w+') as ff:
        ff.write('<pre>{}</pre>'.format(stdout))
    tree1 = '{}/compatibility/{}'.format(yc_gc.my_uri, file_name1)
    tree2 = '{}/compatibility/{}'.format(yc_gc.my_uri, file_name2)
    diff_url = ('https://www.ietf.org/rfcdiff/rfcdiff.pyht?url1={}&url2={}'
                .format(tree1, tree2))
    response = requests.get(diff_url)
    os.unlink(full_path_file1)
    os.unlink(full_path_file2)
    return '<html><body>{}</body></html>'.format(response.text)


@app.route('/get-common', methods=['POST'])
def get_common():
    body = request.json
    if body is None:
        return abort(400, description='body of request is empty')
    if body.get('input') is None:
        return abort(400, description='body of request need to start with input')
    if body['input'].get('first') is None or body['input'].get('second') is None:
        return abort(400, description='body of request need to contain first and second container')
    response_first = rpc_search({'input': body['input']['first']})
    response_second = rpc_search({'input': body['input']['second']})

    if response_first.status_code == 404 or response_second.status_code == 404:
        return abort(404, description='No hits found either in first or second input')

    data = json.JSONDecoder(object_pairs_hook=collections.OrderedDict)\
        .decode(response_first.get_data(as_text=True))
    modules_first = data['yang-catalog:modules']['module']
    data = json.JSONDecoder(object_pairs_hook=collections.OrderedDict)\
        .decode(response_second.get_data(as_text=True))
    modules_second = data['yang-catalog:modules']['module']

    output_modules_list = []
    names = []
    for mod_first in modules_first:
        for mod_second in modules_second:
            if mod_first['name'] == mod_second['name']:
                if mod_first['name'] not in names:
                    names.append(mod_first['name'])
                    output_modules_list.append(mod_first)
    if len(output_modules_list) == 0:
        return abort(404, description='No common modules found within provided input')
    return Response(json.dumps({'output': output_modules_list}),
                    mimetype='application/json')


@app.route('/compare', methods=['POST'])
def compare():
    body = request.json
    if body is None:
        return abort(400, description='body of request is empty')
    if body.get('input') is None:
        return abort(400, description='body of request need to start with input')
    if body['input'].get('old') is None or body['input'].get('new') is None:
        return abort(400, description='body of request need to contain new and old container')
    response_new = rpc_search({'input': body['input']['new']})
    response_old = rpc_search({'input': body['input']['old']})

    if response_new.status_code == 404 or response_old.status_code == 404:
        return abort(404, description='No hits found either in first or second input')

    data = json.loads(response_new.data)
    modules_new = data['yang-catalog:modules']['module']
    data = json.loads(response_old.data)
    modules_old = data['yang-catalog:modules']['module']

    new_mods = []
    for mod_new in modules_new:
        new_rev = mod_new['revision']
        new_name = mod_new['name']
        found = False
        new_rev_found = False
        for mod_old in modules_old:
            old_rev = mod_old['revision']
            old_name = mod_old['name']
            if new_name == old_name and new_rev == old_rev:
                found = True
                break
            if new_name == old_name and new_rev != old_rev:
                new_rev_found = True
        if not found:
            mod_new['reason-to-show'] = 'New module'
            new_mods.append(mod_new)
        if new_rev_found:
            mod_new['reason-to-show'] = 'Different revision'
            new_mods.append(mod_new)
    if len(new_mods) == 0:
        return abort(404, description='No new modules or modules with different revisions found')
    output = {'output': new_mods}
    return make_response(jsonify(output), 200)


@app.route('/check-semantic-version', methods=['POST'])
#@cross_origin(headers='Content-Type')
def check_semver():
    body = request.json
    if body is None:
        return abort(400, description='body of request is empty')
    if body.get('input') is None:
        return abort(400, description='body of request need to start with input')
    if body['input'].get('old') is None or body['input'].get('new') is None:
        return abort(400, description='body of request need to contain new and old container')
    response_new = rpc_search({'input': body['input']['new']})
    response_old = rpc_search({'input': body['input']['old']})

    if response_new.status_code == 404 or response_old.status_code == 404:
        return abort(404, description='No hits found either in first or second input')

    data = json.loads(response_new.data)
    modules_new = data['yang-catalog:modules']['module']
    data = json.loads(response_old.data)
    modules_old = data['yang-catalog:modules']['module']

    output_modules_list = []
    for mod_old in modules_old:
        name_new = None
        semver_new = None
        revision_new = None
        status_new = None
        name_old = mod_old['name']
        revision_old = mod_old['revision']
        organization_old = mod_old['organization']
        status_old = mod_old['compilation-status']
        for mod_new in modules_new:
            name_new = mod_new['name']
            revision_new = mod_new['revision']
            organization_new = mod_new['organization']
            status_new = mod_new['compilation-status']
            if name_new == name_old and organization_new == organization_old:
                if revision_old == revision_new:
                    break
                semver_new = mod_new.get('derived-semantic-version')
                break
        if semver_new:
            semver_old = mod_old.get('derived-semantic-version')
            if semver_old:
                if semver_new != semver_old:
                    output_mod = {}
                    if status_old != 'passed' and status_new != 'passed':
                        reason = 'Both modules failed compilation'
                    elif status_old != 'passed' and status_new == 'passed':
                        reason = 'Older module failed compilation'
                    elif status_new != 'passed' and status_old == 'passed':
                        reason = 'Newer module failed compilation'
                    else:
                        file_name = ('{}/services/file1={}@{}/check-update-from/file2={}@{}'
                                     .format(yc_gc.yangcatalog_api_prefix, name_new,
                                             revision_new, name_old,
                                             revision_old))
                        reason = ('pyang --check-update-from output: {}'.
                                  format(file_name))

                    diff = (
                        '{}/services/diff-tree/file1={}@{}/file2={}@{}'.
                            format(yc_gc.yangcatalog_api_prefix, name_old,
                                   revision_old, name_new, revision_new))

                    output_mod['yang-module-pyang-tree-diff'] = diff

                    output_mod['name'] = name_old
                    output_mod['revision-old'] = revision_old
                    output_mod['revision-new'] = revision_new
                    output_mod['organization'] = organization_old
                    output_mod['old-derived-semantic-version'] = semver_old
                    output_mod['new-derived-semantic-version'] = semver_new
                    output_mod['derived-semantic-version-results'] = reason
                    diff = ('{}/services/diff-file/file1={}@{}/file2={}@{}'
                            .format(yc_gc.yangcatalog_api_prefix, name_old,
                                    revision_old, name_new, revision_new))
                    output_mod['yang-module-diff'] = diff
                    output_modules_list.append(output_mod)
    if len(output_modules_list) == 0:
        return abort(404, description='No different semantic versions with provided input')
    output = {'output': output_modules_list}
    return make_response(jsonify(output), 200)


@app.route('/search/vendor/<org>', methods=['GET'])
def search_vendor_statistics(org):
    vendor = org

    yc_gc.LOGGER.info('Searching for vendors')
    data = vendors_data(False).get('vendor', {})
    ven_data = None
    for d in data:
        if d['name'] == vendor:
            ven_data = d
            break

    os_type = {}
    if ven_data is not None:
        for plat in ven_data['platforms']['platform']:
            version_list = set()
            os = {}
            for ver in plat['software-versions']['software-version']:
                for flav in ver['software-flavors']['software-flavor']:
                    os[ver['name']] = flav['modules']['module'][0]['os-type']
                    if os[ver['name']] not in os_type:
                        os_type[os[ver['name']]] = {}
                    break
                if ver['name'] not in os_type[os[ver['name']]]:
                    os_type[os[ver['name']]][ver['name']] = set()

                version_list.add(ver['name'])
            for ver in version_list:
                os_type[os[ver]][ver].add(plat['name'])

    os_types = {}
    for key, vals in os_type.items():
        os_types[key] = {}
        for key2, val in os_type[key].items():
            os_types[key][key2] = list(os_type[key][key2])
    return Response(json.dumps(os_types), mimetype='application/json')


@app.route('/search/vendors/<path:value>', methods=['GET'])
def search_vendors(value):
    """Search for a specific vendor, platform, os-type, os-version depending on
    the value sent via API.
            Arguments:
                :param value: (str) path that contains one of the @module_keys and
                    ends with /value searched for
                :return response to the request.
    """
    value = value.replace('vendor/', 'vendor=')
    value = value.replace('/platfrom/', '/platform=')
    value = value.replace('/software-version/', '/software-version=')
    value = value.replace('/software-flavor/', '/software-flavor=')
    yc_gc.LOGGER.info('Searching for specific vendors {}'.format(value))
    path = yc_gc.protocol + '://' + yc_gc.confd_ip + ':' + repr(yc_gc.confdPort) + '/restconf/data/yang-catalog:catalog/vendors/' + value
    data = requests.get(path, auth=(yc_gc.credentials[0], yc_gc.credentials[1]),
                        headers={'Accept': 'application/yang-data+json'})
    if data.status_code == 200 or data.status_code == 204:
        data = json.JSONDecoder(object_pairs_hook=collections.OrderedDict) \
            .decode(data.text)
        return Response(json.dumps(data), mimetype='application/json')
    else:
        return abort(404, description='No vendors found on path {}'.format(value))


@app.route('/search/modules/<name>,<revision>,<organization>', methods=['GET'])
def search_module(name, revision, organization):
    """Search for a specific module defined with name, revision and organization
            Arguments:
                :param name: (str) name of the module
                :param revision: (str) revision of the module
                :param organization: (str) organization of the module
                :return response to the request with job_id that user can use to
                    see if the job is still on or Failed or Finished successfully
    """

    yc_gc.LOGGER.info('Searching for module {}, {}, {}'.format(name, revision, organization))
    module_data = yc_gc.redis.get("{}@{}/{}".format(name, revision, organization))
    if module_data is not None:
        return Response(json.dumps({'module': [json.JSONDecoder(object_pairs_hook=collections.OrderedDict)
                                   .decode(module_data)]
                                    }), mimetype='application/json')
    return abort(404, description='Module {}@{}/{} not found'.format(name, revision, organization))


@app.route('/search/modules', methods=['GET'])
def get_modules():
    """Search for a all the modules populated in confd
            :return response to the request with all the modules
    """
    yc_gc.LOGGER.info('Searching for modules')
    data = json.dumps(modules_data())
    if data is None or data == '{}':
        return abort(404, description="No module is loaded")
    return Response(data, mimetype='application/json')


@app.route('/search/vendors', methods=['GET'])
def get_vendors():
    """Search for a all the vendors populated in confd
            :return response to the request with all the vendors
    """
    yc_gc.LOGGER.info('Searching for vendors')
    data = json.dumps(vendors_data())
    if data is None or data == '{}':
        return abort(404, description="No vendor is loaded")
    return Response(data, mimetype='application/json')


@app.route('/search/catalog', methods=['GET'])
def get_catalog():
    """Search for a all the data populated in confd
                :return response to the request with all the data
    """
    yc_gc.LOGGER.info('Searching for catalog data')
    data = catalog_data()
    if data is None or data == '{}':
        return abort(404, description='No data loaded to YangCatalog')
    else:
        return Response(json.dumps(data), mimetype='application/json')


@app.route('/services/tree/<f1>@<r1>.yang', methods=['GET'])
def create_tree(f1, r1):
    path_to_yang = '{}/{}@{}.yang'.format(yc_gc.save_file_dir, f1, r1)
    plugin.plugins = []
    plugin.init([])
    ctx = create_context('{}:{}'.format(yc_gc.yang_models, yc_gc.save_file_dir))
    ctx.opts.lint_namespace_prefixes = []
    ctx.opts.lint_modulename_prefixes = []

    for p in plugin.plugins:
        p.setup_ctx(ctx)
    try:
        with open(path_to_yang, 'r') as f:
            a = ctx.add_module(path_to_yang, f.read())
    except:
        abort(400, descritpion='File {} was not found'.format(path_to_yang))
    if ctx.opts.tree_path is not None:
        path = ctx.opts.tree_path.split('/')
        if path[0] == '':
            path = path[1:]
    else:
        path = None

    ctx.validate()
    f = io.StringIO()
    emit_tree(ctx, [a], f, ctx.opts.tree_depth, ctx.opts.tree_line_length, path)
    stdout = f.getvalue()
    if stdout == '' and len(ctx.errors) != 0:
        return create_bootstrap_danger()
    elif stdout != '' and len(ctx.errors) != 0:
        return create_bootstrap_warning(stdout)
    elif stdout == '' and len(ctx.errors) == 0:
        return create_bootstrap_info()
    else:
        return '<html><body><pre>{}</pre></body></html>'.format(stdout)


@app.route('/services/reference/<f1>@<r1>.yang', methods=['GET'])
def create_reference(f1, r1):
    schema1 = '{}/{}@{}.yang'.format(yc_gc.save_file_dir, f1, r1)
    arguments = ['cat', schema1]
    cat = subprocess.Popen(arguments,
                             stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE)
    stdout, stderr = cat.communicate()
    if sys.version_info >= (3, 4):
        stdout = stdout.decode(encoding='utf-8', errors='strict')
        stderr = stderr.decode(encoding='utf-8', errors='strict')
    if stdout == '' and stderr != '':
        return create_bootstrap_danger()
    elif stdout != '' and stderr != '':
        return create_bootstrap_warning(stdout)
    else:
        return '<html><body><pre>{}</pre></body></html>'.format(stdout)


### HELPER DEFINITIONS
def filter_using_api(res_row, payload):
    try:
        if 'filter' not in payload or 'module-metadata-filter' not in payload['filter']:
            reject = False
        else:
            reject = False
            keywords = payload['filter']['module-metadata-filter']
            for key, value in keywords.items():
                # Module doesn not contain such key as searched for, then reject
                if res_row['module'].get(key) is None:
                    reject = True
                    break
                if isinstance(res_row['module'][key], dict):
                    # This means the key is either implementations or ietf (for WG)
                    if key == 'implementations':
                        exists = True
                        if res_row['module'][key].get('implementations') is not None:
                            for val in value['implementation']:
                                val_found = False
                                for impl in res_row['module'][key]['implementations']['implementation']:
                                    vendor = impl.get('vendor')
                                    software_version = impl.get('software_version')
                                    software_flavor = impl.get('software_flavor')
                                    platform = impl.get('platform')
                                    os_version = impl.get('os_version')
                                    feature_set = impl.get('feature_set')
                                    os_type = impl.get('os_type')
                                    conformance_type = impl.get('conformance_type')
                                    local_exist = True
                                    if val.get('vendor') is not None:
                                        if vendor != val['vendor']:
                                            local_exist = False
                                    if val.get('software-version') is not None:
                                        if software_version != val['software-version']:
                                            local_exist = False
                                    if val.get('software-flavor') is not None:
                                        if software_flavor != val['software-flavor']:
                                            local_exist = False
                                    if val.get('platform') is not None:
                                        if platform != val['platform']:
                                            local_exist = False
                                    if val.get('os-version') is not None:
                                        if os_version != val['os-version']:
                                            local_exist = False
                                    if val.get('feature-set') is not None:
                                        if feature_set != val['feature-set']:
                                            local_exist = False
                                    if val.get('os-type') is not None:
                                        if os_type != val['os-type']:
                                            local_exist = False
                                    if val.get('conformance-type') is not None:
                                        if conformance_type != val['conformance-type']:
                                            local_exist = False
                                    if local_exist:
                                        val_found = True
                                        break
                                if not val_found:
                                    exists = False
                                    break
                            if not exists:
                                reject = True
                                break
                        else:
                            # No implementations that is searched for, reject
                            reject = True
                            break
                    elif key == 'ietf':
                        values = value.split(',')
                        reject = True
                        for val in values:
                            if res_row['module'][key].get('ietf-wg') is not None:
                                if res_row['module'][key]['ietf-wg'] == val['ietf-wg']:
                                    reject = False
                                    break
                        if reject:
                            break
                elif isinstance(res_row['module'][key], list):
                    # this means the key is either dependencies or dependents
                    exists = True
                    for val in value:
                        val_found = False
                        for dep in res_row['module'][key]:
                            name = dep.get('name')
                            rev = dep.get('revision')
                            schema = dep.get('schema')
                            local_exist = True
                            if val.get('name') is not None:
                                if name != val['name']:
                                    local_exist = False
                            if val.get('revision') is not None:
                                if rev != val['revision']:
                                    local_exist = False
                            if val.get('schema') is not None:
                                if schema != val['schema']:
                                    local_exist = False
                            if local_exist:
                                val_found = True
                                break
                        if not val_found:
                            exists = False
                            break
                    if not exists:
                        reject = True
                        break
                else:
                    # Module key has different value then serached for then reject
                    values = value.split(',')
                    reject = True
                    for val in values:
                        if res_row['module'].get(key) is not None:
                            if res_row['module'][key] == val:
                                reject = False
                                break
                    if reject:
                        break

        return reject
    except Exception as e:
        res_row['module'] = {'error': 'Metadata search failed with: {}'.format(e)}
        return False


def search_recursive(output, module, leaf, resolved):
    r_name = module['name']
    if r_name not in resolved:
        resolved.add(r_name)
        response = rpc_search({'input': {'dependencies': [{'name': r_name}]}})
        modules = json.loads(response.get_data(as_text=True)).get('yang-catalog:modules')
        if modules is None:
            return
        modules = modules.get('module')
        if modules is None:
            return
        for mod in modules:
            search_recursive(output, mod, leaf, resolved)
            meta_data = mod.get(leaf)
            output.add(meta_data)


def process(data, passed_data, value, module, split, count):
    """Iterates recursively through the data to find only modules
    that are searched for
            Arguments:
                :param data: (dict) module that is searched
                :param passed_data: (list) data that contain value searched
                    for are saved in this variable
                :param value: (str) value searched for
                :param module: (dict) module that is searched
                :param split: (str) key value that conatins value searched for
                :param count: (int) if split contains '/' then we need to know
                    which part of the path are we searching.
    """
    if isinstance(data, str):
        if data == value:
            passed_data.append(module)
            return True
    elif isinstance(data, list):
        for part in data:
            if process(part, passed_data, value, module, split, count):
                break
    elif isinstance(data, dict):
        if data:
            count += 1
            return process(data.get(split[count]), passed_data, value, module, split, count)
    return False


def modules_data():
    data = yc_gc.redis.get("modules-data")
    if data is None:
        data = '{}'
    return json.JSONDecoder(object_pairs_hook=collections.OrderedDict).decode(data)


def vendors_data(clean_data=True):
    data = yc_gc.redis.get("vendors-data")
    if data is None:
        data = "{}"
    if clean_data:
        json_data = \
            json.JSONDecoder(object_pairs_hook=collections.OrderedDict).decode(data)
    else:
        json_data = json.loads(data)
    return json_data


def catalog_data():
    data = yc_gc.redis.get("all-catalog-data")
    if data is None:
        data = '{}'
    return json.JSONDecoder(object_pairs_hook=collections.OrderedDict).decode(data)


def create_bootstrap_info():
    with open(get_curr_dir(__file__) + '/../../template/info.html', 'r') as f:
        template = f.read()
    return template


def create_bootstrap_warning(tree):
    yc_gc.LOGGER.info('Rendering bootstrap data')
    context = {'tree': tree}
    path, filename = os.path.split(
        get_curr_dir(__file__) + '/../../template/warning.html')
    return jinja2.Environment(loader=jinja2.FileSystemLoader(path or './')
                              ).get_template(filename).render(context)


def create_bootstrap_danger():
    with open(get_curr_dir(__file__) + '/../../template/danger.html', 'r') as f:
        template = f.read()
    return template
