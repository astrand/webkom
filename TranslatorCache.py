
import gettext
import string
from webkom_constants import *

class TranslatorCache:
    def __init__(self):
        self.known_languages = {}
        self.known_languages["en"] = gettext.NullTranslations()


    def get_translator(self, lang_string):
        "Get a preferred GNU gettext translator, based on a HTTP_ACCEPT_LANG string."
        # Split into list
        lang_list = string.split(lang_string, ',')
        # Strip white space
        lang_list = [string.strip(lang) for lang in lang_list]

        for lang in lang_list:
            translator = self.try_lang(lang)
            if translator:
                return translator

        translator = self.try_lang(DEFAULT_LANG)
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
                new_translator = gettext.translation("webkom", LOCALE_DIR, languages=[lang])
            except:
                # The translator files was not found or something.
                new_translator = None
                
            # Save information about this new language (yes, thread-safe)
            self.known_languages[lang] = new_translator
            return new_translator
