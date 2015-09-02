#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
.py
~~~

PURPOSE


REFERENCES


REQUIRES


:author: R.K.Garcia <rayg@ssec.wisc.edu>
:copyright: 2014 by University of Wisconsin Regents, see AUTHORS for more details
:license: GPLv3, see LICENSE for more details
"""
__author__ = 'rayg'
__docformat__ = 'reStructuredText'


# from PyQt4.QtGui import *
# from PyQt4.QtCore import *
from vispy import app, gloo
# import vispy
# vispy.use(app='PyQt4') #, gl='gl+')

try:
    app_object = app.use_app('pyqt4')
except Exception:
    app_object = app.use_app('pyside')
QtCore = app_object.backend_module.QtCore
QtGui = app_object.backend_module.QtGui

from cspov.view.MapWidget import CspovMainMapWidget
from cspov.view.Layer import LayerStackAsListWidget

import logging, unittest, argparse

LOG = logging.getLogger(__name__)


# this is generated with pyuic4 pov_main.ui >pov_main_ui.py
from cspov.ui.pov_main_ui import Ui_MainWindow

class Main(QtGui.QMainWindow):
    def __init__(self):
        super(Main, self).__init__()
        self.ui = Ui_MainWindow()
        self.ui.setupUi(self)
        # refer to objectName'd entities as self.ui.objectName

        self.mainMap = mainMap = CspovMainMapWidget(parent=self)
        self.ui.mainWidgets.addTab(self.mainMap.native, 'Mercator')

        self.ui.mainWidgets.removeTab(0)
        self.ui.mainWidgets.removeTab(0)

        # convey action between layer list
        # FIXME: put a document model in here
        self.behaviorLayersList = LayerStackAsListWidget(self.ui.layers, mainMap.layers)
        # self.ui.layers

    def updateLayerList(self):
        self.ui.layers.add

if __name__ == '__main__':
    import os
    levels = [logging.ERROR, logging.WARN, logging.INFO, logging.DEBUG]
    verbosity = int(os.environ.get('VERBOSITY', 0))
    logging.basicConfig(level=levels[min(3, verbosity)])

    app.create()
    # app = QApplication(sys.argv)
    window = Main()
    window.show()
    print("running")
    app.run()
    # sys.exit(app.exec_())
#