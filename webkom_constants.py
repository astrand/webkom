# -*- coding: iso-8859-1 -*-
# WebKOM - a web based LysKOM client
# 
# Copyright (C) 2000 by Peter Åstrand
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

import os
import sys
import string


# FIXME: Remove this function when Python provides realpath. 
def realpath(filename):
    """Return the canonical path of the specified filename, eliminating any
symbolic links encountered in the path."""
    filename = os.path.abspath(filename)

    bits = ['/'] + string.split(filename, '/')[1:]
    for i in range(2, len(bits)+1):
        component = apply(os.path.join, bits[0:i])
        if os.path.islink(component):
            resolved = os.readlink(component)
            (dir, file) = os.path.split(component)
            resolved = os.path.normpath(os.path.join(dir, resolved))
            newpath = apply(os.path.join, [resolved] + bits[i:])
            return realpath(newpath)
        
    return filename


def get_origin_dir(argv0=sys.argv[0]):
    """Get program origin directory"""
    
    abs_path = realpath(os.path.abspath(argv0))

    return os.path.dirname(abs_path)


ORIGIN_DIR = get_origin_dir()
LOG_DIR = os.path.join(ORIGIN_DIR, "logs")
#LOG_DIR = "/var/log/webkom"
LOCALE_DIR = os.path.join(ORIGIN_DIR, "locale")
VERSION = "0.21"
BASE_URL = "webkom.py"
DEFAULT_KOM_SERVER = "kom.lysator.liu.se"
LOCK_KOM_SERVER = 0
SERVER_LIST = [
    ("LysKOM", "kom.lysator.liu.se"),
    ("LysKOM (english)", "com.lysator.liu.se"),
    ("LuddKOM", "kom.ludd.luth.se"),
    ("CdKOM", "kom.cd.chalmers.se"),
    ("UppKOM", "kom.update.uu.se"),
    ("RydKOM", "kom.hem.liu.se"),
    ("TokKOM", "kom.stacken.kth.se"),
    ("MDSKOM", "kom.mds.mdh.se"),
    ("DsKOM", "kom.ds.hj.se")]


VALIDATOR_LINK = 1

# Log out the users sessions on the same webkom/komserver if this is set to
# 1.
LOGOUT_OTHER_SESSIONS=1

# Set LOCALBIND to either None or a tuple (hostname, 0). If set to None,
# outgoing connections to the LysKOM server(s) will come from the primary
# interface of the machine running WebKOM. Setting it to something else
# makes it possible to bind to a virtual interface.
LOCALBIND = None
# LOCALBIND = ('webkom.lysator.liu.se', 0)

MAX_SUBJ_PER_PAGE = 25
MAX_CONFS_PER_PAGE = 15
CONSOLE_SOCKET = "/tmp/webkom.console"

MAX_CONFERENCE_LEN = 50
# 12 hours auto-logout
SESSION_TIMEOUT = 60 * 60 * 12 
DEFAULT_LANG = "sv"
LOGLEVEL = 4
COPYPASTE_CHARACTERS = "å Å ä Ä ö Ö ü Ü ! \" @ $ % & / { } [ ] ( ) \ ? ~ < > |"
KNOWN_BUGS_URL = "http://bugzilla.lysator.liu.se/buglist.cgi" \
                 "?bug_status=NEW&bug_status=ASSIGNED" \
                 "&bug_status=REOPENED&product=WebKOM"
BUGREPORT_URL = "http://bugzilla.lysator.liu.se/enter_bug.cgi?product=WebKOM"
CUSTOM_RIGHT_FOOTER = ""
TOPLINK_SEPARATOR = " &gt; "
STYLESHEET = None
#STYLESHEET = "style.css"
