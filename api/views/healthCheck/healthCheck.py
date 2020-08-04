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

import json
import MySQLdb
import requests
import time

import utility.log as log

from elasticsearch import Elasticsearch
from flask import Blueprint, request, make_response, jsonify
from api.globalConfig import yc_gc
from utility.util import create_signature


class HealthcheckBlueprint(Blueprint):

    def __init__(self, name, import_name, static_folder=None, static_url_path=None, template_folder=None,
                 url_prefix=None, subdomain=None, url_defaults=None, root_path=None):
        self.LOGGER = log.get_logger('healthcheck', '/var/yang/logs/healthcheck.log')
        super().__init__(name, import_name, static_folder, static_url_path, template_folder, url_prefix, subdomain,
                         url_defaults, root_path)



app = HealthcheckBlueprint('healthcheck', __name__)


### ROUTE ENDPOINT DEFINITIONS ###
@app.route('/', methods=['GET'])
def get_services_list():
    services = ['my-sql', 'elk', 'confd', 'yang-search', 'yang-validator', 'yangre', 'nginx', 'rabbitmq']
    return make_response(jsonify({'data': services, 'info': 'Success'}), 200)


@app.route('/my-sql', methods=['GET'])
def health_check_mysql():
    try:
        app.LOGGER.info('Trying to connect to MySQL')
        db = MySQLdb.connect(host=yc_gc.dbHost, db=yc_gc.dbName, user=yc_gc.dbUser,
                                passwd=yc_gc.dbPass)
        if db is not None:
            app.LOGGER.info('Successfully connected to database: {}'.format(yc_gc.dbName))
            # prepare a cursor object using cursor() method
            cursor = db.cursor()
            # test if there are tables in db
            cursor.execute('USE {}'.format(yc_gc.dbName))
            cursor.execute('SHOW TABLES')
            tables = cursor.fetchall()
            if len(tables):
                response = {'info': 'MySQL is running',
                            'status': 'running',
                            'message': '{} tables available in the database: {}'.format(len(tables), yc_gc.dbName)}
            else:
                response = {'info': 'MySQL is running',
                            'status': 'problem',
                            'error': 'No tables found in the database: {}'.format(yc_gc.dbName)}
            app.LOGGER.info('{} tables available in the database: {}'.format(len(tables), yc_gc.dbName))
            db.close()
            return make_response(jsonify(response), 200)
    except MySQLdb.MySQLError as err:
        app.LOGGER.error('Cannot connect to database. MySQL error: {}'.format(err))
        if err.args[0] in [1044, 1045]:
            return make_response(jsonify({'info': 'Not OK - Access denied',
                                        'status': 'problem',
                                        'error': 'MySQL error: {}'.format(err)}), 200)
        else:
            return make_response(jsonify({'info': 'Not OK - MySQL is not running',
                                        'status': 'down',
                                        'error': 'MySQL error: {}'.format(err)}), 200)


@app.route('/elk', methods=['GET'])
def health_check_elk():
    service_name = 'Elasticsearch'
    app.LOGGER.info('Trying to ping {}'.format(service_name))
    try:
        es = Elasticsearch([{'host':'{}'.format(yc_gc.es_host), 'port':yc_gc.es_port}])
        # try to ping Elasticsearch
        if es.ping():
            app.LOGGER.info('Successfully connected to Elasticsearch')
            # get health of cluster
            health = es.cluster.health()
            health_status = health.get('status')
            app.LOGGER.info('Health status of cluster: {}'.format(health_status))
            # get list of indices
            indices = es.indices.get_alias().keys()
            if len(indices) == 2:
                return make_response(jsonify({'info': 'Elasticsearch is running',
                                            'status': 'running',
                                            'message': 'Cluster status: {}'.format(health_status)}), 200)
            else:
                return make_response(jsonify({'info': 'Elasticsearch is running',
                                            'status': 'problem',
                                            'message': 'Cluster status: {} Number of indices: {}'
                                                .format(health_status, len(indices))}), 200)
        else:
            app.LOGGER.info('Cannot connect to Elasticsearch database')
            return make_response(jsonify({'info': 'Not OK - Elasticsearch is not running',
                                        'status': 'down',
                                        'error': 'Cannot ping Elasticsearch'}), 200)
    except Exception as err:
        app.LOGGER.error('Cannot connect to Elasticsearch database. Error: {}'.format(err))
        return make_response(jsonify(error_response(service_name, err)), 200)


@app.route('/confd', methods=['GET'])
def health_check_confd():
    service_name = 'ConfD'
    app.LOGGER.info('Trying to ping {}'.format(service_name))
    confd_prefix = '{}://{}:{}'.format(yc_gc.protocol, yc_gc.confd_ip, repr(yc_gc.confdPort))
    headers = {'Content-type': 'application/yang-data+json', 'Accept': 'application/yang-data+json'}

    try:
        # Check if ConfD is running
        response = requests.get('{}/restconf/data'.format(confd_prefix),
                        auth=(yc_gc.credentials[0], yc_gc.credentials[1]), headers=headers)
        if response.status_code == 200:
            app.LOGGER.info('ConfD is running')
            # Check if ConfD is filled with data
            response = requests.get('{}/restconf/data/yang-catalog:catalog/modules'.format(confd_prefix),
                                    auth=(yc_gc.credentials[0], yc_gc.credentials[1]), headers=headers)
            app.LOGGER.info('Status code {} while getting data of yang-catalog:modules'.format(response.status_code))
            if response.status_code != 200 and response.status_code != 201 and response.status_code != 204:
                response = {'info': 'Not OK - ConfD is not filled',
                            'status': 'problem',
                            'error': 'Cannot get data of yang-catalog:modules'}
                return make_response(jsonify(response), 200)
            else:
                modules_data = response.json()
                num_of_modules = len(modules_data['yang-catalog:modules']['module'])
                app.LOGGER.info('{} modules loaded from ConfD'.format(num_of_modules))
                if num_of_modules > 0:
                    return make_response(jsonify({'info': 'ConfD is running',
                                                'status': 'running',
                                                'message': '{} modules loaded from ConfD'.format(num_of_modules)}), 200)
                else:
                    return make_response(jsonify({'info': 'ConfD is running',
                                                'status': 'problem',
                                                'message': 'ConfD is running but no modules loaded'}), 200)
        else:
            app.LOGGER.info('Cannot get data from /restconf/data')
            err = 'Cannot get data from /restconf/data'
            return make_response(jsonify(error_response(service_name, err)), 200)
    except Exception as err:
        app.LOGGER.error('Cannot ping {}. Error: {}'.format(service_name, err))
        return make_response(jsonify(error_response(service_name, err)), 200)


@app.route('/yang-search', methods=['GET'])
def health_check_yang_search():
    service_name = 'yang-search'
    app.LOGGER.info('Trying to ping {}'.format(service_name))
    yang_search_preffix = '{}://{}/yang-search'.format(yc_gc.api_protocol, yc_gc.ip)
    body = json.dumps({'input': {'data': 'ping'}})
    signature = create_signature(yc_gc.search_key, body)
    headers = {'Content-Type': 'application/json',
            'Accept': 'application/json',
            'X-YC-Signature': 'sha1={}'.format(signature)}
    try:
        response = requests.post('{}/ping'.format(yang_search_preffix), data=body, headers=headers)
        app.LOGGER.info('yang-search responded with a code {}'.format(response.status_code))
        if response.status_code == 200:
            return make_response(jsonify({'info': '{} is available'.format(service_name),
                                        'status': 'running',
                                        'message': '{} responded with a code {}'.format(service_name, response.status_code)}), 200)
        elif response.status_code == 400 or response.status_code == 404:
            err = json.loads(response.text).get('error')
            return make_response(jsonify({'info': '{} is available'.format(service_name),
                                        'status': 'problem',
                                        'error': '{} responded with a message: {}'.format(service_name, err)}), 200)
        else:
            err = '{} responded with a code {}'.format(service_name, response.status_code)
            return make_response(jsonify(error_response(service_name, err)), 200)
    except Exception as err:
        app.LOGGER.error('Cannot ping {}. Error: {}'.format(service_name, err))
        return make_response(jsonify(error_response(service_name, err)), 200)


@app.route('/yang-validator', methods=['GET'])
def health_check_yang_validator():
    service_name = 'yang-validator'
    app.LOGGER.info('Trying to ping {}'.format(service_name))
    yang_validator_preffix = '{}://{}/yangvalidator'.format(yc_gc.api_protocol, yc_gc.ip)
    body = json.dumps({'input': {'data': 'ping'}})
    headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}
    try:
        response = requests.post('{}/ping'.format(yang_validator_preffix), data=body, headers=headers)
        app.LOGGER.info('yang-validator responded with a code {}'.format(response.status_code))
        if response.status_code == 200:
            return make_response(jsonify({'info': '{} is available'.format(service_name),
                                        'status': 'running',
                                        'message': '{} responded with a code {}'.format(service_name, response.status_code)}), 200)
        elif response.status_code == 400 or response.status_code == 404:
            return make_response(jsonify({'info': '{} is available'.format(service_name),
                                        'status': 'problem',
                                        'error': '{} responded with a code {}'.format(service_name, response.status_code)}), 200)
        else:
            err = '{} responded with a code {}'.format(service_name, response.status_code)
            return make_response(jsonify(error_response(service_name, err)), 200)
    except Exception as err:
        app.LOGGER.error('Cannot ping {}. Error: {}'.format(service_name, err))
        return make_response(jsonify(error_response(service_name, err)), 200)


@app.route('/yangre', methods=['GET'])
def health_check_yangre():
    service_name = 'yangre'
    app.LOGGER.info('Trying to ping {}'.format(service_name))
    yangre_preffix = '{}://{}/yangre'.format(yc_gc.api_protocol, yc_gc.ip)
    body = json.dumps({'input': {'data': 'ping'}})
    headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}
    try:
        response = requests.post('{}/ping'.format(yangre_preffix), data=body, headers=headers)
        app.LOGGER.info('yangre responded with a code {}'.format(response.status_code))
        if response.status_code == 200:
            return make_response(jsonify({'info': '{} is available'.format(service_name),
                                        'status': 'running',
                                        'message': 'yangre responded with a code {}'.format(response.status_code)}), 200)
        elif response.status_code == 400 or response.status_code == 404:
            return make_response(jsonify({'info': '{} is available'.format(service_name),
                                        'status': 'problem',
                                        'error': 'yangre responded with a code {}'.format(response.status_code)}), 200)
        else:
            err = 'yangre responded with a code {}'.format(response.status_code)
            return make_response(jsonify(error_response(service_name, err)), 200)
    except Exception as err:
        app.LOGGER.error('Cannot ping {}. Error: {}'.format(service_name, err))
        return make_response(jsonify(error_response(service_name, err)), 200)


@app.route('/nginx', methods=['GET'])
def health_check_nginx():
    service_name = 'NGINX'
    app.LOGGER.info('Trying to ping {}'.format(service_name))
    preffix = '{}://{}'.format(yc_gc.api_protocol, yc_gc.ip)
    headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}
    try:
        response = requests.get('{}/nginx-health'.format(preffix), headers=headers)
        app.LOGGER.info('NGINX responded with a code {}'.format(response.status_code))
        if response.status_code == 200 and response.text == 'healthy':
            return make_response(jsonify({'info': 'NGINX is available',
                                        'status': 'running',
                                        'message': 'NGINX responded with a code {}'.format(response.status_code)}), 200)
        else:
            return make_response(jsonify({'info': 'Not OK - NGINX is not available',
                                        'status': 'problem',
                                        'message': 'NGINX responded with a code {}'.format(response.status_code)}), 200)
    except Exception as err:
        app.LOGGER.error('Cannot ping {}. Error: {}'.format(service_name, err))
        return make_response(jsonify(error_response(service_name, err)), 200)


@app.route('/rabbitmq', methods=['GET'])
def health_check_rabbitmq():
    service_name = 'RabbitMQ'
    app.LOGGER.info('Trying to ping {}'.format(service_name))

    arguments = ['run_ping', 'ping']
    preffix = '{}://{}/api/job'.format(yc_gc.api_protocol, yc_gc.ip)
    headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}
    try:
        job_id = yc_gc.sender.send('#'.join(arguments))
        if job_id:
            app.LOGGER.info('Sender successfully connected to RabbitMQ')
        response_type = 'In progress'
        while response_type == 'In progress':
            response = requests.get('{}/{}'.format(preffix, job_id), headers=headers)
            response_type = response.json()['info']['result']
            if response.status_code == 200 and response_type == 'Finished successfully':
                break
            else:
                time.sleep(2)
        app.LOGGER.info('Ping job responded with a message: {}'.format(response_type))
        return make_response(jsonify({'info': '{} is available'.format(service_name),
                                    'status': 'running',
                                    'message': 'Ping job responded with a message: {}'.format(response_type)}), 200)
    except Exception as err:
        if len(err) == 0:
            err = 'Check yang.log file for more details!'
        app.LOGGER.error('Cannot ping {}. Error: {}'.format(service_name, err))
        return make_response(jsonify(error_response(service_name, err)), 200)


### HELPER DEFINITIONS ###
def error_response(service_name, err):
    return {'info': 'Not OK - {} is not available'.format(service_name),
            'status': 'down',
            'error': 'Cannot ping {}. Error: {}'.format(service_name, err)
    }
