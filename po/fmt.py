#!/usr/bin/env python

import os
import glob

for pofile in glob.glob("*.po"):
    lang = pofile[:-3]
    os.system("msgfmt -o " + lang + ".mo " + pofile)

