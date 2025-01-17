# Copyright 2023 Jetperch LLC
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

from joulescope_ui import pubsub_singleton, frozen, __version__
from joulescope_ui.tokens import REPORTER_TOKEN
import datetime
import importlib.metadata
import io
import json
import os
import pkgutil
import platform
import psutil
import requests
import sys
import traceback
import zipfile


CONFIG_PATH = pubsub_singleton.query('common/settings/paths/config')
LOG_PATH = pubsub_singleton.query('common/settings/paths/log')
REPORTER_PATH = pubsub_singleton.query('common/settings/paths/reporter')
_API_URL = 'https://k9x78sjeqi.execute-api.us-east-1.amazonaws.com/uploads'


def platform_info() -> dict:
    """Get the platform information.

    :return: A dict containing information about the host system.
    """
    cpufreq = psutil.cpu_freq()
    vm = psutil.virtual_memory()
    return {
        'python': sys.version,
        'python_impl': platform.python_implementation(),
        'executable': sys.executable,
        'platform': platform.platform(),
        'processor': platform.processor(),
        'cpu_cores': {
            'physical': psutil.cpu_count(logical=False),
            'logical': psutil.cpu_count(logical=True),
        },
        'cpu_frequency': {
            'current': cpufreq.current,
            'min': cpufreq.min,
            'max': cpufreq.max,
        },
        'ram': {
            'used': vm.used,
            'available': vm.total - vm.used,
            'total': vm.total,
        },
        'frozen': frozen,
    }


def package_versions():
    p = []
    for m in pkgutil.iter_modules():
        try:
            p.append((m.name, importlib.metadata.version(m.name)))
        except importlib.metadata.PackageNotFoundError:
            pass
    return dict(sorted(p))


def update_description(path: str, description):
    """Update the report description.

    :param path: The full report path, as returned by :func:`create`.
    :param description: The markdown formatted description.
    """
    with zipfile.ZipFile(path, mode='a') as z:
        try:
            if isinstance(description, str):
                with z.open('description.md', 'w') as f1:
                    f1.write(description.encode('utf-8'))
        except Exception:
            print('Could not save description')


def update_contact(path: str, contact: dict):
    """Update the contact information

    :param path: The full report path, as returned by :func:`create`.
    :param contact: The contact information dict with keys:
        * first_name: The contact's first name.
        * email: The contact's email address.
    """
    with zipfile.ZipFile(path, mode='a') as z:
        try:
            with z.open('contact.json', 'w') as f1:
                f1.write(json.dumps(contact).encode('utf-8'))
        except Exception:
            print('Could not save contact')


def create(subtype, description=None, exception=None):
    """Create a new report.

    :param subtype: The report subtype, which is normally one of:
        * crash: A crash report.
        * user: A user bug report.
    :param description: The detailed description, if available.
    :param exception: The Exception instance, if available.
    :return: The path to the error report ZIP file.
    """
    d = datetime.datetime.utcnow()
    time_str = d.strftime('%Y%m%d_%H%M%S')
    filename = f'{time_str}.zip'
    path = os.path.join(REPORTER_PATH, filename)
    os.makedirs(REPORTER_PATH, exist_ok=True)

    index = {
        'ui_version': __version__,
        'type': 'joulescope-ui-report',
        'subtype': subtype,
        'version': 1,
        'time': d.isoformat() + 'Z',
        'filename': filename,
        'platform': platform_info(),
    }

    try:
        log_files = sorted(os.listdir(LOG_PATH))[-4:]
    except FileNotFoundError:
        log_files = []
    try:
        config_files = sorted(os.listdir(CONFIG_PATH))
    except FileNotFoundError:
        config_files = []

    with zipfile.ZipFile(path, mode='w') as z:
        try:
            if isinstance(exception, Exception):
                with z.open('exception.txt', 'w') as f1:
                    with io.TextIOWrapper(f1, 'utf-8') as f2:
                        traceback.print_exception(exception, file=f2)
        except Exception:
            print('Could not save exception')

        try:
            if isinstance(description, str):
                with z.open('description.md', 'w') as f1:
                    f1.write(description.encode('utf-8'))
        except Exception:
            print('Could not save description')

        try:
            with z.open('package_list.json', 'w') as f1:
                with io.TextIOWrapper(f1, 'utf-8') as f2:
                    json.dump(package_versions(), f2, indent=2)
        except Exception:
            print('Could not save package versions')

        try:
            for config_file in config_files:
                fname = f'config/{config_file}'
                with open(os.path.join(CONFIG_PATH, config_file), 'rb') as fin:
                    try:
                        with z.open(fname, 'w') as fout:
                            fout.write(fin.read())
                    except Exception:
                        print(f'Could not save config file {fname}')
        except Exception:
            print('Could not save config files')

        try:
            for log_file in log_files:
                fname = f'log/{log_file}'
                with open(os.path.join(LOG_PATH, log_file), 'rb') as fin:
                    try:
                        with z.open(fname, 'w') as fout:
                            fout.write(fin.read())
                    except Exception:
                        print(f'Could not save log file {fname}')
        except Exception:
            print('Could not save log files')

        try:
            with z.open('index.json', 'w') as f1:
                with io.TextIOWrapper(f1, 'utf-8') as f2:
                    json.dump(index, f2, indent=2)
        except Exception:
            print('Could not save index')

    return path


def publish():
    results = []
    for fname in sorted(os.listdir(REPORTER_PATH), reverse=True):
        try:
            r = requests.get(_API_URL, params={'token': REPORTER_TOKEN})
            if r.status_code != 200:
                print(f'Could not get publish url: status code {r.status_code}')
                continue
            upload = r.json()
            key = upload['Key'].split('.')[0]
            headers = {
                'Content-Type': 'application/octet-stream',
            }
            path = os.path.join(REPORTER_PATH, fname)
            with open(path, 'rb') as f:
                data = f.read()
            r = requests.put(upload['uploadURL'], headers=headers, data=data)
            if r.status_code == 200:
                os.remove(path)
                results.append(f'Uploaded {key}')
            else:
                print(f'publish failed with status code {r.status_code}')
                results.append(f'Error {r.status_code} while uploading {key}')
        except Exception:
            traceback.print_exception()
            print(f'could not publish {fname}')
    return results
