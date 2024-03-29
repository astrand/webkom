# -*-python-*-
# -*- coding: iso-8859-1 -*-
#
#   Copyright � 1999-2001 The ViewCVS Group. All rights reserved. 
#
#   By using this software, you agree to the terms and conditions set forth
#   below:
#
#   Redistribution and use in source and binary forms, with or without
#   modification, are permitted provided that the following conditions are
#   met:
#    1. Redistributions of source code must retain the above copyright
#       notice, this list of conditions and the following disclaimer.
#    2. Redistributions in binary form must reproduce the above copyright
#       notice, this list of conditions and the following disclaimer in
#       the documentation and/or other materials provided with the
#       distribution.
#
#   THIS SOFTWARE IS PROVIDED BY THE AUTHOR AND CONTRIBUTORS ``AS IS'' AND
#   ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
#   IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR
#   PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE AUTHOR OR CONTRIBUTORS
#   BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
#   CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
#   SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR
#   BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY,
#   WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE
#   OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN
#   IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

import re
import string


def language(hdr):
    "Parse an Accept-Language header."

    # parse the header, storing results in a _LanguageSelector object
    return _parse(hdr, _LanguageSelector())

# -----------------------------------------------------------------------

_re_token = re.compile(r'\s*([^\s;,"]+|"[^"]*")+\s*')
_re_param = re.compile(r';\s*([^;,"]+|"[^"]*")+\s*')
_re_split_param = re.compile(r'([^\s=])\s*=\s*(.*)')

def _parse(hdr, result):
    # quick exit for empty or not-supplied header
    if not hdr:
        return result

    pos = 0
    while pos < len(hdr):
        name = _re_token.match(hdr, pos)
        if not name:
            raise AcceptParseError()
        a = result.item_class(string.lower(name.group(1)))
        pos = name.end()
        while 1:
            # are we looking at a parameter?
            match = _re_param.match(hdr, pos)
            if not match:
                break
            param = match.group(1)
            pos = match.end()

            # split up the pieces of the parameter
            match = _re_split_param.match(param)
            if not match:
                # the "=" was probably missing
                continue

            pname = string.lower(match.group(1))
            if pname == 'q' or pname == 'qs':
                try:
                    a.quality = float(match.group(2))
                except ValueError:
                    # bad float literal
                    pass
            elif pname == 'level':
                try:
                    a.level = float(match.group(2))
                except ValueError:
                    # bad float literal
                    pass
            elif pname == 'charset':
                a.charset = string.lower(match.group(2))

        result.append(a)
        if hdr[pos:pos+1] == ',':
            pos = pos + 1

    return result

class _AcceptItem:
    def __init__(self, name):
        self.name = name
        self.quality = 1.0
        self.level = 0.0
        self.charset = ''

    def __str__(self):
        s = self.name
        if self.quality != 1.0:
            s = '%s;q=%.3f' % (s, self.quality)
        if self.level != 0.0:
            s = '%s;level=%.3f' % (s, self.level)
        if self.charset:
            s = '%s;charset=%s' % (s, self.charset)
        return s

class _LanguageRange(_AcceptItem):
    def matches(self, tag):
        "Match the tag against self. Returns the qvalue, or None if non-matching."
        if tag == self.name:
            return self.quality

        # are we a prefix of the available language-tag
        name = self.name + '-'
        if tag[:len(name)] == name:
            return self.quality
        return None

class _LanguageSelector:
    item_class = _LanguageRange

    def __init__(self):
        self.requested = [ ]

    def select_from(self, avail):
        """Select one of the available choices based on the request.

        Note: if there isn't a match, then the first available choice is
        considered the default.

        avail is a list of language-tag strings of available languages
        """

        # tuples of (qvalue, language-tag)
        matches = [ ]

        # try matching all pairs of desired vs available, recording the
        # resulting qvalues. we also need to record the longest language-range
        # that matches since the most specific range "wins"
        for tag in avail:
            longest = 0
            final = 0.0

            # check this tag against the requests from the user
            for want in self.requested:
                qvalue = want.matches(tag)
                #print 'have %s. want %s. qvalue=%s' % (tag, want.name, qvalue)
                if qvalue is not None and len(want.name) > longest:
                    # we have a match and it is longer than any we may have had
                    final = qvalue
                    longest = len(want.name)

            # a non-zero qvalue is a potential match
            if final:
                matches.append((final, tag))

        # if we have any matches, then look at the highest qvalue
        if matches:
            matches.sort()
            qvalue, tag = matches[-1]

            if len(matches) >= 2 and matches[-2][0] == qvalue:
                #print "non-deterministic choice", avail
                pass

            # if the qvalue is non-zero, then we have a valid match
            if qvalue:
                return tag
            # the qvalue is zero (non-match). drop thru to return the default

        # return the default language tag
        return avail[0]

    def append(self, item):
        self.requested.append(item)

class AcceptParseError(Exception):
    pass

def _test():
    s = language('en')
    assert s.select_from(['en']) == 'en'
    assert s.select_from(['en', 'de']) == 'en'
    assert s.select_from(['de', 'en']) == 'en'

    s = language('fr, de;q=0.9, en-gb;q=0.7, en;q=0.6, en-gb-foo;q=0.8')
    assert s.select_from(['en']) == 'en'
    assert s.select_from(['en-gb-foo']) == 'en-gb-foo'
    assert s.select_from(['de', 'fr']) == 'fr'
    assert s.select_from(['de', 'en-gb']) == 'de'
    assert s.select_from(['en-gb', 'en-gb-foo']) == 'en-gb-foo'
    assert s.select_from(['en-bar']) == 'en-bar'
    assert s.select_from(['en-gb-bar', 'en-gb-foo']) == 'en-gb-foo'

    # non-deterministic. en-gb;q=0.7 matches both avail tags.
    #assert s.select_from(['en-gb-bar', 'en-gb']) == 'en-gb'
