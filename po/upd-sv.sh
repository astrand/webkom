#!/bin/sh
../utils/pygettext.py -d webkom ../webkom.py 
mv sv.po sv.po.old
msgmerge sv.po.old webkom.pot > sv.po
