#!/usr/bin/env python
# -*- coding: iso-8859-1 -*-

# WebKOM - a web based LysKOM client
# 
# Copyright (C) 2000 by Peter �strand
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; version 2
# of the License. 
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA 02111-1307, USA. 


from webkom_constants import *

# Environment issues
import sys
# Not strictly necessary, but it won't hurt. 
sys.path.append(ORIGIN_DIR)

import os, string, socket
from cStringIO import StringIO
import thfcgi
import urllib
import kom
from HTMLgen import *
# Override default escape
AbstractTag.html_escape = 'OFF'
from HTMLcolors import *
import HTMLutil
import Formtools
import random, time
import thread
from webkom_utils import *
import webkom_js
import TranslatorCache
import acceptlang
import traceback
import types

class SessionSet:
    "A set of active sessions"
    # This class has knowledge about Sessions
    def __init__(self):
        self.sessionset = {}
        self.logins_accepted = TRUE

    def gen_session_key(self):
        key = ""
        for foo in range(0, 4):
            key = key + hex(random.randrange(sys.maxint))[2:]
        return key

    def gen_uniq_key(self):
        "Generate a uniq session key"
        while 1:
            key = self.gen_session_key()
            if not self.valid_session(key):
                return key
            
    def valid_session(self, key):
        "Check if a given key is a valid, active sessionkey"
        # D.keys() is atomic, so has_key() should be as well. 
        return self.sessionset.has_key(key)

    def get_session(self, key):
        "Fetch session, based on sessionkey"
        # This method assumes that the session is locked
        session = self.sessionset.get(key)
        if session:
            session.timestamp = time.time()
        return session

    def get_sessionkeys_by_person_no(self, pno):
        "Fetch session, based on person number"
        ret = []
        for key in self.sessionset.keys():
            sess = self.sessionset.get(key)
            if not sess:
                # The session was perhaps deleted by another thread
                continue
            if sess.conn.get_user() == pno:
                ret.append(key)
        return ret
                

    def add_session(self, sess):
        "Add session to sessionset. Return sessionkey"
        system_log.level_write(2, "Creating session for person %d on server %s"
                         % (sess.conn.get_user(), sess.komserver))
        key = self.gen_uniq_key()
        # D[x] = y is atomic
        self.sessionset[key] = sess

        return key

    def _delete_session(self, key, deltype=""):
        sess = self.sessionset.get(key)
        if not sess:
            # The session was perhaps deleted by another thread
            system_log.level_write(2, "warning: _delete_session couldn't fetch session with key %d" % key)
            return
        
        system_log.level_write(2, "Deleting %ssession for person %d on server %s, sessnum=%d"
                         % (deltype, sess.conn.get_user(), sess.komserver, sess.session_num))
        
        # Be nice and shut down connection and socket. 
        kom.ReqDisconnect(sess.conn, 0).response()
        sess.conn.socket.close()
            
        try:
            # Do the actual deletion.
            # The CachedUserConnection object probably has registered callbacks for
            # asynchronous messages. These should be removed to prevent circular references.
            sess.conn.async_handlers = {}
            # At this point, there should be 5 references to this Session object:
            # * The reference in the sessionset.sessionset dictionary. 
            # * Response().sess
            # * Action().sess (shortcut reference)
            # * At temporary reference from sys.getrefcount()
            # * The variable sess above. 
            # If there are more references, there are probably circular references.
            # The GC will take care of this (as long as no __del__:s are used!),
            # but there is nothing wrong with helping the GC a little bit...
            #
            # del D[x] is atomic
            del self.sessionset[key]
        except:
            system_log.level_write(1, "Exception in _delete_session when deleting session.")

    def del_session(self, key):
        "Delete session from sessionset"
        self._delete_session(key)

    def del_inactive(self):
        "Delete and logout inactive sessions"
        curtime = time.time()
        for key in self.sessionset.keys():
            sess = self.sessionset.get(key)
            if not sess:
                # The session was perhaps deleted by another thread
                continue
            if sess.timestamp + SESSION_TIMEOUT < curtime:
                self._delete_session(key, "inactive ")

    def notify_all_users(self, msg):
        m = Message(-1, "WebKOM administrator", msg)
        for key in self.sessionset.keys():
            sess = self.sessionset.get(key)
            if not sess:
                # The session was perhaps deleted by another thread
                continue
            sess.async_message(m, 0)

    def shutdown(self, msg):
        self.notify_all_users(msg)
        self.logins_accepted = FALSE

    def log_me_out(self, session):
        # D.keys() is atomic
        for key in self.sessionset.keys():
            # The session *might* been deleted by another thread. In
            # that case, get() will return None. 
            if self.sessionset.get(key) == session:
                self._delete_session(key, "remotely logged out ")
                

# Global variables
sessionset = SessionSet()

# Used for debugging purposes in interactive terminal
def first_sess():
    return sessionset.sessionset.items()[0][1]

# Messages
class Message:
    def __init__(self, recipient, sender, message):
        # The following recipients are defined:
        # -1: Message from WebKOM operator
        # 0: Alarm message
        # An integer equals the current user: A personal message
        # An integer equalse some conference: A group message
        self.recipient = recipient
        # The sender can be an integer or a string. If it's an integer,
        # the name will be looked up from the LysKOM server. 
        self.sender = sender
        self.message = message
        self.time = time.time()

class RemotelyLoggedOutException(Exception):
    pass

class Session:
    "A session class. Lives as long as the session (and connection)"
    def __init__(self, conn, komserver):
        self.conn = conn
        self.komserver = komserver # For debugging and logging
        self.current_conf = 0
        self.comment_tree = []
        self.timestamp = time.time()
        self.lock = thread.allocate_lock()
        # Lock initially
        self.lock.acquire()
        # Holds pending messages
        self.pending_messages = []
        self.session_num = kom.ReqWhoAmI(self.conn).response()
        self.last_active = 0
        # Result of submission. There is no problem when two submits are done
        # at the same time from one session, since the session is locked. 
        self.submit_result = {}
        self.saved_shortcuts = []

        self.marked_texts = {}
        for mark in kom.ReqGetMarks(self.conn).response():
            self.marked_texts[mark.text_no] = [mark.type]

    def lock_sess(self):
        "Lock session"
        self.lock.acquire()
        
    def unlock_sess(self):
        "Unlock session"
        if self.lock.locked():
            self.lock.release()

    def async_message(self, msg, c):
        self.pending_messages.append(Message(msg.recipient, msg.sender, msg.message))

    def async_logout(self, msg, c):
        if msg.session_no == self.session_num:
            sessionset.log_me_out(self)
            raise RemotelyLoggedOutException

    def user_is_active(self):
        now = time.time()
        if now - self.last_active > 30:
            kom.ReqUserActive(self.conn).response()
            self.last_active = now


class Response:
    "A response class. Used during the construction of a response."
    def __init__(self, req, env, form):
        self.req = req
        self.env = env
        self.form = form
        self.doc = WebKOMSimpleDocument(title="WebKOM", bgcolor=HTMLcolors.WHITE,
                                        vlinkcolor=HTMLcolors.BLUE, stylesheet=STYLESHEET)
        
        self.key = ""
        self.sess = None
        self.shortcuts = []
        self.shortcuts_active = 1
        self.docstart_written = 0

        # Default HTTP headers. 
        self.http_headers = ["Content-type: text/html; charset=iso-8859-1",
                            "Cache-Control: no-cache",
                            "Pragma: no-cache",
                            "Expires: 0"]

    def write_docstart(self):
        if not self.docstart_written:
            http_header = string.join(self.http_headers + 2*[""], "\r\n")
            self.req.out.write(http_header)
            self.req.out.write(self.doc.get_doc_start())
            self.docstart_written = 1

    def write_docstart_refresh(self, seconds, url_text):
        if not self.docstart_written:
            url_text = self._get_url_base() + "?sessionkey=" + self.key + url_text
            http_headers = self.http_headers + ["Refresh: %s; URL=%s" % (seconds, url_text)]
            http_header = string.join(http_headers + 2*[""], "\r\n")
            self.req.out.write(http_header)
            self.req.out.write(self.doc.get_doc_start())
            self.docstart_written = 1

    def flush(self):
        self.write_docstart()
        self.req.out.write(self.doc.flush_doc_contents())
        self.req.flush_out()

    def _get_url_base(self):
        server_name = self.env["HTTP_HOST"]
        if not server_name:
            server_name = self.env["SERVER_NAME"]
        if self.env.has_key("HTTPS"):
            server_name = "https://" + server_name
        else:
            server_name = "http://" + server_name
        script_name = self.env["SCRIPT_NAME"]
        return server_name + script_name

    def _get_my_url(self):
        return self._get_url_base() + "?" + self.env["QUERY_STRING"]
    
    def set_redir(self, url_text):
        # Do not print shortcuts code after redirection, this leads to internal error.
        self.shortcuts_active = 0
        url_base = self._get_url_base()
        self.http_headers = ["Location: " + url_base + url_text]

    def add_shortcut(self, key, url):
        self.shortcuts.append((key, url))

    def get_translator(self):
        header = self.env.get("HTTP_ACCEPT_LANGUAGE", "")
        lang_selector = acceptlang.language(header)
        # Get selected language, a string like 'en'
        selected_lang = lang_selector.select_from(installed_langs)
        return translator_cache.get_translator(selected_lang).gettext

class Action:
    "Abstract class for actions. Action- and Submit-methods inherits this class."
    def __init__(self, resp):
        self.resp = resp
        # Shortcuts
        self.doc = resp.doc
        self.form = resp.form
        self.key = resp.key
        self.sess = resp.sess
        # Language
        self._ = resp.get_translator()
        self.redirect_urls = not self.resp.env.has_key("HTTPS")

    def gen_error(self, *msg):
        "Generate error message in bold, with a BR following"
        msg = map(webkom_escape, msg)
        return Container(BR(), Bold(self._("Error: "), *msg), BR())

    def print_error(self, *msg):
        "Print error message"
        self.doc.append(self.gen_error(*msg))
        
    #
    # Small and frequently-used KOM utility methods. The rest in webkom_utils.py
    def change_conf(self, conf_num):
        "Change current LysKOM conference"
        self.sess.current_conf = conf_num
        # Tell KOM-server that we have changed conference
        try:
            kom.ReqChangeConference(self.sess.conn, conf_num).response()
            return 1
        except:
            return 0
    
    def get_conf_name(self, num):
        "Get conference name"
        default = self._("Conference %d (does not exist)")
        return webkom_escape(self.get_truncated_conf_name(num, default))

    def get_pers_name(self, num):
        "Get persons name"
        default = self._("Person %d (does not exist)")
        return webkom_escape(self.get_truncated_conf_name(num, default))

    def get_truncated_conf_name(self, num, default):
        name = self.sess.conn.conf_name(num, default=default)
        if len(name) > MAX_CONFERENCE_LEN:
            name = name[:MAX_CONFERENCE_LEN] + "..."
        return name

    def get_presentation(self, num):
        "Get presentation of a conference"
        try:
            return self.sess.conn.conferences[num].presentation
        except:
            # Zero is special: It indicates that the text does not exist. 
            return 0

    def get_article_text(self, num):
        try:
            text = kom.ReqGetText(self.sess.conn, num).response()
        except:
            self.print_error(self._("An error occurred when fetching article."))
            return ""
        # Skip over the subject
        text = text[string.find(text, "\n"):]
        return text
    # End of KOM utility methods.
    #
            
    def base_session_url(self):
        "Return base url with sessionkey appended"
        return BASE_URL + "?sessionkey=" + self.key

    def action_href(self, actionstr, text, active_link=1):
        "Return an Href object with base url, sessionkey and more"
        if active_link:
            return Href(self.base_session_url() + "&amp;action=" + actionstr, text)
        else:
            return Font(text, color=INACTIVE_LINK_COLOR)

    def external_href(self, url, text):
        if self.redirect_urls:
            url = self.resp._get_url_base() + "?redirect=" + urllib.quote_plus(url)
        return external_href(url, text)

    def webkom_escape_linkify(self, s):
        s = del_8859_1_invalid_chars(s)
        s = self.linkify_text(s)
        s = HTMLutil.latin1_escape(escape(s))
        s = unquote_specials(s)
        return s

    def linkify_text(self, text):
        # FIXME: Linkify everything that looks like an URL or mail adress.
        # Do a better job. 
        # NOTE:
        # < = \001
        # > = \002
        # This is because otherwise these are quoted by HTMLgen.escape.

        # http URLs
        pat = re.compile("(?P<fullurl>(http://|(?=www\\.))(?P<url>[^\t \012\014\"<>|\\\]*[^\t \012\014\"<>|.,!(){}?'`:]))")
        # We quote the URL now, because it will be an argument to the ?redirect mechanism (added below)
        if self.redirect_urls:
            repl = lambda m: '\001a href="http://' + urllib.quote_plus(m.group("url")) + '"\002' + m.group("fullurl") + '\001/a\002'
        else:
            repl = lambda m: '\001a href="http://' + m.group("url") + '"\002' + m.group("fullurl") + '\001/a\002'
        text = pat.sub(repl, text)

        # https URLs
        pat = re.compile("(?P<fullurl>(https://)(?P<url>[^\t \012\014\"<>|\\\]*[^\t \012\014\"<>|.,!(){}?'`:]))")
        if self.redirect_urls:
            repl = lambda m: '\001a href="https://' + urllib.quote_plus(m.group("url")) + '"\002' + m.group("fullurl") + '\001/a\002'
        else:
            repl = lambda m: '\001a href="https://' + m.group("url") + '"\002' + m.group("fullurl") + '\001/a\002'
        text = pat.sub(repl, text)

        # ftp URLs
        pat = re.compile("(?P<fullurl>(ftp://|(?=ftp\\.))(?P<url>[^\t \012\014\"<>|\\\]*[^\t \012\014\"<>|.,!(){}?'`:]))")
        repl = '\001a href="ftp://\\g<url>"\002\\g<fullurl>\001/a\002' 
        text = pat.sub(repl, text)

        # file URLs
        pat = re.compile("(?P<fullurl>(file://)(?P<url>[^\t \012\014\"<>|\\\]*[^\t \012\014\"<>|.,!(){}?'`:]))")
        repl = '\001a href="file://\\g<url>"\002\\g<fullurl>\001/a\002'
        text = pat.sub(repl, text)

        if self.redirect_urls:
            # Change all links to go through our redirection mechanism. 
            redirect = self.resp._get_url_base() + "?redirect="
            # Both for http and https. 
            text = text.replace('\001a href="http', '\001a href="%shttp' % redirect)
        
        return text

    def action_shortcut(self, key, actionstr):
        self.resp.add_shortcut(key, self.base_session_url() + "&amp;action=" + actionstr)

    def add_stdaction(self, container, resp, action, caption):
        "Add a link to a standard action and also the keyboard shortcut space"
        # Add link to page
        container.append(self.action_href(action, caption))
        # Add keyboard shortcut
        std_url = self.base_session_url() + "&amp;action=" + action
        resp.add_shortcut(" ", std_url)


    def unread_info(self, current_conf=0):
        "Return a string (current/total) with information about number of unread"
        total = get_total_num_unread(self.sess.conn, 
                                     self.sess.conn.member_confs)
        if current_conf:
            try:
                unread_current_conf = str(self.sess.conn.no_unread[current_conf]) + "/"
            except:
                unread_current_conf = "?/"
        else:
            unread_current_conf = ""

        return NBSP*4 + self._("Unread: ") + unread_current_conf + str(total) + str(BR())
    

    def current_conflink(self):
        "Return a link to current conference with correct caption, depending on"
        "whether there are unread articles in this conference or not"
        try:
            num_unread = self.sess.conn.no_unread[self.sess.current_conf]
        except kom.NotMember:
            return self._("Conferences (you are not a member of)")
        
        if num_unread:
            # We are in a conference with unread articles
            return self.action_href("viewconfs_unread", self._("Conferences (with unread)"))
        else:
            # No unread articles in this conference
            return self.action_href("viewconfs", self._("Conferences (you are a member of)"))
        
    # Only used on pages with forms
    def hidden_key(self):
        "Return a hidden key, to be used in a form"
        return Input(type="hidden", name="sessionkey", value=self.key)
    
    def gen_std_top(self, leftobj):
        "Create an standard top header table, including about-link"
        if self.key:
            aboutlink = self.action_href("about", self._("About WebKOM"))
            logoutlink = self.action_href("logout", self._("Logout"))
        else:
            aboutlink = Href(BASE_URL + "?action=about", self._("About WebKOM"))
            logoutlink = ""
        tab = [[leftobj, aboutlink],
               ["", logoutlink]]
        return Table(body=tab, border=0, cell_padding=0,
                     column1_align="left", cell_align="right", width="100%")

    def append_std_top(self, leftobj):
        self.doc.append(self.gen_std_top(leftobj))

    def append_right_footer(self):
        "Add link to W3C validator and CUSTOM_RIGHT_FOOTER"
        div = Div(align = "right")
        self.doc.append(div)
        div.append(str(CUSTOM_RIGHT_FOOTER))
        div.append(NBSP*4)
        if VALIDATOR_LINK:
            image = Image(src="/webkom/images/check.png", border=0, height=17, width=22,
                          alt="[check HTML validity]")
            div.append(Href("http://validator.w3.org/check?uri=%s" % (urllib.quote(self.resp._get_my_url())),
                            str(image)))
        
    def submit_redir(self, submit_result):
        self.sess.submit_result = submit_result
        # Save shortcuts
        self.sess.saved_shortcuts = self.resp.shortcuts
        # Redirect to result page. Note: Since this is not HTML, do not escape "&"
        self.resp.set_redir("?sessionkey=" + self.resp.key + "&action=submit_result")

    def gen_search_line(self):
        cont = Container()
        cont.append(Input(name="searchtext", value=self.form.getvalue("searchtext")))
        cont.append(Input(type="checkbox", name="searchconf_komconvention",
                          checked=self.form.getvalue("searchconf_komconvention")))
        cont.append(self._("KOM matching"), 2*NBSP)
        return cont

    def gen_search_result_table(self, searchtext, matches, maxhits=20,
                                match_handler=None):
        """
        Generate a table with search results

        match_handler, if used, takes (rcpt_num, rcpt_name) as
        arguments and must return a list.
        """
        cont = Container()
        infotext = None
        if len(matches) == 0:
            infotext = self._("(Nothing matches %s)") % searchtext
        elif len(matches) > maxhits:
            infotext = self._("(Too many matches, search result truncated)")

        cont.append(self._("Search result:"), BR())
        tab=[]
        for (rcpt_num, rcpt_name) in matches[:maxhits]:
            if match_handler:
                tab.append(match_handler(rcpt_num, rcpt_name))
            else:
                # Generate radio buttons
                tab.append([webkom_escape(rcpt_name),
                            Input(type="radio", name="selected_conf", value=str(rcpt_num))])
        if infotext:
            tab.append([infotext, ""])

        cont.append(Table(body=tab, cell_padding=2, border=3, column1_align="left",
                          cell_align="right", width="100%"))
        return cont

    def do_search(self, searchtext, want_pers=0, want_confs=1):
        """Do either a KOM or regexp search, depending on if
        self.form.getvalue("searchconf_komconvention") exists"""
        if self.form.getvalue("searchconf_komconvention"):
            return self.sess.conn.lookup_name(searchtext, want_pers=1, want_confs=1)
        else:
            return self.sess.conn.regexp_lookup(searchtext, want_pers=1, want_confs=1)

    def search_help(self, want_pers=0, want_confs=1):
        result = ""
        if want_pers and want_confs:
            result += self._("Type in a part of a conference name or person to search for. ")
        elif want_pers:
            result += self._("Type in a part of a person to search for. ")
        elif want_confs:
            result += self._("Type in a part of a conference name to search for. ")

        result += self._("The search is not case sensitive. ")
        result += self._("Regular expressions are allowed, unless KOM matching is used.")
        return result


class ViewPendingMessages(Action):
    "View pending messages"
    def print_heading(self, msg):
        if msg.recipient == -1:
            text = self._("WebKOM server message")
        elif msg.recipient == 0:
            text = self._("Alarm message")
        elif msg.recipient == self.sess.conn.get_user():
            text = self._("Personal message")
        else:
            recipient_name = self.get_conf_name(msg.recipient)
            text = self._("Group message to ") + recipient_name

        self.doc.append(Heading(2, text))
    
    def response(self):
        was_pending = (self.sess.pending_messages and 1)

        while self.sess.pending_messages:
            msg = self.sess.pending_messages.pop(0)
            self.print_heading(msg)
            if type(msg.sender) == types.StringType:
                sender_name = msg.sender
            else:
                sender_name = self.get_pers_name(msg.sender)
                
            self.doc.append(Bold(self._("From: ") + webkom_escape(sender_name)), BR())
            self.doc.append(Bold(self._("Time: ") +
                                 time.strftime("%Y-%m-%d %H:%M", time.localtime(msg.time))))
            
            self.doc.append(BR(), webkom_escape(msg.message))

        if was_pending:
            self.doc.append("<hr noshade size=2>")
        return


class AddShortCuts:
    def __init__(self, resp, base_sess_url):
        self.resp = resp
        self.base_sess_url = base_sess_url

    def shortcut_case(self, key, location):
        ret = """    case '%s':
            window.location="%s";
            return false;
            break;
""" % (key, location)
        return ret

    def add(self):
        # Begin Javascript
        ret = webkom_js.code_begin
        # Shortcut functions
        ret = ret + webkom_js.shortcut_functions
        # Begin case
        ret = ret + webkom_js.begin_switch
        # Add case for disabling shortcuts
        ret = ret + webkom_js.disable_shortcuts
        
        # Example:
        #ret = ret + self.shortcut_case("q", "http://www.abc.se")
        
        for (key, url) in self.resp.shortcuts:
            if key == " ":
                ret = ret + webkom_js.space_case % url
            else:
                ret = ret + self.shortcut_case(key, url)

        ret = ret + webkom_js.end_switch + webkom_js.code_end
        self.resp.doc.append(ret)
                
    
class LoginPageActions(Action):
    "Generate the login page"
    def response(self):
        self.resp.shortcuts_active = 0
        self.doc.onLoad = "document.loginform.username.focus()"
        toplink = Href(BASE_URL, "WebKOM")
        cont = Container(toplink, TOPLINK_SEPARATOR + self._("Login"))
        self.append_std_top(cont)
        default_kom_server = DEFAULT_KOM_SERVER
        if self.form.has_key("komserver") and not LOCK_KOM_SERVER:
            default_kom_server = self.form["komserver"].value
        submitbutton = Input(type="submit", name="loginsubmit", value=self._("Login"))

        cont = Container()
        self.doc.append(Center(cont))
        cont.append(BR(2))
        cont.append(Center(Heading(2, self._("WebKOM login"))))
        cont.append(BR(2))

        F = Form(BASE_URL, name="loginform", submit="")
        cont.append(F)

        logintable = []

        if LOCK_KOM_SERVER:
            logintable.append((self._("Server"), default_kom_server))
            F.append(Input(type="hidden", name="komserver", value=default_kom_server))
        else:
            if SERVER_LIST:
                serverlist_href = Href(BASE_URL + "?action=select_server", self._("select from list"))
            else:
                serverlist_href = ""
            
            logintable.append((self._("Server"),
                               Container(Input(name="komserver", size=20, value=default_kom_server),
                                         serverlist_href)))

        logintable.append((self._("Username"),
                           Container(Input(name="username", size=20, value=self.form.getvalue("username")),
                                     Input(type="checkbox", name="searchconf_komconvention",
                                           checked=self.form.getvalue("searchconf_komconvention")),
                                     self._("KOM matching"))))
        logintable.append((self._("Password"), Input(type="password",name="password",size=20)))

        F.append(Center(Formtools.InputTable(logintable)))
        F.append(Center(submitbutton))

        self.doc.append(Href(BASE_URL + "?action=create_user&amp;komserver=" + default_kom_server, self._("Create new user") + "..."))
        self.append_right_footer()

        return


class AboutPageActions(Action):
    "Generate about page"
    def response(self):
        if self.key:
            toplink = Href(self.base_session_url(), "WebKOM")
            aboutlink = self.action_href("about", self._("About WebKOM"))
        else:
            toplink = Href(BASE_URL, "WebKOM")
            aboutlink = Href(BASE_URL + "?action=about", self._("About WebKOM"))
            
        cont = Container(toplink, TOPLINK_SEPARATOR, aboutlink)
        self.append_std_top(cont)
        
        self.doc.append(Heading(2, self._("About WebKOM")))
        last_changed = time.strftime("%Y-%m-%d-%H:%M", time.localtime(os.stat(sys.argv[0])[9]))
        self.doc.append(self._("Version running: ") + VERSION + self._(" (last modified ") + last_changed + ")")

        self.doc.append(BR())
        self.doc.append(self._("This server instance started "),
                        time.strftime("%Y-%m-%d %H:%M",
                                      time.localtime(serverstarttime)))
        timediff = int(time.time() - serverstarttime)
        days = timediff // 86400
        hours = (timediff - 86400*days) // 3600
        minutes = ((timediff - 86400*days) - 3600*hours) // 60
        self.doc.append(self._(", %(days)d days, %(hours)d hours and "\
                               "%(minutes)d minutes ago") % locals())

        self.doc.append(Heading(3, self._("Overview")))
        self.doc.append(self._("WebKOM is a WWW-interface for "))
        self.doc.append(self.external_href("http://www.lysator.liu.se/lyskom", "LysKOM"), ".")
        self.doc.append(self._("The goal is a simple, easy-to-use client."))
        
        self.doc.append(Heading(3, self._("License")))
        self.doc.append(self._("WebKOM is free software, licensed under GPL."))
        
        self.doc.append(Heading(3, self._("Authors")))
        self.doc.append(self._("The following people have in one way or another "
                               "contributed to WebKOM:"), BR(2))
        self.doc.append(self.external_href("http://www.lysator.liu.se/~astrand/",
                                      self._("Peter &Aring;strand (project starter, most of implementation)")), BR())
        self.doc.append("Kent Engstr�m (python-lyskom)", BR())
        self.doc.append("Per Cederqvist (LysKOM server etc.)", BR())
        self.doc.append(self.external_href("http://www.lysator.liu.se/~forsberg/",
                                      "Erik Forsberg (implementation)"), BR())
        self.doc.append("Kjell Enblom", BR())
        self.doc.append("Niklas Lindgren", BR())
        self.doc.append(self.external_href("http://www.helsinki.fi/~eisaksso/",
                                      self._("Eva Isaksson (Finnish translation)")), BR())

        self.doc.append(Heading(3, self._("Technology")))
        self.doc.append(self._("WebKOM is written in Python and is a persistent, threaded "))
        self.doc.append(self.external_href("http://www.fastcgi.com", "FastCGI"), self._(" application."))
        self.doc.append(self._("The HTML code is generated by "))
        self.doc.append(self.external_href("http://starship.python.net/crew/friedrich/HTMLgen/html/main.html",
                                      "HTMLgen"), ".")

        self.doc.append(Heading(3, self._("Translations")))
        self.doc.append(self._("Translations are provided by the GNU gettext library."))
        self.doc.append(self._("The following translations are installed on this system:"), BR())
        self.doc.append(list2string(installed_langs))

        self.doc.append(Heading(3, self._("Web page")))
        self.doc.append(self._("You can find more information about WebKOM on the"))
        self.doc.append(self.external_href("http://www.lysator.liu.se/lyskom/klienter/webkom/",
                                      self._("homepage.")))
        
        self.doc.append(Heading(3, self._("Bugs and feedback")))
        self.doc.append(self._("There is a "),
                        self.external_href(webkom_escape(KNOWN_BUGS_URL), self._("list with known bugs")),
                        self._(" in the Bugzilla at Lysator. "))
        self.doc.append(self._("It should be used for bug reports, feature requests and general feedback."))

        if not self.key:
            self.append_right_footer()


class SelectServerPageActions(Action):
    "Page for selecting LysKOM server"
    def response(self):
        toplink = Href(BASE_URL, "WebKOM")
        aboutlink = Href(BASE_URL + "?action=select_server", self._("Select server"))
            
        cont = Container(toplink, TOPLINK_SEPARATOR, aboutlink)
        self.append_std_top(cont)
        
        self.doc.append(Heading(2, self._("Select server")))
        if SERVER_LIST:
            tab = map(self._make_row, SERVER_LIST)
            self.doc.append(Table(body=tab, cell_padding=2, border=3, width="40%"))
        
        self.append_right_footer()

    def _make_row(self, server):
        description, hostname = server
        return [description, Href(BASE_URL + "?komserver=" + hostname, hostname)]


class RedirectToExternalURL(Action):
    def response(self):
        url = self.resp.form.getvalue("redirect")
        self.doc.meta = Meta(equiv="Refresh", content="0; url=%s" % url)
        self.doc.append("Redirecting to")
        self.doc.append(Href(url, url))


class WhatsImplementedActions(Action):
    "Generate a page with implementation details"
    def response(self):
        toplink = Href(self.base_session_url(), "WebKOM")
        wilink = self.action_href("whats_implemented", self._("What can WebKOM do?"))
        cont = Container(toplink, TOPLINK_SEPARATOR, wilink)
        self.append_std_top(cont)
        
        self.doc.append(Heading(2, self._("What can WebKOM do?")))

        self.doc.append(Heading(3, self._("Implemented")))
        self.doc.append(List([
            self._("Check who is logged in"),
            self._("List unread articles"),
            self._("Write articles"),
            self._("Write comments"),
            self._("Write personal letters"),
            self._("Read presentation"),
            self._("Change password"),
            self._("Read comments"),
            self._("Join conference"),
            self._("Set unread"),
            self._("Leave conference"),
            self._("Mark/unmark articles"),
            self._("Read marked articles")]))

        self.doc.append(Heading(3, self._("Not Implemented")))
        self.doc.append(List([
            self._("Read article by specifying global article number"),
            self._("Write footnotes"),
            self._("Send messages"),
            self._("Set/remove notes on letterbox"),
            self._("Prioritize conferences"),
            self._("Create conferences"),
            self._("Jump"),
            self._("View sessionstatus for persons"),
            self._("Change name"),
            self._("Delete articles"),
            self._("Status for conference/persons"),
            self._("Add recipients and comments to existing articles"),
            self._("Move articles between conferences"),
            self._("Prevent comments"),
            self._("Request personal answer"),
            self._("Request read confirmation")]))


class MainPageActions(Action):
    "Generate the mainpage"
    def response(self):
        toplink = Href(self.base_session_url(), "WebKOM")
        cont = Container(toplink)
        self.append_std_top(cont)

        cont = Container()
        cont.append(Heading(2, self._("Main Page")))

        cont.append(Heading(3, self._("Read")))
        l = List([
            self.action_href("viewconfs_unread", self._("List conferences with unread")),
            self.action_href("viewconfs", self._("List all conferences you are a member of")),
            self.action_href("view_markings", self._("List marked articles")),
            self.action_href("specify_article_number", self._("View article with specified number"))])
        if SEARCH_LIMIT != 0:
            l.append(self.action_href("search", self._("Search article")))
        cont.append(l)

        cont.append(Heading(3, self._("Write")))
        cont.append(List([
            self.action_href("writeletter&amp;rcpt=" + str(self.sess.conn.get_user()),
                             self._("Write letter"))]))

        cont.append(Heading(3, self._("Conference")))
        cont.append(List([
            self.action_href("joinconf", self._("Join conference")),
            self.action_href("choose_conf", self._("Go to conference")),
            self.action_href("view_presentation", self._("View presentation"))]))

        cont.append(Heading(3, self._("Person")))
        cont.append(List([
            self.action_href("whoison", self._("Who is logged in")),
            self.action_href("changepw", self._("Change password")),
            self.action_href("writepresentation" + "&amp;presentationfor="
                             + str(self.sess.conn.get_user()), self._("Change your presentation"))]))

        cont.append(Heading(3, self._("Other")))
        cont.append(List([
            self.action_href("logout", self._("Logout")),
            self.action_href("logoutothersessions", self._("Logout my other sessions")),
            self.action_href("whats_implemented", self._("What can WebKOM do?"))]))

        tab=[[cont]]
        self.doc.append(Table(body=tab, border=0, cell_padding=50, width="100%"))
        self.action_shortcut(" ", "viewconfs_unread")

        return


class LogOutActions(Action):
    "Do logout actions"
    def response(self):
        sessionset.del_session(self.key)
        # This session object will sone be destroyed, but lets play it safe
        # and delete the reference to the session. 
        self.resp.sess = None
        
        self.resp.shortcuts_active = 0

        # Redirect to loginpage
        self.resp.set_redir("")
        return 


class LogInActions(Action):
    "Do login actions"
    def error_message(self, errmsg):
        toplink = Href(BASE_URL, "WebKOM")
        self.doc.append(Container(toplink, TOPLINK_SEPARATOR + self._("Login")))
        self.doc.append(Heading(2, self._("Login failed")))
        self.doc.append(webkom_escape(errmsg), BR(2))
        komserver = self.form.getvalue("komserver")
        username = self.form.getvalue("username")
        self.doc.append(Href(BASE_URL + "?komserver=%s&amp;username=%s" % (komserver, username),
                             "Go back"))

    def gen_table(self, matches):
        # Ambiguity
        # Create top
        toplink = Href(BASE_URL, "WebKOM")
        self.doc.append(Container(toplink, TOPLINK_SEPARATOR + self._("Login")))
        self.doc.append(Heading(2, self._("The username is ambiguous")))

        F = Form(BASE_URL, name="loginform", submit="")
        F.append(Input(type="hidden", name="komserver", value=self.komserver))
        F.append(Input(type="hidden", name="password", value=self.password))

        F.append(self._("Choose user:"), BR())
        tab=[]
        infotext = None
        if len(matches) > 15:
            infotext = self._("(Too many hits, the table is truncated)")
        
        for (pers_num, pers_name) in matches[:15]:
            tab.append([webkom_escape(pers_name),
                        pers_num,
                        Input(type="radio", name="username", value="#" + str(pers_num))])
        if infotext:
            tab.append([infotext, "", ""])

        headings = [self._("User name"), self._("User number"), ""]
        F.append(Table(body=tab, border=3, cell_padding=2, column1_align="left",
                       cell_align="right", width="100%", heading=headings))


        addsubmit = Input(type="submit", name="loginsubmit",
                          value=self._("Login with selected user"))
        tab = [["", addsubmit]]
        F.append(Table(body=tab, border=0, cell_align="right", width="100%"))
        self.doc.append(F)
        return

    def valid_parameters(self):
        if not self.form.getvalue("komserver"):
            self.error_message(self._("No server given."))
            return FALSE

        if not self.form.getvalue("username"):
            self.error_message(self._("No username given."))
            return FALSE

        if not self.form.has_key("password"):
            self.error_message(self._("Password variable is missing."))
            return FALSE

        return TRUE

    def logins_accepted(self):
        if not sessionset.logins_accepted:
            self.error_message(self._("Logins are not accepted right now. Try again later."))
            return FALSE
        else:
            return TRUE

    def setup_asyncs(self, conn):
        ACCEPTING_ASYNCS = [
            kom.ASYNC_NEW_NAME,
            kom.ASYNC_LEAVE_CONF,
            kom.ASYNC_SEND_MESSAGE,
            kom.ASYNC_DELETED_TEXT,
            kom.ASYNC_NEW_TEXT,
            kom.ASYNC_NEW_RECIPIENT,
            kom.ASYNC_SUB_RECIPIENT,
            kom.ASYNC_NEW_MEMBERSHIP,
            kom.ASYNC_LOGOUT ]
        conn.add_async_handler(kom.ASYNC_SEND_MESSAGE, self.resp.sess.async_message)
        conn.add_async_handler(kom.ASYNC_LOGOUT, self.resp.sess.async_logout)
        kom.ReqAcceptAsync(conn, ACCEPTING_ASYNCS).response()

    def print_motd(self, conn):
        info = kom.ReqGetInfo(conn).response()
        if not info.motd_of_lyskom: return 0
        
        self.doc.append(Heading(2, self._("Message Of The Day")))
        self.doc.append(Heading(3,
                                self._("This server has a message"
                                       " of the day:")))

        text = kom.ReqGetText(conn, info.motd_of_lyskom).response()
        (subject, body) = string.split(text, "\n", 1)
        conn.socket.close()

        self.doc.append(Bold(webkom_escape(subject)))
        self.doc.append(BR())
        body = self.webkom_escape_linkify(body)
        body = string.replace(body, "\n","<br>\n")
        self.doc.append(body)
        self.doc.append(HR())

        F = Form(BASE_URL, name="loginform", submit="")
        self.doc.append(F)
        F.append(Input(type="hidden", name="komserver",
                       value=self.komserver))
        F.append(Input(type="hidden", name="username",
                       value=self.username))
        F.append(Input(type="hidden", name="password",
                       value=self.password))
        F.append(Input(type="hidden", name="skipmotd",
                       value="yes"))

        submitbutton = Input(type="submit",
                             name="loginsubmit",
                             value=self._("Continue Logging in"))

        F.append(submitbutton)
        return 1

    def response(self):
        self.resp.shortcuts_active = 0

        if not self.logins_accepted():
            return
        
        if not self.valid_parameters():
            return

        serverfields = self.form["komserver"].value.split(":")
        self.komserver = serverfields[0]
        if len(serverfields) > 1:
            # note: string
            self.komport = serverfields[1]
        else:
            self.komport = "4894"
        self.username = self.form["username"].value
        self.password = self.form["password"].value

        # The remote_host is only used as part of the user name sent at login
        try:
            remote_addr = self.resp.env["REMOTE_ADDR"]
            remote_host = socket.gethostbyaddr(remote_addr)[0]
        except:
            remote_host = "(unknown)"

        try:
            conn = kom.CachedUserConnection(self.komserver, int(self.komport),
                                            "WebKOM%" + remote_host,
                                            localbind=LOCALBIND)
        except:
            self.error_message(self._("Cannot connect to server."))
            return

        if not self.form.has_key("skipmotd"):
            if self.print_motd(conn): return

        if self.form.getvalue("searchconf_komconvention"):
            matches = conn.lookup_name(self.username, want_pers=1, want_confs=0)
        else:
            matches = conn.regexp_lookup(self.username, want_pers=1, want_confs=0)

        # Check number of matches
        if len(matches) == 0:
            self.error_message(self._("The user %s does not exist.") % self.username)
            return
        elif len(matches) > 1:
            # Name is ambiguous. Generate table for selection. 
            self.gen_table(matches)
            return

        pers_num = matches[0][0]
        
        try:
            kom.ReqLogin(conn, pers_num, self.password, invisible = 0).response()
        except kom.InvalidPassword:
            self.error_message(self._("Wrong password."))
            return

        # Set user_no in connection
        conn.set_user(pers_num, set_member_confs=0)
        kom.ReqSetClientVersion(conn, "WebKOM", VERSION).response()

        if LOGOUT_OTHER_SESSIONS:
            # Logout other sessions
            othersessions_keys = sessionset.\
                                 get_sessionkeys_by_person_no(pers_num)
            for key in othersessions_keys:
                if self.komserver == sessionset.\
                       get_session(key).conn.host and \
                       int(self.komport) == sessionset.\
                       get_session(key).conn.port:
                    sessionset.del_session(key)

        # Create new session
        self.resp.sess = Session(conn, self.komserver)
        # Add to sessionset
        self.resp.key = sessionset.add_session(self.resp.sess)
        
        # Setup async handling
        self.setup_asyncs(conn)

        # Redirect to progress page
        self.resp.set_redir("?sessionkey=" + self.resp.key + "&action=login_progress")


class LoginProgressPageActions(Action):
    def response(self):
        self.resp.shortcuts_active = 0
        self.doc.style = """\
SPAN.countdownstyle {
    background-color: #ffffff;
    position:absolute; 
    left:40; 
    top:140; 
}
SPAN.countdownfinished {
    background-color: #ffffff;
    position:absolute; 
    left:5; 
    top:200; 
}

"""
        self.resp.write_docstart_refresh(1, "")
        self.doc.append(Heading(2, self._("Login progress")))
        self.doc.append(self._("Please wait while your conference list is loading..."), BR())
        self.doc.append(self._("Number of conferences loaded:"))
        self.resp.req.out.write(self.doc.flush_doc_contents())
        self.resp.req.flush_out()

        self.sess.conn.set_member_confs()
        last_update = 0

        # Pre-fetch information about conferences & unread
        total_num_confs = len(self.sess.conn.member_confs)
        for conf_pos in range(total_num_confs):
            curtime = time.time()
            # Display progress every second
            if curtime - last_update > 1:
                self.print_progress(conf_pos, total_num_confs)
                last_update = curtime

            #time.sleep(1) # For debugging
            conf_num = self.sess.conn.member_confs[conf_pos]
            self.sess.conn.no_unread[conf_num]

        self.print_progress(total_num_confs, total_num_confs)
        self.print_loaded()
        
    def print_progress(self, confs_loaded, total_num_confs):
        self.resp.req.out.write('<span id="counter" class="countdownstyle">%s/%s</span><br>\n' % (str(confs_loaded), str(total_num_confs)))
        self.resp.req.flush_out()

    def print_loaded(self):
        # It's impossible to use class as a keyword argument directly. 
        kwargs = {"class": "countdownfinished", "id": "counter"}
        span = Span(**kwargs)
        self.doc.append(span)
        span.append(self._("All conferences loaded. "))
        span.append(Href(self.base_session_url(), self._("Go to main page")))
        
        self.resp.req.out.write(self.doc.flush_doc_contents())
        self.resp.req.flush_out()
        

class InvalidSessionPageActions(Action):
    "Generate a page informating about an invalid session"
    def response(self):
        toplink = Href(BASE_URL, "WebKOM")
        self.doc.append(toplink)
        self.doc.append(Heading(2, self._("Not logged in")))
        self.doc.append(Container(self._("Go to "), Href(BASE_URL, self._("the login page")),
                                  self._(" and login again.")))
        return 


class ViewConfsUnreadActions(Action):
    def response(self):
        ViewConfsActions(self.resp).response(only_unread=1)
        
class ViewConfsActions(Action):
    "Generate a page with all member conferences"
    def response(self, only_unread=0):
        toplink = Href(self.base_session_url(), "WebKOM")
        if only_unread:
            action_url = "viewconfs_unread"
            title = self._("Conferences (with unread)")
            conflink = self.action_href(action_url, title)
        else:
            action_url = "viewconfs"
            title = self._("Conferences (you are a member of)")
            conflink = self.action_href(action_url, title)

        cont = Container(toplink, TOPLINK_SEPARATOR, conflink)
        self.append_std_top(cont)

        if only_unread:
            self.doc.append(Heading(2, title))
        else:
            self.doc.append(Heading(2, title))

        std_cmd = Container()
        self.doc.append(self._("Default command: "), std_cmd)
        self.add_stdaction(std_cmd, self.resp, "goconf_with_unread", self._("Next conference with unread"))
        self.resp.flush()

        # Information about number of unread
        self.doc.append(self.unread_info())

        first_conf = int(self.form.getvalue("first_conf", "0"))

        # We ask for one extra, so we can know if we should display a next-page-link
        memberships = get_active_memberships(self.sess.conn, first_conf, MAX_CONFS_PER_PAGE + 1, only_unread)
        prev_first = next_first = None
        if first_conf:
            # Link to previous page
            prev_first = first_conf - MAX_CONFS_PER_PAGE
            if prev_first < 0:
                prev_first = 0
                
        if len(memberships) > MAX_CONFS_PER_PAGE:
            # We cannot show all confs on the same page. Link to next page
            next_first = first_conf + MAX_CONFS_PER_PAGE
            # Remove the highest conference
            memberships.pop()

        # Now that we (possibly) have pop:d the extra one, sort!
        memberships.sort(lambda first, second: cmp(second.priority, first.priority))

        # Add the previous-page-link
        self.doc.append(self.action_href(action_url + "&amp;first_conf=" + str(prev_first),
                                         self._("Previous page"), prev_first is not None), NBSP)

        # Add a table
        headings = [self._("Conference name"), self._("Number of unread")]
        tab = []
        self.doc.append(Table(heading=headings, body=tab, cell_padding=2, width="60%"))

        for conf in memberships:
            n_unread = self.sess.conn.no_unread[conf.conference]
            name = self.get_conf_name(conf.conference)
            name = string.upper(name[:1]) + name[1:]
            if n_unread > 500:
                comment = webkom_escape(">500")
                name = Bold(name)
            elif n_unread > 0:
                comment = str(n_unread)
                name = Bold(name)
            else:
                comment = self._("none")
                
            tab.append([self.action_href("goconf&amp;conf=" + str(conf.conference), name),
                        comment])

        

        # Add the next-page-link
        self.doc.append(self.action_href(action_url + "&amp;first_conf=" + str(next_first),
                                         self._("Next page"), next_first is not None), NBSP)
        # Link for next conference with unread
        self.doc.append(self.action_href("goconf_with_unread",
                                         self._("Next conference with unread")), NBSP)

        return


class GoConfWithUnreadActions(Action):
    "Go to conference with unread articles"
    def response(self):
        next_conf = get_conf_with_unread(self.sess.conn, self.sess.conn.member_confs, self.sess.current_conf)
        if next_conf:
            GoConfActions(self.resp).response(next_conf)
        else:
            toplink = Href(self.base_session_url(), "WebKOM")
            conflink = self.action_href("viewconfs", self._("Conferences (you are a member of)"))
            cont = Container(toplink, TOPLINK_SEPARATOR, conflink)
            self.append_std_top(cont)
            self.doc.append(Heading(3, self._("No unread")))
            self.doc.append(self._("There are no unread articles."))
        return


class GoConfActions(Action):
    "Generate a page with the subjects of articles"
    def print_faq_link(self, conf_num):
        aux_items = self.sess.conn.conferences[conf_num].aux_items
        ai_faq = kom.first_aux_items_with_tag(aux_items, kom.AI_FAQ_TEXT)
        if ai_faq:
            textnum = ai_faq.data
        else:
            textnum = ""

        link = self.action_href("viewtext&amp;textnum=" + textnum,
                                self._("View FAQ"), ai_faq)
        self.doc.append(link, NBSP)

    
    def response(self, conf_num=None):
        # FIXME: The routines for stepping forward/backward pagewise is
        # more or less broken. Instead of just adding/subtracting MAX_SUBJ_PER_PAGE to the
        # local textnumbers, we should call local-to-global and actually check
        # the number of texts returned. We may have to do this several times
        # with lower and lower first-local-no, to get the desired number of texts.
        # Implement later... :-)
        
        # conf_num provided by internal method call?
        if conf_num == None:
            # No, fetch via CGI
            conf_num = int(self.form["conf"].value)

        # Change conference
        ismember =  self.change_conf(conf_num)
        
        # Fetch conference name
        conf_name = self.get_conf_name(conf_num)
        toplink = Href(self.base_session_url(), "WebKOM")
        cont = Container(toplink, TOPLINK_SEPARATOR, self.current_conflink())
        self.append_std_top(cont)
        cont.append(TOPLINK_SEPARATOR, self.action_href("goconf&amp;conf=" + str(conf_num),
                                            conf_name))
        self.doc.append(Heading(2, conf_name))

        if not ismember:
            self.doc.append("You must",
                            self.action_href("joinconfsubmit&amp;selected_conf=" + str(conf_num),
                                             self._("join this conference")),
                            "before you can read articles.")
            return
        
        # Standard action
        std_cmd = Container()
        self.doc.append(self._("Default command: "), std_cmd)
        next_text = get_next_unread(self.sess.conn, self.sess.current_conf)
        if next_text:
            std_url = "viewtext&amp;textnum=" + str(next_text)
            self.add_stdaction(std_cmd, self.resp, std_url, self._("Read next unread"))
        else:
            self.add_stdaction(std_cmd, self.resp, "goconf_with_unread", self._("Next conference with unread"))
        del std_cmd
        self.resp.flush()
        
        # Information about number of unread
        self.doc.append(self.unread_info(self.sess.current_conf))

        self.doc.append(self.action_href("writearticle&amp;rcpt=" + str(conf_num),
                                         self._("Write article")), NBSP)
        # Link to view presentation for this conference
        presentation = self.get_presentation(conf_num)
        self.doc.append(self.action_href("viewtext&amp;textnum=" + str(presentation),
                                         self._("View presentation"), presentation), NBSP)

        self.doc.append(self.action_href("set_unread", self._("Set unread")), NBSP)
        self.doc.append(self.action_href("leaveconf", self._("Leave conference")), NBSP)
        self.print_faq_link(conf_num)
        self.doc.append(self.action_href("search&amp;conf=" + str(conf_num),
                                         self._("Search")), NBSP)
        
        self.doc.append(BR(), Heading(3, self._("Articles")))

        # local_num is the first local_num we are interested in
        if self.form.has_key("local_num"):
            ask_for = int(self.form["local_num"].value)
        else:
            ask_for = None

        # Get unread texts
        # FIXME: error handling
        texts = get_texts(self.sess.conn, conf_num, MAX_SUBJ_PER_PAGE, ask_for)
        
        # Prepare for links to earlier/later pages of texts
        first_local_num = self.sess.conn.conferences[conf_num].first_local_no
        highest_local_num = self.sess.conn.uconferences[conf_num].highest_local_no
        prev_first = next_first = None
        if len(texts) > 0:
            first_in_set = texts[0][0]
            last_in_set = texts[-1:][0][0]
            if first_in_set > first_local_num:
                prev_first = first_in_set - MAX_SUBJ_PER_PAGE
            next_first = last_in_set + 1
        else:
            # We got no texts. Only show link to earlier texts.
            prev_first = highest_local_num - MAX_SUBJ_PER_PAGE

        # Check for validity
        if prev_first < first_local_num:
            prev_first = first_local_num
        if next_first > highest_local_num:
            next_first = None

        ediv = Div(align="right")
        ediv.append(self.action_href("goconf&amp;conf=" + str(conf_num) \
                                     + "&amp;local_num=" + str(prev_first),
                                     self._("Earlier articles"),
                                     prev_first), NBSP)
        self.doc.append(ediv)
        
        headings = [self._("Unread"), self._("Subject"), self._("Author"), self._("Date"), self._("Number")]
        tab = []
        self.doc.append(Table(heading=headings, body=tab, cell_padding=2,
                              column1_align="right", cell_align="left", width="100%"))

        # Format and append text numbers, authors and subjects to the page
        for (local_num, global_num) in texts:
            ts = self.sess.conn.textstats[global_num]
            # Textnum
            textnum = self.action_href("viewtext&amp;textnum=" + str(global_num), str(global_num))
            
            # Date
            date = self.sess.conn.textstats[global_num].creation_time.to_date_and_time()
            
            # Author
            ai_from = kom.first_aux_items_with_tag(ts.aux_items,
                                                   kom.AI_MX_FROM)
            author = ""
            if ai_from:
                ai_author =  ai_author = kom.\
                            first_aux_items_with_tag(ts.aux_items,
                                                     kom.AI_MX_AUTHOR)
                if ai_author:
                    author = ai_author.data + " "
                author = author + str(Href("mailto:" + ai_from.data,
                                       ai_from.data))
            else:
                author = self.get_pers_name(ts.author)
                
            # Subject
            subjtext = self.sess.conn.subjects[global_num]
            if not subjtext:
                # If subject is empty, the table gets ugly
                subjtext = "&nbsp;"
            else:
                subjtext = webkom_escape(subjtext)
                
            subj = self.action_href("viewtext&amp;textnum=" + str(global_num),
                                    subjtext)
            
            if is_unread(self.sess.conn, conf_num, local_num):
                subj = Bold(subj)
                textnum = Bold(textnum)
                unreadindicator = Bold("x")
            else:
                unreadindicator = "&nbsp;"

            tab.append([unreadindicator, subj, author, date, textnum])

        tl = TableLite(width="100%")
        tr = TR()
        tr.append(TD(self.action_href("goconf_with_unread",
                                      self._("Next conference with unread"))
                     , align="left"))
        tr.append(TD(self.action_href("goconf&amp;conf=" +\
                                      str(conf_num) \
                                      + "&amp;local_num=" +\
                                      str(next_first),
                                      self._("Later articles"),
                                      next_first),
                     align="right"))
        tl.append(tr)
        self.doc.append(tl)



class SpecifyArticleNumberPageActions(Action):
    """Specify article number to view"""
    def response(self):
        self.resp.shortcuts_active = 0
        self.doc.onLoad = "document.specify_article_form.textnum.focus()"
        toplink = Href(self.base_session_url(), "WebKOM")
        thispage = self.action_href("specify_article_number", self._("View article with specified number"))
        cont = Container(toplink, TOPLINK_SEPARATOR, thispage)
        self.append_std_top(cont)
        self.doc.append(Heading(2, self._("View article with specified number")))

        submitbutton = Input(type="submit", name="viewtext",
                             value=self._("View article"))
        F = Form(BASE_URL, name="specify_article_form", submit=submitbutton)
        self.doc.append(F)
        F.append(self.hidden_key())
        F.append(Input(name="textnum"))
        self.sess.current_conf = 0


class ViewTextActions(Action):
    "Generate a page with a requested article"
    def get_subject(self, global_num):
        subject = self.sess.conn.subjects[global_num]
        if not subject:
            # If subject is empty, the table gets ugly
            subject = "&nbsp;"
        else:
            subject = webkom_escape(subject)
        return subject


    def add_comments_to(self, ts, header):
        # Comment to
        for c in ts.misc_info.comment_to_list:
            # Fetch info about commented text
            try:
                c_ts = self.sess.conn.textstats[c.text_no]
                c_authortext = self._(" by ") + self.get_pers_name(c_ts.author)
            except:
                c_authortext = ""
            if c.type == kom.MIC_FOOTNOTE:
                header.append([self._("Footnote to:"),
                               str(self.action_href("viewtext&amp;textnum=" + str(c.text_no), str(c.text_no))) \
                               + c_authortext])
            else:
                header.append([self._("Comment to:"),
                               str(self.action_href("viewtext&amp;textnum=" + str(c.text_no), str(c.text_no))) \
                               + c_authortext])
                
            if c.sent_by is not None:
                presentation = str(self.get_presentation(c.sent_by))
                header.append([self._("Added by:"),
                               self.action_href("viewtext&amp;textnum=" + presentation, 
                                                self.get_pers_name(c.sent_by), presentation)])
            if c.sent_at is not None:
                header.append([self._("Added:"), c.sent_at.to_date_and_time()])

    def do_recipients(self, ts, header):
        "Add recipients to header. Also return a URL substring for commenting."
        comment_url = ""
        for r in ts.misc_info.recipient_list:
            leftcol = mir2caption(self, r.type)
            presentation = str(self.get_presentation(r.recpt))
            # Recipient, with hyperlink to presentation
            rightcol = str(self.action_href("viewtext&amp;textnum=" + presentation, 
                                            self.get_conf_name(r.recpt), presentation))
            # Prepare comment-url
            # Do not keep CC and BCC recipients when writing comment. 
            if r.type == kom.MIR_TO:
                comment_url = comment_url + "&amp;" + mir2keyword(r.type) + "=" + str(r.recpt)
            
            if r.sent_by is not None:
                leftcol = leftcol + "<br>" + self._("Sent by:")
                rightcol = rightcol + "<br>" + self.get_pers_name(r.sent_by)
            if r.sent_at is not None:
                leftcol = leftcol + "<br>" + self._("Sent:")
                rightcol = rightcol + "<br>" + r.sent_at.to_date_and_time()
            if r.rec_time is not None:
                leftcol = leftcol + "<br>" + self._("Received:")
                rightcol = rightcol + "<br>" + r.rec_time.to_date_and_time()
            header.append([leftcol, rightcol])
            # Mark as read
            try:
                kom.ReqMarkAsRead(self.sess.conn, r.recpt, [r.loc_no]).response()
            except:
                pass

            # Update memberships and no_unread in cache 
            if r.recpt in self.sess.conn.member_confs:
                # Note: update_unread must be called before update_membership, otherwise
                # update_unread thinks this text is already read...
                update_unread(self.sess.conn, r.recpt, r.loc_no)
                update_membership(self.sess.conn, r.recpt, r.loc_no)
            
            # Fetch the local_num
            ## if r.recpt == self.sess.current_conf:
            ## local_num = r.loc_no
                
        return comment_url


    def add_comments_in(self, ts, new_comments):
        header = []
        for c in ts.misc_info.comment_in_list:
            # Fetch info about comment
            try:
                c_ts = self.sess.conn.textstats[c.text_no]
                c_authortext = self._(" by ") + self.get_pers_name(c_ts.author)
            except:
                c_authortext = ""
                
            if c.type == kom.MIC_FOOTNOTE:
                if "" != c_authortext:
                    header.append([self._("Footnote in article:"),
                                   str(self.action_href("viewtext&amp;textnum=" + str(c.text_no), str(c.text_no))) \
                                   + c_authortext])
                else:
                    header.append([self._("Footnote in article:"),
                                   Strike(str(c.text_no)),
                                   Emphasis(self._("(Not readable)"))])
            else:
                if "" != c_authortext:
                    header.append([self._("Comment in article:"),
                                   str(self.action_href("viewtext&amp;textnum=" + str(c.text_no), str(c.text_no))) \
                                   + c_authortext])
                else:
                    header.append([self._("Comment in article:"),
                                   Strike(str(c.text_no)),
                                   Emphasis(self._("(Not readable)"))])
                
                # The text seems to exist. Maybe add it to comment_tree. 
                if c_authortext:
                    for rcpt in self.sess.conn.textstats[c.text_no].misc_info.recipient_list:
                        if rcpt.recpt in self.sess.conn.member_confs:
                            new_comments.append(c.text_no)
                            break

        # Do not add the table if there are no comments; this will
        # render as incorrect HTML. Tables may not be empty. 
        if header:
            self.doc.append(Table(body=header, cell_padding=2, column1_align="right", border=0, width="80%"))

    def check_content_type(self, ts):
        ai_ct = kom.first_aux_items_with_tag(ts.aux_items, kom.AI_CONTENT_TYPE)
        if not ai_ct:
            return
            
        type_subtype = mime_content_type(ai_ct.data)
        if type_subtype not in ["text/x-kom-basic", "x-kom/basic",
                                "x-kom/text", "text/plain"]:
            self.doc.append(Bold("Warning: article has unknown content type ",
                                 webkom_escape(type_subtype), BR()))
        
        ct_params = mime_content_params(ai_ct.data)
        charset = ct_params.get("charset")
        if charset and charset not in ["iso-8859-1", "us-ascii"]:
            self.doc.append(Bold("Warning: article has unknown character set ",
                                 webkom_escape(charset), BR()))

    def print_fast_replies(self, ts):
        ai_fr_list = kom.all_aux_items_with_tag(ts.aux_items, kom.AI_FAST_REPLY)
        for ai_fr in ai_fr_list:
            replystring = ai_fr.data + " /" + self.get_pers_name(ai_fr.creator)
            self.doc.append(webkom_escape(replystring), BR())

    def print_cross_refs(self, ts):
        ai_cr_list = kom.all_aux_items_with_tag(ts.aux_items, kom.AI_CROSS_REFERENCE)
        for ai_cr in ai_cr_list:
            reftype = ai_cr.data[0]
            refdata = ai_cr.data[1:]
            if reftype == "T":
                textlink = self.action_href("viewtext&amp;textnum=" + str(refdata), str(refdata))
                self.doc.append(self._("See text "), textlink)
            elif reftype == "C":
                conf_num = int(refdata)
                confname = self.get_conf_name(conf_num)
                conf_num_link = self.action_href("goconf&amp;conf=" + refdata,
                                                 "<%s>" % refdata)
                conf_name_link = self.action_href("goconf&amp;conf=" + refdata,
                                                  confname)
                self.doc.append(self._("See conference "), conf_num_link,
                                " ", conf_name_link)
            elif reftype == "P":
                pers_num = int(refdata)
                persname = self.get_pers_name(pers_num)
                presentation = str(self.get_presentation(pers_num))
                pers_num_link = self.action_href("viewtext&amp;textnum=" + presentation,
                                                 "<%s>" % refdata)
                pers_name_link = self.action_href("viewtext&amp;textnum=" + presentation,
                                                  persname)
                self.doc.append(self._("See person "), pers_num_link,
                                " ", pers_name_link)
            else:
                self.print_error(self._("Invalid cross reference:"), webkom_escape(ai_cr.data))
                return

            authstring = " /" + self.get_pers_name(ai_cr.creator)
            self.doc.append(webkom_escape(authstring), BR())

    def print_no_comments(self, ts):
        if kom.first_aux_items_with_tag(ts.aux_items, kom.AI_NO_COMMENTS):
            self.doc.append(self._("The author has requested others not to comment this text."), BR())

    def print_personal_comment(self, ts):
        if kom.first_aux_items_with_tag(ts.aux_items, kom.AI_PERSONAL_COMMENT):
            self.doc.append(self._("The author requests private replies only."))

    def print_read_confirmations(self, global_num, ts):
        # FIXME: Maybe remove duplicate confirmations
        read_confirms = kom.all_aux_items_with_tag(ts.aux_items, kom.AI_READ_CONFIRM)
        creators = [ai.creator for ai in read_confirms]
        me = self.sess.conn.get_user()
        if not me in creators:
            self.print_request_confirmation(global_num, ts)

        for confirmation in read_confirms:
            self.doc.append(self._("Confirmed reading: "),
                            webkom_escape(self.get_pers_name(confirmation.creator)),
                            BR())

    def print_request_confirmation(self, global_num, ts):
        if kom.first_aux_items_with_tag(ts.aux_items, kom.AI_REQUEST_CONFIRMATION):
            confirm_link = self.action_href("read_confirmation&amp;textnum=" + \
                                            str(global_num),
                                            self._("Confirm reading this text?"))
            self.doc.append(self._("The author requests read confirmation. "),
                            Bold(confirm_link), BR())

    def print_faq_info(self, ts):
        ai_list = kom.all_aux_items_with_tag(ts.aux_items, kom.AI_FAQ_FOR_CONF)
        for ai_faq in ai_list:
            # FIXME: This is a bit ugly. Make some common method for printing
            # conferenc links both by number and name. Same goes for print_cross_refs. 
            conf_num = ai_faq.data
            confname = self.get_conf_name(int(conf_num))
            conf_num_link = self.action_href("goconf&amp;conf=" + conf_num,
                                             "<%s>" % conf_num)
            conf_name_link = self.action_href("goconf&amp;conf=" + conf_num,
                                              confname)
            self.doc.append(self._("This text is FAQ for conference "),
                            conf_num_link, " ", conf_name_link, BR())

    def print_creating_software(self, ts):
        ai_cs = kom.first_aux_items_with_tag(ts.aux_items, kom.AI_CREATING_SOFTWARE)
        if ai_cs:
            self.doc.append(self._("Created with %s." % ai_cs.data), BR())
            
    def response(self):
        # Global text number
        try:
            global_num = int(self.form["textnum"].value)
        except ValueError:
            self.print_error(self._("Invalid article number."))
            return 

        # If current_conf is zero, change to the texts first
        # recipient.
        if 0 == self.sess.current_conf:
            try:
                new_current_conf = self.sess.conn.textstats[global_num].misc_info.recipient_list[0].recpt
            except kom.NoSuchText:
                # Not perfect, but this shouldn't happen anyway. 
                new_current_conf = self.sess.conn.get_user()
            self.change_conf(new_current_conf)
        
        # Toplink
        toplink = Href(self.base_session_url(), "WebKOM")
        # Link to conferences
        cont = Container(toplink, TOPLINK_SEPARATOR, self.current_conflink())
        self.append_std_top(cont)

        # Valid article?
        try:
            if global_num == 0:
                raise kom.NoSuchText
            ts = self.sess.conn.textstats[global_num]
        except kom.NoSuchText:
            self.print_error(self._("The article does not exist."))
            return 
        except:
            self.print_error(self._("An error occurred when fetching article information."))
            return 
        
        # Link to current conference
        # Note: It's possible to view texts from other conferences,
        # while still staying in another conference
        cont.append(TOPLINK_SEPARATOR)
        cont.append(self.action_href("goconf&amp;conf=" + str(self.sess.current_conf),
                                     self.get_conf_name(self.sess.current_conf)))
        # Link to this page
        cont.append(TOPLINK_SEPARATOR)
        cont.append(self.action_href("viewtext" + "&amp;textnum=" + str(global_num),
                                     webkom_escape(self.sess.conn.subjects[global_num])))
        self.doc.append(BR())
        del cont
        self.resp.flush()
        
        lower_actions = Container()
        #
        # Upper actions
        #
        std_cmd = Container()
        self.doc.append(self._("Default command: "), std_cmd)

        # Information about number of unread
        unread_cont = Container()
        self.doc.append(unread_cont)

        upper_actions = Container()
        self.doc.append(upper_actions)

        # Link for next conference with unread
        upper_actions.append(self.action_href("goconf_with_unread",
                                              self._("Next conference with unread")), NBSP)

        self.doc.append(BR())
        
        # Warn for strange content-types
        self.check_content_type(ts)

        body = self.get_article_text(global_num)
        
        ismail = 0
        if kom.first_aux_items_with_tag(ts.aux_items, kom.AI_MX_FROM):
            ismail = 1
        viewmailheadercode = ""
        if kom.first_aux_items_with_tag(ts.aux_items, kom.AI_MX_MISC):
            if self.form.getvalue("viewmailheader"):
                body = kom.first_aux_items_with_tag(ts.aux_items,
                                                    kom.AI_MX_MISC).data + body
                viewmailheadercode = "&amp;viewmailheader=true"                
            else:
                body = body[1:]
        else:
            body = body[1:]

        header = []
        header.append([self._("Article number:"),
                       self.action_href("viewtext&amp;textnum=" + str(global_num), str(global_num))])

        presentation = str(self.get_presentation(ts.author))
        importdate = kom.first_aux_items_with_tag(ts.aux_items,
                                                  kom.AI_MX_DATE)

        if importdate:
            header.append([self._("Date:"),
                           importdate.data])
        else:
            header.append([self._("Date:"), ts.creation_time.to_date_and_time()]);
        if ismail:
            ai_from = kom.first_aux_items_with_tag(ts.aux_items,
                                                   kom.AI_MX_FROM)
            ai_author = kom.first_aux_items_with_tag(ts.aux_items,
                                                     kom.AI_MX_AUTHOR)
            realname = ""
            if ai_author:
                realname = ai_author.data + " "
            header.append([self._("Author:"),
                           realname + str(Href("mailto:" + ai_from.data,
                                               ai_from.data))])
            
            header.append([self._("Imported:"),
                           ts.creation_time.to_date_and_time() +\
                           self._(" by ") +
                           str(self.action_href("viewtext&amp;textnum=" +\
                                                presentation,
                                                self.get_pers_name(ts.author),
                                                presentation))])
            for recipient in kom.all_aux_items_with_tag(ts.aux_items,
                                                        kom.AI_MX_TO):
                header.append([self._("External recipient:"),
                               Href("mailto:" + recipient.data,
                                    recipient.data)])
            for recipient in kom.all_aux_items_with_tag(ts.aux_items,
                                                        kom.AI_MX_CC):
                header.append([self._("External carbon copy:"),
                               Href("mailto:" + recipient.data,
                                    recipient.data)])
            
        else:            
            header.append([self._("Author:"),
                           self.action_href("viewtext&amp;textnum=" + presentation,
                                            self.get_pers_name(ts.author), presentation)])

        # Comments-to
        self.add_comments_to(ts, header)
        
        # Recipients
        comment_url = self.do_recipients(ts, header)

        # The number of unread has been updated in do_recipients, so now it's OK to add it
        unread_cont.append(self.unread_info(self.sess.current_conf))

        markline = str(ts.no_of_marks)
        if self.sess.marked_texts.has_key(global_num):
            markline += self._(", marked by you. ")
            markline += str(self.action_href("unmark_text&amp;textnum="+\
                                           str(global_num),
                                           self._("Unmark")))
        else:
            markline += self._(", not marked by you. ")
            markline += str(self.action_href("mark_text&amp;textnum="+\
                                           str(global_num),
                                           self._("Mark")))

                             
        header.append([self._("Marks:"), markline])

        header.append([self._("Subject:"), Bold(self.get_subject(global_num))])

        self.doc.append(BR())
        self.doc.append(Table(body=header, cell_padding=2, column1_align="right", width="75%"))
        
        # Body
        # FIXME: Reformatting according to protocol A.
        body = self.webkom_escape_linkify(body)
        body = string.replace(body, "\n","<br>\n")
        bodycont = Container()

        # Add formatting style
        format = self.form.getvalue("viewformat")
        if format == "code":
            bodycont.append("<code>")
            body = string.replace(body, " ", "&nbsp;")
            body = string.replace(body, "\t", "&nbsp;"*8)
            bodycont.append(body)
            bodycont.append("</code>")
        elif format:
            # Generic style. May be useful. 
            bodycont.append("<" + format + ">")
            bodycont.append(body)
            bodycont.append("</" + format + ">")
        else:
            bodycont.append(body)

        # We are constructing a table manuall, since HTMLgen insists of
        # modify the text put into the cells. 
        self.doc.append("<table width=\"100%\" border=2 cellpadding=2>")
        self.doc.append("<tr><td>" + str(bodycont) + "</td></tr>")
        self.doc.append("</table>")

        # Ok, the body is done. Lets add fast replies.
        self.print_fast_replies(ts)

        # Print cross reference aux-items
        self.print_cross_refs(ts)

        # no-comments, personal-comment
        self.print_no_comments(ts)
        self.print_personal_comment(ts)

        # request-confirmation
        self.print_read_confirmations(global_num, ts)

        # if this text is FAQ for something, then tell so
        self.print_faq_info(ts)

        # Creating software. Currently disabled. 
        #self.print_creating_software(ts)

        # Add all comments.
        new_comments = []
        self.add_comments_in(ts, new_comments)

        # Handling for reading comments
        reading_comment = self.form.getvalue("reading_comment", 0)
        if not reading_comment:
            # Zero comment_tree
            self.sess.comment_tree = []
        else:
            # Did we just read the first in the comment_tree? Delete it, then.
            if self.sess.comment_tree:
                if global_num == self.sess.comment_tree[0]:
                    del self.sess.comment_tree[0]

        #
        # Lower actions
        #
        self.doc.append(lower_actions)

        # Add links for reading next unread
        next_text = get_next_unread(self.sess.conn, 
                                    self.sess.current_conf)
        next_text_url = "viewtext&amp;textnum=" + str(next_text)
        lower_actions.append(self.action_href(next_text_url, self._("Read next unread"),
                                              next_text), NBSP)

        # Add new comments
        self.sess.comment_tree = new_comments + self.sess.comment_tree

        # If a global_no is in the comment_tree, it should belong to
        # this conference and be valid. So, if reading comments, add a
        # link.
        if self.sess.comment_tree:
            next_comment = self.sess.comment_tree[0]
        else:
            next_comment = None
        next_comment_url = "viewtext&amp;textnum=" + str(next_comment) + "&amp;reading_comment=1"
        lower_actions.append(self.action_href(next_comment_url, self._("Read next comment"),
                                              next_comment), NBSP)

        # Standard action
        if next_comment:
            self.add_stdaction(std_cmd, self.resp, next_comment_url, self._("Read next comment"))
        elif next_text:
            self.add_stdaction(std_cmd, self.resp, next_text_url, self._("Read next unread"))
        else:
            self.add_stdaction(std_cmd, self.resp, "goconf_with_unread", self._("Next conference with unread"))
            
            
        # Maybe the user want to comment?
        comment_url = comment_url + "&amp;comment_to=" + str(global_num)
        lower_actions.append(self.action_href("writearticle" + comment_url,
                                              self._("Write comment")), NBSP)

        personal_comment_url = "&amp;rcpt=%d&amp;rcpt=%d&amp;comment_to=%d" % \
                               (ts.author,
                                self.sess.conn.get_user(),
                                global_num)

        lower_actions.append(self.action_href("writearticle" +\
                                              personal_comment_url,
                                              self._("Write personal "\
                                                     "comment")), NBSP)
        # Add keyboard shortcut
        self.action_shortcut("k", "writearticle" + comment_url)

        if format:
            lower_actions.append(self.action_href("viewtext&amp;textnum=" + str(global_num),
                                                  self._("View in normal style")))
        else:
            lower_actions.append(self.action_href("viewtext&amp;textnum=" + str(global_num) + "&amp;viewformat=code",
                                                  self._("View in code style")))

        if ismail:
            if "" != viewmailheadercode:
                lower_actions.append(self.action_href("viewtext&amp;textnum=" +\
                                                      str(global_num),
                                                      self._("View without mail headers")))
            else:
                lower_actions.append(self.action_href("viewtext&amp;textnum=" +\
                                                      str(global_num) +\
                                                      "&amp;viewmailheader=true",
                                                      self._("View with mail headers")))
        return 



class ChangePwActions(Action):
    "Generate a page for changing LysKOM password"
    def response(self):
        self.resp.shortcuts_active = 0
        self.doc.onLoad = "document.changepwform.oldpw.focus()"
        toplink = Href(self.base_session_url(), "WebKOM")
        cont = Container(toplink, TOPLINK_SEPARATOR + self._("Change password"))
        self.append_std_top(cont)
        submitbutton = Center(Input(type="submit", name="changepwsubmit",
                                    value=self._("Change password")))
        F = Form(BASE_URL, name="changepwform", submit=submitbutton)
        self.doc.append(F)
        F.append(self.hidden_key())
        
        F.append(BR(2))
        F.append(Center(Heading(2, self._("Change password"))))
        F.append(BR(2))
        logintable = [(self._("Old password"), Input(type="password", name="oldpw",size=20)),
                      (self._("New password"), Input(type="password", name="newpw1", size=20)),
                      (self._("Repeat new password"), Input(type="password", name="newpw2",size=20)) ]
        F.append(Center(Formtools.InputTable(logintable)))
        return


class ChangePwSubmit(Action):
    "Change LysKOM password"
    def response(self):
        assert(self.form.has_key("oldpw") and self.form.has_key("newpw1")
               and self.form.has_key("newpw2"))

        (oldpw, newpw1, newpw2) = (self.form["oldpw"].value,
                                   self.form["newpw1"].value,
                                   self.form["newpw2"].value)

        toplink = Href(self.base_session_url(), "WebKOM")
        changepwlink = self.action_href("changepw", self._("Change password"))

        std_top = self.gen_std_top(Container(toplink, TOPLINK_SEPARATOR, changepwlink))
        result_cont = Container(std_top)
        
        if newpw1 != newpw2:
            result_cont.append(self.gen_error(self._("The two new passwords didn't match.")))
            self.submit_redir(result_cont)
            return

        try:
            kom.ReqSetPasswd(self.sess.conn, self.sess.conn.get_user(), oldpw, newpw1).response()
        except:
            result_cont.append(self.gen_error(self._("The server rejected your password change request")))
            self.submit_redir(result_cont)
            return


        # No problems, it seems. 
        result_cont.append(Heading(3, self._("Ok")))
        result_cont.append(self._("Your password has been changed"))
        self.submit_redir(result_cont)
        return
                           


class CreateUserActions(Action):
    "Generate a page for creating a new LysKOM user"
    def response(self):
        self.doc.onLoad = "document.create_user_form.username.focus()"
        toplink = Href(BASE_URL, "WebKOM")
        create_user_link = Href(BASE_URL + "?action=create_user", self._("Create new user"))
        cont = Container(toplink, TOPLINK_SEPARATOR, create_user_link)
        self.append_std_top(cont)

        default_kom_server = DEFAULT_KOM_SERVER
        if self.form.has_key("komserver") and not LOCK_KOM_SERVER:
            default_kom_server = self.form["komserver"].value
        
        submitbutton = Center(Input(type="submit", name="create_user_submit", value=self._("Create new user")))
        F = Form(BASE_URL, name="create_user_form", submit="")
        F.append(Input(type="hidden", name="create_user_submit"))
        self.doc.append(F)
        
        F.append(BR(2))
        F.append(Center(Heading(2, self._("Create new user"))))
        F.append(BR(2))

        logintable = []

        if LOCK_KOM_SERVER:
            logintable.append((self._("Server"), default_kom_server))
            F.append(Input(type="hidden", name="komserver", value=default_kom_server))
        else:
            logintable.append((self._("Server"), Input(name="komserver", size=20, value=default_kom_server)))

        logintable.append((self._("Username"), Input(name="username", size=20),
                           self._("(like John Doe, ACM)")))
        logintable.append((self._("Password"), Input(type="password", name="password1", size=20)),)
        logintable.append((self._("Repeat password"), Input(type="password", name="password2", size=20)))
        
        F.append(Center(Formtools.InputTable(logintable, notecolor=BLACK)))
        F.append(Center(submitbutton))

        return


class CreateUserSubmit(Action):
    "Create new LysKOM user"
    def response(self):
        assert(self.form.has_key("komserver") and self.form.has_key("username")
               and self.form.has_key("password1") and self.form.has_key("password2"))

        (komserver, username) = (self.form["komserver"].value, self.form["username"].value)
        (password1, password2) = (self.form["password1"].value, self.form["password2"].value)

        toplink = Href(BASE_URL, "WebKOM")
        create_user_link = Href(BASE_URL + "?action=create_user", self._("Create new user"))
        cont = Container(toplink, TOPLINK_SEPARATOR, create_user_link)
        self.append_std_top(cont)

        if password1 != password2:
            self.print_error(self._("The two new passwords didn't match."))
            return

        # Connect to server
        try:
            conn = kom.Connection(komserver, 4894)
        except:
            self.print_error(self._("Cannot connect to server."))
            return

        # Create person
        flags = kom.PersonalFlags()
        try:
            kom.ReqCreatePerson(conn, username, password1, flags).response()
        except kom.LoginFirst:
            self.print_error(self._("The server requires login before new users can be created"))
            return
        except kom.PermissionDenied:
            self.print_error(self._("You lack permissions to create new users"))
            return
        except kom.PersonExists:
            self.print_error(self._("A user with this name exists."))
            return
        except kom.InvalidPassword:
            self.print_error(self._("Invalid password"))
            return
            
        self.doc.append(Heading(3, "Ok"))
        self.doc.append(self._("User created."))

class WritePresentationActions(Action):
    "Write presentation"
    def response(self):
        serverinfo = kom.ReqGetInfo(self.sess.conn).response()
        presfor = self.form.getvalue("presentationfor")
        if int(presfor) == self.sess.conn.get_user():
            self.change_conf(serverinfo.pers_pres_conf)
            WriteArticleActions(self.resp).response(presentationfor = int(presfor), presconf = serverinfo.pers_pres_conf)
        else:
            self.change_conf(serverinfo.conf_pres_conf)
            # FIXME: Shouldn't it be presconf = serverinfo.conf_pres_conf?
            WriteArticleActions(self.resp).response(presentationfor = int(presfor), presconf = serverinfo.pers_pres_conf)


class WriteLetterActions(Action):
    "Write personal letter"
    def response(self):
        self.change_conf(self.sess.conn.get_user())
        WriteArticleActions(self.resp).response()
        return 

    
class WriteArticleActions(Action):
    "Generate a page for writing or commenting an article"
    def response(self, presentationfor = None, presconf = None):
        self.resp.shortcuts_active = 0
        self.doc.onLoad = "document.writearticleform.articlesubject.focus()"
        # Fetch conference name
        conf_num = self.sess.current_conf
#        cs = self.sess.conn.conferences[conf_num]
#        if cs.type.original:
#            conf_num = cs.super_conf
        conf_name = self.get_conf_name(conf_num)
        
        toplink = Href(self.base_session_url(), "WebKOM")
        thisconf = self.action_href("goconf&amp;conf=" + str(conf_num), conf_name)

        comment_to_list = get_values_as_list(self.form, "comment_to")
        if presentationfor:
            if 0 != self.sess.conn.conferences[presentationfor].presentation:
                if not self.sess.conn.conferences[presentationfor].\
                       presentation in comment_to_list:
                    comment_to_list += [self.sess.conn.\
                                        conferences[presentationfor].\
                                        presentation]
        footnote_to_list = get_values_as_list(self.form, "footnote_to")

        submitname = "writearticlesubmit"
        submitvalue = self._("Submit")
        if presentationfor:
            submitname = "writepresentationsubmit"
            submitvalue = self._("Set as presentation")
            page_heading = self._("Change your presentation")
        else:
            if comment_to_list:
                page_heading = self._("Write comment")
            else:
                page_heading = self._("Write article")

        writeart = self.action_href("writearticle&amp;rcpt=" + str(conf_num),
                                    page_heading)
        
        cont = Container(toplink, TOPLINK_SEPARATOR, self.current_conflink(),
                         TOPLINK_SEPARATOR, thisconf, TOPLINK_SEPARATOR, writeart)

        self.append_std_top(cont)
        del cont
        self.resp.flush()

        submitbutton = Input(type="submit", name=submitname,
                             value=submitvalue)

        F = Form(BASE_URL, name="writearticleform", submit="")
        self.doc.append(F)
        if presentationfor:
            F.append(Input(type="hidden", name="presentationfor",
                           value=presentationfor))
        F.append(self.hidden_key())
        for comment in comment_to_list:
            F.append(Input(type="hidden", name="comment_to", value=comment))
        for footnote in footnote_to_list:
            F.append(Input(type="hidden", name="footnote_to", value=footnote))
        
        F.append(BR())
        F.append(Heading(2, page_heading))
        F.append(BR())

        # Get recipients
        # rcpt_dict is an dictionary index with the rcpt_number and rcpt_type as value
        rcpt_dict = {}
        for rcpt_type in ["rcpt", "cc", "bcc"]:
            rcptparams = get_values_as_list(self.form, rcpt_type)
            if not rcptparams:
                continue
            for rcpt in rcptparams:
                if not self.form.getvalue("searchrcptsubmit") and \
                       comment_to_list and not presentationfor:
                    cs = self.sess.conn.conferences[int(rcpt)]
                    # If it's a comment, and the conference is of type
                    # original, replace with it's supermeeting.
                    # Don't do this if the user explicitly adds the meeting.
                    if cs.type.original:
                        rcpt_dict[int(cs.super_conf)] = rcpt_type
                        continue
                rcpt_dict[int(rcpt)] = rcpt_type
        if presconf and not rcpt_dict.has_key(presconf):
            rcpt_dict[presconf] = "rcpt"
            

        # Remove removed recipients
        removed = get_values_as_list(self.form, "removercpt")
        if removed:
            for rcpt_num in rcpt_dict.keys():
                if str(rcpt_num) in removed:
                    del rcpt_dict[rcpt_num]

        # Create type_list
        type_list = []
        keywords = mir_keywords_dict.keys()
        keywords.sort()
        for mir in keywords:
            keyword = mir_keywords_dict[mir]
            type_list.append( (mir2caption(self, mir), keyword) )

        # Add new recipients
        newones = get_values_as_list(self.form, "selected_conf")
        if newones:
            for rcpt in newones:
                rcpt_dict[int(rcpt)] = "rcpt"

        # If user did a search and the result was not ambiguous, add recipient.
        searchtext = self.form.getvalue("searchtext", None)
        if searchtext:
            matches = self.do_search(searchtext, want_pers=1, want_confs=1)
            if len(matches) == 1:
                rcpt_dict[matches[0][0]] = "rcpt"
        
        # Change recipient types
        # Loop over selectedtypeX etc
        for rcpt_num in rcpt_dict.keys():
            rcpt_type = rcpt_dict[rcpt_num]
            keyname = "selectedtype" + str(rcpt_num)
            if self.form.has_key(keyname):
                new_type = self.form.getvalue(keyname)
                if rcpt_type != new_type:
                    rcpt_dict[rcpt_num] = new_type

        # Construct the recipient table
        tab=[]
        for rcpt_num in rcpt_dict.keys():
            rcpt_type = rcpt_dict[rcpt_num]
            selectobj = Select(type_list, name="selectedtype" + str(rcpt_num),
                               selected=[rcpt_type])
            removeobj = Input(type="checkbox", name="removercpt", value=str(rcpt_num))
            tab.append([selectobj, self.get_conf_name(rcpt_num), removeobj])
            # Append a hidden varible to the document
            F.append(Input(type="hidden", name=rcpt_type, value=rcpt_num))


        # Add recipient table to document
        headings = [self._("Type"), self._("Name"), self._("Remove?")]
        F.append(Table(body=tab, heading=headings, border=3, cell_padding=2, column1_align="right",
                       cell_align="left", width="100%"))
        
        ## Search and remove submit
        cont = Container()
        cont.append(self._("Search for new recipient:"))
        cont.append(self.gen_search_line())
        cont.append(Input(type="submit", name="searchrcptsubmit", value=self._("Search")))
        removesubmit = Input(type="submit", name="removercptsubmit",
                             value=self._("Remove marked recipients"))
        tab = [[cont, removesubmit]]
        F.append(Table(body=tab, border=0, cell_align="right", width="100%"))

        ## Search result
        searchtext = self.form.getvalue("searchtext", None)
        if searchtext and (len(matches) <> 1):
            F.append(self.gen_search_result_table(searchtext, matches, maxhits=10))
            addsubmit = Input(type="submit", name="addrcptsubmit",
                              value=self._("Add marked"))
            tab = [["", addsubmit]]
            F.append(Table(body=tab, border=0, cell_align="right", width="100%"))

        F.append(self._("Subject:"))
        subject = self.form.getvalue("articlesubject")
        if not subject:
            # No subject given, default to commented text, if any
            if comment_to_list:
                subject = self.sess.conn.subjects[int(comment_to_list[0])]
        F.append(Input(name="articlesubject", value=subject, size=60), BR())

        text = self.form.getvalue("text_area", "")
        if presentationfor:
            pres_text = self.sess.conn.conferences[presentationfor].presentation
            if pres_text != 0:
                text = self.get_article_text(pres_text)
        elif self.form.getvalue("quotesubmit") and comment_to_list:
            comment_text = self.get_article_text(int(comment_to_list[0]))
            text = quote_text(comment_text) + "\n\n" + text
                
        F.append(self._("Article text:"), BR())
        F.append(Textarea(rows=20, cols=70, text=text))
        F.append(BR())
        if comment_to_list and not presentationfor:
            quotebutton = Input(type="submit", name="quotesubmit", value=self._("Quote"))
        else:
            quotebutton = ""

        # Append table with quote and submit button
        F.append(Table(body=[[submitbutton, quotebutton]], border=0,
                       column1_align="left", cell_align="right", width="500"))

        self.doc.append(self._("If certain characters are hard to write with your keyword, "
                               "you can copy and paste from the line below:"), BR())
        self.doc.append(webkom_escape(COPYPASTE_CHARACTERS))

        return

class WriteArticleSubmit(Action):
    "Submit the article"
    def response(self):
        # We add to a container instead of document, since we are going to redirect. 
        result_cont = Container()
        
        # Fetch conference name
        conf_num = self.sess.current_conf
        conf_name = self.get_conf_name(conf_num)
        
        toplink = Href(self.base_session_url(), "WebKOM")
        thisconf = self.action_href("goconf&amp;conf=" + str(conf_num), conf_name)
        writeart = self.action_href("writearticle&amp;rcpt=" + str(conf_num),
                                    self._("Write article"))
        
        top_cont = Container(toplink, TOPLINK_SEPARATOR, self.current_conflink(),
                             TOPLINK_SEPARATOR, thisconf, TOPLINK_SEPARATOR, writeart)
        result_cont.append(self.gen_std_top(top_cont))
        result_cont.append(Heading(2, self._("Write article")))
        

        # Get recipients
        # rcpt_dict is an dictionary index with the rcpt_number and rcpt_type as value
        rcpt_dict = {}
        for rcpt_type in ["rcpt", "cc", "bcc"]:
            rcptparams = get_values_as_list(self.form, rcpt_type)
            if rcptparams == None:
                continue
            for rcpt in rcptparams:
                rcpt_dict[int(rcpt)] = rcpt_type

        # Change recipient types
        # Loop over selectedtypeX etc
        for rcpt_num in rcpt_dict.keys():
            rcpt_type = rcpt_dict[rcpt_num]
            keyname = "selectedtype" + str(rcpt_num)
            if self.form.has_key(keyname):
                new_type = self.form.getvalue(keyname)
                if rcpt_type != new_type:
                    rcpt_dict[rcpt_num] = new_type

        # Create MiscInfo
        misc_info = kom.CookedMiscInfo()
        type_names = {"rcpt" : kom.MIR_TO, "cc" : kom.MIR_CC, "bcc" : kom.MIR_BCC}
        
        for rcpt_num in rcpt_dict.keys():
            rcpt_type = rcpt_dict[rcpt_num]
            rec = kom.MIRecipient(type_names[rcpt_type], rcpt_num)
            misc_info.recipient_list.append(rec)

        # Comment/footnote to
        comment_to_list = get_values_as_list(self.form, "comment_to")
        footnote_to_list = get_values_as_list(self.form, "footnote_to")

        for (type, typename, list) in \
            [(kom.MIC_COMMENT, "Comment to", comment_to_list),
             (kom.MIC_FOOTNOTE, "Footnote to", footnote_to_list)]:
            
            for text_num_str in list:
                try:
                    text_num = int(text_num_str)
                    try:
                        ts = self.sess.conn.textstats[text_num]
                        mic = kom.MICommentTo(type, text_num)
                        misc_info.comment_to_list.append(mic)
                    except:
                        result_cont.append(self.gen_error(self._("%s: %d -- text not found") % (typename, text_num)))
                        self.submit_redir(result_cont)
                        return
                except:
                    result_cont.append(self.gen_error(self._("%s: %s -- bad text number") % (typename, text_num_str)))
                    self.submit_redir(result_cont)
                    return

        if not len(misc_info.recipient_list) > 0:
            result_cont.append(self.gen_error(self._("No recipients!")))
            self.submit_redir(result_cont)
            return

        subject = self.form.getvalue("articlesubject", "")
        text = self.form.getvalue("text_area", "")
        # Make sure we have UNIX linebreaks
        text = text.replace("\r\n", "\n")
        text = text.replace("\r", "\n")
        # Reformat text (eg. make it maximum 70 chars
        text = reformat_text(text)
        creating_software = kom.AuxItem(tag=kom.AI_CREATING_SOFTWARE,
                                        data="WebKOM %s" % VERSION)

        text_num = 0
        try:
            text_num = kom.ReqCreateText(self.sess.conn, subject + "\n" + text,
                                         misc_info, [creating_software]).response()
            # Mark as read
            ts = self.sess.conn.textstats[text_num]
            for r in ts.misc_info.recipient_list:
                try:
                    kom.ReqMarkAsRead(self.sess.conn, r.recpt, [r.loc_no]).response()
                except:
                    pass
                if r.recpt in self.sess.conn.member_confs:
                    # Note: update_unread must be called before update_membership, otherwise
                    # update_unread thinks this text is already read...
                    update_unread(self.sess.conn, r.recpt, r.loc_no)
                    update_membership(self.sess.conn, r.recpt, r.loc_no)
                
        except kom.Error:
            result_cont.append(self.gen_error(self._("Unable to create article.")))
            self.submit_redir(result_cont)
            return

        result_cont.append(self._("Article submitted."), BR(2))

        for text_str in comment_to_list+footnote_to_list:
            self.sess.conn.textstats.invalidate(int(text_str))
        
        if comment_to_list:
            actionstr = "viewtext&amp;textnum=" + comment_to_list[0]
            actiondescription = self._("Go back to the article you commented")
        elif footnote_to_list:
            actionstr = "viewtext&amp;textnum="+ footnote_to_list[0]
            actiondescription = self._("Go back to the article you footnoted")
        else:
            actionstr = "goconf_with_unread"
            actiondescription = self._("Next conference with unread")

        self.action_shortcut(" ", actionstr)
        result_cont.append(self.action_href(actionstr, actiondescription))
        self.submit_redir(result_cont)

        return text_num 

class WritePresentationSubmit(Action):
    "Submit a presentation"
    def response(self):
        text_num = WriteArticleSubmit(self.resp).response()
        presentation_for = int(self.form.getvalue("presentationfor"))
        if text_num:
            kom.ReqSetPresentation(self.sess.conn,
                                   presentation_for,
                                   text_num).response()
            self.sess.conn.conferences.invalidate(presentation_for)


class LogoutOtherSessionsActions(Action):
    "Logout other sessions"
    def response(self):
        toplink = Href(self.base_session_url(), "WebKOM")
        loslink = self.action_href("logoutothersessions",
                                   self._("Logout my other sessions"))
        cont = Container(toplink, TOPLINK_SEPARATOR, loslink)
        self.append_std_top(cont)
        try:
            who_list = kom.ReqWhoIsOnDynamic(self.sess.conn,
                                             want_invisible = 1,
                                             active_last = 0).response()
        except:
            self.doc.append(self._("Request failed"))
        killring = []
        for who in who_list:
            if self.sess.conn.get_user() == who.person and self.sess.session_num != who.session:
                killring.append(who.session)
        if 0 == len(killring):
            self.doc.append(Header(3, self._("You do not have any other "
                                             "sessions with this server")))
            return
        self.doc.append(Header(3, self._("Killed the following session(s):")))
        self.doc.append(P())
        for session in killring:
            static = kom.ReqGetStaticSessionInfo(self.sess.conn, session).\
                     response()
            kom.ReqDisconnect(self.sess.conn, session).response()
            self.doc.append(static.username + "@" + static.hostname)
            self.doc.append(BR())
            
    

class WhoIsOnActions(Action):
    "Generate a page with active LysKOM users"
    def response(self):
        toplink = Href(self.base_session_url(), "WebKOM")
        wholink = self.action_href("whoison", self._("Who is logged in"))
        cont = Container(toplink, TOPLINK_SEPARATOR, wholink)
        self.append_std_top(cont)
        self.doc.append(Heading(3, self._("Who is logged in")))

        self.doc.append(self._("Showing all sessions active within the last 30 minutes."), BR())
        self.resp.flush()

        try:
            who_list = kom.ReqWhoIsOnDynamic(self.sess.conn, active_last = 1800).response()
        except:
            self.doc.append(self._("Request failed"))

        headings = [self._("Session"), self._("User<br>From"), self._("Working conference<br>Is doing")]
        tab = []
        
        for who in who_list:
            try:
                static = kom.ReqGetStaticSessionInfo(self.sess.conn, who.session).response()
                name = self.get_pers_name(who.person)
                user_and_host = static.username + "@" + static.hostname
                conf_name = self.get_truncated_conf_name(who.working_conference,
                                                         default=self._("No working conference"))
            except kom.UndefinedSession:
                # The session got deleted not long ago. 
                continue
            
            tab.append([who.session, 
                        name[:37] + "<br>" + user_and_host[:37],
                        conf_name \
                        + "<br>" + who.what_am_i_doing[:37]])

        self.doc.append(Table(heading=headings, cell_padding=2, body=tab, width="100%"))
        self.doc.append(self._("A total of %s active users.") % len(who_list))
        
        return


class JoinConfActions(Action):
    "Generate a page for joinging a conference"
    def response(self):
        if self.form.getvalue("joinconfsubmit", None):
            JoinConfSubmit(self.resp).response()
        else:
            self.search_page()

    def search_page(self):
        self.resp.shortcuts_active = 0
        self.doc.onLoad = "document.joinconfform.searchtext.focus()"
        toplink = Href(self.base_session_url(), "WebKOM")
        joinlink = self.action_href("joinconf", self._("Join conference"))
        self.append_std_top(Container(toplink, TOPLINK_SEPARATOR, joinlink))
        self.resp.flush()

        F = Form(BASE_URL, name="joinconfform", submit="")
        self.doc.append(F)
        F.append(self.hidden_key())

        F.append(BR())
        F.append(Heading(2, self._("Join conference")))
        F.append(BR())

        # Max hits
        if self.form.has_key("maxhits"):
            maxhits = int(self.form.getvalue("maxhits"))
        else:
            maxhits = 20

        # Search text
        searchtext = self.form.getvalue("searchtext")

        ## Search and remove submit
        cont = Container()
        F.append(self.search_help(want_pers=0, want_confs=1), BR(2))
        F.append(self.gen_search_line())
        F.append(Input(type="submit", name="searchconfsubmit", value=self._("Search")), 2*NBSP)
        F.append(Input(type="submit", name="searchconfsall", value=self._("View all conferences")), BR(2))
        F.append(self._("Search result will be limited to "))
        F.append(Input(name="maxhits", size=4, value="%d" % maxhits), self._(" conferences."), BR())

        if self.form.getvalue("searchconfsall"):
            matches = self.sess.conn.lookup_name("", want_pers=0, want_confs=1)
        elif self.form.getvalue("searchconfsubmit"):
            matches = self.do_search(searchtext, want_pers=0, want_confs=1)
        else:
            matches = None

        if matches is not None:
            F.append(self.gen_search_result_table(searchtext, matches, maxhits))
            addsubmit = Input(type="submit", name="joinconfsubmit",
                              value=self._("Join marked conference"))
            tab = [["", addsubmit]]
            F.append(Table(body=tab, border=0, cell_align="right", width="100%"))

        return


class JoinConfSubmit(Action):
    "Handles submits for joining a conference."
    def response(self):
        # We add to a container instead of document, since we are going to redirect. 
        result_cont = Container()

        toplink = Href(self.base_session_url(), "WebKOM")
        joinlink = self.action_href("joinconf", self._("Join conference"))
        top_cont = Container(toplink, TOPLINK_SEPARATOR, joinlink)
        result_cont.append(self.gen_std_top(top_cont))

        if not self.form.getvalue("selected_conf"):
            self.doc.append(self.gen_std_top(top_cont))
            self.print_error(self._("No conference selected."))
            return
        
        conf = int(self.form.getvalue("selected_conf"))
        type = kom.ConfType()
        try:
            # FIXME: User settable priority
            kom.ReqAddMember(self.sess.conn, conf, self.sess.conn.get_user(), 100, 1000, type).response()
        except:
            result_cont.append(self.gen_error(self._("Unable to join conference.")))
            self.submit_redir(result_cont)
            return

        result_cont.append(Heading(3, self._("Ok")))
        result_cont.append(self._("You are now a member of conference "))
        result_cont.append(self.action_href("goconf&amp;conf=" + str(conf), self.get_conf_name(conf)))
        result_cont.append(".")
        self.submit_redir(result_cont)
        
        return

class ViewMarkingsActions(Action):
    "View marked articles"
    # Note: It's possible we want to flush output, since this operation
    # takes long time for the server.
    def response(self):
        markings = self.sess.marked_texts.keys()
        headings = [self._("Number"), self._("Author"),
                     self._("Subject"), self._("Marktype")]
        tab = []

        toplink = Href(self.base_session_url(), "WebKOM")
        
        cont = Container(toplink)
        self.append_std_top(cont)
        cont.append(TOPLINK_SEPARATOR, self.action_href("view_markings",
                    self._("List marked articles")))
        del cont
        self.doc.append(Header(2, self._("List marked articles")))
        self.resp.flush()

        self.doc.append(Table(heading=headings, body=tab,
                              cell_padding=2,
                              column1_align="right",
                              cell_align="left",
                              width="100%"))
        for mark in markings:
            if len(self.sess.marked_texts[mark]) > 1:
                try:
                    [tpe, author, subject] = self.sess.marked_texts[mark]
                except ValueError:
                    continue # Non-existing text
            else:
                try:
                    [tpe] = self.sess.marked_texts[mark]
                except ValueError:
                    continue # Non-existing text
                try:
                    ts = self.sess.conn.textstats[mark]
                except kom.NoSuchText:
                    self.sess.marked_texts[mark] = [] # Mark as non-existing
                    continue
                ai_from = kom.first_aux_items_with_tag(ts.aux_items,
                                                   kom.AI_MX_FROM)
                author = ""
                if ai_from:
                    ai_author =  ai_author = kom.first_aux_items_with_tag(
                        ts.aux_items,
                        kom.AI_MX_AUTHOR)
                    if ai_author:
                        author = ai_author.data + " "
                        author = author + str(Href("mailto:" + ai_from.data,
                                                   ai_from.data))
                else:
                    author = self.get_pers_name(ts.author)
                subject = webkom_escape(self.sess.conn.subjects[mark])

            textnum = self.action_href("viewtext&amp;textnum="+\
                                       str(mark),
                                       str(mark))
            tab.append([textnum, author, subject, tpe])
        return


class TriggerInternalErrorActions(Action):
    def response(self):
        return this_variable_does_not_exist
        

class MarkTextActions(Action):
    "Mark a text"
    def response(self):
        marktype = 100
        textnum = int(self.form.getvalue("textnum"))
        kom.ReqMarkText(self.sess.conn, textnum, marktype).response()
        self.sess.conn.textstats.invalidate(textnum)
        self.sess.marked_texts[textnum] = [100]
        self.resp.set_redir("?sessionkey="+self.resp.key+\
                            "&action=viewtext&textnum="+str(textnum))
        return


class UnmarkTextActions(Action):
    "Unmark a text"
    def response(self):
        textnum = int(self.form.getvalue("textnum"))
        kom.ReqUnmarkText(self.sess.conn, textnum).response()
        self.sess.conn.textstats.invalidate(textnum)
        del self.sess.marked_texts[textnum]
        self.resp.set_redir("?sessionkey="+self.resp.key+\
                            "&action=viewtext&textnum="+str(textnum))
        return


class SetUnreadActions(Action):
    "Generate a page for setting unread"
    def response(self):
        self.resp.shortcuts_active = 0
        self.doc.onLoad = "document.set_unread_form.num_unread.focus()"
        toplink = Href(self.base_session_url(), "WebKOM")
        cont = Container(toplink, TOPLINK_SEPARATOR, self.current_conflink())
        self.append_std_top(cont)

        conf_num = self.sess.current_conf
        conf_name = self.get_conf_name(conf_num)
        cont.append(TOPLINK_SEPARATOR, self.action_href("goconf&amp;conf=" + str(conf_num),
                                            conf_name))

        submitbutton = Input(type="submit", name="set_unread_submit",
                             value=self._("Submit"))
        F = Form(BASE_URL, name="set_unread_form", submit=submitbutton)
        self.doc.append(F)
        F.append(self.hidden_key())

        F.append(Heading(2, self._("Set unread")))

        F.append(self._("Set read marks to "))
        F.append(Input(name="num_unread", size=4, value="20"))
        F.append(self._(" unread articles in this conference."), BR())
        F.append(Input(type="hidden", name="set_unread_submit"))

        return


class SetUnreadSubmit(Action):
    "Handles submits for joining a conference."
    def response(self):
        # We add to a container instead of document, since we are going to redirect. 
        result_cont = Container()
        toplink = Href(self.base_session_url(), "WebKOM")
        top_cont = Container(toplink, TOPLINK_SEPARATOR, self.current_conflink())
        result_cont.append(self.gen_std_top(top_cont))

        conf_num = self.sess.current_conf
        conf_name = self.get_conf_name(conf_num)
        top_cont.append(TOPLINK_SEPARATOR, self.action_href("goconf&amp;conf=" + str(conf_num),
                                                conf_name))
        
        try:
            num_unread = int(self.form.getvalue("num_unread"))
            if num_unread < 0: raise ValueError
        except ValueError:
            result_cont.append(Heading(3, self._("Invalid input")))
            result_cont.append(self._("You must give a positive number as argument."))
            result_cont.append(self.action_href("set_unread",
                                                self._("Try again!")))
            
            self.submit_redir(result_cont)
            return
        try:
            kom.ReqSetUnread(self.sess.conn, conf_num, num_unread).response()
        except:
            result_cont.append(self.gen_error(self._("Unable to set number of unread.")))
            self.submit_redir(result_cont)
            return

        # Invalidate caches
        self.sess.conn.memberships.invalidate(conf_num)
        self.sess.conn.no_unread.invalidate(conf_num)
        
        result_cont.append(Heading(3, self._("Ok")))
        result_cont.append(self._("The number of unread articles is now ") + str(num_unread) + ".")

        self.submit_redir(result_cont)
        return



class LeaveConfActions(Action):
    "Generate a page for leaving conference"
    def response(self):
        self.resp.shortcuts_active = 0
        toplink = Href(self.base_session_url(), "WebKOM")
        cont = Container(toplink, TOPLINK_SEPARATOR, self.current_conflink())
        self.append_std_top(cont)

        conf_num = self.sess.current_conf
        conf_name = self.get_conf_name(conf_num)
        cont.append(TOPLINK_SEPARATOR, self.action_href("goconf&amp;conf=" + str(conf_num),
                                            conf_name))

        # Not allowed to leave letterbox
        if self.sess.conn.conferences[conf_num].type.letterbox:
            self.print_error(self._("You cannot leave a letterbox."))
            return

        submitbutton = Input(type="submit", name="leaveconfsubmit", value=self._("Yes, leave conference"))
        F = Form(BASE_URL, name="set_unread_form", submit=submitbutton)
        self.doc.append(F)
        F.append(self.hidden_key())
        F.append(Heading(2, self._("Leave conference")))
        F.append(self._("Do you really want to leave conference ") + conf_name + "?")
        F.append(BR(2))


class LeaveConfSubmit(Action):
    "Handles submits for leaving a conference."
    def response(self):
        # We add to a container instead of document, since we are going to redirect. 
        result_cont = Container()
        
        toplink = Href(self.base_session_url(), "WebKOM")
        top_cont = Container(toplink, TOPLINK_SEPARATOR, self.current_conflink())
        result_cont.append(self.gen_std_top(top_cont))

        conf_num = self.sess.current_conf
        conf_name = self.get_conf_name(conf_num)
        top_cont.append(TOPLINK_SEPARATOR, conf_name)

        try:
            kom.ReqSubMember(self.sess.conn, conf_num, self.sess.conn.get_user()).response()
        except:
            result_cont.append(self.gen_error(self._("Unable to leave conference.")))
            self.submit_redir(result_cont)
            return

        # Note:
        # We don't need to invalidate any caches, since a async
        # message should do that for us. 
        
        result_cont.append(Heading(3, self._("Ok")))
        result_cont.append(self._("You are no longer a member of conference ") + conf_name + ".")
        self.submit_redir(result_cont)
        
        return

class ViewPresentationActions(Action):
    def response(self):
        self.resp.shortcuts_active = 0 
        self.doc.onLoad = "document.view_pres_form.searchtext.focus()"
        toplink = Href(self.base_session_url(), "WebKOM")
        golink = self.action_href("view_presentation",
                                  self._("View presentation"))
        self.append_std_top(Container(toplink, TOPLINK_SEPARATOR, golink))
        self.resp.flush()
        
        F = Form(BASE_URL, name="view_pres_form", submit="")
        self.doc.append(F)
        F.append(self.hidden_key())

        F.append(BR())
        F.append(Heading(2, self._("View presentation")))
        F.append(BR())

        F.append(self.search_help(want_pers=1, want_confs=1), BR(2))
        F.append(self.gen_search_line())
        F.append(Input(type="hidden", name="view_presentation_search"))
        F.append(Input(type="submit", name="view_presentation_search",
                          value=self._("Search")), BR())
        
        searchtext = self.form.getvalue("searchtext", None)
        if searchtext:
            matches = self.sess.conn.lookup_name(searchtext, want_pers=1,
                                                 want_confs=1)

            self.doc.append(self.gen_search_result_table(searchtext, matches,
                                                         match_handler=self.match_handler))
        return

    def match_handler(self, rcpt_num, rcpt_name):
        presentation = self.get_presentation(rcpt_num)
        if 0 == presentation:
            return [rcpt_name+self._(" has no presentation")]
        else:
            return [self.action_href("viewtext&amp;textnum=" + str(presentation),
                                     rcpt_name)]
    

class ChooseConfActions(Action):
    "Generate a page for choosing active conference"
    def response(self):
        self.resp.shortcuts_active = 0
        self.doc.onLoad = "document.choose_conf_form.searchtext.focus()"
        toplink = Href(self.base_session_url(), "WebKOM")
        golink = self.action_href("choose_conf", self._("Choose working conference"))
        self.append_std_top(Container(toplink, TOPLINK_SEPARATOR, golink))
        self.resp.flush()

        F = Form(BASE_URL, name="choose_conf_form", submit="")
        self.doc.append(F)
        F.append(self.hidden_key())

        F.append(BR())
        F.append(Heading(2, self._("Choose working conference")))
        F.append(BR())
        F.append(self.search_help(want_pers=0, want_confs=1), BR(2))
        F.append(self.gen_search_line())
        F.append(Input(type="submit", name="choose_conf_search", value=self._("Search")), BR())
        
        searchtext = self.form.getvalue("searchtext", None)
        if searchtext:
            matches = self.do_search(searchtext, want_pers=0, want_confs=1)
            # Only show member conferences
            matches = filter(lambda match: match[0] in self.sess.conn.member_confs,
                             matches)
            self.doc.append(self.gen_search_result_table(searchtext, matches,
                                                         match_handler=self.match_handler))
        return

    def match_handler(self, rcpt_num, rcpt_name):
        return [self.action_href("goconf&amp;conf=" + str(rcpt_num),
                                 webkom_escape(rcpt_name))]



class SubmitResultActions(Action):
    "Generate a page with result of submission. All submissions are redirected"
    "to this page, to prevent re-submission via browser reload etc."
    def response(self):
        # Restore saved shortcuts
        self.resp.shortcuts = self.sess.saved_shortcuts
        self.doc.append(self.sess.submit_result)
        return

class ReadConfirmationActions(Action):
    def response(self):
        # Fetch conference name
        conf_num = self.sess.current_conf
        conf_name = self.get_conf_name(conf_num)
        toplink = Href(self.base_session_url(), "WebKOM")
        thisconf = self.action_href("goconf&amp;conf=" + str(conf_num), conf_name)
        cont = Container(toplink, TOPLINK_SEPARATOR, self.current_conflink(),
                         TOPLINK_SEPARATOR, thisconf, TOPLINK_SEPARATOR, "Confirm reading")
        self.append_std_top(cont)

        self.doc.append(Heading(2, "Read confirmation"))

        # FIXME: Maybe handle case when text is removed. 
        global_num = int(self.form["textnum"].value)
        read_confirmation = kom.AuxItem(tag=kom.AI_READ_CONFIRM)
        kom.ReqModifyTextInfo(self.sess.conn, global_num, [], [read_confirmation]).response()
        # Invalidate cache
        self.sess.conn.textstats.invalidate(global_num)
        
        textlink = self.action_href("viewtext&amp;textnum=" + str(global_num), str(global_num))
        self.doc.append(BR(), self._("You have confirmed reading text "),
                        textlink, ".", BR())


class SearchActions(Action):
    def response(self):
        self.resp.shortcuts_active = 0
        self.doc.onLoad = "document.search_form.searchtext.focus()"
        toplink = Href(self.base_session_url(), "WebKOM")
        cont = Container(toplink)
        
        conf_num = int(self.form.getvalue("conf", 0))
        if conf_num:
            searcher = LocalArticleSearcher(self.sess.conn, SEARCH_LIMIT, conf_num)
            conf_name = self.get_conf_name(conf_num)
            title = self._("Search article in %s") % conf_name
            cont.append(TOPLINK_SEPARATOR, self.current_conflink())
            cont.append(TOPLINK_SEPARATOR, self.action_href("goconf&amp;conf=" + str(conf_num),
                                                            conf_name))
            cont.append(TOPLINK_SEPARATOR,
                        self.action_href("search&amp;conf=" + str(conf_num),
                                         self._("Search")))
        else:
            searcher = GlobalArticleSearcher(self.sess.conn, SEARCH_LIMIT)
            title = self._("Search article in all conferences")
            cont.append(self.action_href("search", title))
            self.sess.current_conf = 0
        self.append_std_top(cont)

        F = Form(BASE_URL, name="search_form", submit="")
        self.doc.append(F)
        F.append(self.hidden_key())
        F.append(BR())
        F.append(Heading(2, title))

        helpstring = self._("Search for articles and subjects containing some text.")
        helpstring += " " + self._("The search is not case sensitive.")
        helpstring += " " + self._("Regular expressions are allowed.")
        if SEARCH_LIMIT is not None:
            helpstring += " " + self._("The search is limited to the last %d articles.") % SEARCH_LIMIT
        F.append(helpstring, BR(2))
        
        F.append(Input(name="searchtext", value=self.form.getvalue("searchtext")))
        F.append(Input(type="hidden", name="conf", value=str(conf_num)))
        F.append(Input(type="hidden", name="search_submit"))
        F.append(Input(type="submit", name="search_submit",
                       value=self._("Search")), BR())
        self.resp.flush()

        searchtext = self.form.getvalue("searchtext", None)
        if searchtext:
            matches = searcher.search(searchtext)
            self.doc.append(self._("Search result:"), BR())
            self.print_matches(matches)

        
    def print_matches(self, matches):
        # FIXME: The code below is duplicated in many places. Make common method. 
        headings = [self._("Subject"), self._("Author"),
                    self._("Date"), self._("Number")]
        tab = []
        self.doc.append(Table(heading=headings, body=tab,
                              cell_padding=2,
                              cell_align="left",
                              width="100%"))
        for match in matches:
            try:
                ts = self.sess.conn.textstats[match]
            except kom.NoSuchText:
                # Perhaps the text just disappeared or access was revoked.
                continue
            # FIXME: Support AI_MX_FROM
            author = self.get_pers_name(ts.author)
            # FIXME: Might want to print a NBSP if subject is empty. 
            subject = self.action_href("viewtext&amp;textnum="+\
                                       str(match),
                                       webkom_escape(self.sess.conn.subjects[match]))
            textnum = self.action_href("viewtext&amp;textnum="+\
                                       str(match),
                                       str(match))
            tab.append([subject, author,
                        ts.creation_time.to_date_and_time(),
                        textnum])
    

def actions(resp):
    "Do requested actions based on CGI keywords"
    if resp.form.has_key("loginsubmit"):
        LogInActions(resp).response()
        return

    if resp.form.has_key("sessionkey"):
        resp.key = resp.form["sessionkey"].value
    elif resp.form.getvalue("action") == "about":
        AboutPageActions(resp).response()
        return
    elif resp.form.getvalue("action") == "select_server":
        SelectServerPageActions(resp).response()
        return
    elif resp.form.getvalue("action") == "create_user":
        CreateUserActions(resp).response()
        return
    elif resp.form.has_key("create_user_submit"):
        CreateUserSubmit(resp).response()
        return
    elif resp.form.has_key("redirect"):
        RedirectToExternalURL(resp).response()
        return
    else:
        LoginPageActions(resp).response()
        return

    # "loginsubmit" and "about" excluded
    submit_keywords = {"changepwsubmit" : ChangePwSubmit,
                       "removercptsubmit" : WriteArticleActions,
                       "addrcptsubmit" : WriteArticleActions,
                       "searchrcptsubmit" : WriteArticleActions,
                       "quotesubmit" : WriteArticleActions,
                       "writearticlesubmit" : WriteArticleSubmit,
                       "writepresentationsubmit" : WritePresentationSubmit,
                       "joinconfsubmit" : JoinConfSubmit,
                       "searchconfsubmit" : JoinConfActions,
                       "searchconfsall" : JoinConfActions,
                       "leaveconfsubmit" : LeaveConfSubmit,
                       "set_unread_submit" : SetUnreadSubmit,
                       "choose_conf_search" : ChooseConfActions,
                       "search_submit" : SearchActions,
                       "viewtext" : ViewTextActions,
                       "view_presentation_search" : ViewPresentationActions }
    
    action_keywords = {"logout" : LogOutActions,
                       "viewconfs" : ViewConfsActions,
                       "viewconfs_unread" : ViewConfsUnreadActions,
                       "goconf" : GoConfActions,
                       "goconf_with_unread" : GoConfWithUnreadActions,
                       "choose_conf" : ChooseConfActions, 
                       "viewtext" : ViewTextActions,
                       "changepw" : ChangePwActions,
                       "whoison" : WhoIsOnActions,
                       "writearticle" : WriteArticleActions,
                       "writeletter" : WriteLetterActions,
                       "writepresentation" : WritePresentationActions,
                       "whats_implemented" : WhatsImplementedActions,
                       "joinconf" : JoinConfActions,
                       "joinconfsubmit" : JoinConfSubmit,
                       "leaveconf" : LeaveConfActions,
                       "about" : AboutPageActions,
                       "set_unread" : SetUnreadActions,
                       "logoutothersessions" : LogoutOtherSessionsActions,
                       "submit_result" : SubmitResultActions,
                       "login_progress" : LoginProgressPageActions,
                       "read_confirmation" : ReadConfirmationActions,
                       "view_presentation" : ViewPresentationActions,
                       "unmark_text" : UnmarkTextActions,
                       "mark_text" : MarkTextActions,
                       "view_markings" : ViewMarkingsActions,
                       "search" : SearchActions,
                       "specify_article_number" : SpecifyArticleNumberPageActions,
                       "internal_error": TriggerInternalErrorActions}


    if not sessionset.valid_session(resp.key):
        InvalidSessionPageActions(resp).response()
        return 

    # Submits
    submits = []
    for keyword in resp.form.keys():
        if keyword in submit_keywords.keys():
            submits.append(keyword)

    actions = get_values_as_list(resp.form, "action")

    # It's OK with two submits at the same time: Forms with textfields
    # may be submitted via ENTER and in that case a hidden variable
    # "<something>submit" is submitted. In addition, the form may have other
    # submits. Look at how JoinConfSubmit handles this, for example. 
    
    # Never two actions at the same time!
    assert (not (len(actions) > 1))
    # Never submits and actions at the same time!
    assert (not (len(submits) and (len(actions))))

    if submits:
        response_type = submit_keywords[submits[0]]
    elif actions:
        response_type = action_keywords[actions[0]]
    else:
        response_type = MainPageActions

    # This is the one place where we fetch and lock the session
    resp.sess = sessionset.get_session(resp.key)
    if not resp.sess:
        raise RuntimeError("Your session mysteriously disappeared!")
    resp.sess.lock_sess()

    # Set page title
    resp.doc.title = "WebKOM: " \
                     + webkom_escape(resp.sess.conn.conf_name(resp.sess.conn.get_user())[:MAX_CONFERENCE_LEN])

    # Tell the server the user is active
    resp.sess.user_is_active()

    # Parse all responses that have arrived from LysKOM server. This is important
    # for ViewPendingMessages, auto-logout etc. 
    resp.sess.conn.parse_present_data()

    # View messages
    ViewPendingMessages(resp).response()

    # Create an instance of apropriate class and let it generate response
    action = response_type(resp)

    # Generate page. Note: if this is the logout page, resp.sess will be cleared.
    action.response()

    action.append_right_footer()

    # Add Javascript shortcuts
    if resp.shortcuts_active and resp.sess:
        # Add global shortcuts
        resp.add_shortcut("v", action.base_session_url() + "&amp;action=whoison")
        resp.add_shortcut("b", action.base_session_url() + "&amp;action=writeletter&amp;rcpt=" 
                          + str(resp.sess.conn.get_user()))
        resp.add_shortcut("g", action.base_session_url() + "&amp;action=choose_conf")
        AddShortCuts(resp, action.base_session_url()).add()


    # For debugging 
    #resp.doc.append(str(resp.env))
        
    return


def write_traceback(resp):
    # Something failed in response generation.
    # Note: You can trigger an internal server error by specifying
    # ?action=internal_error. Useful for testing. 
    _ = resp.get_translator()
    
    # Save a copy on disk
    timetext = time.strftime("%y%m%d-%H%M", time.localtime(time.time()))                
    f = open(os.path.join(LOG_DIR, "traceback-" + timetext), "w")
    traceback.print_exc(file = f)
    f.close()
    
    # Put it on the web.
    resp.doc.append(Heading(3, "Internal server error"))
    resp.doc.append(_("Check if this bug is listed on"))
    resp.doc.append(external_href(KNOWN_BUGS_URL,
                                  _("the list with known bugs.")))
    resp.doc.append(_("If not, please"))
    resp.doc.append(external_href(BUGREPORT_URL, _("submit a bug report.")))
    resp.doc.append(_("Attach the error message below."))
    resp.doc.append(_("The server time was: ") + \
                    time.strftime("%Y%m%d-%H:%M", time.localtime(time.time())))
    f = open(os.path.join(LOG_DIR, "traceback-" + timetext), "r")
    resp.doc.append(Pre(str(f.read())))
    f.close()


def print_logged_out_response(resp):
    _ = resp.get_translator()
    cont = Container(Href(BASE_URL, "WebKOM"))
    cont.append(Heading(3, _("Logged out remotely")))
    cont.append(_("Someone (probably you) ended this session remotely"))
    cont.append(P())
    cont.append(Href(BASE_URL, _("Login again")))
    resp.doc.append(cont)
    resp.sess = None


def print_not_implemented(resp):
    _ = resp.get_translator()
    cont = Container(Href(BASE_URL, "WebKOM"))
    cont.append(Heading(3, _("Server call not implemented")))
    cont.append(_("WebKOM made a server call that this server did not "
                  "understand. WebKOM only works with LysKOM servers with "
                  "version 2.1.0 or higher"))
    cont.append(P())
    cont.append(Href(BASE_URL, _("Login again")))
    resp.doc.append(cont)
    resp.sess = None

def print_receive_error(resp):
    _ = resp.get_translator()
    cont = Container(Href(BASE_URL, "WebKOM"))
    cont.append(Heading(3, _("Server communications error")))
    cont.append(_("An error occured while communicating with the LysKOM server. "
                  "This could be a network problem or a LysKOM server failure."))
    cont.append(P())
    cont.append(Href(BASE_URL, _("Login again")))
    resp.doc.append(cont)
    resp.sess = None

# Main action routine. This function is critical and must obey these rules:
# req.finish() should always be executed. If it failes, a manual thread.exit()
# should be done.
# unlock_sess() should always be executed, even after tracebacks. 
def handle_req(req, env, form):
    try: # Exceptions within this clause are critical and not sent to browser.
        resp = Response(req, env, form)
        try:
            actions(resp)
        except RemotelyLoggedOutException:
            print_logged_out_response(resp)
        except kom.ReceiveError:
            print_receive_error(resp)
        except kom.NotImplemented:
            print_not_implemented(resp)
        except:
            write_traceback(resp)

        # Unlock session (it was probably locked in "actions")
        if resp.sess:
            resp.sess.unlock_sess()

        # Print HTTP header and start of document, if not already done. 
        resp.write_docstart()
        
        req.out.write(resp.doc.get_doc_contents())
        req.out.write(resp.doc.get_doc_end())

    # Something went wrong when creating Response instance or
    # printing response doc. 
    except:
        f = open(os.path.join(LOG_DIR, "traceback.req"), "w")
        traceback.print_exc(file = f)
        f.close()

    # Finish thread and send all data back to the FCGI parent
    try:
        req.finish()
    except SystemExit:
        pass
    except:
        f = open(os.path.join(LOG_DIR, "traceback.finish"), "w")
        traceback.print_exc(file = f)
        f.close()


class Logger:
    "Write log messages to files, with current time prefixed"
    def __init__(self, filename):
        # 0 means unbuffered.
        self.log = open(filename, "a", 0)

    def __getattr__(self, attrname):
        return getattr(self.log, attrname)

    def write(self, msg):
        self.log.write(time.strftime("%Y-%m-%d %H:%M ", time.localtime(time.time())))
        self.log.write(str(msg))
        if not msg[-1] == "\n":
            self.log.write("\n")

    def level_write(self, level, msg):
        if level <= LOGLEVEL:
            self.write(msg)

        
# Console
def run_console():
    system_log.level_write(2, "Console thread started")
    system_log.level_write(4, "Console using socket " + CONSOLE_SOCKET)
    import consoleserver
    try:
        consoleserver.main_thread(globals(), CONSOLE_SOCKET)
    except SystemExit:
        system_log.level_write(2, "Console thread exited")
    except:
        f = open(os.path.join(LOG_DIR, "traceback.console"), "w")
        traceback.print_exc(file = f)
        f.close()

def run_maintenance():
    system_log.level_write(2, "Maintenance thread started")
    while 1:
        time.sleep(60)
        sessionset.del_inactive()

def run_fcgi():
    try:
        fcgi.run()
    except:
        f = open(os.path.join(LOG_DIR, "traceback.main"), "w")
        traceback.print_exc(file = f)
        f.close()

#
# MAIN
#
if __name__=="__main__":
    # Global log file
    system_log = Logger(os.path.join(LOG_DIR, "system.log"))
    system_log.level_write(1, "WebKOM started, LOGLEVEL=%d" % LOGLEVEL)

    # Take care of output to stdout and stderr. 
    # Note: stdout and stderr should not normally be used for any output. 
    sys.stdout = system_log
    sys.stderr = system_log

    # Start console thread
    thread.start_new_thread(run_console,())
    # Start maintenance thread
    thread.start_new_thread(run_maintenance,())

    # Get a list of installed languages
    installed_langs = get_installed_languages()
    # Make sure DEFAULT_LANG is first in list, if it's available
    try:
        installed_langs.pop(installed_langs.index(DEFAULT_LANG))
        installed_langs.insert(0, DEFAULT_LANG)
    except ValueError:
        pass
    
    # Create instance of translator
    translator_cache = TranslatorCache.TranslatorCache("webkom", LOCALE_DIR)

    # Save time of server start.
    serverstarttime = time.time()

    FinalizerChecker(system_log)

    # Create an instance of THFCGI...
    fcgi = thfcgi.THFCGI(handle_req)

    # ...and let it run
    run_fcgi()

