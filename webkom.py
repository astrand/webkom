#!/usr/bin/env python

# WebKOM - a web based LysKOM client
# 
# Copyright (C) 2000 by Peter Åstrand
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
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
sys.path.append(MODULES_PATH)

import os, sys, string, socket, errno
from cStringIO import StringIO
import cgi
import sz_fcgi
import kom
from HTMLgen import *
from HTMLcolors import *
import HTMLutil
import Formtools
import random, time
import thread
from webkom_utils import *
import webkom_js
import TranslatorCache
import traceback

class SessionSet:
    "A set of active sessions"
    def __init__(self):
        self.sessionset = {}
        # Lock variable, for updating "sessions"
        self.sessionset_lock = thread.allocate_lock()
        # Global session log
        self.log = open(LOG_DIR + "session.log", "a")

    def valid_session(self, key):
        "Check if a given key is a valid, active sessionkey"
        self.sessionset_lock.acquire()
        had_key = self.sessionset.has_key(key)
        self.sessionset_lock.release()
        return had_key

    def get_session(self, key):
        "Fetch session, based on sessionkey"
        # This method assumes that the session is locked
        session = self.sessionset[key]
        session.timestamp = time.time()
        return session

    def new_session(self, key, session):
        "Create new session"
        self.sessionset_lock.acquire()
        if self.sessionset.has_key(key):
            # Something is totally wrong
            self.sessionset_lock.release()
            assert(0)
        self.sessionset[key] = session
        self.write_log("Creating session", key)
        self.sessionset_lock.release()

    def del_session(self, key):
        "Delete session from sessionset"
        self.sessionset_lock.acquire()
        self.write_log("Deleting session", key)
        del self.sessionset[key]
        self.sessionset_lock.release()

    def del_inactive(self):
        "Delete and logout inactive sessions"
        self.sessionset_lock.acquire()
        curtime = time.time()
        for key in self.sessionset.keys():
            if self.sessionset[key].timestamp + SESSION_TIMEOUT < curtime:
                try:
                    kom.ReqLogout(self.sessionset[key].conn).response()
                except:
                    pass
                self.write_log("Deleting inactive session", key)
                del self.sessionset[key]

        self.sessionset_lock.release()

    def write_log(self, msg, key):
        self.log.write(time.strftime("%Y-%m-%d %H:%M ", time.localtime(time.time())))
        self.log.write(msg +", key=" + str(key) + " pers_num=" +
                       str(self.sessionset[key].conn.get_user()) + "\n")
        self.log.flush()
             

# Global variables
sessionset = SessionSet()

# Used for debugging purposes in interactive terminal
def first_conn():
    return sessionset.sessionset.items()[0][1].conn

# Messages
class Message:
    def __init__(self, recipient, sender, message):
        self.recipient = recipient
        self.sender = sender
        self.message = message
        self.time = time.time()

class Session:
    "A session class. Lives as long as the session (and connection)"
    def __init__(self, conn):
        self.conn = conn
        self.current_conf = 0
        self.comment_tree = []
        self.timestamp = time.time()
        self.lock = thread.allocate_lock()
        # Lock initially
        self.lock.acquire()
        # Holds pending messages
        self.pending_messages = []
        
    def lock_sess(self):
        "Lock session"
        self.lock.acquire()
        
    def unlock_sess(self):
        "Unlock session"
        if self.lock.locked():
            self.lock.release()

    def async_message(self, msg, c):
        self.pending_messages.append(Message(msg.recipient, msg.sender, msg.message))



class Response:
    "A response class. Used during the construction of a response."
    def __init__(self, env, form):
        self.doc = SimpleDocument(title="WebKOM", bgcolor=HTMLcolors.WHITE, vlinkcolor=HTMLcolors.BLUE)
        self.env = env
        self.form = form
        self.key = ""
        self.sess = None
        self.shortcuts = []
        self.shortcuts_active = 1

        # Default HTTP header. 
        self.http_header = "Content-type: text/html\r\n" \
                           "Cache-Control: no-cache\r\n" \
                           "Pragma: no-cache\r\n" \
                           "Expires: 0\r\n" \
                           "\r\n"

    def redir(self, url_text):
        server_name = self.env["HTTP_HOST"]
        if not server_name:
            server_name = self.env["SERVER_NAME"]
        if self.env.has_key("HTTPS"):
            server_name = "https://" + server_name
        else:
            server_name = "http://" + server_name
        script_name = self.env["SCRIPT_NAME"]
        self.http_header = "Location: " + server_name + script_name + url_text + "\n\n"
        
    def add_shortcut(self, key, url):
        self.shortcuts.append((key, url))


class Action:
    "Abstract class for actions. Action- and Submit-methods inherits this class."
    def __init__(self, resp, translator):
        self.resp = resp
        # Shortcuts
        self.doc = resp.doc
        self.form = resp.form
        self.key = resp.key
        self.sess = resp.sess
        # Language
        self._ = translator
        
    def print_error(self, msg):
        "Print error message"
        self.doc.append(Bold(self._("Error: ") + msg), BR())

    #
    # Small and frequently-used KOM utility methods. The rest in webkom_utils.py
    def change_conf(self, conf_num):
        "Change current LysKOM conference"
        self.sess.current_conf = conf_num
        # Tell KOM-server that we have changed conference
        try:
            kom.ReqChangeConference(self.sess.conn, conf_num)
        except:
            self.print_error(self._("Unable to change current conference."))
    
    def get_conf_name(self, num):
        "Get conference name"
        # FIXME: Do linebreaks instead of truncating
        return self.sess.conn.conf_name(num, default=self._("Conference %d (does not exist)"))[:MAX_CONFERENCE_LEN]

    def get_pers_name(self, num):
        "Get persons name"
        # FIXME: Do linebreaks instead of truncating
        return self.sess.conn.conf_name(num, default=self._("Person %d (does not exist)"))[:MAX_CONFERENCE_LEN]

    def get_presentation(self, num):
        "Get presentation of a conference"
        try:
            return self.sess.conn.conferences[num].presentation
        except:
            return None
    # End of KOM utility methods.
    #
            
    def base_session_url(self):
        "Return base url with sessionkey appended"
        return BASE_URL + "?sessionkey=" + self.key

    def action_href(self, actionstr, text, active_link=1):
        "Return an Href object with base url, sessionkey and more"
        if active_link:
            return Href(self.base_session_url() + "&action=" + actionstr, text)
        else:
            return Font(text, color=INACTIVE_LINK_COLOR)

    def add_stdaction(self, container, resp, action, caption):
        "Add a link to a standard action and also the keyboard shortcut space"
        # Add link to page
        container.append(self.action_href(action, caption))
        # Add keyboard shortcut
        std_url = self.base_session_url() + "&action=" + action
        resp.add_shortcut(" ", std_url)

    def unread_info(self, current_conf=0):
        "Return a string with information about number of unread"
        total = get_total_num_unread(self.sess.conn, self.sess.conn.get_user(),
                                     self.sess.conn.member_confs)
        retval = NBSP*4 + self._("Unread: ")
        if current_conf:
            unread = self.sess.conn.no_unread[current_conf] 
            retval = retval + str(unread) + "/" + str(total)
        else:
            retval = retval + str(total) 
        return retval + str(BR())

    # Only used on pages with forms
    def hidden_key(self):
        "Return a hidden key, to be used in a form"
        return Input(type="hidden", name="sessionkey", value=self.key)
    
    def append_std_top(self, leftobj):
        "Append a standard top header to the document, including about-link"
        if self.key:
            aboutlink = self.action_href("about", self._("About WebKOM"))
        else:
            aboutlink = Href(BASE_URL + "?action=about", self._("About WebKOM"))
        tab=[[leftobj, aboutlink]]
        self.doc.append(Table(body=tab, border=0, cell_padding=0,
                              column1_align="left", cell_align="right", width="100%"))


class ViewPendingMessages(Action):
    "View pending messages"
    def print_heading(self, msg):
        if msg.recipient == 0:
            text = self._("Alarm message")
        elif msg.recipient == self.sess.conn.get_user():
            text = self._("Personal message")
        else:
            recipient_name = self.get_conf_name(msg.recipient)
            text = self._("Group message to ") + recipient_name

        self.doc.append(Heading(2, text))
    
    def response(self):
        # Use ReqQueryAsync as a dummy-op for reading the socket.
        # FIXME: Better way to do this?
        kom.ReqQueryAsync(self.sess.conn).response()
        was_pending = (self.sess.pending_messages and 1)

        while self.sess.pending_messages:
            msg = self.sess.pending_messages.pop(0)
            self.print_heading(msg)
            sender_name = self.get_pers_name(msg.sender)
            self.doc.append(Bold(self._("From: ") + sender_name), BR())
            self.doc.append(Bold(self._("Time: ") +
                                 time.strftime("%Y-%m-%d %H:%M", time.localtime(msg.time))))
            
            self.doc.append(BR(), msg.message)

        if was_pending:
            self.doc.append("<hr noshade size=2>")
        return


class AddShortCuts(Action):
    def shortcut_case(self, key, location):
        ret = """    case '%s':
            window.location="%s";
            break;
""" % (key, location)
        return ret
    
    def response(self):
        # Begin Javascript
        ret = webkom_js.code_begin
        # Determine browser type
        ret = ret + webkom_js.browser_type
        # Shortcut functions
        ret = ret + webkom_js.shortcut_functions
        # Begin case
        ret = ret + webkom_js.begin_switch
        # Add case for disabling shortcuts
        ret = ret + webkom_js.disable_shortcuts
        
        # Example:
        #ret = ret + self.shortcut_case("q", "http://www.abc.se")
        
        for s in self.resp.shortcuts:
            ret = ret + self.shortcut_case(s[0], s[1])

        ret = ret + webkom_js.end_switch + webkom_js.code_end
        self.doc.append(ret)
    
    
class LoginPageActions(Action):
    "Generate the login page"
    def response(self):
        self.resp.shortcuts_active = 0
        toplink = Href(BASE_URL, "WebKOM")
        cont = Container(toplink, " : " + self._("Login"))
        self.append_std_top(cont)
        default_kom_server = DEFAULT_KOM_SERVER
        if self.form.has_key("komserver"):
            default_kom_server = self.form["komserver"].value
        submitbutton = Input(type="submit", name="loginsubmit", value=self._("Login"))

        # Ugly focus-hack to work around broken Netscape
        # Non-JS capable browsers should ignore this
        self.doc.script = webkom_js.code_begin + webkom_js.focus_username + webkom_js.code_end
        self.doc.onLoad = "setTimeout('focus_username()', 10)"
        
        cont = Container()
        self.doc.append(Center(cont))
        cont.append(BR(2))
        cont.append(Center(Heading(2, self._("WebKOM login"))))
        cont.append(BR(2))

        #
        # Code for Javascript-version
        #
        js_cont = Container()
        F_komserver = Form(BASE_URL, name="komserver_form", submit="")
        F_username = Form(BASE_URL, name="username_form", submit="")
        F_password = Form(BASE_URL, name="password_form", submit="")
        F_submit = Form(BASE_URL, name="submit_form", submit="")
        F_submit.append(submitbutton)

        formtable = [(self._("Server"), F_komserver),
                     (self._("Username"), F_username),
                     (self._("Password"), F_password) ]
        js_cont.append(Formtools.InputTable(formtable))
        js_cont.append(F_submit)
        
        # komserver_form
        F_komserver.append(Input(name="komserver", size=20, value=default_kom_server, onChange="onchange_komserver(this)"))
        F_komserver.append(Input(name="username", type="hidden"))
        F_komserver.append(Input(name="password", type="hidden"))
        F_komserver.append(Input(name="loginsubmit", type="hidden"))

        # username_form
        F_username.append(Input(name="komserver", type="hidden", value=default_kom_server))
        F_username.append(Input(name="username", size=20, onChange="onchange_username(this)"))
        F_username.append(Input(name="password", type="hidden"))
        F_username.append(Input(name="loginsubmit", type="hidden"))

        # password_form
        F_password.append(Input(name="komserver", type="hidden", value=default_kom_server))
        F_password.append(Input(name="username", type="hidden"))
        F_password.append(Input(type="password", name="password", size=20, onChange="onchange_password(this)"))
        F_password.append(Input(name="loginsubmit", type="hidden"))

        # submit_form
        F_submit.append(Input(name="komserver", type="hidden", value=default_kom_server))
        F_submit.append(Input(name="username", type="hidden"))
        F_submit.append(Input(name="password", type="hidden"))

        # Translate abstract container into document.write statements
        # FIXME: Use string.join instead. 
        form_code = ""
        for line in string.split(str(Center(js_cont)), '\n'):
            form_code = form_code + "document.write('" + line + "');\n"
            
        #
        # Non-JS version
        #
        nonjs_cont = Container()
        F = Form(BASE_URL, name="loginform", submit="")

        nonjs_cont.append(F)
        logintable = [(self._("Server"), Input(name="komserver", size=20, value=default_kom_server)),
                      (self._("Username"), Input(name="username",size=20)),
                      (self._("Password"), Input(type="password",name="password",size=20)) ]

        F.append(Center(Formtools.InputTable(logintable)))
        F.append(Center(submitbutton))

        
        # Write out Javascript version
        self.doc.append(webkom_js.code_begin)
        self.doc.append(form_code)
        self.doc.append(webkom_js.onchange_komserver)
        self.doc.append(webkom_js.onchange_username)
        self.doc.append(webkom_js.onchange_password)
        self.doc.append(webkom_js.code_end)

        # Write non-JS version
        self.doc.append(webkom_js.noscript_begin)
        self.doc.append(nonjs_cont)
        self.doc.append(webkom_js.noscript_end)

        self.doc.append(Href(BASE_URL + "?action=create_user", self._("Create new user") + "..."))

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
            
        cont = Container(toplink, " : ", aboutlink)
        self.append_std_top(cont)
        
        self.doc.append(Heading(2, self._("About WebKOM")))
        last_changed = time.strftime("%Y-%m-%d-%H:%M", time.localtime(os.stat(BASE_URL)[9]))
        self.doc.append(self._("Version running: ") + VERSION + self._(" (last modified ") + last_changed + ")")

        self.doc.append(Heading(3, self._("Overview")))
        self.doc.append(self._("WebKOM is a WWW-interface for "))
        self.doc.append(external_href("http://www.lysator.liu.se/lyskom", "LysKOM"), ".")
        self.doc.append(self._("The goal is a simple, easy-to-use client."))
        
        self.doc.append(Heading(3, self._("License")))
        self.doc.append(self._("WebKOM is free software, licensed under GPL."))
        
        self.doc.append(Heading(3, self._("Authors")))
        self.doc.append(self._("The following people have in one way or another "
                               "contributed to WebKOM:"), BR(2))
        self.doc.append(external_href("http://www.lysator.liu.se/~astrand/",
                                      self._("Peter Åstrand (project starter)")), BR())
        self.doc.append("Kent Engström", BR())
        self.doc.append("Per Cederqvist", BR())
        self.doc.append(external_href("http://www.lysator.liu.se/~forsberg/",
                                      "Erik Forsberg"), BR())
        self.doc.append("Kjell Enblom", BR())
        self.doc.append("Niklas Lindgren", BR())

        self.doc.append(Heading(3, self._("Technology")))
        self.doc.append(self._("WebKOM is written in Python and is a persistent, threaded "))
        self.doc.append(external_href("http://www.fastcgi.com", "FastCGI"), self._(" application."))
        self.doc.append(self._("The HTML code is generated by "))
        self.doc.append(external_href("http://starship.python.net/crew/friedrich/HTMLgen/html/main.html",
                                      "HTMLgen"), ".")

        self.doc.append(Heading(3, self._("Translations")))
        self.doc.append(self._("Translations are provided by the GNU gettext library."))
        self.doc.append(self._("The following translations are installed on this system:"), BR())
        self.doc.append(get_installed_languages())
        
        self.doc.append(Heading(3, self._("Bugs")))
        self.doc.append(self._("There is a "),
                        external_href("http://webkom.lysator.liu.se/bugs.html",
                                      self._("list with known bugs")), ".")

class WhatsImplementedActions(Action):
    "Generate a page with implementation details"
    def response(self):
        toplink = Href(self.base_session_url(), "WebKOM")
        wilink = self.action_href("whats_implemented", self._("What can WebKOM do?"))
        cont = Container(toplink, " : ", wilink)
        self.append_std_top(cont)
        
        self.doc.append(Heading(2, self._("What can WebKOM do?")))
        page = """
        <h3>Implemented</h3>
        <ul>
        <li>Check who is logged in</li>
        <li>List unread articles</li>
        <li>Write articles</li>
        <li>Write comments</li>
        <li>Write personal letters</li>
        <li>Read conference presentation</li>
        <li>Change password</li>
        <li>Read comments in depth-first order</li>
        <li>Join conference</li>
        <li>Set unread</li>
        <li>Leave conference</li>
        </ul>

        <h3>May be implemented in a near future</h3>
        <ul>
        <li>Read article by specifying global article number</li>
        <li>Write footnotes</li>
        <li>Mark/unmark articles</li>
        <li>Read marked articles</li>
        </ul>

        <h3>Things that probably won't be implemented soon</h3>
        <ul>
        <li>Send messages</li>
        <li>Set/remove notes on letterbox</li>
        <li>Prioritize conferences</li>
        <li>Create conferences</li>
        <li>Jump</li>
        <li>View sessionstatus for persons</li>
        <li>Change name</li>
        <li>Delete articles</li>
        <li>Status for conference/persons</li>
        <li>Add recipients and comments to existing articles</li>
        <li>Move articles between conferences</li>
        <li>FAQ handling</li>
        <li>Prevent comments</li>
        <li>Request personal answer</li>
        <li>Request read confirmation</li>
        <li>Cross references</li>
        </ul>

        """
        self.doc.append(page)


class MainPageActions(Action):
    "Generate the mainpage"
    def response(self):
        toplink = Href(self.base_session_url(), "WebKOM")
        cont = Container(toplink)
        cont.append(" : ")
        self.append_std_top(cont)

        cont = Container()
        cont.append(Heading(2, self._("Main Page")))
        cont.append(Heading(3, self.action_href("viewconfs", self._("List conferences"))))
        cont.append(Heading(3, self.action_href("viewconfs_unread", self._("List conferences with unread"))))
        cont.append(Heading(3, self.action_href("writeletter&rcpt=" + str(self.sess.conn.get_user()),
                                                self._("Write letter"))))
        cont.append(Heading(3, self.action_href("joinconf", self._("Join conference"))))
        cont.append(Heading(3, self.action_href("choose_conf", self._("Go to conference"))))
        cont.append(Heading(3, self.action_href("whoison", self._("Who is logged in"))))
        cont.append(Heading(3, self.action_href("changepw", self._("Change password"))))
        cont.append(Heading(3, self.action_href("writepresentation" + "&presentationfor="
                                                + str(self.sess.conn.get_user()), self._("Write presentation"))))
        cont.append(Heading(3, self.action_href("logout", self._("Logout"))))
        cont.append(BR(), Heading(3, self.action_href("whats_implemented",
                                                      self._("What can WebKOM do?"))))

        tab=[[cont]]
        self.doc.append(Table(body=tab, border=0, cell_padding=50, width="100%"))

        return


class LogOutActions(Action):
    "Do logout actions"
    def response(self):
        try:
            kom.ReqLogout(self.sess.conn).response()
        except:
            pass
        
        sessionset.del_session(self.key)
        self.resp.sess.conn.socket.close()
        self.resp.sess = None

        self.resp.shortcuts_active = 0

        # Redirect to loginpage
        self.resp.redir("")
        return 


class LogInActions(Action):
    "Do login actions"
    def error_message(self, errmsg):
        toplink = Href(BASE_URL, "WebKOM")
        self.doc.append(Container(toplink, " : " + self._("Login")))
        self.doc.append(Heading(2, self._("Login failed")))
        self.doc.append(errmsg)

    def gen_table(self, matches):
        # Ambiguity
        # Create top
        toplink = Href(BASE_URL, "WebKOM")
        self.doc.append(Container(toplink, " : " + self._("Login")))
        self.doc.append(Heading(2, self._("The username is ambigious")))

        F = Form(BASE_URL, name="loginform", submit="")
        F.append(Input(type="hidden", name="komserver", value=self.komserver))
        F.append(Input(type="hidden", name="password", value=self.password))

        F.append(self._("Choose user:"), BR())
        tab=[]
        infotext = None
        if len(matches) > 15:
            infotext = self._("(To many hits, the table is truncated)")
        
        for (pers_num, pers_name) in matches[:15]:
            tab.append([pers_name,
                        Input(type="radio", name="login_persno", value=str(pers_num))])
        if infotext:
            tab.append([infotext, ""])

        F.append(Table(body=tab, border=3, cell_padding=2, column1_align="left",
                       cell_align="right", width="100%"))

        addsubmit = Input(type="submit", name="loginsubmit",
                          value=self._("Login with selected user"))
        tab = [["", addsubmit]]
        F.append(Table(body=tab, border=0, cell_align="right", width="100%"))
        self.doc.append(F)
        return


    def response(self):
        self.resp.shortcuts_active = 0
        # If some keyword is missing, view main page.
        if (not (self.form.has_key("komserver") and
                 (self.form.has_key("username") or self.form.has_key("login_persno"))
                 and self.form.has_key("password"))):
            LoginPageActions(self.resp, self._).response()
            return

        self.komserver = self.form["komserver"].value
        self.password = self.form["password"].value

        try:
            remote_addr = self.resp.env["REMOTE_ADDR"]
            remote_host = socket.gethostbyaddr(remote_addr)[0]
        except:
            remote_host = "(unknown)"

        try:
            conn = kom.CachedUserConnection(self.komserver, 4894, "WebKOM%" + remote_host)
            # Set up asyncs
            ACCEPTING_ASYNCS = [
                kom.ASYNC_NEW_NAME,
                kom.ASYNC_LEAVE_CONF,
                kom.ASYNC_SEND_MESSAGE,
                kom.ASYNC_DELETED_TEXT,
                kom.ASYNC_NEW_TEXT,
                kom.ASYNC_NEW_RECIPIENT,
                kom.ASYNC_SUB_RECIPIENT,
                kom.ASYNC_NEW_MEMBERSHIP ]
            kom.ReqAcceptAsync(conn, ACCEPTING_ASYNCS).response()
        except:
            self.error_message(self._("Cannot connect to server."))
            return

        # Via number?
        login_persno = self.form.getvalue("login_persno")
        if login_persno:
            matches=[(int(login_persno), "")]
        else:
            username = self.form["username"].value
            matches = conn.lookup_name(username, want_pers=1, want_confs=0)

        # Check number of matches
        if len(matches) == 0:
            self.error_message(self._("The user %s does not exist." % username))
            return
        elif len(matches) > 1:
            # Name is ambigious. Generate table for selection. 
            self.gen_table(matches)
            return

        pers_num = matches[0][0]
        
        try:
            kom.ReqLogin(conn, pers_num, self.password, invisible = 0).response()
        except kom.InvalidPassword:
            self.error_message(self._("Wrong password."))
            return

        kom.ReqSetClientVersion(conn, "WebKOM", VERSION)

        # Create new session
        sessionkey = gen_session_key()
        # If the sessionkey is valid, someone else is using it. 
        while sessionset.valid_session(sessionkey):
            sessionkey = gen_session_key()
        self.resp.sess = Session(conn)
        # Add to sessionset
        sessionset.new_session(sessionkey, self.resp.sess)
        self.resp.key = sessionkey

        # Set user_no in connection
        conn.set_user(pers_num)

        # Handle messages
        conn.add_async_handler(kom.ASYNC_SEND_MESSAGE, self.resp.sess.async_message)

        # Pre-fetch information about half of the conferences
        prefetch_num = len(conn.member_confs)/2
        conf_list = conn.member_confs[0:prefetch_num]
        for conf_num in conf_list:
            conn.no_unread[conf_num]

        # Redirect to mainpage
        self.resp.redir("?sessionkey=" + self.resp.key)


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
        ViewConfsActions(self.resp, self._).response(only_unread=1)
        
class ViewConfsActions(Action):
    "Generate a page with all member conferences"
    def response(self, only_unread=0):
        toplink = Href(self.base_session_url(), "WebKOM")
        if only_unread:
            action_url = "viewconfs_unread"
        else:
            action_url = "viewconfs"

        conflink = self.action_href(action_url, self._("Conferences"))
        cont = Container(toplink, " : ", conflink)
        self.append_std_top(cont)

        if only_unread:
            self.doc.append(Heading(2, self._("Conferences (with unread)")))
        else:
            self.doc.append(Heading(2, self._("Conferences (you are are member of)")))

        std_cmd = Container()
        self.doc.append(self._("Default command: "), std_cmd)
        self.add_stdaction(std_cmd, self.resp, "goconf_with_unread", self._("Next conference with unread"))

        # Information about number of unread
        self.doc.append(self.unread_info())

        if self.form.getvalue("first_conf"):
            ask_for = int(self.form.getvalue("first_conf"))
        else:
            ask_for = 0

        # We ask for one extra, so we can know if we should display a next-page-link
        if only_unread:
            memberships = get_active_memberships_unread(self.sess.conn, ask_for, MAX_CONFS_PER_PAGE + 1)
        else:
            memberships = get_active_memberships(self.sess.conn, ask_for, MAX_CONFS_PER_PAGE + 1)
        prev_first = next_first = None
        if ask_for:
            # Link to previous page
            prev_first = ask_for - MAX_CONFS_PER_PAGE
            if prev_first < 0:
                prev_first = 0
                
        if len(memberships) > MAX_CONFS_PER_PAGE:
            # We cannot show all confs on the same page. Link to next page
            next_first = ask_for + MAX_CONFS_PER_PAGE
            # Remove the highest conference
            memberships.pop()

        # Add the previous-page-link
        self.doc.append(self.action_href(action_url + "&first_conf=" + str(prev_first),
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
                comment = escape(">500")
                name = Bold(name)
            elif n_unread > 0:
                comment = str(n_unread)
                name = Bold(name)
            else:
                comment = self._("none")
                
            tab.append([self.action_href("goconf&conf=" + str(conf.conference), name),
                        comment])

        

        # Add the next-page-link
        self.doc.append(self.action_href(action_url + "&first_conf=" + str(next_first),
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
            GoConfActions(self.resp, self._).response(next_conf)
        else:
            toplink = Href(self.base_session_url(), "WebKOM")
            conflink = self.action_href("viewconfs", self._("Conferences"))
            cont = Container(toplink, " : ", conflink)
            self.append_std_top(cont)
            self.doc.append(Heading(3, self._("No unread")))
            self.doc.append(self._("There are no unread articles."))
        return


class GoConfActions(Action):
    "Generate a page with the subjects of articles"
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
        self.change_conf(conf_num)
        # Fetch conference name
        conf_name = self.get_conf_name(conf_num)
        
        toplink = Href(self.base_session_url(), "WebKOM")
        conflink = self.action_href("viewconfs", self._("Conferences"))
        cont = Container(toplink, " : ", conflink)
        self.append_std_top(cont)

        cont.append(" : ", self.action_href("goconf&conf=" + str(conf_num),
                                            conf_name))

        self.doc.append(Heading(2, conf_name))

        # Standard action
        std_cmd = Container()
        self.doc.append(self._("Default command: "), std_cmd)
        # Information about number of unread
        self.doc.append(self.unread_info(self.sess.current_conf))

        self.doc.append(self.action_href("writearticle&rcpt=" + str(conf_num),
                                         self._("Write article")), NBSP)
        # Link to view presentation for this conference
        presentation = self.get_presentation(conf_num)
        self.doc.append(self.action_href("viewtext&textnum=" + str(presentation),
                                         self._("View presentation"), presentation), NBSP)

        self.doc.append(self.action_href("set_unread", self._("Set unread")), NBSP)
        self.doc.append(self.action_href("leaveconf", self._("Leave conference")), NBSP)
        
        self.doc.append(BR(), Heading(3, self._("Article subjects")))

        # local_num is the first local_num we are interested in
        if self.form.has_key("local_num"):
            ask_for = int(self.form["local_num"].value)
        else:
            ask_for = None

        # Get unread texts
        # FIXME: error handling
        ms = self.sess.conn.memberships[conf_num]
        texts = get_texts(self.sess.conn, self.sess.conn.get_user(), conf_num, MAX_SUBJ_PER_PAGE, ask_for)
        
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
            
        self.doc.append(self.action_href("goconf&conf=" + str(conf_num) \
                                         + "&local_num=" + str(prev_first),
                                         self._("Earlier articles"), prev_first), NBSP)
        
        headings = [self._("Unread"), self._("Subject"), self._("Author"), self._("Date"), self._("Number")]
        tab = []
        self.doc.append(Table(heading=headings, body=tab, cell_padding=2,
                              column1_align="right", cell_align="left", width="100%"))

        # Format and append text numbers, authors and subjects to the page
        for (local_num, global_num) in texts:
            ts = self.sess.conn.textstats[global_num]
            # Textnum
            textnum = self.action_href("viewtext&textnum=" + str(global_num), str(global_num))
            # Date
            date = self.sess.conn.textstats[global_num].creation_time.to_date_and_time()
            # Author
            ai_from = kom.first_aux_items_with_tag(ts.aux_items,
                                                   kom.AI_MX_FROM)
            author = ""
            if ai_from:
                ai_author =  ai_author = kom.first_aux_items_with_tag(ts.aux_items,
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
            subj = self.action_href("viewtext&textnum=" + str(global_num),
                                    subjtext)
            
            if is_unread(self.sess.conn, conf_num, local_num):
                subj = Bold(subj)
                textnum = Bold(textnum)
                unreadindicator = Bold("x")
            else:
                unreadindicator = "&nbsp;"

            tab.append([unreadindicator, subj, author, date, textnum])
                
        self.doc.append(self.action_href("goconf&conf=" + str(conf_num) \
                                         + "&local_num=" + str(next_first),
                                         self._("Later articles"), next_first), NBSP)

        self.doc.append(self.action_href("goconf_with_unread",
                                         self._("Next conference with unread")), NBSP)


        # Standard action
        next_text = get_next_unread(self.sess.conn, self.sess.conn.get_user(),
                                    self.sess.current_conf)
        if next_text:
            std_url = "viewtext&textnum=" + str(next_text)
            self.add_stdaction(std_cmd, self.resp, std_url, self._("Read next unread"))
        else:
            self.add_stdaction(std_cmd, self.resp, "goconf_with_unread", self._("Next conference with unread"))
            

class ViewTextActions(Action):
    "Generate a page with a requested article"
    def get_subject(self, global_num):
        subject = self.sess.conn.subjects[global_num]
        if not subject:
            # If subject is empty, the table gets ugly
            subject = "&nbsp;"
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
                               str(self.action_href("viewtext&textnum=" + str(c.text_no), str(c.text_no))) \
                               + c_authortext])
            else:
                header.append([self._("Comment to:"),
                               str(self.action_href("viewtext&textnum=" + str(c.text_no), str(c.text_no))) \
                               + c_authortext])
                
            if c.sent_by is not None:
                presentation = str(self.get_presentation(c.sent_by))
                header.append([self._("Added by:"),
                               self.action_href("viewtext&textnum=" + presentation, 
                                                self.get_pers_name(c.sent_by), presentation)])
            if c.sent_at is not None:
                header.append([self._("Added:"), c.sent_at.to_date_and_time()])

    def do_recipients(self, ts, header):
        "Add recipients to header. Also return a URL substring for commenting."
        comment_url = ""
        for r in ts.misc_info.recipient_list:
            leftcol = mir2caption(r.type)
            presentation = str(self.get_presentation(r.recpt))
            # Recepient, with hyperlink to presentation
            rightcol = str(self.action_href("viewtext&textnum=" + presentation, 
                                            self.get_conf_name(r.recpt), presentation))
            # Prepare comment-url
            # Do not keep CC and BCC recipients when writing comment. 
            if r.type == kom.MIR_TO:
                comment_url = comment_url + "&" + mir2keyword(r.type) + "=" + str(r.recpt)
            
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


    def add_comments_in(self, ts, header, new_comments):
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
                                   str(self.action_href("viewtext&textnum=" + str(c.text_no), str(c.text_no))) \
                                   + c_authortext])
                else:
                    header.append([self._("Footnote in article:"),
                                   Strike(str(c.text_no)),
                                   Emphasis(self._("(Not readable)"))])
            else:
                if "" != c_authortext:
                    header.append([self._("Comment in article:"),
                                   str(self.action_href("viewtext&textnum=" + str(c.text_no), str(c.text_no))) \
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

        self.doc.append(Table(body=header, cell_padding=2, column1_align="right", border=0, width="80%"))

    def response(self):
        # Toplink
        toplink = Href(self.base_session_url(), "WebKOM")
        # Link to conferences
        conflink = self.action_href("viewconfs", self._("Conferences"))
        cont = Container(toplink, " : ", conflink)
        self.append_std_top(cont)

        # Local and global text number
        global_num = int(self.form["textnum"].value)
        
        # Link to current conference
        # Note: It's possible to view texts from other conferences,
        # while still staying in another conference
        cont.append(" : ")
        cont.append(self.action_href("goconf&conf=" + str(self.sess.current_conf),
                                     self.get_conf_name(self.sess.current_conf)))
        # Link to this page
        cont.append(" : ")
        cont.append(self.action_href("viewtext" + "&textnum=" + str(global_num),
                                     self.sess.conn.subjects[global_num]))
        self.doc.append(BR())
        lower_actions = Container()
        #
        # Upper actions
        #
        std_cmd = Container()
        self.doc.append("Standardkommando: ", std_cmd)

        # Information about number of unread
        unread_cont = Container()
        self.doc.append(unread_cont)

        upper_actions = Container()
        self.doc.append(upper_actions)

        # Link for next conference with unread
        upper_actions.append(self.action_href("goconf_with_unread",
                                              self._("Next conference with unread")), NBSP)

        self.doc.append(BR())

        # Valid article?
        try:
            if global_num == 0:
                raise kom.NoSuchText
            ts = self.sess.conn.textstats[global_num]
        except kom.NoSuchText:
            self.print_error(self._("The article does not exist."))
            return 
        except:
            self.print_error(self._("An error occured when fetching article information."))
            return 

        # Fetch text
        try:
            text = kom.ReqGetText(self.sess.conn, global_num, 0, ts.no_of_chars).response()
        except:
            self.print_error(self._("An error occured when fetching article."))
            return
            

        # Skip over the subject
        body = text[string.find(text, "\n"):]
        # ...and the empty line
        ismail = 0
        if kom.first_aux_items_with_tag(ts.aux_items, kom.AI_MX_FROM):
            ismail = 1
        viewmailheadercode = ""
        if kom.first_aux_items_with_tag(ts.aux_items, kom.AI_MX_MISC):
            if self.form.getvalue("viewmailheader"):
                body = kom.first_aux_items_with_tag(ts.aux_items,
                                                    kom.AI_MX_MISC).data + body
                viewmailheadercode = "&viewmailheader=true"                
            else:
                body = body[1:]
        else:
            body = body[1:]

        header = []
        header.append([self._("Article number:"),
                       self.action_href("viewtext&textnum=" + str(global_num), str(global_num))])

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
                           str(self.action_href("viewtext&textnum=" +\
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
                           self.action_href("viewtext&textnum=" + presentation,
                                            self.get_pers_name(ts.author), presentation)])

        # Comments-to
        self.add_comments_to(ts, header)
        
        # Recipients
        comment_url = self.do_recipients(ts, header)

        # The number of unread has been updated in do_recipients, so now it's OK to add it
        unread_cont.append(self.unread_info(self.sess.current_conf))

        if ts.no_of_marks:
            header.append([self._("Marks:"), str(ts.no_of_marks)])
    
        header.append([self._("Subject:"), Bold(self.get_subject(global_num))])

        self.doc.append(BR())
        self.doc.append(Table(body=header, cell_padding=2, column1_align="right", width="75%"))
        
        # Body
        # FIXME: Reformatting according to protocol A.
        body = linkify_text(body)
        body = HTMLutil.latin1_escape(escape(body))
        body = unquote_specials(body)
        body = string.replace(body, "\n","<br>\n")

        bodycont = Container()

        # Add formatting style
        format = self.form.getvalue("viewformat")
        if format == "code":
            bodycont.append("<code>")
            body = string.replace(body, " ", "&nbsp;")
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
        self.doc.append("<tr><td>" + str(bodycont) + "</tr></td>")
        self.doc.append("</table>")

        # Ok, the body is done. Let's add all comments.
        header = []
        new_comments = []
        self.add_comments_in(ts, header, new_comments)

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
        next_text = get_next_unread(self.sess.conn, self.sess.conn.get_user(),
                                    self.sess.current_conf)
        next_text_url = "viewtext&textnum=" + str(next_text)
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
        next_comment_url = "viewtext&textnum=" + str(next_comment) + "&reading_comment=1"
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
        comment_url = comment_url + "&comment_to=" + str(global_num)
        lower_actions.append(self.action_href("writearticle" + comment_url,
                                              self._("Write comment")), NBSP)

        if format:
            lower_actions.append(self.action_href("viewtext&textnum=" + str(global_num),
                                                  self._("View in normal style")))
        else:
            lower_actions.append(self.action_href("viewtext&textnum=" + str(global_num) + "&viewformat=code",
                                                  self._("View in code style")))

        if ismail:
            if "" != viewmailheadercode:
                lower_actions.append(self.action_href("viewtext&textnum=" +\
                                                      str(global_num),
                                                      "View without mail headers"))
            else:
                lower_actions.append(self.action_href("viewtext&textnum=" +\
                                                      str(global_num) +\
                                                      "&viewmailheader=true",
                                                      "View with mail headers"))
        return 



class ChangePwActions(Action):
    "Generate a page for changing LysKOM password"
    def response(self):
        self.resp.shortcuts_active = 0
        toplink = Href(self.base_session_url(), "WebKOM")
        cont = Container(toplink, " : " + self._("Change password"))
        self.append_std_top(cont)
        submitbutton = Center(Input(type="submit", name="changepwsubmit", value="Byt lösenord"))
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
        self.doc.append(Container(toplink, " : ", changepwlink), BR(2))

        if newpw1 != newpw2:
            self.print_error(self._("The two new passwords didn't match."))
            return

        try:
            kom.ReqSetPasswd(self.sess.conn, self.sess.conn.get_user(), oldpw, newpw1).response()
        except:
            self.print_error(self._("The server rejected your password change request"))
            return
        
        self.doc.append(Heading(3, self._("Ok")))
        self.doc.append(self._("Your password has been changed"))


class CreateUserActions(Action):
    "Generate a page for creating a new LysKOM user"
    def response(self):
        toplink = Href(BASE_URL, "WebKOM")
        create_user_link = Href(BASE_URL + "?action=create_user", self._("Create new user"))
        cont = Container(toplink, " : ", create_user_link)
        self.append_std_top(cont)
        
        submitbutton = Center(Input(type="submit", name="create_user_submit", value=self._("Create new user")))
        F = Form(BASE_URL, name="create_user_form", submit="")
        F.append(Input(type="hidden", name="create_user_submit"))
        self.doc.append(F)
        
        F.append(BR(2))
        F.append(Center(Heading(2, self._("Create new user"))))
        F.append(BR(2))
        logintable = [(self._("Server"), Input(name="komserver", size=20, value=DEFAULT_KOM_SERVER)),
                      (self._("Username"), Input(name="username", size=20)),
                      (self._("Password"), Input(type="password", name="password1", size=20)), 
                      (self._("Repeat password"), Input(type="password", name="password2", size=20)) ]
        
        F.append(Center(Formtools.InputTable(logintable)))
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
        cont = Container(toplink, " : ", create_user_link)
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
            self.print_error(self._("An user with this name exists."))
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
            WriteArticleActions(self.resp, self._).response(presentationfor = int(presfor), presconf = serverinfo.pers_pres_conf)
        else:
            self.change_conf(serverinfo.conf_pres_conf)
            WriteArticleActions(self.resp, self._).response(presentationfor = int(presfor), presconf=serverinfo.pers_pres_conf)


class WriteLetterActions(Action):
    "Write personal letter"
    def response(self):
        self.change_conf(self.sess.conn.get_user())
        WriteArticleActions(self.resp, self._).response()


class WriteArticleActions(Action):
    "Generate a page for writing or commenting an article"
    def response(self, presentationfor = None, presconf = None):
        self.resp.shortcuts_active = 0
        # Fetch conference name
        conf_num = self.sess.current_conf
#        cs = self.sess.conn.conferences[conf_num]
#        if cs.type.original:
#            conf_num = cs.super_conf
        conf_name = self.get_conf_name(conf_num)
        
        toplink = Href(self.base_session_url(), "WebKOM")
        conflink = self.action_href("viewconfs", self._("Conferences"))
        thisconf = self.action_href("goconf&conf=" + str(conf_num), conf_name)

        comment_to_list = get_values_as_list(self.form, "comment_to")
        if presentationfor:
            if 0 != self.sess.conn.conferences[presentationfor].presentation:
                if not self.sess.conn.conferences[presentationfor].presentation in comment_to_list:
                    comment_to_list += [self.sess.conn.conferences[presentationfor].presentation]
        footnote_to_list = get_values_as_list(self.form, "footnote_to")

        submitname = "writearticlesubmit"
        submitvalue = self._("Submit")
        if presentationfor:
            submitname = "writepresentationsubmit"
            submitvalue = self._("Set as presentation")
            page_heading = self._("Write presentation")
        else:
            if comment_to_list:
                page_heading = self._("Write comment")
            else:
                page_heading = self._("Write article")

        writeart = self.action_href("writearticle", page_heading)
        
        cont = Container(toplink, " : ", conflink, " : ", thisconf, " : ", writeart)
        self.append_std_top(cont)

        submitbutton = Input(type="submit", name=submitname,
                             value=submitvalue)
        
        F = Form(BASE_URL, name="writearticleform", submit=submitbutton)
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
                if not self.form.getvalue("searchrcptsubmit") and comment_to_list and not presentationfor:
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
        for mir in mir_keywords_dict.keys():
            keyword = mir_keywords_dict[mir]
            type_list.append( (mir2caption(mir), keyword) )

        # Add new recipients
        newones = get_values_as_list(self.form, "addrcpt")
        if newones:
            for rcpt in newones:
                rcpt_dict[int(rcpt)] = "rcpt"

        # If user did a search and the result was not ambigious, add recipient.
        searchtext = self.form.getvalue("searchtext", None)
        if searchtext:
            matches = self.sess.conn.lookup_name(searchtext, want_pers=1, want_confs=1)
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
        cont=Container()
        cont.append(self._("Search for new recipient:"))
        cont.append(Input(name="searchtext"))
        cont.append(Input(type="submit", name="searchrcptsubmit", value=self._("Search")))
        removesubmit = Input(type="submit", name="removercptsubmit",
                             value=self._("Remove marked recipients"))
        tab = [[cont, removesubmit]]
        F.append(Table(body=tab, border=0, cell_align="right", width="100%"))

        ## Search result
        searchtext = self.form.getvalue("searchtext", None)
        if searchtext and (len(matches) <> 1):
            infotext = None
            if len(matches) == 0:
                infotext = self._("(Nothing matches %s)") % searchtext
            elif len(matches) > 10:
                infotext = self._("(Too many matches, search result truncated)")
                
            F.append(self._("Search result:"), BR())
            tab=[]
            for (rcpt_num, rcpt_name) in matches[:10]:
                tab.append([rcpt_name,
                            Input(type="checkbox", name="addrcpt", value=str(rcpt_num))])
            if infotext:
                tab.append([infotext, ""])
                
            F.append(Table(body=tab, cell_padding=2, border=3, column1_align="left",
                           cell_align="right", width="100%"))

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
        if presentationfor and self.sess.conn.conferences[presentationfor].presentation != 0:
            try:
                ts = self.sess.conn.textstats[self.sess.conn.conferences[presentationfor].presentation]
                text = kom.ReqGetText(self.sess.conn,
                                      self.sess.conn.conferences[presentationfor].presentation,
                                      0,
                                      ts.no_of_chars).response()
                text = text[string.find(text, "\n")+1:]
            except:
                self.print_error("An error occured when fetching article information for text %d" \
                                 % self.sess.conn.conferences[presentationfor].presentation)
                
        F.append("Article text:", BR())
        # F.append(Textarea(text, rows=20, cols=70))
        # NOTE: This is non-standardized way to get linewrapping. Then why use it?
        # Because there are no way to achieve this without Javascript etc. 
        F.append("\n<textarea name=\"text_area\" rows=20 cols=70 wrap=\"virtual\">")
        F.append(text)
        F.append("</textarea>")
        F.append(BR())

        return

class WriteArticleSubmit(Action):
    "Submit the article"
    def response(self):
        # Fetch conference name
        conf_num = self.sess.current_conf
        conf_name = self.get_conf_name(conf_num)
        
        toplink = Href(self.base_session_url(), "WebKOM")
        conflink = self.action_href("viewconfs", self._("Conferences"))
        thisconf = self.action_href("goconf&conf=" + str(conf_num), conf_name)
        writeart = self.action_href("writearticle", self._("Write article"))
        
        cont = Container(toplink, " : ", conflink, " : ", thisconf, " : ", writeart)
        self.append_std_top(cont)

        self.doc.append(Heading(2, self._("Write article")))
        self.doc.append(self._("Article submitted"), BR())

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
                        self.print_error(self._("%s: %d -- text not found") % (typename, text_num))
                except:
                    self.print_error(self._("%s: %s -- bad text number") % (typename, text_num_str))

        if not len(misc_info.recipient_list) > 0:
            self.print_error(self._("No recipients!"))

        subject = self.form.getvalue("articlesubject", "")
        text = self.form.getvalue("text_area", "")
        # Reformat text (eg. make it maximum 70 chars
        text = reformat_text(text)
        # Remove \m
        text = string.replace(text, "\015", "")
        aux_items = []

        text_num = 0
        try:
            text_num = kom.ReqCreateText(self.sess.conn, subject + "\n" + text,
                                         misc_info, aux_items).response()
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
            self.print_error(self._("Unable to create article."))

        return text_num

class WritePresentationSubmit(Action):
    "Submit a presentation"
    def response(self):
        text_num = WriteArticleSubmit(self.resp, self._).response()
        presentation_for = int(self.form.getvalue("presentationfor"))
        if 0 != text_num:
            kom.ReqSetPresentation(self.sess.conn,
                                   presentation_for,
                                   text_num).response()
            self.sess.conn.conferences.invalidate(presentation_for)



class WhoIsOnActions(Action):
    "Generate a page with active LysKOM users"
    def response(self):
        toplink = Href(self.base_session_url(), "WebKOM")
        wholink = self.action_href("whoison", self._("Who is logged in"))
        cont = Container(toplink, " : ", wholink)
        self.append_std_top(cont)
        self.doc.append(Heading(3, self._("Who is logged in")))
        # FIXME: This function seems not to show sessions that have been active the
        # last 30 minutes, but rather all sessions. Therefore, the comment below is
        # invalid. Fix this function, and re-activate the statement below!
        #self.doc.append("Nedan visas alla sessioner som har varit \
        #aktiva de senaste 30 minuterna.", BR())

        try:
            who_list = kom.ReqWhoIsOnDynamic(self.sess.conn, active_last = 0).response()
        except:
            self.doc.append(self._("Request failed"))

        headings = [self._("Session"), self._("User<br>From"), self._("Working conference<br>Is doing")]
        tab = []
        
        for who in who_list:
            static = kom.ReqGetStaticSessionInfo(self.sess.conn, who.session).response()
            name = self.get_pers_name(who.person)
            user_and_host = static.username + "@" + static.hostname
            conf_name = self.sess.conn.conf_name(who.working_conference,
                                                 default=self._("No working conference"))[:MAX_CONFERENCE_LEN]
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
            JoinConfSubmit(self.resp, self._).response()
        else:
            self.search_page()

    def search_page(self):
        self.resp.shortcuts_active = 0
        toplink = Href(self.base_session_url(), "WebKOM")
        joinlink = self.action_href("joinconf", self._("Join conference"))
        cont = Container(toplink, " : ", joinlink)
        self.append_std_top(cont)

        F = Form(BASE_URL, name="joinconfform", submit="")
        self.doc.append(F)
        F.append(self.hidden_key())

        F.append(BR())
        F.append(Heading(2, self._("Join conference")))
        F.append(BR())

        ## Search and remove submit
        cont=Container()
        cont.append(self._("Search conference:"))
        cont.append(Input(name="searchtext"))
        cont.append(Input(type="hidden", name="searchconfsubmit"))
        cont.append(Input(type="submit", name="searchconfsubmit", value=self._("Search")), BR())
        F.append(cont)
        
        ## Search result
        searchtext = self.form.getvalue("searchtext", None)
        if searchtext:
            matches = self.sess.conn.lookup_name(searchtext, want_pers=0, want_confs=1)
            infotext = None
            if len(matches) == 0:
                infotext = self._("(Nothing matches %s)") % searchtext
            elif len(matches) > 10:
                infotext = self._("(Too many matches, search result truncated)")
                
            F.append(self._("Search result:"), BR())
            tab=[]
            for (rcpt_num, rcpt_name) in matches[:10]:
                tab.append([rcpt_name,
                            Input(type="radio", name="new_conference", value=str(rcpt_num))])
            if infotext:
                tab.append([infotext, ""])
                
            F.append(Table(body=tab, cell_padding=2, border=3, column1_align="left",
                           cell_align="right", width="100%"))

            addsubmit = Input(type="submit", name="joinconfsubmit",
                              value=self._("Join marked conference"))
            tab = [["", addsubmit]]
            F.append(Table(body=tab, border=0, cell_align="right", width="100%"))

        return


class JoinConfSubmit(Action):
    "Handles submits for joining a conference."
    def response(self):
        toplink = Href(self.base_session_url(), "WebKOM")
        joinlink = self.action_href("joinconf", self._("Join conference"))
        cont = Container(toplink, " : ", joinlink)
        self.append_std_top(cont)
        
        conf = int(self.form.getvalue("new_conference"))
        type = kom.ConfType()
        try:
            # FIXME: User settable priority
            kom.ReqAddMember(self.sess.conn, conf, self.sess.conn.get_user(), 100, 1000, type).response()
        except:
            self.print_error(self._("Unable to join conference."))
            return

        self.doc.append(Heading(3, self._("Ok")))
        self.doc.append(self._("You are now a member of conference "))
        self.doc.append(self.action_href("goconf&conf=" + str(conf), self.get_conf_name(conf)))
        self.doc.append(".")
        
        return

class SetUnreadActions(Action):
    "Generate a page for setting unread"
    def response(self):
        self.resp.shortcuts_active = 0
        toplink = Href(self.base_session_url(), "WebKOM")
        conflink = self.action_href("viewconfs", self._("Conferences"))
        cont = Container(toplink, " : ", conflink)
        self.append_std_top(cont)

        conf_num = self.sess.current_conf
        conf_name = self.get_conf_name(conf_num)
        cont.append(" : ", self.action_href("goconf&conf=" + str(conf_num),
                                            conf_name))

        submitbutton = Input(type="submit", name="set_unread_submit", value="Utför")
        F = Form(BASE_URL, name="set_unread_form", submit=submitbutton)
        self.doc.append(F)
        F.append(self.hidden_key())

        F.append(Heading(2, self._("Set unread")))

        F.append(self._("Set read marks to "))
        F.append(Input(name="num_unread", size=4, value="20"))
        F.append(self._(" unread articles in this conference."), BR())

        return


class SetUnreadSubmit(Action):
    "Handles submits for joining a conference."
    def response(self):
        toplink = Href(self.base_session_url(), "WebKOM")
        conflink = self.action_href("viewconfs", self._("Conferences"))
        cont = Container(toplink, " : ", conflink)
        self.append_std_top(cont)

        conf_num = self.sess.current_conf
        conf_name = self.get_conf_name(conf_num)
        cont.append(" : ", self.action_href("goconf&conf=" + str(conf_num),
                                            conf_name))
        
        
        num_unread = int(self.form.getvalue("num_unread"))
        try:
            kom.ReqSetUnread(self.sess.conn, conf_num, num_unread).response()
        except:
            self.print_error(self._("Unable to set number of unread."))
            return

        # Invalidate caches
        self.sess.conn.memberships.invalidate(conf_num)
        self.sess.conn.no_unread.invalidate(conf_num)
        
        self.doc.append(Heading(3, self._("Ok")))
        self.doc.append(self._("The number of unread articles is now ") + str(num_unread) + ".")
        
        return



class LeaveConfActions(Action):
    "Generate a page for leaving conference"
    def response(self):
        self.resp.shortcuts_active = 0
        toplink = Href(self.base_session_url(), "WebKOM")
        conflink = self.action_href("viewconfs", "Möten")
        cont = Container(toplink, " : ", conflink)
        self.append_std_top(cont)

        conf_num = self.sess.current_conf
        conf_name = self.get_conf_name(conf_num)
        cont.append(" : ", self.action_href("goconf&conf=" + str(conf_num),
                                            conf_name))

        submitbutton = Input(type="submit", name="leaveconfsubmit", value="Ja, utträd ur mötet")
        F = Form(BASE_URL, name="set_unread_form", submit=submitbutton)
        self.doc.append(F)
        F.append(self.hidden_key())
        F.append(Heading(2, self._("Leave conference")))
        F.append(self._("Do you really want to leave conference ") + conf_name + "?")
        F.append(BR(2))
        return


class LeaveConfSubmit(Action):
    "Handles submits for leaving a conference."
    def response(self):
        toplink = Href(self.base_session_url(), "WebKOM")
        conflink = self.action_href("viewconfs", self._("Conferences"))
        cont = Container(toplink, " : ", conflink)
        self.append_std_top(cont)

        conf_num = self.sess.current_conf
        conf_name = self.get_conf_name(conf_num)
        cont.append(" : ", conf_name)
        
        try:
            kom.ReqSubMember(self.sess.conn, conf_num, self.sess.conn.user_no).response()
        except:
            self.print_error(self._("Unable to leave conference."))
            return

        # Note:
        # We don't need to invalidate any caches, since a async
        # message should do that for us. 
        
        self.doc.append(Heading(3, self._("Ok")))
        self.doc.append(self._("You are no longer a member of conference ") + conf_name + ".")
        
        return



class ChooseConfActions(Action):
    "Generate a page for choosing active conference"
    def response(self):
        self.resp.shortcuts_active = 0
        # Non-JS capable browsers should ignore this
        self.doc.onLoad = "document.choose_conf_form.searchtext.focus()"
        toplink = Href(self.base_session_url(), "WebKOM")
        golink = self.action_href("joinconf", self._("Choose working conference"))
        cont = Container(toplink, " : ", golink)
        self.append_std_top(cont)

        F = Form(BASE_URL, name="choose_conf_form", submit="")
        self.doc.append(F)
        F.append(self.hidden_key())

        F.append(BR())
        F.append(Heading(2, self._("Choose working conference")))
        F.append(BR())
        F.append(self._("Type in the beginning of the conference name. You can also search "
                        "via conference numbers by giving # followed by the conference number."), BR())

        ## Search and remove submit
        cont=Container()
        cont.append(self._("Search for conference:"))
        cont.append(Input(name="searchtext"))
        cont.append(Input(type="hidden", name="choose_conf_search"))
        cont.append(Input(type="submit", name="choose_conf_search", value=self._("Search")), BR())
        F.append(cont)
        
        ## Search result
        searchtext = self.form.getvalue("searchtext", None)
        if searchtext:
            matches = self.sess.conn.lookup_name(searchtext, want_pers=0, want_confs=1)
            member_matches = []
            for match in matches:
                if match[0] in self.sess.conn.member_confs:
                    member_matches.append(match)
            
            infotext = None
            if len(member_matches) == 0:
                infotext = self._("(Nothing matches %s)") % searchtext
            elif len(member_matches) > 10:
                infotext = self._("(Too many matches, search result truncated)")
                
            self.doc.append(self._("Search result:"), BR())
            tab=[]
            for (rcpt_num, rcpt_name) in member_matches[:10]:
                tab.append([self.action_href("goconf&conf=" + str(rcpt_num), rcpt_name)])

            if infotext:
                tab.append([infotext, ""])
                
            self.doc.append(Table(body=tab, cell_padding=2, border=3, column1_align="left",
                                  cell_align="right", width="100%"))

        return


def actions(resp):
    "Do requested actions based on CGI keywords"
    try:
        lang_string = resp.env["HTTP_ACCEPT_LANGUAGE"]
    except KeyError:
        lang_string = ""
    
    trans = translator_cache.get_translator(lang_string).gettext
    
    if resp.form.has_key("loginsubmit"):
        LogInActions(resp, trans).response()
        return

    if resp.form.has_key("sessionkey"):
        resp.key = resp.form["sessionkey"].value
    elif resp.form.has_key("action") and (resp.form["action"].value == "about"):
        # It's possible to view about page withour being logged in
        AboutPageActions(resp, trans).response()
        return
    elif resp.form.has_key("action") and (resp.form["action"].value == "create_user"):
        CreateUserActions(resp, trans).response()
        return
    elif resp.form.has_key("create_user_submit"):
        CreateUserSubmit(resp, trans).response()
        return
    else:
        LoginPageActions(resp, trans).response()
        return 
    
    # "loginsubmit" and "about" excluded
    submit_keywords = {"changepwsubmit" : ChangePwSubmit,
                       "removercptsubmit" : WriteArticleActions,
                       "addrcptsubmit" : WriteArticleActions,
                       "searchrcptsubmit" : WriteArticleActions,
                       "writearticlesubmit" : WriteArticleSubmit,
                       "writepresentationsubmit" : WritePresentationSubmit,
                       "joinconfsubmit" : JoinConfSubmit,
                       "searchconfsubmit" : JoinConfActions,
                       "leaveconfsubmit" : LeaveConfSubmit,
                       "set_unread_submit" : SetUnreadSubmit,
                       "choose_conf_search" : ChooseConfActions }
    
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
                       "leaveconf" : LeaveConfActions,
                       "about" : AboutPageActions,
                       "set_unread" : SetUnreadActions }

    if not sessionset.valid_session(resp.key):
        InvalidSessionPageActions(resp, trans).response()
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
    resp.sess.lock_sess()

    # View messages
    ViewPendingMessages(resp, trans).response()

    # Create an instance of apropriate class and let it generate response
    action = response_type(resp, trans)
    # Generate page
    action.response()

    # Add Javascript shortcuts
    if resp.shortcuts_active:
        # Add global shortcuts
        resp.add_shortcut("v", action.base_session_url() + "&action=whoison")
        resp.add_shortcut("b", action.base_session_url() + "&action=writeletter&rcpt=" 
                          + str(resp.sess.conn.get_user()))
        resp.add_shortcut("g", action.base_session_url() + "&action=choose_conf")
        AddShortCuts(resp, trans).response()

    # Set page title
    resp.doc.title = "WebKOM: " + resp.sess.conn.conf_name(resp.sess.conn.get_user())[:MAX_CONFERENCE_LEN]

    # For debugging 
    #resp.doc.append(str(resp.env))
        
    return


def write_traceback(resp):
    # Something failed in response generation.
    try:
        lang_string = resp.env["HTTP_ACCEPT_LANGUAGE"]
    except KeyError:
        lang_string = ""
    
    _ = translator_cache.get_translator(lang_string).gettext
    
    # Save a copy on disk
    import traceback
    import time
    timetext = time.strftime("%y%m%d-%H%M", time.localtime(time.time()))                
    f = open(LOG_DIR + "traceback-" + timetext, "w")
    traceback.print_exc(file = f)
    f.close()
    
    # Put it on the web.
    # (Is it possible to print it directly, without going via the file?
    # Then tell me!)
    resp.doc.append(Heading(3, "Internal server error"))
    resp.doc.append(_("Check if this bug is listed on"))
    resp.doc.append(Href("../bugs.html", _("the list with known bugs")))
    resp.doc.append(_("If it doesn't, please report this problem to"))
    resp.doc.append(MAINTAINER_NAME)
    resp.doc.append(Href("mailto: " + MAINTAINER_MAIL, MAINTAINER_MAIL + "."))

    resp.doc.append(_("Attach the error message below."))
    resp.doc.append(_("The server time was: ") + \
                    time.strftime("%Y%m%d-%H:%M", time.localtime(time.time())))
    f = open(LOG_DIR + "traceback-" + timetext, "r")
    resp.doc.append(Pre(str(f.read())))
    f.close()


# Main action routine
# Note: "func" is a sz_fcgi magic name
def func(fcg, env, form):
    try: # Catch everything else
        try: # Don't catch SystemExit
            resp = Response(env, form)
            try: # For response generation
                actions(resp)
            except:
                write_traceback(resp)

            # Unlock session (it was probably locked in "actions")
            if resp.sess:
                resp.sess.unlock_sess()

            # Produce output
            fcg.pr(resp.http_header)
            fcg.pr(str(resp.doc))
            fcg.finish()
        except SystemExit:
            # We are not interested in these exceptions
            pass
    except:
        import traceback
        f = open(LOG_DIR + "traceback.func", "w")
        traceback.print_exc(file = f)
    return


# Interaction via FIFO
def run_console(self, *args):
    import fifoconsole
    try:
        fifoconsole.interact(local=globals(), fifoprefix="testwebkom")
    except:
        f=open(LOG_DIR + "traceback.fifoconsole", "w")
        traceback.print_exc(file = f)
        f.close()

def run_maintenance(self, *args):
    while 1:
        time.sleep(60)
        sessionset.del_inactive()

def run_fcgi():
    try:
        fcgi.run()
    except:
        import traceback
        f = open(LOG_DIR + "traceback.main", "w")
        traceback.print_exc(file = f)
        f.close()

#
# MAIN
#
# Start console thread
thread.start_new_thread(run_console,(0,0))
# Start maintenance thread
thread.start_new_thread(run_maintenance,(0,0))

# Create instance of translator
translator_cache = TranslatorCache.TranslatorCache("webkom", LOCALE_DIR, DEFAULT_LANG)

# Create an instance of our FCGI wrapper
fcgi = sz_fcgi.SZ_FCGI(func)

if __name__=="__main__":
    # and let it run
    run_fcgi()


 
