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

import fnmatch
import grp
import gzip
import hashlib
import json
import math
import os
import pwd
import re
import shutil
import stat
import sys
from datetime import datetime
from pathlib import Path

import MySQLdb
import requests
from api.globalConfig import yc_gc
from flask import Blueprint, abort, jsonify, make_response, redirect, request
from flask_cors import CORS
from utility.util import create_signature


class YangCatalogAdminBlueprint(Blueprint):

    def __init__(self, name, import_name, static_folder=None, static_url_path=None, template_folder=None,
                 url_prefix=None, subdomain=None, url_defaults=None, root_path=None):
        super().__init__(name, import_name, static_folder, static_url_path, template_folder, url_prefix, subdomain,
                         url_defaults, root_path)


app = YangCatalogAdminBlueprint('admin', __name__)
CORS(app, supports_credentials=True)

### ROUTE ENDPOINT DEFINITIONS ###


@app.route('/api/admin/login')
@app.route('/admin')
@app.route('/admin/login')
@yc_gc.oidc.require_login
def login():
    if yc_gc.oidc.user_loggedin:
        return redirect('{}/admin/healthcheck'.format(yc_gc.my_uri), code=302)
    else:
        abort(401, 'user not logged in')
    return make_response(jsonify({'info': 'Success'}), 200)


@app.route('/api/admin/logout', methods=['POST'])
def logout():
    yc_gc.oidc.logout()
    return make_response(jsonify({'info': 'Success'}), 200)


@app.route('/api/admin/ping')
def ping():
    yc_gc.LOGGER.info('ping {}'.format(yc_gc.oidc.user_loggedin))
    if yc_gc.oidc.user_loggedin:
        response = {'info': 'Success'}
    else:
        response = {'info': 'user not logged in'}
    return make_response(jsonify(response), 200)


@app.route('/api/admin/check', methods=['GET'])
def check():
    response = {'info': 'Success'}
    return make_response(jsonify(response), 200)


@app.route('/api/admin/directory-structure/read/<path:direc>', methods=['GET'])
def read_admin_file(direc):
    yc_gc.LOGGER.info('Reading admin file {}'.format(direc))
    try:
        file_exist = os.path.isfile('{}/{}'.format(yc_gc.var_yang, direc))
    except:
        file_exist = False
    if file_exist:
        with open('{}/{}'.format(yc_gc.var_yang, direc), 'r') as f:
            processed_file = f.read()
        response = {'info': 'Success',
                    'data': processed_file}
        return make_response(jsonify(response), 200)
    else:
        return abort(400, description='error - file does not exist')


@app.route("/api/admin/directory-structure", defaults={"direc": ""}, methods=['DELETE'])
@app.route('/api/admin/directory-structure/<path:direc>', methods=['DELETE'])
def delete_admin_file(direc):
    yc_gc.LOGGER.info('Deleting admin file {}'.format(direc))
    try:
        exist = os.path.exists('{}/{}'.format(yc_gc.var_yang, direc))
    except:
        exist = False
    if exist:
        if os.path.isfile('{}/{}'.format(yc_gc.var_yang, direc)):
            os.unlink('{}/{}'.format(yc_gc.var_yang, direc))
        else:
            shutil.rmtree('{}/{}'.format(yc_gc.var_yang, direc))
        response = {'info': 'Success',
                    'data': 'directory of file {} removed succesfully'.format('{}/{}'.format(yc_gc.var_yang, direc))}
        return make_response(jsonify(response), 200)
    else:
        return abort(400, description='error - file or folder does not exist')


@app.route('/api/admin/directory-structure/<path:direc>', methods=['PUT'])
def write_to_directory_structure(direc):
    yc_gc.LOGGER.info("Updating file on path {}".format(direc))

    body = request.json
    input = body.get('input')
    if input is None or input.get('data') is None:
        return make_response(jsonify({'error': 'payload needs to have body with input and data container'}), 400)

    try:
        file_exist = os.path.isfile('{}/{}'.format(yc_gc.var_yang, direc))
    except:
        file_exist = False
    if file_exist:
        with open('{}/{}'.format(yc_gc.var_yang, direc), 'w') as f:
            f.write(input['data'])
        response = {'info': 'Success',
                    'data': input['data']}
        return make_response(jsonify(response), 200)
    else:
        return abort(400, description='error - file does not exist')


@app.route("/api/admin/directory-structure", defaults={"direc": ""}, methods=['GET'])
@app.route('/api/admin/directory-structure/<path:direc>', methods=['GET'])
def get_var_yang_directory_structure(direc):

    def walk_through_dir(path):
        structure = {'folders': [], 'files': []}
        for root, dirs, files in os.walk(path):
            structure['name'] = os.path.basename(root)
            for f in files:
                file_structure = {'name': f}
                file_stat = Path('{}/{}'.format(path, f)).stat()
                file_structure['size'] = file_stat.st_size
                try:
                    file_structure['group'] = grp.getgrgid(file_stat.st_gid).gr_name
                except:
                    file_structure['group'] = file_stat.st_gid

                try:
                    file_structure['user'] = pwd.getpwuid(file_stat.st_uid).pw_name
                except:
                    file_structure['user'] = file_stat.st_uid
                file_structure['permissions'] = oct(stat.S_IMODE(os.lstat('{}/{}'.format(path, f)).st_mode))
                file_structure['modification'] = int(file_stat.st_mtime)
                structure['files'].append(file_structure)
            for directory in dirs:
                dir_structure = {'name': directory}
                p = Path('{}/{}'.format(path, directory))
                dir_size = sum(f.stat().st_size for f in p.glob('**/*') if f.is_file())
                dir_stat = p.stat()
                try:
                    dir_structure['group'] = grp.getgrgid(dir_stat.st_gid).gr_name
                except:
                    dir_structure['group'] = dir_stat.st_gid

                try:
                    dir_structure['user'] = pwd.getpwuid(dir_stat.st_uid).pw_name
                except:
                    dir_structure['user'] = dir_stat.st_uid
                dir_structure['size'] = dir_size
                dir_structure['permissions'] = oct(stat.S_IMODE(os.lstat('{}/{}'.format(path, directory)).st_mode))
                dir_structure['modification'] = int(dir_stat.st_mtime)
                structure['folders'].append(dir_structure)
            break
        return structure

    yc_gc.LOGGER.info('Getting directory structure')

    ret = walk_through_dir('/var/yang/{}'.format(direc))
    response = {'info': 'Success',
                'data': ret}
    return make_response(jsonify(response), 200)


@app.route('/api/admin/yangcatalog-nginx', methods=['GET'])
def read_yangcatalog_nginx_files():
    yc_gc.LOGGER.info('Getting list of nginx files')
    files = os.listdir('{}/sites-enabled'.format(yc_gc.nginx_dir))
    files_final = ['sites-enabled/' + sub for sub in files]
    files_final.append('nginx.conf')
    files = os.listdir('{}/conf.d'.format(yc_gc.nginx_dir))
    files_final.extend(['conf.d/' + sub for sub in files])
    response = {'info': 'Success',
                'data': files_final}
    return make_response(jsonify(response), 200)


@app.route('/api/admin/yangcatalog-nginx/<path:nginx_file>', methods=['GET'])
def read_yangcatalog_nginx(nginx_file):
    yc_gc.LOGGER.info('Reading nginx file {}'.format(nginx_file))
    with open('{}/{}'.format(yc_gc.nginx_dir, nginx_file), 'r') as f:
        nginx_config = f.read()
    response = {'info': 'Success',
                'data': nginx_config}
    return make_response(jsonify(response), 200)


@app.route('/api/admin/yangcatalog-config', methods=['GET'])
def read_yangcatalog_config():
    yc_gc.LOGGER.info('Reading yangcatalog config file')

    with open(yc_gc.config_path, 'r') as f:
        yangcatalog_config = f.read()
    response = {'info': 'Success',
                'data': yangcatalog_config}
    return make_response(jsonify(response), 200)


@app.route('/api/admin/yangcatalog-config', methods=['PUT'])
def update_yangcatalog_config():
    yc_gc.LOGGER.info('Updating yangcatalog config file')
    body = request.json
    input = body.get('input')
    if input is None or input.get('data') is None:
        return abort(400, description='payload needs to have body with "input" and "data" container')

    with open(yc_gc.config_path, 'w') as f:
        f.write(input['data'])
    resp = {'api': 'error loading data',
            'yang-search': 'error loading data',
            'receiver': 'error loading data'}
    yc_gc.load_config()
    resp['api'] = 'data loaded successfully'
    yc_gc.sender.send('reload_config')
    resp['receiver'] = 'data loaded succesfully'
    path = '{}://{}/yang-search/reload_config'.format(yc_gc.api_protocol, yc_gc.ip)
    signature = create_signature(yc_gc.search_key, json.dumps(input))

    response = requests.post(path, data=json.dumps(input),
                             headers={'Content-Type': 'app/json', 'Accept': 'app/json',
                                      'X-YC-Signature': 'sha1={}'.format(signature)}, verify=False)
    code = response.status_code

    if code != 200 and code != 201 and code != 204:
        yc_gc.LOGGER.error('could not send data to realod config. Reason: {}'
                           .format(response.text))
    else:
        resp['yang-search'] = response.json()['info']
    response = {'info': resp,
                'new-data': input['data']}
    return make_response(jsonify(response), 200)


@app.route('/api/admin/logs', methods=['GET'])
def get_log_files():

    def find_files(directory, pattern):
        for root, dirs, files in os.walk(directory):
            for basename in files:
                if fnmatch.fnmatch(basename, pattern):
                    filename = os.path.join(root, basename)
                    yield filename

    yc_gc.LOGGER.info('Getting yangcatalog log files')

    files = find_files(yc_gc.logs_dir, '*.log*')
    resp = set()
    for f in files:
        resp.add(f.split('/logs/')[-1].split('.')[0])
    return make_response(jsonify({'info': 'success',
                                  'data': list(resp)}), 200)


@app.route('/api/admin/logs', methods=['POST'])
def get_logs():

    def find_files(directory, pattern):
        for root, dirs, files in os.walk(directory):
            for basename in files:
                if fnmatch.fnmatch(basename, pattern):
                    filename = os.path.join(root, basename)
                    yield filename
            break

    date_regex = r'([12]\d{3}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01]))'
    time_regex = r'(?:[01]\d|2[0-3]):(?:[0-5]\d):(?:[0-5]\d)'
    yc_gc.LOGGER.info('Reading yangcatalog log file')
    if request.json is None:
        return abort(400, description='bad-request - body has to start with "input" and can not be empty')

    body = request.json.get('input')

    if body is None:
        return abort(400, description='bad-request - body has to start with "input" and can not be empty')
    number_of_lines_per_page = body.get('lines-per-page', 1000)
    page_num = body.get('page', 1)
    filter = body.get('filter')
    from_date_timestamp = body.get('from-date', None)
    to_date_timestamp = body.get('to-date', None)
    file_names = body.get('file-names', ['yang'])
    log_files = []

    # Check if file modification date is greater than from timestamp
    for file_name in file_names:
        if from_date_timestamp is None:
            log_files.append('{}/{}.log'.format(yc_gc.logs_dir, file_name))
        else:
            files = find_files('{}/{}'.format(yc_gc.logs_dir, '/'.join(file_name.split('/')[:-1])),
                               '{}.log*'.format(file_name.split('/')[-1]))
            for f in files:
                if os.path.getmtime(f) >= from_date_timestamp:
                    log_files.append(f)
    send_out = []

    # Try to find a timestamp in a log file using regex
    if from_date_timestamp is None:
        with open(log_files[0], 'r') as f:
            for line in f.readlines():
                if from_date_timestamp is None:
                    try:
                        d = re.findall(date_regex, line)[0][0]
                        t = re.findall(time_regex, line)[0]
                        from_date_timestamp = datetime.strptime('{} {}'.format(d, t), '%Y-%m-%d %H:%M:%S').timestamp()
                    except:
                        # ignore and accept
                        pass
                else:
                    break

    yc_gc.LOGGER.debug('Searching for logs from timestamp: {}'.format(str(from_date_timestamp)))
    whole_line = ''
    if to_date_timestamp is None:
        to_date_timestamp = datetime.now().timestamp()

    log_files.reverse()
    # Decide whether the output will be formatted or not (default False)
    format_text = False
    for log_file in log_files:
        if '.gz' in log_file:
            f = gzip.open(log_file, 'rt')
        else:
            f = open(log_file, 'r')
        file_stream = f.read()
        level_regex = r'[A-Z]{4,10}'
        two_words_regex = r'(\s*(\S*)\s*){2}'
        line_regex = '({} {}[ ]{}{}[=][>])'.format(date_regex, time_regex, level_regex, two_words_regex)
        hits = re.findall(line_regex, file_stream)
        if len(hits) > 1 or file_stream == '':
            format_text = True
        else:
            format_text = False
            break

    if not format_text:
        for log_file in log_files:
            # Different way to open a file, but both will return a file object
            if '.gz' in log_file:
                f = gzip.open(log_file, 'rt')
            else:
                f = open(log_file, 'r')
            for line in reversed(f.readlines()):
                if filter is not None:
                    match_case = filter.get('match-case', False)
                    match_whole_words = filter.get('match-words', False)
                    filter_out = filter.get('filter-out', None)
                    searched_string = filter.get('search-for', '')
                    level = filter.get('level', '').upper()
                    level_formats = ['']
                    if level != '':
                        level_formats = [
                            ' {} '.format(level), '<{}>'.format(level),
                            '[{}]'.format(level).lower(), '{}:'.format(level)]
                    if match_whole_words:
                        if searched_string != '':
                            searched_string = ' {} '.format(searched_string)
                    for level_format in level_formats:
                        if level_format in line:
                            if match_case and searched_string in line:
                                if filter_out is not None and filter_out in line:
                                    continue
                                send_out.append('{}'.format(line).rstrip())
                            elif not match_case and searched_string.lower() in line.lower():
                                if filter_out is not None and filter_out.lower() in line.lower():
                                    continue
                                send_out.append('{}'.format(line).rstrip())
                else:
                    send_out.append('{}'.format(line).rstrip())

    if format_text:
        for log_file in log_files:
            # Different way to open a file, but both will return a file object
            if '.gz' in log_file:
                f = gzip.open(log_file, 'rt')
            else:
                f = open(log_file, 'r')
            for line in reversed(f.readlines()):
                line_timestamp = None
                try:
                    d = re.findall(date_regex, line)[0][0]
                    t = re.findall(time_regex, line)[0]
                    line_beginning = '{} {}'.format(d, t)
                    line_timestamp = datetime.strptime(line_beginning, '%Y-%m-%d %H:%M:%S').timestamp()
                except:
                    # ignore and accept
                    pass
                if line_timestamp is None or not line.startswith(line_beginning):
                    whole_line = '{}{}'.format(line, whole_line)
                    continue
                if from_date_timestamp <= line_timestamp <= to_date_timestamp:
                    if filter is not None:
                        match_case = filter.get('match-case', False)
                        match_whole_words = filter.get('match-words', False)
                        filter_out = filter.get('filter-out', None)
                        searched_string = filter.get('search-for', '')
                        level = filter.get('level', '').upper()
                        level_formats = ['']
                        if level != '':
                            level_formats = [
                                ' {} '.format(level), '<{}>'.format(level),
                                '[{}]'.format(level).lower(), '{}:'.format(level)]
                        if match_whole_words:
                            if searched_string != '':
                                searched_string = ' {} '.format(searched_string)
                        for level_format in level_formats:
                            if level_format in line:
                                if match_case and searched_string in line:
                                    if filter_out is not None and filter_out in line:
                                        whole_line = ''
                                        continue
                                    send_out.append('{}{}'.format(line, whole_line).rstrip())
                                elif not match_case and searched_string.lower() in line.lower():
                                    if filter_out is not None and filter_out.lower() in line.lower():
                                        whole_line = ''
                                        continue
                                    send_out.append('{}{}'.format(line, whole_line).rstrip())
                    else:
                        send_out.append('{}{}'.format(line, whole_line).rstrip())
                whole_line = ''

    pages = math.ceil(len(send_out) / number_of_lines_per_page)
    len_send_out = len(send_out)

    metadata = {'file-names': file_names,
                'from-date': from_date_timestamp,
                'to-data': to_date_timestamp,
                'lines-per-page': number_of_lines_per_page,
                'page': page_num,
                'pages': pages,
                'filter': filter,
                'format': format_text}
    from_line = (page_num - 1) * number_of_lines_per_page
    if page_num * number_of_lines_per_page > len_send_out:
        output = send_out[from_line:]
    else:
        output = send_out[from_line:page_num * number_of_lines_per_page]
    return make_response(jsonify({'meta': metadata,
                                  'output': output}), 200)


@app.route('/api/admin/sql-tables', methods=['GET'])
def get_sql_tables():
    return make_response(jsonify([
        {
            'name': 'users',
            'label': 'approved users'
        },
        {
            'name': 'users_temp',
            'label': 'users waiting for approval'
        }
    ]), 200)


@app.route('/api/admin/move-user', methods=['POST'])
def move_user():
    body = request.json.get('input')
    if body is None:
        return abort(400, description='bad request - did you not start with input json container?')
    unique_id = body.get('id')
    if unique_id is None:
        return abort(400, description='Id of a user is missing')
    models_provider = body.get('models-provider', '')
    sdo_access = body.get('access-rights-sdo', '')
    vendor_access = body.get('access-rights-vendor', '')
    username = body.get('username')
    name = body.get('first-name')
    last_name = body.get('last-name')
    email = body.get('email')
    if sdo_access == '' and vendor_access == '':
        return abort(400, description='access-rights-sdo OR access-rights-vendor must be specified')
    try:
        db = MySQLdb.connect(host=yc_gc.dbHost, db=yc_gc.dbName, user=yc_gc.dbUser, passwd=yc_gc.dbPass)
        # prepare a cursor object using cursor() method
        cursor = db.cursor()

        cursor = db.cursor()
        sql = """SELECT * FROM `{}` WHERE Id = %s""".format('users_temp')
        cursor.execute(sql, (unique_id,))

        data = cursor.fetchall()

        password = ''
        for x in data:
            if x[0] == int(unique_id):
                password = x[2]

        sql = """INSERT INTO `{}` (Username, Password, Email, ModelsProvider,
         FirstName, LastName, AccessRightsSdo, AccessRightsVendor) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""" \
            .format('users')
        cursor.execute(sql, (username, password, email, models_provider,
                             name, last_name, sdo_access, vendor_access,))
        db.commit()
        db.close()
    except MySQLdb.MySQLError as err:
        if err.args[0] not in [1049, 2013]:
            db.close()
        yc_gc.LOGGER.error("Cannot connect to database. MySQL error: {}".format(err))
        return make_response(jsonify({'error': 'Server problem connecting to database'}), 500)
    try:
        db = MySQLdb.connect(host=yc_gc.dbHost, db=yc_gc.dbName, user=yc_gc.dbUser, passwd=yc_gc.dbPass)
        # prepare a cursor object using cursor() method
        cursor = db.cursor()
        sql = """SELECT * FROM `{}` WHERE Id = %s""".format('users_temp')
        cursor.execute(sql, (unique_id,))

        data = cursor.fetchall()

        found = False
        for x in data:
            if x[0] == int(unique_id):
                found = True
        if found:
            # execute SQL query using execute() method.
            cursor = db.cursor()
            sql = """DELETE FROM `{}` WHERE Id = %s""".format('users_temp')
            cursor.execute(sql, (unique_id,))
            db.commit()
        db.close()
    except MySQLdb.MySQLError as err:
        if err.args[0] not in [1049, 2013]:
            db.close()
        yc_gc.LOGGER.error("Cannot connect to database. MySQL error: {}".format(err))
        return make_response(jsonify({'error': 'Server problem connecting to database'}), 500)
    response = {'info': 'data successfully added to database users and removed from users_temp',
                'data': body}
    return make_response(jsonify(response), 201)


@app.route('/api/admin/sql-tables/<table>', methods=['POST'])
def create_sql_row(table):
    if table not in ['users', 'users_temp']:
        return make_response(jsonify({'error': 'table {} not implemented use only users or users_temp'.format(table)}),
                             501)
    body = request.json.get('input')
    if body is None:
        return abort(400, description='bad request - did you not start with input json container?')
    username = body.get('username')
    name = body.get('first-name')
    last_name = body.get('last-name')
    email = body.get('email')
    password = body.get('password')
    if body is None or username is None or name is None or last_name is None or email is None or password is None:
        return abort(400, description='username - {}, firstname - {}, last-name - {}, email - {} and password - {} must be specified'.format(
            username,
            name,
            last_name,
            email,
            password))
    models_provider = body.get('models-provider', '')
    sdo_access = body.get('access-rights-sdo', '')
    vendor_access = body.get('access-rights-vendor', '')
    hashed_password = hash_pw(password)
    if table == 'users' and sdo_access == '' and vendor_access == '':
        return abort(400, description='access-rights-sdo OR access-rights-vendor must be specified')
    try:
        db = MySQLdb.connect(host=yc_gc.dbHost, db=yc_gc.dbName, user=yc_gc.dbUser, passwd=yc_gc.dbPass)
        # prepare a cursor object using cursor() method
        cursor = db.cursor()
        sql = """INSERT INTO `{}` (Username, Password, Email, ModelsProvider,
         FirstName, LastName, AccessRightsSdo, AccessRightsVendor) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""" \
            .format(table)
        cursor.execute(sql, (username, hashed_password, email, models_provider,
                             name, last_name, sdo_access, vendor_access,))
        db.commit()
        db.close()
        response = {'info': 'data successfully added to database',
                    'data': body}
        return make_response(jsonify(response), 201)
    except MySQLdb.MySQLError as err:
        if err.args[0] not in [1049, 2013]:
            db.close()
        yc_gc.LOGGER.error("Cannot connect to database. MySQL error: {}".format(err))
        return make_response(jsonify({'error': 'Server problem connecting to database'}), 500)


@app.route('/api/admin/sql-tables/<table>/id/<unique_id>', methods=['DELETE'])
def delete_sql_row(table, unique_id):
    try:
        db = MySQLdb.connect(host=yc_gc.dbHost, db=yc_gc.dbName, user=yc_gc.dbUser, passwd=yc_gc.dbPass)
        # prepare a cursor object using cursor() method
        cursor = db.cursor()
        sql = """SELECT * FROM `{}` WHERE Id = %s""".format(table)
        cursor.execute(sql, (unique_id,))

        data = cursor.fetchall()

        found = False
        for x in data:
            if x[0] == int(unique_id):
                found = True
        if found:
            # execute SQL query using execute() method.
            cursor = db.cursor()
            sql = """DELETE FROM `{}` WHERE Id = %s""".format(table)
            cursor.execute(sql, (unique_id,))
            db.commit()

        db.close()
    except MySQLdb.MySQLError as err:
        if err.args[0] not in [1049, 2013]:
            db.close()
        yc_gc.LOGGER.error("Cannot connect to database. MySQL error: {}".format(err))
        return make_response(jsonify({'error': 'Server problem connecting to database'}), 500)
    if found:
        return make_response(jsonify({'info': 'id {} deleted successfully'.format(unique_id)}), 200)
    else:
        return abort(404, description='id {} not found in table {}'.format(unique_id, table))


@app.route('/api/admin/sql-tables/<table>/id/<unique_id>', methods=['PUT'])
def update_sql_row(table, unique_id):
    try:
        db = MySQLdb.connect(host=yc_gc.dbHost, db=yc_gc.dbName, user=yc_gc.dbUser, passwd=yc_gc.dbPass)
        # prepare a cursor object using cursor() method
        cursor = db.cursor()
        sql = """SELECT * FROM `{}` WHERE Id = %s""".format(table)
        cursor.execute(sql, (unique_id,))

        data = cursor.fetchall()

        body = request.json.get('input')
        username = body.get('username')
        email = body.get('email')
        models_provider = body.get('models-provider')
        first_name = body.get('first-name')
        last_name = body.get('last-name')
        access_rights_sdo = body.get('access-rights-sdo', '')
        access_rights_vendor = body.get('access-rights-vendor', '')
        found = False
        for x in data:
            if x[0] == int(unique_id):
                found = True
        if found:
            # execute SQL query using execute() method.
            cursor = db.cursor()
            sql = """UPDATE {} SET Username=%s, Email=%s, ModelsProvider=%s, FirstName=%s,
                    LastName=%s, AccessRightsSdo=%s, AccessRightsVendor=%s WHERE Id=%s""".format(table)
            cursor.execute(sql, (username, email, models_provider, first_name, last_name, access_rights_sdo, access_rights_vendor, unique_id,))
            db.commit()

        db.close()
    except MySQLdb.MySQLError as err:
        if err.args[0] not in [1049, 2013]:
            db.close()
        yc_gc.LOGGER.error('Cannot connect to database. MySQL error: {}'.format(err))
        return make_response(jsonify({'error': 'Server problem connecting to database'}), 500)
    if found:
        yc_gc.LOGGER.info('Record with ID {} in table {} updated successfully'.format(unique_id, table))
        return make_response(jsonify({'info': 'ID {} updated successfully'.format(unique_id)}), 200)
    else:
        return abort(404, description='ID {} not found in table {}'.format(unique_id, table))


@app.route('/api/admin/sql-tables/<table>', methods=['GET'])
def get_sql_rows(table):
    try:
        db = MySQLdb.connect(host=yc_gc.dbHost, db=yc_gc.dbName, user=yc_gc.dbUser, passwd=yc_gc.dbPass)
        # prepare a cursor object using cursor() method
        cursor = db.cursor()
        # execute SQL query using execute() method.
        sql = """SELECT * FROM {}""".format(table)
        cursor.execute(sql)
        data = cursor.fetchall()
        db.close()

    except MySQLdb.MySQLError as err:
        yc_gc.LOGGER.error("Cannot connect to database. MySQL error: {}".format(err))
        if err.args[0] not in [1049, 2013]:
            db.close()
        return make_response(jsonify({'error': 'Server problem connecting to database'}), 500)
    ret = []
    for row in data:
        data_set = {'id': row[0],
                    'username': row[1],
                    'email': row[3],
                    'models-provider': row[4],
                    'first-name': row[5],
                    'last-name': row[6],
                    'access-rights-sdo': row[7],
                    'access-rights-vendor': row[8]}
        ret.append(data_set)
    return make_response(jsonify(ret), 200)


@app.route('/api/admin/scripts/<script>', methods=['GET'])
def get_script_details(script):
    module_name = get_module_name(script)
    if module_name is None:
        return abort(400, description='"{}" is not valid script name'.format(script))

    module = __import__(module_name, fromlist=[script])
    submodule = getattr(module, script)
    script_conf = submodule.ScriptConfig()
    script_args_list = script_conf.get_args_list()
    script_args_list.pop('credentials', None)

    response = {'data': script_args_list}
    response.update(script_conf.get_help())
    return make_response(jsonify(response), 200)


@app.route('/api/admin/scripts/<script>', methods=['POST'])
def run_script_with_args(script):
    module_name = get_module_name(script)
    if module_name is None:
        return abort(400, description='"{}" is not valid script name'.format(script))

    body = request.json

    if body is None:
        return abort(400, description='bad-request - body can not be empty')
    if body.get('input') is None:
        return abort(400, description='missing "input" root json object')
    if script == 'validate':
        try:
            if not body['input']['row_id'] or not body['input']['user_email']:
                return abort(400, description='Failed to validate - user-email and row-id cannot be empty strings')
        except:
            return abort(400, description='Failed to validate - user-email and row-id must exist')

    arguments = ['run_script', module_name, script, json.dumps(body['input'])]
    job_id = yc_gc.sender.send('#'.join(arguments))

    yc_gc.LOGGER.info('job_id {}'.format(job_id))
    return make_response(jsonify({'info': 'Verification successful', 'job-id': job_id, 'arguments': arguments[1:]}), 202)


@app.route('/api/admin/scripts', methods=['GET'])
def get_script_names():
    scripts_names = ['populate', 'runCapabilities', 'draftPull', 'draftPullLocal', 'openconfigPullLocal', 'statistics',
                     'recovery', 'elkRecovery', 'elkFill', 'resolveExpiration', 'mariadbRecovery']
    return make_response(jsonify({'data': scripts_names, 'info': 'Success'}), 200)


@app.route('/api/admin/disk-usage', methods=['GET'])
def get_disk_usage():
    total, used, free = shutil.disk_usage('/')
    usage = {}
    usage['total'] = total
    usage['used'] = used
    usage['free'] = free
    return make_response(jsonify({'data': usage, 'info': 'Success'}), 200)


### HELPER DEFINITIONS ###
def get_module_name(script_name):
    if script_name in ['populate', 'runCapabilities']:
        return 'parseAndPopulate'
    elif script_name in ['draftPull', 'draftPullLocal', 'openconfigPullLocal']:
        return 'ietfYangDraftPull'
    elif script_name in ['recovery', 'elkRecovery', 'elkFill', 'mariadbRecovery']:
        return 'recovery'
    elif script_name == 'statistics':
        return 'statistic'
    elif script_name == 'resolveExpiration':
        return 'utility'
    elif script_name == 'validate':
        return 'validate'
    else:
        return None


def hash_pw(password):
    if sys.version_info >= (3, 4):
        password = password.encode(encoding='utf-8', errors='strict')
    return hashlib.sha256(password).hexdigest()
