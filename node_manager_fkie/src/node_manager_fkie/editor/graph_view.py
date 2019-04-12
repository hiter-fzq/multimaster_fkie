# Software License Agreement (BSD License)
#
# Copyright (c) 2012, Fraunhofer FKIE/US, Alexander Tiderko
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions
# are met:
#
#  * Redistributions of source code must retain the above copyright
#    notice, this list of conditions and the following disclaimer.
#  * Redistributions in binary form must reproduce the above
#    copyright notice, this list of conditions and the following
#    disclaimer in the documentation and/or other materials provided
#    with the distribution.
#  * Neither the name of Fraunhofer nor the names of its
#    contributors may be used to endorse or promote products derived
#    from this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
# "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
# LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS
# FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
# COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT,
# INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
# BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
# LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
# LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN
# ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.

from __future__ import division, absolute_import, print_function, unicode_literals

from python_qt_binding import loadUi
from python_qt_binding.QtCore import QObject, Signal, Qt
from python_qt_binding.QtGui import QStandardItemModel, QStandardItem
import os
import threading
import rospy

from node_manager_daemon_fkie import exceptions
from node_manager_daemon_fkie.common import sizeof_fmt
import node_manager_fkie as nm
from node_manager_fkie.common import package_name
from node_manager_fkie.html_delegate import HTMLDelegate

try:
    from python_qt_binding.QtGui import QDockWidget, QAbstractItemView, QItemSelectionModel
except Exception:
    from python_qt_binding.QtWidgets import QDockWidget, QAbstractItemView
    from python_qt_binding.QtCore import QItemSelectionModel


class GraphViewWidget(QDockWidget):
    '''
    A frame to find text in the Editor.
    '''
    load_signal = Signal(str, bool)
    ''' :ivar: filename of file to load, True if insert after the current open tab'''
    goto_signal = Signal(str, int)
    ''' :ivar: filename, line to go'''
    finished_signal = Signal()
    ''' :ivar: graph was updated'''
    info_signal = Signal(str, bool)
    ''' :ivar: emit information about current progress (message, warning or not)'''
    DATA_FILE = Qt.UserRole + 1
    DATA_LINE = Qt.UserRole + 2
    DATA_INC_FILE = Qt.UserRole + 3
    DATA_LEVEL = Qt.UserRole + 4
    DATA_SIZE = Qt.UserRole + 5
    DATA_RAW = Qt.UserRole + 6
    DATA_ARGS = Qt.UserRole + 7

    def __init__(self, tabwidget, parent=None):
        QDockWidget.__init__(self, "LaunchGraph", parent)
        graph_ui_file = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'GraphDockWidget.ui')
        loadUi(graph_ui_file, self)
        self.setObjectName('LaunchGraph')
        self.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable)
        self._tabwidget = tabwidget
        self._current_path = None
        self._root_path = None
        self._current_deep = 0
        self.graphTreeView.setSelectionBehavior(QAbstractItemView.SelectRows)
        model = QStandardItemModel()
        self.graphTreeView.setModel(model)
        self.graphTreeView.setUniformRowHeights(True)
        self.graphTreeView.header().hide()
        self.htmlDelegate = HTMLDelegate(palette=self.palette())
        self.graphTreeView.setItemDelegateForColumn(0, self.htmlDelegate)
        self.graphTreeView.activated.connect(self.on_activated)
        self.graphTreeView.clicked.connect(self.on_clicked)
        self._created_tree = False
        self.has_none_packages = True
        self._refill_tree([], False)
        self._fill_graph_thread = None

    def clear_cache(self):
        if self._root_path:
            nm.nmd().clear_cache(self._root_path)
        self._created_tree = False
        self.graphTreeView.model().clear()
        crp = self._current_path
        self._current_path = None
        self.set_file(crp, self._root_path)

    def set_file(self, current_path, root_path):
        self._root_path = root_path
        if self._current_path != current_path:
            self._current_path = current_path
            # run analyzer/path parser in a new thread
            self.setWindowTitle("Include Graph - loading...")
            self._fill_graph_thread = GraphThread(current_path, root_path)
            self._fill_graph_thread.graph.connect(self._refill_tree)
            self._fill_graph_thread.error.connect(self._on_load_error)
            self._fill_graph_thread.info_signal.connect(self._on_info)
            self._fill_graph_thread.start()

    def _on_load_error(self, msg):
        self.setWindowTitle("Include Graph - %s" % os.path.basename(self._root_path))
        if not self._created_tree:
            inc_item = QStandardItem('%s' % msg)
            self.graphTreeView.model().appendRow(inc_item)
            self.finished_signal.emit()
            self.info_signal.emit("build tree failed: %s" % msg, True)

    def _on_info(self, msg, warning):
        self.info_signal.emit(msg, warning)

    def is_loading(self):
        result = False
        if self._fill_graph_thread:
            result = self._fill_graph_thread.is_alive()
        return result

    def find_parent_file(self):
        selected = self.graphTreeView.selectionModel().selectedIndexes()
        for index in selected:
            item = self.graphTreeView.model().itemFromIndex(index.parent())
            if item is not None:
                rospy.logdebug("graph_view: send request to load parent file %s" % item.data(self.DATA_INC_FILE))
                self.load_signal.emit(item.data(self.DATA_INC_FILE), self._current_deep < item.data(self.DATA_LEVEL))

    def on_activated(self, index):
        item = self.graphTreeView.model().itemFromIndex(index)
        if item is not None:
            rospy.logdebug("graph_view: send request to load %s" % item.data(self.DATA_INC_FILE))
            self.load_signal.emit(item.data(self.DATA_INC_FILE), self._current_deep < item.data(self.DATA_LEVEL))

    def on_clicked(self, index):
        item = self.graphTreeView.model().itemFromIndex(index)
        if item is not None:
            self.goto_signal.emit(item.data(self.DATA_FILE), item.data(self.DATA_LINE))

    def enable(self):
        self.setVisible(True)
        self.raise_()
        self.activateWindow()
        self.graphTreeView.setFocus()

    def get_include_args(self, arglist, inc_string, from_file):
        '''
        Searches for each argument in arglist argument values, which are set while include files.
        :rtype: {key: [str]}
        '''
        selected = self.graphTreeView.selectionModel().selectedIndexes()
        from_file_selected = False
        result = {arg: [] for arg in arglist}
        for index in selected:
            item = self.graphTreeView.model().itemFromIndex(index)
            if from_file == item.data(self.DATA_INC_FILE):
                from_file_selected = True
                items = self.graphTreeView.model().match(index, self.DATA_RAW, inc_string, 10, Qt.MatchRecursive)
                for item in items:
                    for arg in arglist:
                        # add only requested args and if value is not already in
                        incargs = item.data(self.DATA_ARGS)
                        if arg in incargs and incargs[arg] not in result[arg]:
                            result[arg].append(incargs[arg])
        # global search if from_file was not in selected
        if not from_file_selected:
            items = self.graphTreeView.model().match(self.graphTreeView.model().index(0, 0), self.DATA_RAW, inc_string, 10, Qt.MatchRecursive)
            for item in items:
                for arg in arglist:
                    incargs = item.data(self.DATA_ARGS)
                    if arg in incargs and incargs[arg] not in result[arg]:
                        result[arg].append(incargs[arg])
        return result

    def _refill_tree(self, tree, create_tree=True):
        deep = 0
        file_dsrc = self._root_path
        try:
            file_dsrc = os.path.basename(self._root_path)
        except Exception:
            pass
        self.setWindowTitle("Include Graph - %s" % file_dsrc)
        if not self._created_tree and create_tree:
            has_none_packages = False
            self.graphTreeView.model().clear()
            pkg, _ = package_name(os.path.dirname(self._root_path))
            if pkg is None:
                has_none_packages = True
            itemstr = '%s [%s]' % (os.path.basename(self._root_path), pkg)
            inc_item = QStandardItem('%s' % itemstr)
            inc_item.setData(self._root_path, self.DATA_FILE)
            inc_item.setData(-1, self.DATA_LINE)
            inc_item.setData(self._root_path, self.DATA_INC_FILE)
            inc_item.setData(deep, self.DATA_LEVEL)
            self._append_items(inc_item, deep, tree)
            self.graphTreeView.model().appendRow(inc_item)
            # self.graphTreeView.expand(self.graphTreeView.model().indexFromItem(inc_item))
            self._created_tree = True
            self.has_none_packages = has_none_packages
        items = self.graphTreeView.model().match(self.graphTreeView.model().index(0, 0), self.DATA_INC_FILE, self._current_path, 10, Qt.MatchRecursive)
        first = True
        self.graphTreeView.selectionModel().clear()
        for item in items:
            if first:
                self._current_deep = item.data(self.DATA_LEVEL)
                first = False
            self.graphTreeView.selectionModel().select(item, QItemSelectionModel.Select)
        self.graphTreeView.expandAll()
        self.finished_signal.emit()

    def _append_items(self, item, deep, items=[]):
        sub_items = []
        inc_item = None
        for inc_file in items:
            if inc_file.rec_depth == deep:
                if inc_item is not None:
                    if sub_items:
                        self._append_items(inc_item, deep + 1, sub_items)
                        sub_items = []
                    item.appendRow(inc_item)
                    inc_item = None
                if inc_item is None:
                    pkg, _ = package_name(os.path.dirname(inc_file.inc_path))
                    size_color = 'gray'
                    if inc_file.size == 0 or inc_file.size > 1000000:
                        size_color = 'orange'
                    itemstr = '%s   <span style="color:%s;"><em>%s</em></span>   [%s]' % (os.path.basename(inc_file.inc_path), size_color, sizeof_fmt(inc_file.size), pkg)
                    inc_item = QStandardItem('%d: %s' % (inc_file.line_number, itemstr))
                    inc_item.setData(inc_file.path_or_str, self.DATA_FILE)
                    inc_item.setData(inc_file.line_number, self.DATA_LINE)
                    inc_item.setData(inc_file.inc_path, self.DATA_INC_FILE)
                    inc_item.setData(inc_file.rec_depth + 1, self.DATA_LEVEL)
                    inc_item.setData(inc_file.size, self.DATA_SIZE)
                    inc_item.setData(inc_file.raw_inc_path, self.DATA_RAW)
                    inc_item.setData(inc_file.args, self.DATA_ARGS)
            elif inc_file.rec_depth > deep:
                sub_items.append(inc_file)
        if inc_item is not None:
            if sub_items:
                self._append_items(inc_item, deep + 1, sub_items)
                sub_items = []
            item.appendRow(inc_item)
            inc_item = None


class GraphThread(QObject, threading.Thread):
    '''
    A thread to parse file for includes
    '''
    graph = Signal(list)
    '''
    :ivar: graph is a signal, which emit two list for files include the current path and a list with included files.
    Each entry is a tuple of the line number and path.
    '''
    error = Signal(str)
    info_signal = Signal(str, bool)
    '''
    :ivar: emit information about current progress (message, warning or not)'''

    def __init__(self, current_path, root_path):
        '''
        :param root_path: the open root file
        :type root_path: str
        '''
        QObject.__init__(self)
        threading.Thread.__init__(self)
        self.setDaemon(True)
#        self.current_path = current_path
        self.root_path = root_path

    def run(self):
        '''
        '''
        try:
            self.info_signal.emit("build tree: start for %s" % self.root_path, False)
            result = []
            filelist = nm.nmd().launch.get_included_files(self.root_path, recursive=True, search_in_ext=nm.settings().SEARCH_IN_EXT)
            for inc_file in filelist:
                rospy.logdebug("build tree: append file: %s" % inc_file)
                result.append(inc_file)
                if not inc_file.exists:
                    self.info_signal.emit("build tree: skip parse %s, not exist" % inc_file.inc_path, True)
            self.graph.emit(result)
        except exceptions.GrpcTimeout as tout:
            rospy.logwarn("Build launch tree failed! Daemon not responded within %.2f seconds while"
                          " get configuration file: %s\nYou can try to increase"
                          " the timeout for GRPC requests in node manager settings." % (nm.settings().timeout_grpc, tout.remote))
            self.error.emit('failed: timeout')
        except Exception:
            import traceback
            # print("Error while parse launch file for includes:\n\t%s" % traceback.format_exc())
            formatted_lines = traceback.format_exc(1).splitlines()
            try:
                rospy.logwarn("Error while parse launch file for includes:\n\t%s", formatted_lines[-5])
            except Exception:
                pass
            self.error.emit('failed: %s' % formatted_lines[-1])
