import kom
from HTMLgen import *
from HTMLcolors import *
from webkom_constants import *
import re
import string

# KOM utility functions

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


# Modified version av get_unread_texts (from kom.py)
def get_num_unread_texts(conn, pers_num, conf_num):
    "Get number of unread texts in a conference (500 max)"
    num_unread = 0
    ms = kom.ReqQueryReadTexts(conn, pers_num, conf_num).response()

    # Start asking for translations
    ask_for = ms.last_text_read + 1
    more_to_fetch = 1
    while more_to_fetch:
        try:
            mapping = kom.ReqLocalToGlobal(conn, conf_num,
                                           ask_for, 255).response()
            for (local_num, global_num) in mapping.list:
                if (local_num not in ms.read_texts) and global_num:
                    num_unread = num_unread + 1
                    if num_unread > 500:
                        return num_unread
            ask_for = mapping.range_end
            more_to_fetch = mapping.later_texts_exists
        except kom.NoSuchLocalText:
            # No unread texts
            more_to_fetch = 0

    return num_unread


def get_next_unread(conn, pers_num, conf_num, current_text):
        "Get next unread text"
        # FIXME: Make more server-friendly
        ms = kom.ReqQueryReadTexts(conn, pers_num, conf_num).response()

        # Start asking for translations
        ask_for = ms.last_text_read + 1
        more_to_fetch = 1
        while more_to_fetch:
            try:
                mapping = kom.ReqLocalToGlobal(conn, conf_num,
                                               ask_for, 5).response()
                for (local_num, global_num) in mapping.list:
                    if (local_num not in ms.read_texts) and global_num:
                        return global_num
                ask_for = mapping.range_end
                more_to_fetch = mapping.later_texts_exists
            except kom.NoSuchLocalText:
                # No unread texts
                more_to_fetch = 0

        return 0


def get_texts(conn, pers_num, conf_num, lowest_local=None):
    "Get all unread texts. Return a list of tuples (local_num, global_num, unread)"
    # Start list
    texts = []
    
    # Get membership record
    ms = kom.ReqQueryReadTexts(conn, pers_num, conf_num).response()

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
                unread = (local_num not in ms.read_texts) and (local_num > ms.last_text_read)
                # global_num may be zero if texts are deleted
                if global_num:
                    # The last entry in the tuple indicates if the text is unread
                    texts.append((local_num, global_num, unread))
                    n_texts = n_texts + 1 
                if n_texts >= MAX_SUBJ_PER_PAGE:
                    return texts
                
            ask_for = mapping.range_end
            more_to_fetch = mapping.later_texts_exists
        except kom.NoSuchLocalText:
            # No unread texts
            more_to_fetch = 0

    return texts


def get_active_memberships(conn, pers_num, ask_for, max_num):
    "Get a limited number of active memberships"
    retlist = []

    while 1:
        try:
            # It doesn't matter how many confs to get each time;
            # MAX_CONFS_PER_PAGE + 1 is just chosen because thats a common number
            memberships = kom.ReqGetMembership(conn, pers_num, ask_for,
                                               MAX_CONFS_PER_PAGE + 1, 1).response()
            for ms in memberships:
                if (ms.priority != 0) and (not ms.type.passive):
                    retlist.append(ms)
                    if len(retlist) >= max_num:
                        return retlist
            ask_for = ask_for + len(memberships)
        except kom.IndexOutOfRange:
            return retlist


def get_conf_with_unread(conn, pers_num):
    "Get next conference with unread articles"
    unread_confs = kom.ReqGetUnreadConfs(conn, pers_num).response()

    for conf_num in unread_confs:
        ms = kom.ReqQueryReadTexts(conn, pers_num, conf_num).response()
        highest_local_num = conn.uconferences[conf_num].highest_local_no
        if highest_local_num > ms.last_text_read:
            # Start asking for translations
            ask_for = ms.last_text_read + 1
            more_to_fetch = 1
            while more_to_fetch:
                try:
                    mapping = kom.ReqLocalToGlobal(conn, conf_num,
                                                   ask_for, 128).response()
                    for (local_num, global_num) in mapping.list:
                        if (local_num not in ms.read_texts) and global_num:
                            # Yes, an unread article exist
                            return conf_num

                    ask_for = mapping.range_end
                    more_to_fetch = mapping.later_texts_exists
                except kom.NoSuchLocalText:
                    # No unread texts
                    more_to_fetch = 0
    return None


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
    # FIXME: Linkify everything that looks like an URL. 
    # http URLs
    pat = re.compile("(?P<fullurl>(http://|(?=www\\.))(?P<url>\\S*\\w))")
    repl = '<a href="http://\\g<url>">\\g<fullurl></a>'
    text = pat.sub(repl, text)

    # ftp URLs
    pat = re.compile("(?P<fullurl>(ftp://|(?=ftp\\.))(?P<url>\\S*\\w))")
    repl = '<a href="ftp://\\g<url>">\\g<fullurl></a>'
    text = pat.sub(repl, text)

    # file URLs
    pat = re.compile("(?P<fullurl>(file://)(?P<url>\\S*\\w))")
    repl = '<a href="file://\\g<url>">\\g<fullurl></a>'
    text = pat.sub(repl, text)
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


NBSP = "&nbsp;"
