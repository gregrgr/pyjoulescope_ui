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

from PySide6 import QtCore, QtGui, QtWidgets
from joulescope_ui.expanding_widget import ExpandingWidget
import logging
from joulescope_ui import N_, register, tooltip_format, pubsub_singleton, \
    get_instance, get_topic_name, Metadata, urls
from joulescope_ui.styles import color_as_qcolor
from joulescope_ui.ui_util import comboBoxConfig
from .device_info_dialog import DeviceInfoDialog
from .current_limits import CurrentLimits
import webbrowser


_DOC_TOOLTIP = tooltip_format(
    N_('Device documentation'),
    N_('Click to display the device documentation PDF.')
)

_INFO_TOOLTIP = tooltip_format(
    N_('Device information'),
    N_('Click to display detailed information about the device.')
)

_DEFAULT_DEVICE_TOOLTIP = tooltip_format(
    N_('Select this device as the default'),
    N_("""\
    When selected, this device because the default.  All widgets
    using the default device will use the data provided by this
    device.
    
    When unselected, another device is the default.  Widgets
    can still be configured to use data from this device.\
    """),
)

_OPEN_TOOLTIP = tooltip_format(
    N_('Open and close the device'),
    N_("""\
    When closed, click to attempt to open the device.  The icon
    will only change on a successful device open.  Only one
    application can use a Joulescope device at a time.
    
    When open, click to close the device.  This allows the device
    to be used in other programs.\
    """),
)

_RESET_TO_DEFAULTS_TOOLTIP = tooltip_format(
    N_('Reset to default settings'),
    N_('Click this button to reset this device to the default settings'),
)

_CLEAR_ACCUM_TOOLTIP = tooltip_format(
    N_('Clear accumulators'),
    N_("""\
    Click this button to clear the charge and energy accumulators.
    The "Accrue" feature of the value display widgets, including
    the multimeter, will be unaffected.\
    """),
)

_BUTTON_SIZE = (20, 20)


def _construct_pushbutton(parent, name, checkable=False, tooltip=None):
    b = QtWidgets.QPushButton(parent)
    b.setObjectName(name)
    b.setProperty('blink', False)
    b.setCheckable(checkable)
    b.setFixedSize(*_BUTTON_SIZE)
    b.setToolTip(tooltip)
    return b


class Js220CtrlWidget(QtWidgets.QWidget):

    def __init__(self, parent, unique_id):
        self._parent = parent
        self.unique_id = unique_id
        self._widgets = []
        self._control_widgets = {}  # name, widget
        self._unsub = []  # (topic, fn)
        self._row = 0
        self._signals = {}
        self._gpo = {}
        self._footer = {}
        self._log = logging.getLogger(f'{__name__}.{unique_id}')
        if 'JS110' in unique_id:
            self._USERS_GUIDE_URL = urls.JS110_USERS_GUIDE
            self._GPI_SIGNALS = ['0', '1']
        elif 'JS220' in unique_id:
            self._USERS_GUIDE_URL = urls.JS220_USERS_GUIDE
            self._GPI_SIGNALS = ['0', '1', '2', '3', 'T']
        else:
            raise ValueError(f'unsupported device {unique_id}')
        self._DEVICE_SETTINGS = get_instance(unique_id).SETTINGS
        self._buttons_blink = []
        self._target_power_button: QtWidgets.QPushButton = None
        self._info_button: QtWidgets.QPushButton = None
        super().__init__(parent)

        self._layout = QtWidgets.QVBoxLayout()
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._expanding = ExpandingWidget(self)
        self._expanding.title = unique_id

        self._body = QtWidgets.QWidget(self)
        self._body_layout = QtWidgets.QGridLayout(self)
        self._body_layout.setContentsMargins(0, 0, 0, 0)
        self._body_layout.setSpacing(1)
        self._body.setLayout(self._body_layout)
        self._expanding.body_widget = self._body

        self._layout.addWidget(self._expanding)
        self.setLayout(self._layout)

        self._header_widgets = []
        self._expanding.header_ex_widget = self._construct_header()

        self._add_signal_buttons()
        self._add_settings()
        self._add_gpo()
        self._add_footer()
        self._subscribe('registry/ui/events/blink_slow', self._on_blink)
        self._subscribe('registry/app/settings/target_power', self._on_target_power_app)
        topic = get_topic_name(self.unique_id)
        for signal in self._GPI_SIGNALS:
            self._gpi_subscribe(f'{topic}/events/signals/{signal}/!data', signal)
        self._subscribe(f'{topic}/settings/state', self._on_setting_state)

    @property
    def is_js220(self):
        return 'JS220' in self.unique_id

    def _subscribe(self, topic, update_fn):
        pubsub_singleton.subscribe(topic, update_fn, ['pub', 'retain'])
        self._unsub.append((topic, update_fn))

    def _gpi_subscribe(self, topic, signal):
        self._subscribe(topic, lambda v: self._on_gpi_n(signal, v))

    def _on_gpi_n(self, signal, value):
        d = value.get('data')
        if d is None or 0 == len(d):
            self._log.info('Empty GPI %s', signal)
            return
        signal_level = 1 if (d[0] != 0) else 0
        b = self._signals['buttons'][signal]
        if b.property('signal_level') != signal_level:
            open = self.findChild(QtWidgets.QPushButton, "open")
            if not open.isChecked():
                signal_level = 0
            name = 'device_control_signal_on' if signal_level else 'device_control_signal'
            b.setObjectName(name)
            b.setProperty('signal_level', signal_level)
            b.style().unpolish(b)
            b.style().polish(b)

    def _on_setting_state(self, value):
        if self._info_button is not None:
            self._info_button.setEnabled(value == 2)

    def _on_target_power_app(self, value):
        b = self._target_power_button
        b.setEnabled(bool(value))
        b.style().unpolish(b)
        b.style().polish(b)

    def _on_info(self, *args, **kwargs):
        self._log.info('on_info')
        info = pubsub_singleton.query(f'{get_topic_name(self.unique_id)}/settings/info')
        DeviceInfoDialog(info)

    def _construct_header(self):
        w = QtWidgets.QWidget(self._expanding)
        layout = QtWidgets.QHBoxLayout(w)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)

        doc = _construct_pushbutton(w, 'doc', tooltip=_DOC_TOOLTIP)
        doc.clicked.connect(lambda checked: webbrowser.open_new_tab(self._USERS_GUIDE_URL))
        layout.addWidget(doc)

        if self.is_js220:
            info = _construct_pushbutton(w, 'info', tooltip=_INFO_TOOLTIP)
            info.clicked.connect(self._on_info)
            self._info_button = info
            layout.addWidget(info)
        else:
            info = None

        default_device = self._construct_default_device_button(w)
        layout.addWidget(default_device)

        target_power = self._construct_target_power_button(w)
        layout.addWidget(target_power)

        open_button = self._construct_open_button(w)
        layout.addWidget(open_button)

        w.setLayout(layout)
        self._header_widgets = [w, layout, doc, info, default_device, target_power, open_button]
        return w

    def _construct_default_device_button(self, parent):
        topics = [
            'registry/app/settings/defaults/statistics_stream_source',
            'registry/app/settings/defaults/signal_stream_source',
        ]
        b = _construct_pushbutton(parent, 'default_device', checkable=True, tooltip=_DEFAULT_DEVICE_TOOLTIP)

        def update_from_pubsub(value):
            block_state = b.blockSignals(True)
            b.setChecked(value == self.unique_id)
            b.blockSignals(block_state)

        def on_pressed(checked):
            block_state = b.blockSignals(True)
            b.setChecked(True)
            b.blockSignals(block_state)
            for topic in topics:
                pubsub_singleton.publish(topic, self.unique_id)

        self._subscribe(topics[0], update_from_pubsub)
        b.toggled.connect(on_pressed)
        return b

    def _construct_target_power_button(self, parent):
        topic = f'{get_topic_name(self.unique_id)}/settings/target_power'
        meta = pubsub_singleton.metadata(topic)
        b = _construct_pushbutton(parent, 'target_power', checkable=True,
                                  tooltip=tooltip_format(meta.brief, meta.detail))
        self._buttons_blink.append(b)

        def update_from_pubsub(value):
            block_state = b.blockSignals(True)
            b.setChecked(bool(value))
            b.blockSignals(block_state)

        self._target_power_button = b
        self._subscribe(topic, update_from_pubsub)
        b.toggled.connect(lambda checked: pubsub_singleton.publish(topic, bool(checked)))
        return b

    def _gpi_state_clear(self):
        for signal in self._GPI_SIGNALS:
            b = self._signals['buttons'][signal]
            b.setObjectName('device_control_signal')
            b.setProperty('signal_level', 0)
            b.style().unpolish(b)
            b.style().polish(b)

    def _construct_open_button(self, parent):
        self_topic = get_topic_name(self.unique_id)
        state_topic = f'{self_topic}/settings/state'
        b = _construct_pushbutton(parent, 'open', checkable=True, tooltip=_OPEN_TOOLTIP)

        def state_from_pubsub(value):
            checked = (value == 2)  # open (not closed, opening, or closing)
            block_state = b.blockSignals(True)
            b.setChecked(checked)
            b.blockSignals(block_state)
            if not checked and 'buttons' in self._signals:
                self._gpi_state_clear()

        def on_toggle(checked):
            checked = bool(checked)
            block_state = b.blockSignals(True)
            b.setChecked(not checked)
            b.blockSignals(block_state)
            state_req = 1 if checked else 0
            pubsub_singleton.publish(f'{self_topic}/settings/state_req', state_req)

        self._subscribe(state_topic, state_from_pubsub)
        b.toggled.connect(on_toggle)
        return b

    def _add_signal_buttons(self):
        widget = QtWidgets.QWidget(self)
        self._body_layout.addWidget(widget, self._row, 0, 1, 2)
        self._widgets.append(widget)
        layout = QtWidgets.QHBoxLayout(widget)
        layout.setContentsMargins(3, 3, 3, 3)
        layout.setSpacing(3)
        widget.setLayout(layout)
        self._signals = {
            'widget': widget,
            'layout': layout,
            'buttons': {},
            'spacer': QtWidgets.QSpacerItem(0, 0,
                                            QtWidgets.QSizePolicy.Expanding,
                                            QtWidgets.QSizePolicy.Minimum),
        }
        for name, value in self._DEVICE_SETTINGS.items():
            if not name.endswith('/enable'):
                continue
            signal = name.split('/')[1]
            meta = Metadata(value)
            self._add_signal_button(signal, meta)
        layout.addItem(self._signals['spacer'])
        self._row += 1

    def _add_signal_button(self, signal, meta):
        topic = f'{get_topic_name(self.unique_id)}/settings/signals/{signal}/enable'
        b = QtWidgets.QPushButton(self._signals['widget'])
        b.setObjectName('device_control_signal')
        b.setProperty('signal_level', 0)
        b.setCheckable(True)
        b.setText(signal)
        b.setFixedSize(20, 20)
        b.setToolTip(tooltip_format(meta.brief, meta.detail))

        def update_from_pubsub(value):
            block_state = b.blockSignals(True)
            b.setChecked(bool(value))
            b.blockSignals(block_state)

        self._subscribe(topic, update_from_pubsub)
        b.toggled.connect(lambda checked: pubsub_singleton.publish(topic, bool(checked)))
        self._signals['layout'].addWidget(b)
        self._signals['buttons'][signal] = b

    def _add_gpo(self):
        lbl = QtWidgets.QLabel(N_('GPO'), self)
        self._body_layout.addWidget(lbl, self._row, 0, 1, 1)
        self._widgets.append(lbl)

        widget = QtWidgets.QWidget(self)
        self._body_layout.addWidget(widget, self._row, 1, 1, 1)
        self._widgets.append(widget)
        layout = QtWidgets.QHBoxLayout(widget)
        layout.setContentsMargins(3, 3, 3, 3)
        layout.setSpacing(3)
        widget.setLayout(layout)
        self._gpo = {
            'widget': widget,
            'layout': layout,
            'buttons': [],
            'spacer': QtWidgets.QSpacerItem(0, 0,
                                            QtWidgets.QSizePolicy.Expanding,
                                            QtWidgets.QSizePolicy.Minimum),
        }
        for name, value in self._DEVICE_SETTINGS.items():
            if not name.startswith('out/'):
                continue
            signal = name[4:]
            meta = Metadata(value)
            self._add_gpo_button(signal, meta)
        layout.addItem(self._gpo['spacer'])
        self._row += 1

    def _add_gpo_button(self, signal, meta):
        topic = f'{get_topic_name(self.unique_id)}/settings/out/{signal}'
        b = QtWidgets.QPushButton(self._gpo['widget'])
        b.setObjectName('device_ctrl_gpo')
        b.setCheckable(True)
        b.setText(signal)
        b.setFixedSize(20, 20)
        b.setToolTip(tooltip_format(meta.brief, meta.detail))

        def update_from_pubsub(value):
            block_state = b.blockSignals(True)
            b.setChecked(bool(value))
            b.blockSignals(block_state)

        self._subscribe(topic, update_from_pubsub)
        b.toggled.connect(lambda checked: pubsub_singleton.publish(topic, bool(checked)))
        self._gpo['layout'].addWidget(b)
        self._gpo['buttons'].append(b)

    def _add_settings(self):
        for name, value in self._DEVICE_SETTINGS.items():
            if name.startswith('out/') or name.endswith('/enable'):
                continue
            meta = Metadata(value)
            if 'hide' in meta.flags:
                continue
            self._add(name, meta)

    def _add_str(self, name):
        w = QtWidgets.QLineEdit(self)
        topic = f'{get_topic_name(self.unique_id)}/settings/{name}'
        w.textChanged.connect(lambda s: pubsub_singleton.publish(topic, s))

        def on_change(v):
            block_state = w.blockSignals(True)
            w.setText(str(v))
            w.blockSignals(block_state)

        self._control_widgets[name] = w
        self._subscribe(topic, on_change)
        return w

    def _add_combobox(self, name, meta: Metadata):
        w = QtWidgets.QComboBox(self)
        w.setSizeAdjustPolicy(QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToContents)

        options = meta.options
        option_values = [o[0] for o in options]
        option_strs = [o[1] for o in options]
        comboBoxConfig(w, option_strs)
        topic = f'{get_topic_name(self.unique_id)}/settings/{name}'
        if name in ['signal_frequency']:
            def fn(idx):
                pubsub_singleton.publish('registry/JsdrvStreamBuffer:001/actions/!clear', None)
                pubsub_singleton.publish(topic, options[idx][0])
                pubsub_singleton.publish('registry/JsdrvStreamBuffer:001/actions/!clear', None)
            w.currentIndexChanged.connect(fn)
        else:
            w.currentIndexChanged.connect(lambda idx: pubsub_singleton.publish(topic, options[idx][0]))

        if name == 'current_range':
            w.currentIndexChanged.connect(self._on_current_range)

        def lookup(v):
            try:
                idx = option_values.index(v)
            except ValueError:
                self._log.warning('Invalid value: %s not in %s', v, option_values)
                return
            block_state = w.blockSignals(True)
            w.setCurrentIndex(idx)
            w.blockSignals(block_state)

        self._control_widgets[name] = w
        self._subscribe(topic, lookup)
        return w

    def _add_current_range_limits(self, name, meta: Metadata):
        w = CurrentLimits(self)
        topic = f'{get_topic_name(self.unique_id)}/settings/{name}'
        self._control_widgets[name] = w
        w.values_changed.connect(lambda v0, v1: pubsub_singleton.publish(topic, [v0, v1]))
        combobox = self._control_widgets.get('current_range', None)
        if combobox is not None:
            self._on_current_range(combobox.currentIndex())
        self._subscribe(topic, w.values_set)
        return w

    def _on_current_range(self, v):
        w = self._control_widgets.get('current_range_limits', None)
        if w is not None:
            w.setVisible(v == 0)

    def _add(self, name, meta: Metadata):
        tooltip = tooltip_format(meta.brief, meta.detail)

        if name == 'current_range_limits':
            w = self._add_current_range_limits(name, meta)
            w.setToolTip(tooltip)
            self._body_layout.addWidget(w, self._row, 0, 1, 2)
            self._widgets.append(w)
            self._row += 1
            return

        lbl = QtWidgets.QLabel(meta.brief, self)
        lbl.setToolTip(tooltip)
        self._body_layout.addWidget(lbl, self._row, 0, 1, 1)
        self._widgets.append(lbl)

        w = None
        if meta.options is not None:
            w = self._add_combobox(name, meta)
        elif meta.dtype == 'str':
            w = self._add_str(name)

        if w is not None:
            w.setParent(self)
            w.setToolTip(tooltip)
            self._body_layout.addWidget(w, self._row, 1, 1, 1)
            self._widgets.append(w)
        self._row += 1

    def _add_footer(self):
        widget = QtWidgets.QWidget(self._body)
        self._body_layout.addWidget(widget, self._row, 0, 1, 2)
        self._widgets.append(widget)
        layout = QtWidgets.QHBoxLayout(widget)
        layout.setContentsMargins(3, 3, 3, 3)
        layout.setSpacing(3)
        widget.setLayout(layout)

        b1 = QtWidgets.QPushButton(self._body)
        b1.setText(N_('Reset to defaults'))
        b1.setToolTip(_RESET_TO_DEFAULTS_TOOLTIP)
        b1.clicked.connect(self._reset_to_defaults)
        layout.addWidget(b1)

        b2 = QtWidgets.QPushButton(self._body)
        b2.setText(N_('Clear accum'))
        b2.setToolTip(_CLEAR_ACCUM_TOOLTIP)
        b2.clicked.connect(self._clear_accumulators)
        layout.addWidget(b2)
        self._row += 1
        self._footer = {
            'widget': widget,
            'layout': layout,
            'buttons': [b1, b2],
            'spacer': QtWidgets.QSpacerItem(0, 0,
                                            QtWidgets.QSizePolicy.Expanding,
                                            QtWidgets.QSizePolicy.Minimum),
        }
        layout.addItem(self._footer['spacer'])

    def _reset_to_defaults(self, checked):
        self._log.info('reset to defaults')
        topic_base = f'{get_topic_name(self.unique_id)}/settings'
        # disable all streaming
        for name in self._DEVICE_SETTINGS.keys():
            if name.endswith('/enable'):
                pubsub_singleton.publish(f'{topic_base}/{name}', False)
        pubsub_singleton.publish(f'{topic_base}/state_req', 0)
        for name, meta in self._DEVICE_SETTINGS.items():
            meta = Metadata(meta)
            value = meta.default
            if name == 'name':
                value = self.unique_id
            pubsub_singleton.publish(f'{topic_base}/{name}', value)

    def _clear_accumulators(self, checked):
        self._log.info('clear accumulators')
        topic = f'{get_topic_name(self.unique_id)}/actions/!accum_clear'
        pubsub_singleton.publish(topic, None)

    def clear(self):
        for topic, fn in self._unsub:
            pubsub_singleton.unsubscribe(topic, fn)
        while len(self._widgets):
            w = self._widgets.pop()
            self._body_layout.removeWidget(w)
            w.close()
            w.deleteLater()

    def closeEvent(self, event):
        self._log.info('closeEvent')
        self.clear()
        return super().closeEvent(event)

    def _on_blink(self, value):
        for b in self._buttons_blink:
            b.setProperty('blink', value)
            b.style().unpolish(b)
            b.style().polish(b)

    @property
    def expanded(self):
        return self._expanding.expanded

    @expanded.setter
    def expanded(self, value):
        self._expanding.expanded = value

    def on_parent_style_change(self, style_obj):
        if style_obj is None:
            return
        v = style_obj['vars']
        w = self._control_widgets.get('current_range_limits', None)
        if w is not None:
            w.slider.background = color_as_qcolor(v['base.background_alternate'])
            w.slider.foreground = color_as_qcolor(v['js1.button_checked'])
            w.slider.handles = color_as_qcolor(v['base.foreground_alternate'])

