# Copyright The IETF Trust 2021, All Rights Reserved
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

"""
This script contains shared methods definitions
that are used in both dratfPull.py and draftPullLocal.py scripts.

Contains following method definitions:
    check_name_no_revision_exist()
    check_early_revisions()
    get_latest_revision()
    get_draft_module_content()
    extract_rfc_tgz
"""

__author__ = "Slavomir Mazur"
__copyright__ = "Copyright The IETF Trust 2021, All Rights Reserved"
__license__ = "Apache License, Version 2.0"
__email__ = "slavomir.mazur@pantheon.tech"

import os
import tarfile
from datetime import datetime

import requests
from utility import yangParser


def get_latest_revision(path: str, LOGGER):
    """ Search for revision in yang file

    Arguments:
        :param path     (str) full path to the yang file
        :param LOGGER   (obj) formated logger with the specified name
        :return         revision of the module at the given path
    """
    stmt = yangParser.parse(path)
    if stmt is None:  # In case of invalid YANG syntax, None is returned
        LOGGER.info('Cannot yangParser.parse {}'.format(path))
        return None
    rev = stmt.search_one('revision')
    if rev is None:
        return None

    return rev.arg


def check_name_no_revision_exist(directory: str, LOGGER_temp=None):
    """
    This function checks the format of all the modules filename.
    If it contains module with a filename without revision,
    we check if there is a module that has revision in
    its filename. If such module exists, then module with no revision
    in filename will be removed.

    Arguments:
        :param directory    (str) full path to directory with yang modules
        :param LOGGER_temp  (obj) formated logger with the specified name
    """
    LOGGER = LOGGER_temp
    LOGGER.debug('Checking revision for directory: {}'.format(directory))
    for root, dirs, files in os.walk(directory):
        for basename in files:
            if '@' in basename:
                yang_file_name = basename.split('@')[0] + '.yang'
                revision = basename.split('@')[1].split('.')[0]
                yang_file_path = '{}/{}'.format(directory, yang_file_name)
                exists = os.path.exists(yang_file_path)
                if exists:
                    compared_revision = get_latest_revision(os.path.abspath(yang_file_path), LOGGER)
                    if compared_revision is None:
                        continue
                    if revision == compared_revision:
                        os.remove(yang_file_path)


def check_early_revisions(directory: str, LOGGER_temp=None):
    """
    This function checks all modules revisions and keeps only
    ones that are the newest. If there are two modules with
    two different revisions, then the older one is removed.

    Arguments:
        :param directory    (str) full path to directory with yang modules
        :param LOGGER_temp  (obj) formated logger with the specified name
    """
    if LOGGER_temp is not None:
        LOGGER = LOGGER_temp
    for f in os.listdir(directory):
        # Extract the YANG module name from the filename
        module_name = f.split('.yang')[0].split('@')[0]   # Beware of some invalid file names such as '@2015-03-09.yang'
        if module_name == '':
            continue
        files_to_delete = []
        revisions = []
        for f2 in os.listdir(directory):
            # Same module name ?
            if f2.split('.yang')[0].split('@')[0] == module_name:
                if f2.split(module_name)[1].startswith('.') or f2.split(module_name)[1].startswith('@'):
                    files_to_delete.append(f2)
                    revision = f2.split(module_name)[1].split('.')[0].replace('@', '')
                    if revision == '':
                        yang_file_path = '{}/{}'.format(directory, f2)
                        revision = get_latest_revision(os.path.abspath(yang_file_path), LOGGER)
                        if revision is None:
                            continue
                    try:
                        # Basic date extraction can fail if there are alphanumeric characters in the revision filename part
                        year = int(revision.split('-')[0])
                        month = int(revision.split('-')[1])
                        day = int(revision.split('-')[2])
                        revisions.append(datetime(year, month, day))
                    except Exception:
                        LOGGER.exception('Failed to process revision for {}: (rev: {})'.format(f2, revision))
                        if month == 2 and day == 29:
                            revisions.append(datetime(year, month, 28))
                        else:
                            continue
        # Single revision...
        if len(revisions) == 0:
            continue
        # Keep the latest (max) revision and delete the rest
        latest = revisions.index(max(revisions))
        files_to_delete.remove(files_to_delete[latest])
        for fi in files_to_delete:
            if 'iana-if-type' in fi:
                break
            os.remove('{}/{}'.format(directory, fi))


def get_draft_module_content(ietf_draft_url: str, experimental_path: str, LOGGER):
    """ Update download links for each module found in IETFDraft.json and try to get their content.

    Aruments:
        :param ietf_draft_url       (str) URL to private IETFDraft.json file
        :param experimental_path    (str) full path to the cloned experimental modules
        :param LOGGER               (obj) formated logger with the specified name
    """
    ietf_draft_json = {}
    response = requests.get(ietf_draft_url)
    if response.status_code == 200:
        ietf_draft_json = response.json()
    for key in ietf_draft_json:
        with open('{}/{}'.format(experimental_path, key), 'w+') as yang_file:
            yang_download_link = ietf_draft_json[key][2].split('href="')[1].split('">Download')[0]
            yang_download_link = yang_download_link.replace('new.yangcatalog.org', 'yangcatalog.org')
            try:
                yang_raw = requests.get(yang_download_link).text
                yang_file.write(yang_raw)
            except:
                LOGGER.warning('{} - {}'.format(key, yang_download_link))
                yang_file.write('')


def extract_rfc_tgz(tgz_path: str, extract_to: str, LOGGER):
    """ Extract downloaded rfc.tgz file to directory and remove file.

    Arguments:
        :param tgz_path     (str) full path to the rfc.tgz file
        :param extract_to   (str) path to the directory where rfc.tgz is extractracted to
        :param LOGGER       (obj) formated logger with the specified name
    """
    tar_opened = False
    tgz = ''
    try:
        tgz = tarfile.open(tgz_path)
        tar_opened = True
        tgz.extractall(extract_to)
        tgz.close()
    except tarfile.ReadError:
        LOGGER.warning('tarfile could not be opened. It might not have been generated yet.'
                       ' Did the sdo_analysis cron job run already?')
    os.remove(tgz_path)

    return tar_opened
