# GetIABooksActivity.py

# Copyright (C) 2009, 2010 James D. Simmons
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA  02110-1301  USA
import os
import logging
import time
import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk
gi.require_version('Gdk', '3.0')
from gi.repository import Gdk
import csv
import urllib.request, urllib.parse, urllib.error

_NEW_TOOLBAR_SUPPORT = True
try:
    from sugar3.graphics.toolbarbox import ToolbarBox
    from sugar3.activity.widgets import StopButton
except:
    _NEW_TOOLBAR_SUPPORT = False

from sugar3.graphics.toolbutton import ToolButton
from sugar3.graphics.toolcombobox import ToolComboBox
from sugar3.graphics.combobox import ComboBox
from sugar3 import profile
from sugar3.activity import activity
from sugar3 import network
from sugar3.datastore import datastore
from sugar3.graphics.alert import NotifyAlert
from gettext import gettext as _
from gi.repository import GObject

_TOOLBAR_BOOKS = 1
COLUMN_CREATOR = 0
COLUMN_DESCRIPTION=1
COLUMN_FORMAT = 2
COLUMN_IDENTIFIER = 3
COLUMN_LANGUAGE = 4
COLUMN_PUBLISHER = 5
COLUMN_SUBJECT = 6
COLUMN_TITLE = 7
COLUMN_VOLUME = 8

_logger = logging.getLogger('get-ia-books-activity')

class BooksToolbar(Gtk.Toolbar):
    __gtype_name__ = 'BooksToolbar'

    def __init__(self):
        Gtk.Toolbar.__init__(self)
        book_search_item = Gtk.ToolItem()

        self.search_entry = Gtk.Entry()
        self.search_entry.connect('activate', self.search_entry_activate_cb)

        width = int(Gdk.Screen.width() / 2)
        self.search_entry.set_size_request(width, -1)

        book_search_item.add(self.search_entry)
        self.search_entry.show()

        self.insert(book_search_item, -1)
        book_search_item.show()

        self._download = ToolButton('go-down')
        self._download.set_tooltip(_('Get Book'))
        self._download.props.sensitive = False
        self._download.connect('clicked', self._get_book_cb)
        self.insert(self._download, -1)
        self._download.show()

        self.format_combo = ComboBox()
        self.format_combo.connect('changed', self.format_changed_cb)
        self.format_combo.append_item('.djvu', 'Deja Vu')
        self.format_combo.append_item('_bw.pdf', 'B/W PDF')
        self.format_combo.append_item('.pdf', 'Color PDF')
        self.format_combo.append_item('.epub', 'EPUB')
        self.format_combo.set_active(0)
        self.format_combo.props.sensitive = False
        combotool = ToolComboBox(self.format_combo)
        self.insert(combotool, -1)
        combotool.show()

        self.search_entry.grab_focus()

    def set_activity(self, activity):
        self.activity = activity

    def format_changed_cb(self, combo):
        if self.activity != None:
            self.activity.show_book_data()

    def search_entry_activate_cb(self, entry):
        self.activity.find_books(entry.props.text)

    def _get_book_cb(self, button):
        self.activity.get_book()
 
    def enable_button(self,  state):
        self._download.props.sensitive = state
        self.format_combo.props.sensitive = state

class ReadHTTPRequestHandler(network.ChunkedGlibHTTPRequestHandler):
    """HTTP Request Handler for transferring document while collaborating.

    RequestHandler class that integrates with Glib mainloop. It writes
    the specified file to the client in chunks, returning control to the
    mainloop between chunks.

    """
    def translate_path(self, path):
        """Return the filepath to the shared document."""
        return self.server.filepath

class ReadURLDownloader(network.GlibURLDownloader):
    """URLDownloader that provides content-length and content-type."""

    def get_content_length(self):
        """Return the content-length of the download."""
        if self._info is not None:
            return int(self._info.headers.get('Content-Length'))

    def get_content_type(self):
        """Return the content-type of the download."""
        if self._info is not None:
            return self._info.headers.get('Content-type')
        return None

READ_STREAM_SERVICE = 'read-activity-http'

class GetIABooksActivity(activity.Activity):
    def __init__(self, handle, create_jobject=True):
        "The entry point to the Activity"
        activity.Activity.__init__(self, handle,  False)
 
        if _NEW_TOOLBAR_SUPPORT:
            self.create_new_toolbar()
        else:
            self.create_old_toolbar()
        
        self.scrolled = Gtk.ScrolledWindow()
        self.scrolled.set_policy(Gtk.PolicyType.NEVER,
                                 Gtk.PolicyType.AUTOMATIC)
        self.textview = Gtk.TextView()
        self.textview.set_editable(False)
        self.textview.set_cursor_visible(False)
        self.textview.set_justification(Gtk.Justification.LEFT)
        self.textview.set_wrap_mode(Gtk.WrapMode.WORD)
        self.textview.set_left_margin(5)
        self.textview.set_right_margin(5)
        textbuffer = self.textview.get_buffer()
        textbuffer.set_text(_('Enter words from the Author or Title to begin search') + '.')
        self.scrolled.add(self.textview)
        self.textview.show()
        self.scrolled.show()

        self._download_content_length = 0
        self._download_content_type = None

        self.ls = Gtk.ListStore(GObject.TYPE_STRING, GObject.TYPE_STRING,
                                GObject.TYPE_STRING, GObject.TYPE_STRING,
                                GObject.TYPE_STRING, GObject.TYPE_STRING, 
                                GObject.TYPE_STRING, GObject.TYPE_STRING,
                                GObject.TYPE_STRING)
        self.treeview = Gtk.TreeView(self.ls)
        self.treeview.set_rules_hint(True)
        self.treeview.set_search_column(COLUMN_TITLE)
        selection = self.treeview.get_selection()
        selection.set_mode(Gtk.SelectionMode.SINGLE)
        selection.connect("changed", self.selection_cb)

        renderer = Gtk.CellRendererText()
        renderer.set_property('wrap-mode', Gtk.WrapMode.WORD)
        renderer.set_property('wrap-width', 500)
        renderer.set_property('width', 500)
        col = Gtk.TreeViewColumn(_('Title'), renderer, text=COLUMN_TITLE)
        col.set_sort_column_id(COLUMN_TITLE)
        self.treeview.append_column(col)
    
        renderer = Gtk.CellRendererText()
        col = Gtk.TreeViewColumn(_('Volume'), renderer, text=COLUMN_VOLUME)
        col.set_sort_column_id(COLUMN_VOLUME)
        self.treeview.append_column(col)
    
        renderer = Gtk.CellRendererText()
        renderer.set_property('wrap-mode', Gtk.WrapMode.WORD)
        renderer.set_property('wrap-width', 200)
        renderer.set_property('width', 200)
        col = Gtk.TreeViewColumn(_('Author'), renderer, text=COLUMN_CREATOR)
        col.set_sort_column_id(COLUMN_CREATOR)
        self.treeview.append_column(col)

        renderer = Gtk.CellRendererText()
        col = Gtk.TreeViewColumn(_('Language'), renderer, text=COLUMN_LANGUAGE)
        col.set_sort_column_id(COLUMN_LANGUAGE)
        self.treeview.append_column(col)
    
        self.list_scroller = Gtk.ScrolledWindow(hadjustment=None, vadjustment=None)
        self.list_scroller.set_policy(Gtk.PolicyType.NEVER,
                                      Gtk.PolicyType.AUTOMATIC)
        self.list_scroller.add(self.treeview)
        
        self.progressbar = Gtk.ProgressBar()
        self.progressbar.set_orientation(Gtk.Orientation.HORIZONTAL)
        self.progressbar.set_fraction(0.0)
        
        vbox = Gtk.VBox()
        vbox.pack_start(self.progressbar,  False,  False,  10)
        vbox.add(self.scrolled)
        vbox.pack_end(self.list_scroller, True, True, 0)
        self.set_canvas(vbox)
        self.treeview.show()
        vbox.show()
        self.list_scroller.show()
        self.progressbar.hide()

    def close(self,  skip_save=False):
        "Override the close method so we don't try to create a Journal entry."
        activity.Activity.close(self,  True)

    def create_old_toolbar(self):
        toolbox = activity.ActivityToolbox(self)
        activity_toolbar = toolbox.get_activity_toolbar()
        activity_toolbar.keep.props.visible = False
        activity_toolbar.share.props.visible = False
        self.set_toolbox(toolbox)

        self._books_toolbar = BooksToolbar()
        toolbox.add_toolbar(_('Books'), self._books_toolbar)
        self._books_toolbar.set_activity(self)
        self._books_toolbar.show()

        toolbox.show()
        self.toolbox.set_current_toolbar(_TOOLBAR_BOOKS)
        self._books_toolbar.search_entry.grab_focus()
        
    def create_new_toolbar(self):
        toolbar_box = ToolbarBox()

        book_search_item = Gtk.ToolItem()

        self.search_entry = Gtk.Entry()
        self.search_entry.connect('activate', self.search_entry_activate_cb)

        width = int(Gdk.Screen.width() / 2.1)
        self.search_entry.set_size_request(width, -1)

        book_search_item.add(self.search_entry)
        self.search_entry.show()

        toolbar_box.toolbar.insert(book_search_item, -1)
        book_search_item.show()

        self._download = ToolButton('go-down')
        self._download.set_tooltip(_('Get Book'))
        self._download.props.sensitive = False
        self._download.connect('clicked', self._get_book_cb)
        toolbar_box.toolbar.insert(self._download, -1)
        self._download.show()

        self.format_combo = ComboBox()
        self.format_combo.connect('changed', self.format_changed_cb)
        self.format_combo.append_item('.djvu', 'Deja Vu')
        self.format_combo.append_item('_bw.pdf', 'B/W PDF')
        self.format_combo.append_item('.pdf', 'Color PDF')
        self.format_combo.append_item('.epub', 'EPUB')
        self.format_combo.set_active(0)
        self.format_combo.props.sensitive = False
        combotool = ToolComboBox(self.format_combo)
        toolbar_box.toolbar.insert(combotool, -1)
        combotool.show()

        self.search_entry.grab_focus()

        separator = Gtk.SeparatorToolItem()
        separator.props.draw = False
        separator.set_expand(True)
        toolbar_box.toolbar.insert(separator, -1)
        separator.show()

        stop_button = StopButton(self)
        toolbar_box.toolbar.insert(stop_button, -1)
        stop_button.show()

        self.set_toolbar_box(toolbar_box)
        toolbar_box.show()

    def format_changed_cb(self, combo):
        self.show_book_data()

    def search_entry_activate_cb(self, entry):
        self.find_books(entry.props.text)

    def _get_book_cb(self, button):
        self.get_book()
 
    def enable_button(self,  state):
        self._download.props.sensitive = state
        self.format_combo.props.sensitive = state

    def selection_cb(self, selection):
        self.clear_downloaded_bytes()
        tv = selection.get_tree_view()
        model = tv.get_model()
        sel = selection.get_selected()
        if sel:
            model, iter = sel
            self.book_data = model.get_value(iter,COLUMN_TITLE) + '\n\n'
            self.selected_title = self.truncate(model.get_value(iter,COLUMN_TITLE),  75)
            self.selected_volume = model.get_value(iter,COLUMN_VOLUME) 
            if self.selected_volume != '':
                self.book_data +=  _('Volume') + ': ' +  self.selected_volume + '\n\n'
            self.book_data +=  model.get_value(iter,COLUMN_CREATOR) + '\n\n'
            self.selected_author =  self.truncate(model.get_value(iter,COLUMN_CREATOR),  40)
            description = model.get_value(iter,COLUMN_DESCRIPTION)
            if description != '':
                self.book_data +=  description  + '\n\n'
            subject = model.get_value(iter,COLUMN_SUBJECT) 
            if subject != '':
                self.book_data +=  _('Subject') + ': ' +  subject + '\n\n'
            self.book_data +=  _('Publisher') + ': ' + model.get_value(iter,COLUMN_PUBLISHER) + '\n\n'
            self.book_data +=  _('Language') +': '+ model.get_value(iter,COLUMN_LANGUAGE) + '\n\n'
            self.download_url =   'http://www.archive.org/download/' 
            identifier = model.get_value(iter,COLUMN_IDENTIFIER)
            self.download_url +=  identifier + '/' + identifier
            self.show_book_data()

    def show_book_data(self):
        if _NEW_TOOLBAR_SUPPORT:
            format = self.format_combo.props.value
        else:
            format = self._books_toolbar.format_combo.props.value
        if not hasattr(self, 'textview'): return
        textbuffer = self.textview.get_buffer()
        textbuffer.set_text(self.book_data + _('Download URL') + ': ' + self.download_url + format)
        if _NEW_TOOLBAR_SUPPORT:
            self.enable_button(True)
        else:
            self._books_toolbar.enable_button(True)

    def find_books(self, search_text):
        if _NEW_TOOLBAR_SUPPORT:
            self.enable_button(False)
        else:
            self._books_toolbar.enable_button(False)
        self.clear_downloaded_bytes()
        textbuffer = self.textview.get_buffer()
        textbuffer.set_text(_('Performing lookup, please wait') + '...')
        self.book_selected = False
        self.ls.clear()
        search_tuple = search_text.lower().split()
        if len(search_tuple) == 0:
            self._alert(_('Error'), _('You must enter at least one search word.'))
            if _NEW_TOOLBAR_SUPPORT:
                self.search_entry.grab_focus()
            else:
                self._books_toolbar.search_entry.grab_focus()
            return
        FL = urllib.parse.quote('fl[]')
        SORT = urllib.parse.quote('sort[]')
        self.search_url = 'http://www.archive.org/advancedsearch.php?q=' +  \
            urllib.parse.quote('(title:(' + search_text.lower() + ') OR creator:(' + search_text.lower() +')) AND format:(DJVU)')
        self.search_url += '&' + FL + '=creator&' + FL + '=description&' + FL + '=format&' + FL + '=identifier&'  \
            + FL + '=language'
        self.search_url += '&' + FL +  '=publisher&' + FL + '=subject&' + FL + '=title&' + FL + '=volume'
        self.search_url += '&' + SORT + '=title&' + SORT + '&' + SORT + '=&rows=500&save=yes&fmt=csv&xmlsearch=Search'
        GObject.idle_add(self.download_csv,  self.search_url)
    
    def get_book(self):
        if _NEW_TOOLBAR_SUPPORT:
            self.enable_button(False)
            format = self.format_combo.props.value
        else:
            self._books_toolbar.enable_button(False)
            format = self._books_toolbar.format_combo.props.value
        self.progressbar.show()
        GObject.idle_add(self.download_book,  self.download_url + format)
        
    def download_csv(self,  url):
        print("get csv from",  url)
        path = os.path.join(self.get_activity_root(), 'instance',
                            'tmp%i.csv' % time.time())
        print('path=', path)
        getter = ReadURLDownloader(url)
        getter.connect("finished", self._get_csv_result_cb)
        getter.connect("progress", self._get_csv_progress_cb)
        getter.connect("error", self._get_csv_error_cb)
        _logger.debug("Starting download to %s...", path)
        try:
            getter.start(path)
        except:
            self._alert(_('Error'), _('Connection timed out for CSV: ') + url)
           
        self._download_content_type = getter.get_content_type()

    def _get_csv_progress_cb(self, getter, bytes_downloaded):
        if self._download_content_length > 0:
            _logger.debug("Downloaded %u of %u bytes...",
                          bytes_downloaded, self._download_content_length)
        else:
            _logger.debug("Downloaded %u bytes...",
                          bytes_downloaded)

    def _get_csv_error_cb(self, getter, err):
        _logger.debug("Error getting CSV: %s", err)
        self._alert(_('Error'), _('Error getting CSV') )
        self._download_content_length = 0
        self._download_content_type = None

    def _get_csv_result_cb(self, getter, tempfile, suggested_name):
        print('Content type:',  self._download_content_type)
        if self._download_content_type.startswith('text/html'):
            # got an error page instead
            self._get_csv_error_cb(getter, 'HTTP Error')
            return
        self.process_downloaded_csv(tempfile,  suggested_name)

    def process_downloaded_csv(self,  tempfile,  suggested_name):
        textbuffer = self.textview.get_buffer()
        textbuffer.set_text(_('Finished'))
        reader = csv.reader(open(tempfile,  'r'))
        next(reader) # skip the first header row.
        for row in reader:
            if len(row) < 9:
                self._alert("Server Error",  self.search_url)
                return
            iter = self.ls.append()
            self.ls.set(iter, 0, row[0],  1,  row[1],  2,  row[2],  3,  row[3],  4,  row[4],  5,  row[5],  \
                        6,  row[6],  7,  row[7],  8,  row[8])
        os.remove(tempfile)

    def download_book(self,  url):
        self.treeview.props.sensitive = False
        path = os.path.join(self.get_activity_root(), 'instance',
                            'tmp%i' % time.time())
        getter = ReadURLDownloader(url)
        getter.connect("finished", self._get_book_result_cb)
        getter.connect("progress", self._get_book_progress_cb)
        getter.connect("error", self._get_book_error_cb)
        _logger.debug("Starting download to %s...", path)
        try:
            getter.start(path)
        except:
            self._alert(_('Error'), _('Connection timed out for ') + self.selected_title)
           
        self._download_content_length = getter.get_content_length()
        self._download_content_type = getter.get_content_type()

    def _get_book_result_cb(self, getter, tempfile, suggested_name):
        self.treeview.props.sensitive = True
        if self._download_content_type.startswith('text/html'):
            # got an error page instead
            self._get_book_error_cb(getter, 'HTTP Error')
            return
        self.process_downloaded_book(tempfile,  suggested_name)

    def _get_book_progress_cb(self, getter, bytes_downloaded):
        if self._download_content_length > 0:
            _logger.debug("Downloaded %u of %u bytes...",
                          bytes_downloaded, self._download_content_length)
        else:
            _logger.debug("Downloaded %u bytes...",
                          bytes_downloaded)
        total = self._download_content_length
        self.set_downloaded_bytes(bytes_downloaded,  total)
        while Gtk.events_pending():
            Gtk.main_iteration()

    def set_downloaded_bytes(self, bytes,  total):
        fraction = float(bytes) / float(total)
        self.progressbar.set_fraction(fraction)
        
    def clear_downloaded_bytes(self):
        self.progressbar.set_fraction(0.0)

    def _get_book_error_cb(self, getter, err):
        self.treeview.props.sensitive = True
        if _NEW_TOOLBAR_SUPPORT:
            self.enable_button(True)
        else:
            self._books_toolbar.enable_button(True)
        self.progressbar.hide()
        _logger.debug("Error getting document: %s", err)
        self._alert(_('Error'), _('Could not download ') + self.selected_title + _(' path in catalog is incorrect.  ' \
                                                                                   + '  If you tried to download B/W PDF try another format.'))
        self._download_content_length = 0
        self._download_content_type = None

    def process_downloaded_book(self,  tempfile,  suggested_name):
        _logger.debug("Got document %s (%s)", tempfile, suggested_name)
        self.create_journal_entry(tempfile)

    def create_journal_entry(self,  tempfile):
        journal_entry = datastore.create()
        journal_title = self.selected_title
        if self.selected_volume != '':
            journal_title +=  ' ' + _('Volume') + ' ' +  self.selected_volume
        if self.selected_author != '':
            journal_title = journal_title  + ', by ' + self.selected_author
        journal_entry.metadata['title'] = journal_title
        journal_entry.metadata['title_set_by_user'] = '1'
        journal_entry.metadata['keep'] = '0'
        if _NEW_TOOLBAR_SUPPORT:
            format = self.format_combo.props.value
        else:
            format = self._books_toolbar.format_combo.props.value
        if format == '.epub':
            journal_entry.metadata['mime_type'] = 'application/epub+zip'
        if format == '.djvu':
            journal_entry.metadata['mime_type'] = 'image/vnd.djvu'
        if format == '.pdf' or format == '_bw.pdf':
            journal_entry.metadata['mime_type'] = 'application/pdf'
        journal_entry.metadata['buddies'] = ''
        journal_entry.metadata['preview'] = ''
        journal_entry.metadata['icon-color'] = profile.get_color().to_string()
        textbuffer = self.textview.get_buffer()
        journal_entry.metadata['description'] = textbuffer.get_text(textbuffer.get_start_iter(),  textbuffer.get_end_iter(),  True)
        journal_entry.file_path = tempfile
        datastore.write(journal_entry)
        os.remove(tempfile)
        self.progressbar.hide()
        self._alert(_('Success'), self.selected_title + _(' added to Journal.'))

    def truncate(self,  str,  length):
        if len(str) > length:
            return str[0:length-1] + '...'
        else:
            return str
    
    def _alert(self, title, text=None):
        alert = NotifyAlert(timeout=20)
        alert.props.title = title
        alert.props.msg = text
        self.add_alert(alert)
        alert.connect('response', self._alert_cancel_cb)
        alert.show()

    def _alert_cancel_cb(self, alert, response_id):
        self.remove_alert(alert)
        self.textview.grab_focus()
