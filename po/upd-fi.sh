#!/bin/sh
../utils/pygettext.py -d webkom ../webkom.py ../webkom_utils.py
mv fi.po fi.po.old
msgmerge fi.po.old webkom.pot > fi.po
