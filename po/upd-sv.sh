#!/bin/sh
../utils/pygettext.py --no-location -d webkom ../webkom.py ../webkom_utils.py
mv sv.po sv.po.old
msgmerge sv.po.old webkom.pot > sv.po
