from qtpy.QtWidgets import *
from ..TSHHotkeys import TSHHotkeys
from ..SettingsManager import SettingsManager
import textwrap
from pathlib import Path


class SettingsWidget(QWidget):
    def __init__(self, settingsBase="", settings=[]):
        super().__init__()

        self.settingsBase = settingsBase

        # Create a layout for the widget and add the label
        layout = QGridLayout()
        layout.setSizeConstraint(QLayout.SizeConstraint.SetMaximumSize)

        # Set the layout for the widget
        self.setLayout(layout)

        for setting in settings:
            self.AddSetting(*setting)

    def AddSetting(self, name: str, setting: str, type: str, defaultValue, callback=lambda: None, tooltip=None):
        callback = callback or (lambda: None)
        
        lastRow = self.layout().rowCount()

        self.layout().addWidget(QLabel(name), lastRow, 0)

        resetButton = QPushButton(
            QApplication.translate("settings", "Default"))

        if type == "checkbox":
            settingWidget = QCheckBox()
            settingWidget.setChecked(SettingsManager.Get(
                self.settingsBase+"."+setting, defaultValue))
            settingWidget.stateChanged.connect(
                lambda val=None: SettingsManager.Set(self.settingsBase+"."+setting, settingWidget.isChecked()))
            resetButton.clicked.connect(
                lambda bt=None, settingWidget=settingWidget:
                settingWidget.setChecked(defaultValue)
            )
        elif type == "hotkey":
            settingWidget = QKeySequenceEdit()
            settingWidget.keySequenceChanged.connect(
                lambda keySequence, settingWidget=settingWidget:
                settingWidget.setKeySequence(keySequence.toString().split(",")[
                    0]) if keySequence.count() > 0 else None
            )
            settingWidget.setKeySequence(SettingsManager.Get(
                self.settingsBase+"."+setting, defaultValue))
            settingWidget.keySequenceChanged.connect(
                lambda sequence=None, setting=setting: [
                    SettingsManager.Set(
                        self.settingsBase+"."+setting, sequence.toString()),
                    callback()
                ]
            )
            resetButton.clicked.connect(
                lambda bt=None, setting=setting, settingWidget=settingWidget: [
                    settingWidget.setKeySequence(defaultValue),
                    callback()
                ]
            )
        elif type == "textbox" or type == "password":
            settingWidget = QLineEdit()
            if type == "password":
                settingWidget.setEchoMode(QLineEdit.Password)
            settingWidget.textChanged.connect(
                lambda val=None: SettingsManager.Set(self.settingsBase+"."+setting, settingWidget.text()))
            settingWidget.setText(SettingsManager.Get(
                self.settingsBase+"."+setting, defaultValue))
            resetButton.clicked.connect(
                lambda bt=None, setting=setting, settingWidget=settingWidget: [
                    settingWidget.setText(defaultValue),
                    callback()
                ]
            )

        elif type == "filedialog":
            layout = QHBoxLayout()
            settingWidget = QLineEdit()
            settingWidget.setText(SettingsManager.Get(
                self.settingsBase + "." + setting, defaultValue))

            browseButton = QPushButton("Browse")
            def openFileDialog():
                file_path, _ = QFileDialog.getOpenFileName(
                    self,
                    "Select File",
                    str(defaultValue) if defaultValue else str(Path.home()),
                    "JSON Files (*.json);;All Files (*)"
                )
                if file_path:
                    settingWidget.setText(file_path)
                    SettingsManager.Set(self.settingsBase + "." + setting, file_path)

            browseButton.clicked.connect(openFileDialog)
            layout.addWidget(settingWidget)
            layout.addWidget(browseButton)

            resetButton.clicked.connect(
                lambda bt=None, settingWidget=settingWidget: [
                    settingWidget.setText(defaultValue),
                    callback()
                ]
            )

            self.layout().addLayout(layout, lastRow, 1)
            self.layout().addWidget(resetButton, lastRow, 2)
            return
        
        elif type == "combobox":
            settingWidget = QComboBox()
            # defaultValue is a list of options; first item is the default
            options = defaultValue if isinstance(defaultValue, list) else [defaultValue]
            settingWidget.addItems(options)
            saved = SettingsManager.Get(self.settingsBase + "." + setting, options[0])
            idx = settingWidget.findText(saved)
            if idx >= 0:
                settingWidget.setCurrentIndex(idx)
            settingWidget.currentTextChanged.connect(
                lambda val, s=setting: SettingsManager.Set(self.settingsBase + "." + s, val))
            resetButton.clicked.connect(
                lambda bt=None, sw=settingWidget, opts=options: sw.setCurrentIndex(0))

        elif type == "button":
            settingWidget = QPushButton(name)
            # When clicked, call the callback function provided
            settingWidget.clicked.connect(callback)
            # No reset button for a simple button
            resetButton.hide()

            # Add button to layout (spanning columns if you want)
            self.layout().addWidget(settingWidget, lastRow, 1)
            self.layout().addWidget(resetButton, lastRow, 2)
            return  # Exit early since we added the button

        
        if tooltip:
            settingWidget.setToolTip('\n'.join(textwrap.wrap(tooltip, 40)))

        self.layout().addWidget(settingWidget, lastRow, 1)
        self.layout().addWidget(resetButton, lastRow, 2)
