# -*- coding: iso-8859-1 -*-
#!/usr/bin/env python


import os
import glob
import sys

if len(sys.argv) < 2:
    print "Usage: " + sys.argv[0] + " <locale-dir>"
    sys.exit(1)

locale_path = sys.argv[1]

for pofile in glob.glob("*.po"):
    lang = pofile[:-3]
    mofile = lang + ".mo"
    destdir = locale_path + "/" + lang + "/LC_MESSAGES"

    # Create lang directory
    try:
        os.makedirs(destdir)
    except OSError, e:
        if e.errno != 17:
            raise e

    # Translate .po -> .mo
    os.system("msgfmt -o " + mofile + " " + pofile)

    # Copy file
    cmd = "cp " + mofile + " " + destdir + "/webkom.mo"
    print cmd
    os.system(cmd)
    
