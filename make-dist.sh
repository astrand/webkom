#!/bin/sh -x
VERSION=`python -c "from webkom_constants import *; print VERSION"`
PWD=`pwd`
SRCDIR=`basename ${PWD}`
./set_version.py
(cd po; ./install.py ../locale)
find . -name '*~' -exec rm \{\} \;
find . -name '*.pyc' -exec rm \{\} \;
find . -name '*.pyo' -exec rm \{\} \;
find logs -mindepth 1 -not -path '*CVS*' \
 -and -not -name .cvsignore \
 -and -not -name README -exec rm -f \{\} \;
cd ..
rm -f webkom-${VERSION}.tgz
DISTFILES=`find ${SRCDIR} \
 -not -path '*CVS*' -and -not -name .cvsignore -and -not -path .\
 -type f`
tar zcvf webkom-${VERSION}.tgz ${DISTFILES}
