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

from PySide6 import QtWidgets, QtGui, QtCore
from joulescope_ui import N_, pubsub_singleton, get_topic_name, register
from joulescope_ui.styles import styled_widget
from joulescope_ui.widgets import DraggableListWidget
from joulescope_ui.styles.manager import RENDER_TOPIC
from joulescope_ui.view import View


_VIEW_LIST_TOPIC = 'registry/view/instances'
_SPECIAL_VIEWS = ['view:multimeter', 'view:oscilloscope', 'view:file']


class _ViewItem(QtWidgets.QWidget):

    def __init__(self, parent, unique_id):
        super().__init__(parent)
        self.unique_id = unique_id
        self._layout = QtWidgets.QHBoxLayout(self)
        self._layout.setContentsMargins(0, 6, 0, 6)

        self._on_pubsub_name_fn = self._on_pubsub_name

        self._move_start = QtWidgets.QLabel(self)
        self._move_start.setObjectName('move')
        self._move_start.setFixedSize(20, 20)
        is_special = unique_id in _SPECIAL_VIEWS
        if is_special:
            self._name = QtWidgets.QLabel(parent=self)
        else:
            self._name = QtWidgets.QLineEdit(parent=self)
            self._name.textEdited.connect(self._on_name_changed)
        self._action = QtWidgets.QPushButton(self)
        self._action.setFixedSize(20, 20)

        self._layout.addWidget(self._move_start)
        self._layout.addWidget(self._name)
        self._spacer = QtWidgets.QSpacerItem(1, 1, QtWidgets.QSizePolicy.Expanding,
                                             QtWidgets.QSizePolicy.Minimum)
        self._layout.addItem(self._spacer)
        self._layout.addWidget(self._action)

        if is_special:
            self._action.setObjectName('view_reset')
            self._action.clicked.connect(self._on_view_reset)
        else:
            self._action.setObjectName('view_delete')
            self._action.clicked.connect(self._on_view_delete)

        pubsub_singleton.subscribe(f'{get_topic_name(self.unique_id)}/settings/name',
                                   self._on_pubsub_name_fn, ['pub', 'retain'])

    def disconnect(self):
        pubsub_singleton.unsubscribe(f'{get_topic_name(self.unique_id)}/settings/name', self._on_pubsub_name_fn)

    def _on_pubsub_name(self, value):
        if value != self._name.text():
            self._name.setText(value)

    def _on_name_changed(self, name):
        pubsub_singleton.publish(f'{get_topic_name(self.unique_id)}/settings/name', name)

    @property
    def is_active_view(self):
        active_view = pubsub_singleton.query('registry/view/settings/active')
        return active_view == self.unique_id

    def _on_view_reset(self, checked):
        active_view = pubsub_singleton.query('registry/view/settings/active')
        pubsub_singleton.publish('registry/view/settings/active', self.unique_id)
        for child in pubsub_singleton.query(f'registry/{self.unique_id}/children'):
            pubsub_singleton.publish('registry/view/actions/!widget_close', child)

        if self.unique_id == 'view:multimeter':
            pubsub_singleton.publish('registry/view/actions/!widget_open', 'MultimeterWidget')
        elif self.unique_id == 'view:oscilloscope':
            pubsub_singleton.publish('registry/view/actions/!widget_open', {
                'value': 'WaveformWidget',
                'kwargs': {'source_filter': 'JsdrvStreamBuffer:001'},
            })
        pubsub_singleton.publish('registry/view/settings/active', active_view)

    def _on_view_delete(self, checked):
        if self.is_active_view:
            items = pubsub_singleton.query(_VIEW_LIST_TOPIC)
            next_view = items[0] if items[0] != self.unique_id else items[1]
            pubsub_singleton.publish('registry/view/settings/active', next_view)
        pubsub_singleton.publish('registry/view/actions/!remove', self.unique_id)
        self.parentWidget().item_remove(self)
        self.disconnect()

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton and self._move_start.underMouse():
            # Start the drag operation
            pos = event.position().toPoint()
            self.parentWidget().drag_start(self, pos)


@register
@styled_widget(N_('sidebar'))
class ViewManagerWidget(QtWidgets.QWidget):
    """Manage the views."""

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self._menu = None
        self._layout = QtWidgets.QVBoxLayout(self)

        self._view_list = DraggableListWidget(self)
        views = pubsub_singleton.query(_VIEW_LIST_TOPIC)
        for unique_id in views:
            item = _ViewItem(self, unique_id)
            self._view_list.item_add(item)
        self._layout.addWidget(self._view_list)
        self._view_list.order_changed.connect(self._on_order_changed)

        self._bottom = QtWidgets.QWidget(self)
        self._layout.addWidget(self._bottom)
        self._bottom_layout = QtWidgets.QHBoxLayout(self._bottom)

        self._add_button = QtWidgets.QPushButton()
        self._add_button.setObjectName('view_add')
        self._add_button.setFixedSize(20, 20)
        self._add_button.clicked.connect(self._on_add)

        self._bottom_layout.addWidget(self._add_button)

        self._spacer = QtWidgets.QSpacerItem(1, 1, QtWidgets.QSizePolicy.Expanding,
                                             QtWidgets.QSizePolicy.Minimum)
        self._bottom_layout.addItem(self._spacer)

        self.ok_button = QtWidgets.QPushButton('OK')
        self._bottom_layout.addWidget(self.ok_button)

    def _on_add(self, checked):
        name = N_('New View')
        view = View()
        pubsub_singleton.register(view)
        pubsub_singleton.publish(f'{get_topic_name(view)}/settings/name', name)
        item = _ViewItem(self, view.unique_id)
        self._view_list.item_add(item)

    def _on_order_changed(self, items):
        unique_ids = [item.unique_id for item in items]
        pubsub_singleton.publish(_VIEW_LIST_TOPIC, unique_ids)

    def cleanup(self):
        for item in self._view_list.items:
            item.disconnect()


class ViewManagerDialog(QtWidgets.QDialog):

    _instances = []

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self._layout = QtWidgets.QVBoxLayout(self)

        self._widget = ViewManagerWidget(self)
        self._layout.addWidget(self._widget)
        pubsub_singleton.register(self._widget, parent='ui')
        pubsub_singleton.publish(RENDER_TOPIC, self._widget)

        self._widget.ok_button.pressed.connect(self.accept)
        self.finished.connect(self._on_finish)

        self.setWindowTitle(N_('View Manager'))
        self.open()
        ViewManagerDialog._instances.append(self)

    def _on_finish(self):
        pubsub_singleton.unregister(self._widget, delete=True)
        self._widget.cleanup()
        while self in ViewManagerDialog._instances:
            ViewManagerDialog._instances.remove(self)

