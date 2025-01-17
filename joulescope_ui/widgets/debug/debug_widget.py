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

from PySide6 import QtWidgets
from joulescope_ui import N_, pubsub_singleton
from joulescope_ui.styles import styled_widget
import cProfile
from io import StringIO
import gc
import os
import pstats
import tracemalloc


_SNAKEVIZ = """\
<html>
<body>
&nbsp;&nbsp;<a href="https://jiffyclub.github.io/snakeviz/">snakeviz</a>
{path}
</body>
</html>
"""


@styled_widget(N_('Debug'))
class DebugWidget(QtWidgets.QWidget):
    """A debug widget for developers."""

    CAPABILITIES = ['widget@']
    SETTINGS = {
        'state': {
            'dtype': 'obj',
            'brief': N_('The debug state.'),
            'flags': ['hide', 'dev', 'noinit', 'tmp'],
        }
    }

    def __init__(self):
        super().__init__()
        self._state = None
        self._menu = None
        self._layout = QtWidgets.QGridLayout(self)

        self._profile_label = QtWidgets.QLabel('cProfile', self)
        self._profile = QtWidgets.QPushButton(self)
        self._profile.setText('Run')
        self._profile.setCheckable(True)
        self._profile.toggled.connect(self._on_profile)
        self._layout.addWidget(self._profile_label, 0, 0, 1, 1)
        self._layout.addWidget(self._profile, 0, 1, 1, 1)

        self._snakeviz_label = QtWidgets.QLabel(self)
        self._snakeviz_label.setOpenExternalLinks(True)
        self._snakeviz_label.setText(_SNAKEVIZ.format(path=''))
        self._snakeviz_copy = QtWidgets.QPushButton('Copy', self)
        self._snakeviz_copy.pressed.connect(self._on_snakeviz_copy)

        self._layout.addWidget(self._snakeviz_label, 1, 0, 1, 1)
        self._layout.addWidget(self._snakeviz_copy, 1, 1, 1, 1)

        self._memory_label = QtWidgets.QLabel('Memory', self)
        self._memory_baseline = QtWidgets.QPushButton(self)
        self._memory_baseline.setText('Baseline')
        self._memory_baseline.pressed.connect(self._on_memory_baseline)

        self._memory_compare = QtWidgets.QPushButton(self)
        self._memory_compare.setText('Compare')
        self._memory_compare.pressed.connect(self._on_memory_compare)

        self._layout.addWidget(self._memory_label, 2, 0, 1, 1)
        self._layout.addWidget(self._memory_baseline, 2, 1, 1, 1)
        self._layout.addWidget(self._memory_compare, 3, 1, 1, 1)

        self._text_note = QtWidgets.QLabel('OUTPUT (automatically copied to clipboard)', self)
        self._layout.addWidget(self._text_note, 4, 0, 1, 2)
        self._text = QtWidgets.QTextEdit()
        self._layout.addWidget(self._text, 5, 0, 5, 2)
        self._text_clipboard = None

    def on_pubsub_register(self):
        self._state = pubsub_singleton.query('registry/DebugWidget/settings/state')
        if self._state is None:
            self._state = {
                'profile': None,
                'profile_path': None,
                'snapshot': None
            }
            pubsub_singleton.publish('registry/DebugWidget/settings/state', self._state)

    def _on_profile(self, checked):
        p = pubsub_singleton.query('common/settings/paths/log')
        idx = 0
        while True:
            path = os.path.join(p, f'{idx:04d}.profile')
            if not os.path.isfile(path):
                break
            idx += 1
        self._state['profile_path'] = path
        profile = self._state['profile']

        if checked:
            profile = cProfile.Profile()
            profile.enable()
            self._state['profile'] = profile
        elif profile is not None:
            profile.disable()
            profile.dump_stats(path)
            t = StringIO()
            s = pstats.Stats(path, stream=t)
            s.strip_dirs().sort_stats("time").print_stats()
            self._state['profile'] = None
            self._text_set(t.getvalue())
            self._snakeviz_label.setText(_SNAKEVIZ.format(path=path))

    def _on_snakeviz_copy(self):
        path = self._state['profile_path']
        text = f'snakeviz {path}'
        self._text_clipboard = text
        QtWidgets.QApplication.clipboard().setText(self._text_clipboard)

    def _on_memory_baseline(self):
        if self._state['snapshot'] is None:
            tracemalloc.start()
        gc.collect()
        self._state['snapshot'] = tracemalloc.take_snapshot()

    def _on_memory_compare(self):
        gc.collect()
        if self._state['snapshot'] is None:
            return
        snapshot = tracemalloc.take_snapshot()
        filters = [tracemalloc.Filter(inclusive=False, filename_pattern='*tracemalloc*')]
        stats = snapshot.filter_traces(filters).compare_to(self._state['snapshot'], 'lineno')
        self._text_set('\n'.join([str(s) for s in stats]))

    def _text_set(self, txt):
        self._text.setText(txt)
        self._text_clipboard = txt
        QtWidgets.QApplication.clipboard().setText(self._text_clipboard)
