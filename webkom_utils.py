
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

import kom
from HTMLgen import *
from HTMLcolors import *
from webkom_constants import *
import re
import string
import random
import sys

NBSP = "&nbsp;"
INACTIVE_LINK_COLOR = GREY6

# Magic!
class Struct:
    pass


# KOM utility functions


# FIXME: Ugly return-API
def kom_login(komserver, username, password):
    "Login to KOM server. Return (connection, pers_no, errorstring)"
    port = 4894
    try:
        conn = kom.CachedConnection(komserver, port)
    except:
        return (0, 0, "Kan inte ansluta till servern.")
        
    matches = conn.lookup_name(username, want_pers=1, want_confs=0)
    if len(matches) == 0:
        return (0, 0, "Användaren %s finns inte." % username)
    elif len(matches) > 1:
        return (0, 0, "Namnet %s är inte entydigt." % username)
    else:
        try:
            kom.ReqLogin(conn, matches[0][0], password, invisible = 0).response()
        except:
            return (0, 0, "Felaktigt lösenord.")

    kom.ReqSetClientVersion(conn, "WebKOM", VERSION)
    kom.ReqAcceptAsync(conn, [kom.ASYNC_NEW_TEXT])

    # Note: matches[0][0] is an integer
    return (conn, matches[0][0], None)


def get_total_num_unread(conn, pers_num, conf_list):
    total = 0
    for conf_num in conf_list:
        total = total + conn.no_unread[conf_num]
    return total


def get_next_unread(conn, pers_num, conf_num):
    "Get next unread text in a conference and return as a global number"
    ms = conn.memberships[conf_num]

    # Start asking for translations
    ask_for = ms.last_text_read + 1
    more_to_fetch = 1
    while more_to_fetch:
        try:
            mapping = kom.ReqLocalToGlobal(conn, conf_num,
                                           ask_for, 16).response()
            for (local_num, global_num) in mapping.list:
                if (local_num not in ms.read_texts) and global_num:
                    return global_num
            ask_for = mapping.range_end
            more_to_fetch = mapping.later_texts_exists
        except kom.NoSuchLocalText:
            # No unread texts
            more_to_fetch = 0
    return 0


def get_texts(conn, pers_num, conf_num, max_num, lowest_local=None):
    "Get all unread texts. Return a list of tuples (local_num, global_num)"
    # Start list
    texts = []
    
    # Get membership record
    ms = conn.memberships[conf_num]

    # Start asking for translations
    if lowest_local:
        ask_for = lowest_local
    else:
        ask_for = ms.last_text_read + 1
        
    more_to_fetch = 1
    n_texts = 0
    while more_to_fetch:
        try:
            mapping = kom.ReqLocalToGlobal(conn, conf_num,
                                           ask_for, 255).response()
            for (local_num, global_num) in mapping.list:
                if n_texts >= max_num: 
                    return texts
                # global_num may be zero if texts are deleted
                if global_num:
                    texts.append((local_num, global_num))
                    n_texts = n_texts + 1 
                    
            ask_for = mapping.range_end
            more_to_fetch = mapping.later_texts_exists
        except kom.NoSuchLocalText:
            # No unread texts
            more_to_fetch = 0

    return texts


def membership_sort_p(first, second):
    "Sort-predicate for memberships, based on priority"
    return cmp(second.priority, first.priority)


def get_active_memberships(conn, first_pos, max_num):
    "Get a limited number of active memberships, starting at position first_pos"
    retlist = []
    for conf_num in conn.member_confs[first_pos:]:
        if len(retlist) >= max_num:
            break
        retlist.append(conn.memberships[conf_num])

    retlist.sort(membership_sort_p)
    return retlist


def get_active_memberships_unread(conn, first_pos, max_num):
    "Get a limited number of active memberships, starting at position first_pos"
    retlist = []
    for conf_num in conn.member_confs[first_pos:]:
        if len(retlist) >= max_num:
            break
        if conn.no_unread[conf_num]:
            retlist.append(conn.memberships[conf_num])

    retlist.sort(membership_sort_p)
    return retlist


def get_conf_with_unread(conn, member_confs, current_conf):
    "Get next conference with unread articles"
    try:
        current_pos = member_confs.index(current_conf)+1
    except:
        current_pos = 0
    ordered_confs = member_confs[current_pos:] + member_confs[:current_pos]
    for conf_num in ordered_confs:
        if conn.no_unread[conf_num] > 0:
            return conf_num
    return None


# FIXME: Obsolete and ugly. Remove me. 
def is_member(conn, pers_num, conf_num):
    try:
        kom.ReqQueryReadTexts(conn, pers_num, conf_num).response()
    except kom.NotMember:
        return 0
    return 1


# MISC helper functions
mir_caption_dict = {
    kom.MIR_TO: "Mottagare",
    kom.MIR_CC: "Extra kopiemottagare",
    kom.MIR_BCC: "För kännedom" }

mir_keywords_dict ={
    kom.MIR_TO : "rcpt",
    kom.MIR_CC : "cc",
    kom.MIR_BCC: "bcc" }

def mir2caption(mir):
    return mir_caption_dict[mir]

def mir2keyword(mir):
    return mir_keywords_dict[mir]

def keyword2mir(keyword):
    for mir in mir_keywords_dict.keys():
        if mir_keywords_dict[mir] == keyword:
            return mir

def keyword2caption(keyword):
    return mir2caption(keyword2mir(keyword))

def get_values_as_list(form, keyword):
    data = form.getvalue(keyword)
    if not data:
        return []
    if type(data) is not type([]):
        # It was a single value. Listify!
        return [data]
    else:
        return data

def external_href(url, text):
    return Href(url, text + str(Image(src="/images/offsite.png", border=0,
                                      height=13, width=17, alt="[extern länk]")))

def linkify_text(text):
    # FIXME: Linkify everything that looks like an URL or mail adress. 
    # NOTE:
    # < = \001
    # > = \002
    # This is because otherwise these are quoted by HTMLgen.escape. 
    
    # http URLs
    pat = re.compile("(?P<fullurl>(http://|(?=www\\.))(?P<url>\\S*\\w))")
    repl = '\001a href="http://\\g<url>"\002\\g<fullurl>\001/a\002'
    text = pat.sub(repl, text)

    # ftp URLs
    pat = re.compile("(?P<fullurl>(ftp://|(?=ftp\\.))(?P<url>\\S*\\w))")
    repl = '\001a href="ftp://\\g<url>"\002\\g<fullurl>\001/a\002'
    text = pat.sub(repl, text)

    # file URLs
    pat = re.compile("(?P<fullurl>(file://)(?P<url>\\S*\\w))")
    repl = '\001a href="file://\\g<url>"\002\\g<fullurl>\001/a\002'
    text = pat.sub(repl, text)
    return text

def unquote_specials(text):
    text = string.replace(text, "\001","<")
    text = string.replace(text, "\002",">")
    return text

def reformat_text(text):
    linelist = string.split(text, "\n")
    result = ""
    outline = ""
    for line in linelist:
        # Clear outline
        outline = ""
        rest = string.split(line)

        while rest:
            newword = rest[0]
            # Remove newword from rest
            rest = rest[1:]
            # The 1 is for the space. 
            if (len(outline) + 1 + len(newword)) > 70:
                # We can't get this word also. Break line here. 
                result = result + outline + "\n"
                outline = newword
            else:
                # Add newword to current line.
                # Only add space if not first word on line. 
                if outline:
                    outline = outline + " " 
                outline = outline + newword

        # Ok, this inputline is done. Add it to output. 
        result = result + outline + "\n"

    return result


def update_membership(conn, conf_num, read_text):
    "Update the Membership object in the cache when we have read a text"
    # Note: read_text is a local number of the read text.
    ms = conn.memberships[conf_num]
    # ms.added_at unchanged
    # ms.added_by unchanged
    # ms.conference unchanged

    # Update ms.last_text_read or ms.read_texts
    # Start asking for translations
    ms.read_texts.append(read_text)

    # Defrag
    ms.read_texts.sort()
    locals = existing_locals(conn, conf_num, ms.last_text_read + 1, ms.read_texts[-1:][0])
    for loc in locals:
        if loc in ms.read_texts:
            ms.last_text_read = loc
            ms.read_texts.remove(loc)
        else:
            break
                        
    # FIXME: update ms.last_time_read (set to current time)
    # ms.position unchanged
    # ms.priority unchanged
    # ms.type unchanged

def update_unread(conn, conf_num, read_loc_num):
    "Update the value of no_unread when a local text is read"
    if is_unread(conn, conf_num, read_loc_num):
        conn.no_unread[conf_num] = conn.no_unread[conf_num] - 1

def is_unread(conn, conf_num, local_num):
    if local_num <= conn.memberships[conf_num].last_text_read:
        return 0
    elif local_num in conn.memberships[conf_num].read_texts:
        return 0
    else:
        return 1


def existing_locals(conn, conf_num, ask_for, highest_local):
    "Fetch all existing local text numbers between [ask_for, highest_local]"
    more_to_fetch = 1
    local_nums = []
    while more_to_fetch:
        try:
            mapping = kom.ReqLocalToGlobal(conn, conf_num,
                                           ask_for, 64).response()
            for (local_num, global_num) in mapping.list:
                if local_num > highest_local:
                    return local_nums
                if global_num:
                    local_nums.append(local_num)
                    
            ask_for = mapping.range_end
            more_to_fetch = mapping.later_texts_exists
        except kom.NoSuchLocalText:
            # No unread texts
            more_to_fetch = 0

    return local_nums


# FIXME: Unused function. 
## def next_local_no(conn, ask_for, conf_num):
##     more_to_fetch = 1
##     while more_to_fetch:
##         try:
##             mapping = kom.ReqLocalToGlobal(conn, conf_num,
##                                            ask_for, 5).response()
##             for (local_num, global_num) in mapping.list:
##                 if global_num:
##                     return local_num
##             ask_for = mapping.range_end
##             more_to_fetch = mapping.later_texts_exists
##         except kom.NoSuchLocalText:
##             # No unread texts
##             more_to_fetch = 0
##     return 0


def gen_session_key():
    key = ""
    for foo in range(0, 4):
        key = key + hex(random.randrange(sys.maxint))[2:]
    return key

