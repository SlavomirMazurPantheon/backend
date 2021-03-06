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

__author__ = "Slavomir Mazur"
__copyright__ = "Copyright The IETF Trust 2020, All Rights Reserved"
__license__ = "Apache License, Version 2.0"
__email__ = "slavomir.mazur@pantheon.tech"

import json
import os
import unittest

from api.globalConfig import yc_gc
from parseAndPopulate.loadJsonFiles import LoadFiles
from parseAndPopulate.modules import Modules


class TestModulesClass(unittest.TestCase):

    def __init__(self, *args, **kwargs):
        super(TestModulesClass, self).__init__(*args, **kwargs)

        # Declare variables
        self.schema = 'https://raw.githubusercontent.com/YangModels/yang/master/standard/ietf/RFC/ietf-yang-types.yang'
        self.tmp_dir = '{}/'.format(yc_gc.temp_dir)
        self.sdo_module_filename = 'ietf-yang-types@2013-07-15.yang'
        self.sdo_module_name = 'ietf-yang-types'
        self.hello_message_filename = 'capabilities-ncs5k.xml'
        self.resources_path = '{}/resources'.format(os.path.dirname(os.path.abspath(__file__)))
        self.test_private_dir = 'tests/resources/html/private'
        self.parsed_jsons = LoadFiles(self.test_private_dir, yc_gc.logs_dir)

    #########################
    ### TESTS DEFINITIONS ###
    #########################

    def test_modules_parse_all_sdo_object(self):
        """
        Create modules object from SDO (= ietf) YANG file,
        and compare object properties values after calling parse_all() method.
        """
        path_to_yang = '{}/{}'.format(yc_gc.save_file_dir, self.sdo_module_filename)

        yang = Modules(yc_gc.yang_models, yc_gc.logs_dir, path_to_yang,
                       yc_gc.result_dir, self.parsed_jsons, self.tmp_dir)
        yang.parse_all('master', self.sdo_module_name, {},
                       self.schema, None, yc_gc.save_file_dir)

        self.assertEqual(yang.document_name, 'rfc6991')
        self.assertEqual(yang.generated_from, 'not-applicable')
        self.assertEqual(yang.maturity_level, 'ratified')
        self.assertEqual(yang.module_type, 'module')
        self.assertEqual(yang.name, 'ietf-yang-types')
        self.assertEqual(yang.namespace, 'urn:ietf:params:xml:ns:yang:ietf-yang-types')
        self.assertEqual(yang.organization, 'ietf')
        self.assertEqual(yang.prefix, 'yang')
        self.assertEqual(yang.reference, 'https://tools.ietf.org/html/rfc6991')
        self.assertEqual(yang.revision, '2013-07-15')
        self.assertEqual(yang.yang_version, '1.0')

    def test_modules_parse_all_sdo_object_already_in_keys(self):
        """
        Create modules object from SDO (= ietf) YANG file,
        and compare object properties values after calling parse_all() method.
        Pass keys as an argument so only some properties will be resolved, while other will stay set to None.
        """
        path_to_yang = '{}/{}'.format(yc_gc.save_file_dir, self.sdo_module_filename)
        keys = {'ietf-yang-types@2013-07-15/ietf'}

        yang = Modules(yc_gc.yang_models, yc_gc.logs_dir, path_to_yang,
                       yc_gc.result_dir, self.parsed_jsons, self.tmp_dir)
        yang.parse_all('master', self.sdo_module_name, keys,
                       self.schema, None, yc_gc.save_file_dir)

        self.assertEqual(yang.name, 'ietf-yang-types')
        self.assertEqual(yang.module_type, 'module')
        self.assertEqual(yang.organization, 'ietf')
        self.assertEqual(yang.revision, '2013-07-15')
        self.assertEqual(yang.namespace, 'urn:ietf:params:xml:ns:yang:ietf-yang-types')
        self.assertEqual(yang.author_email, None)
        self.assertEqual(yang.reference, None)
        self.assertEqual(yang.maturity_level, None)
        self.assertEqual(yang.generated_from, None)
        self.assertEqual(yang.module_classification, None)
        self.assertEqual(yang.document_name, None)

    def test_modules_parse_all_vendor_object(self):
        """
        Create modules object from vendor YANG file,
        and compare object properties values after calling parse_all() method.
        """
        xml_path = '{}/tmp/master/vendor/cisco/xr/701/{}'.format(self.resources_path, self.hello_message_filename)
        yang_lib_data = 'ietf-netconf-acm&revision=2018-02-14&deviations=cisco-xr-ietf-netconf-acm-deviations'
        module_name = yang_lib_data.split('&revision')[0]
        deviation = yang_lib_data.split('&deviations=')[1]

        yang = Modules(yc_gc.yang_models, yc_gc.logs_dir, xml_path, yc_gc.result_dir,
                       self.parsed_jsons, self.tmp_dir, is_vendor=True, data=yang_lib_data)
        yang.parse_all('master', module_name, {},
                       '', None, yc_gc.save_file_dir)

        self.assertEqual(yang.document_name, 'rfc8341')
        self.assertEqual(yang.generated_from, 'not-applicable')
        self.assertEqual(yang.maturity_level, 'ratified')
        self.assertEqual(yang.module_type, 'module')
        self.assertEqual(yang.name, module_name)
        self.assertEqual(yang.namespace, 'urn:ietf:params:xml:ns:yang:ietf-netconf-acm')
        self.assertEqual(yang.organization, 'ietf')
        self.assertEqual(yang.prefix, 'nacm')
        self.assertEqual(yang.reference, 'https://tools.ietf.org/html/rfc8341')
        self.assertEqual(yang.revision, '2018-02-14')
        self.assertIn(deviation, yang.deviations)

    def test_modules_add_vendor_information(self):
        """
        Create modules object from vendor (= cisco) YANG file.
        Vendor information are then added using add_vendor_information() method and object values are compared
        with data from platform-metadata.json.
        """
        xml_path = '{}/tmp/master/vendor/cisco/xr/701/{}'.format(self.resources_path, self.hello_message_filename)
        yang_lib_data = 'ietf-netconf-acm&revision=2018-02-14&deviations=cisco-xr-ietf-netconf-acm-deviations'
        module_name = yang_lib_data.split('&revision')[0]
        platform_name = 'ncs5k'

        platform_data, netconf_version, netconf_capabilities = self.get_platform_data(xml_path, platform_name)

        yang = Modules(yc_gc.yang_models, yc_gc.logs_dir, xml_path, yc_gc.result_dir,
                       self.parsed_jsons, self.tmp_dir, is_vendor=True, data=yang_lib_data)
        yang.parse_all('master', module_name, {},
                       '', None, yc_gc.save_file_dir)
        yang.add_vendor_information(platform_data,
                                    'implement',
                                    netconf_capabilities,
                                    netconf_version,
                                    None,
                                    xml_path.split('/'))

        self.assertNotEqual(len(yang.implementation), 0)
        self.assertNotEqual(len(platform_data), 0)
        for implementation, platform in zip(yang.implementation, platform_data):
            self.assertEqual(implementation.feature_set, platform['feature-set'])
            self.assertEqual(implementation.netconf_version, netconf_version)
            self.assertEqual(implementation.os_type, platform['os'])
            self.assertEqual(implementation.os_version, platform['os-version'])
            self.assertEqual(implementation.platform, platform['platform'])
            self.assertEqual(implementation.software_flavor, platform['software-flavor'])
            self.assertEqual(implementation.software_version, platform['software-version'])
            self.assertEqual(implementation.vendor, platform['vendor'])

    def test_modules_add_vendor_information_is_yang_lib(self):
        """
        Create modules object from yang_lib (= huawei) YANG file.
        Vendor information are then added using add_vendor_information() method and object values are compared
        with data from platform-metadata.json.
        """
        yang_lib_info = {
            'path': '{}/tmp/master/vendor/huawei/network-router/8.20.0/ne5000e'.format(self.resources_path),
            'name': 'huawei-aaa',
            'features': [],
            'deviations': [{'name': 'huawei-aaa-deviations-NE-X1X2', 'revision': '2019-04-23'}],
            'revision': '2020-07-01'
        }
        schema_part = 'https://raw.githubusercontent.com/YangModels/yang/master/'
        xml_path = '{}/tmp/master/vendor/huawei/network-router/8.20.0/ne5000e/ietf-yang-library.xml'.format(self.resources_path)
        module_name = 'huawei-aaa'
        platform_name = 'ne5000e'

        platform_data, netconf_version, netconf_capabilities = self.get_platform_data(xml_path, platform_name)

        yang = Modules(yc_gc.yang_models, yc_gc.logs_dir, xml_path, yc_gc.result_dir,
                       self.parsed_jsons, self.tmp_dir, True, True, yang_lib_info)
        yang.parse_all('master', module_name, {},
                       schema_part, None, yc_gc.save_file_dir)

        yang.add_vendor_information(platform_data,
                                    'implement',
                                    netconf_capabilities,
                                    netconf_version,
                                    None,
                                    xml_path.split('/'))

        self.assertNotEqual(len(yang.implementation), 0)
        self.assertNotEqual(len(platform_data), 0)
        for implementation, platform in zip(yang.implementation, platform_data):
            self.assertEqual(implementation.feature_set, platform['feature-set'])
            self.assertEqual(implementation.netconf_version, netconf_version)
            self.assertEqual(implementation.os_type, platform['os'])
            self.assertEqual(implementation.os_version, platform['os-version'])
            self.assertEqual(implementation.platform, platform['platform'])
            self.assertEqual(implementation.software_flavor, platform['software-flavor'])
            self.assertEqual(implementation.software_version, platform['software-version'])
            self.assertEqual(implementation.vendor, platform['vendor'])

    ##########################
    ### HELPER DEFINITIONS ###
    ##########################

    def get_platform_data(self, xml_path: str, platform_name: str):
        """
        Load content of platform-metadata.json file and parse data of selected platform.

        :param xml_path         (str) Absolute path of selected .xml file
        :param platform_name    (str) Name of platform to find
        """
        platform_data = []
        netconf_version = netconf_capabilities = set()

        with open('/'.join(xml_path.split('/')[:-1]) + '/platform-metadata.json', 'r', encoding='utf-8') as f:
            file_content = json.load(f)
            platforms = file_content['platforms']['platform']
        for platform in platforms:
            if platform_name == platform['name']:
                platform_data.append({'software-flavor': platform['software-flavor'],
                                      'platform': platform['name'],
                                      'os-version': platform['software-version'],
                                      'software-version': platform['software-version'],
                                      'feature-set': "ALL",
                                      'vendor': platform['vendor'],
                                      'os': platform['os-type']})
                if 'netconf-capabilities' in platform:
                    netconf_version = [
                        capability for capability in platform['netconf-capabilities'] if ':netconf:base:' in capability]
                    netconf_capabilities = [
                        capability for capability in platform['netconf-capabilities'] if ':capability:' in capability]

        return platform_data, netconf_version, netconf_capabilities


if __name__ == "__main__":
    unittest.main()
