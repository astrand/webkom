
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

import gettext
import string

class TranslatorCache:
    def __init__(self, domain, locale_dir, default_lang):
        self.known_languages = {}
        self.known_languages["en"] = gettext.NullTranslations()
        self.domain = domain
        self.locale_dir = locale_dir
        self.default_lang = default_lang


    def get_translator(self, lang_string):
        "Get a preferred GNU gettext translator, based on a HTTP_ACCEPT_LANG string."
        # Split into list
        lang_list = string.split(lang_string, ',')
        # Strip white space
        lang_list = [string.strip(lang) for lang in lang_list]

        for lang in lang_list:
            translator = self.try_lang(string.lower(lang))
            if translator:
                return translator

        translator = self.try_lang(self.default_lang)
        if translator:
            return translator

        return gettext.NullTranslations()


    def try_lang(self, lang):
        if lang in self.known_languages.keys():
            # We have alreade stumbled upon this language.
            return self.known_languages[lang]
        else:
            # This is a language new to us. 
            try:
                # Try to initalize this new language
                new_translator = gettext.translation(self.domain, self.locale_dir, languages=[lang])
            except:
                # The translator files was not found or something.
                new_translator = None
                
            # Save information about this new language (yes, thread-safe)
            self.known_languages[lang] = new_translator
            return new_translator
