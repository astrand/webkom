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

import kom
from HTMLgen import *
# Override default escape
AbstractTag.html_escape = 'OFF'
import HTMLcolors
import HTMLutil
from webkom_constants import *
import re
import string
import inspect
import dircache

NBSP = "&nbsp;"
INACTIVE_LINK_COLOR = HTMLcolors.GREY6
FALSE = 0
TRUE = 1

# Magic!
class Struct:
    """Simple structure"""
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

    kom.ReqSetClientVersion(conn, "WebKOM", VERSION).response()
    kom.ReqAcceptAsync(conn, [kom.ASYNC_NEW_TEXT]).response()

    # Note: matches[0][0] is an integer
    return (conn, matches[0][0], None)


def get_total_num_unread(conn, conf_list):
    total = 0
    for conf_num in conf_list:
        total = total + conn.no_unread[conf_num]
    return total


def get_next_unread(conn, conf_num):
    "Get next unread text in a conference and return as a global number"
    try:
        ms = conn.memberships[conf_num]
    except kom.NotMember:
        return 0

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


def get_texts(conn, conf_num, max_num, lowest_local=None):
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


def get_active_memberships(conn, first_pos, max_num, only_unread=0):
    """Get a limited number of active memberships with unread, starting
    at position first_pos (starts at 0)"""

    if only_unread:
        # Select conferences with unread
        conf_nums = filter(lambda conf_num: conn.no_unread[conf_num], conn.member_confs)
    else:
        conf_nums = conn.member_confs

    # Select conferences to display, wrt first_pos and max_num
    interesting_conf_nums = conf_nums[first_pos:first_pos+max_num]

    # Construct list with kom.Membership instances. Sort. Return.
    return [conn.memberships[conf_num] for conf_num in interesting_conf_nums]


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
mir_keywords_dict ={
    kom.MIR_TO : "rcpt",
    kom.MIR_CC : "cc",
    kom.MIR_BCC: "bcc" }

def mir2caption(klass, mir):
    if mir == kom.MIR_TO:
        return klass._("Recipient")
    elif mir == kom.MIR_CC:
        return klass._("Carbon copy")
    elif mir == kom.MIR_BCC:
        return klass._("Blind carbon copy")
    else:
        raise "Invalid MIR in mir2caption"
    
def mir2keyword(mir):
    return mir_keywords_dict[mir]

def keyword2mir(keyword):
    for mir in mir_keywords_dict.keys():
        if mir_keywords_dict[mir] == keyword:
            return mir

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
    return Href(url, text + str(Image(src="/webkom/images/offsite.png", border=0,
                                      height=13, width=17, alt="[extern länk]")))


def gen_8859_1_invalid_chars():
    delchars = ""
    # 0-31 (except 9, 10 and 13) and 127-159 are forbidden in ISO-8859-1
    charset = range(0, 9) + range(11, 13) + range(14, 32)
    for charnum in charset:
        delchars += chr(charnum)
    return delchars

invalid_chars_trans = trans = string.maketrans("", "")
invalid_chars_delchars = gen_8859_1_invalid_chars()

def del_8859_1_invalid_chars(text):
    "Delete all invalid ISO-8859-1 chars"
    return string.translate(text, invalid_chars_trans, invalid_chars_delchars)
    

def unquote_specials(text):
    text = string.replace(text, "\001","<")
    text = string.replace(text, "\002",">")
    return text


def webkom_escape(s):
    s = del_8859_1_invalid_chars(s)
    s = HTMLutil.latin1_escape(escape(s))
    return s


def reformat_text(text):
    result = ""
    
    for line in text.split("\n"):
        line = _reformat_line(line)
        result = _reformat_add_line(result, line)

    return result

def _reformat_add_line(text, line):
    if text:
        text += "\n"
    text += line
    return text

def _reformat_line(line):
    result = ""
    outline = ""

    for word in line.split(" "):
        if word == "":
            # We had a space
            word = " "
        
        # Ok to add another word to this line?
        # The 1 is for the space. 
        if (len(outline) + 1 + len(word)) <= 70:
            # Yepp, word fits on line
            if outline:
                outline += " "
            outline += word
        else:
            # No, must create a new line
            result = _reformat_add_line(result, outline)
            outline = word

    # Add rest
    if outline:
        result = _reformat_add_line(result, outline)

    return result


def quote_text(text):
    result = ""
    for line in text.split("\n"):
        result = _reformat_add_line(result, ">" + line)
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


class ArticleSearcher:
    def __init__(self, conn, limit):
        self.max_int32 = int(2**31L-1)
        self.conn = conn
        if limit != None:
            self.limit = limit
        else:
            self.limit = self.max_int32

    def search(self, needle):
        result = []
        reo = re.compile(needle, re.I)
        while self.limit > 0:
            self.limit -= 1
            textnums = self._fetch_textnums()
            if textnums == []:
                break
            for textnum in textnums:
                try:
                    text = kom.ReqGetText(self.conn, textnum).response()
                    if reo.search(text):
                        result.append(textnum)
                except kom.NoSuchText:
                    # Either the text was just removed, or we have no read
                    # permission
                    pass

        return result

    def _fetch_textnums(self):
        """Return a list of texts to search through. If the result is an
        empty list, there are not more texts. Abstract method. """
        raise NotImplementedError()


class GlobalArticleSearcher(ArticleSearcher):
    def __init__(self, conn, limit):
        ArticleSearcher.__init__(self, conn, limit)
        self.textnum = self.max_int32

    def _fetch_textnums(self):
        try:
            self.textnum = kom.ReqFindPreviousTextNo(self.conn, self.textnum).response()
        except kom.NoSuchText:
            # There are no more texts
            return []
        else:
            return [self.textnum]


class LocalArticleSearcher(ArticleSearcher):
    def __init__(self, conn, limit, conf_num):
        ArticleSearcher.__init__(self, conn, limit)
        self.conf_num = conf_num
        self.local_no_ceiling = 0
        self.later_texts_exists = 1
        
    def _fetch_textnums(self):
        if not self.later_texts_exists:
            return []
        
        no_of_existing_texts = min(self.limit, 255)
        try:
            m = kom.ReqLocalToGlobalReverse(self.conn, self.conf_num, self.local_no_ceiling,
                                            no_of_existing_texts).response()
        except kom.UndefinedConference:
            return []
        except kom.AccessDenied:
            return []

        self.later_texts_exists = m.later_texts_exists
        self.local_no_ceiling = m.range_begin
        textnums = [textnum for local_textnum, textnum in m.list]
        # Some textnums might be zero. 
        textnums = filter(lambda x: x !=0 , textnums)
        textnums.reverse()
        return textnums


def get_ai_dict(ai_list):
    d = {}
    for ai in ai_list:
        d[ai.tag]

def mime_content_type(s):
    """Get the type / subtype part of a MIME content type"""
    return s.split(";")[0]

def mime_content_params(s):
    """Get parameters of a MIME content type, as a dictionary"""
    fields = s.split(";")
    d = {}
    for param in fields[1:]:
        (name, value) = param.split("=")
        d[name] = value
    return d


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

def list2string(l):
    formatstring = "%s, " * len(l)
    formatstring = formatstring[:-2]
    return formatstring % tuple(l)


def get_installed_languages():
    """Returns a list of installed languages"""
    result = ["en"]
    for lang in dircache.listdir(LOCALE_DIR):
        if os.path.exists(os.path.join(LOCALE_DIR, lang, "LC_MESSAGES/webkom.mo")):
            result.append(lang)

    return result


class FinalizerChecker:
    def __init__(self, syslog):
        self.syslog = syslog
        self.check_finalizers()

    def check_module(self, module):
        for (name, klass) in inspect.getmembers(module, inspect.isclass):
            self.check_class(klass)

    def check_class(self, klass):
        for (name, value) in inspect.getmembers(klass):
            if name == "__del__":
                self.syslog.write(1, "ERROR: Class %s has __del__ finalizer!" \
                                  % klass)
    def check_finalizers(self):
        for key in globals().keys():
            attr = globals()[key]
            if inspect.ismodule(attr):
                self.check_module(attr)


class WebKOMSimpleDocument(SimpleDocument):
    def get_doc_start(self):
        s = []
        s.append(DOCTYPE)
        # build the HEAD and BODY tags
        s.append(self.html_head())
        s.append(self.html_body_tag())
        return string.join(s, '')

    def get_doc_end(self):
        return '\n</BODY> </HTML>\n' # CLOSE the document

    def get_doc_contents(self):
        s = []
        # DOCUMENT CONTENT SECTION and FOOTER added on
        bodystring = '%s\n' * len(self.contents)
        s.append((bodystring % tuple(self.contents)))
        return string.join(s, '')

    def flush_doc_contents(self):
        s = self.get_doc_contents()
        self.contents = []
        return s
        
    
