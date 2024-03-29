#!/bin/sh -x
VERSION=`python -c "from webkom_constants import *; print VERSION"`
PWD=`pwd`
SRCDIR=`basename ${PWD}`

./set_version.py
(cd po; ./install.py ../locale)
make clean
find . -name '*~' -exec rm \{\} \;
find . -name '*.pyc' -exec rm \{\} \;
find . -name '*.pyo' -exec rm \{\} \;
find logs -mindepth 1 -not -path '*CVS*' \
 -and -not -name .cvsignore \
 -and -not -name README -exec rm -f \{\} \;

cd ..
TOPDIR=`pwd`
rm -f ${TOPDIR}/webkom-${VERSION}.tgz

# Clean /tmp
rm -rf /tmp/webkom-${VERSION}

# Copy files to /tmp
cp -a ${SRCDIR} /tmp/webkom-${VERSION}

cd /tmp
DISTFILES=`find webkom-${VERSION} \
 -not -path '*CVS*' -and \
 -not -name .cvsignore -and \
 -not -name TODO -and \
 -not -path .\ -and \
 -not -path '*/misc/*' \
 -type f`
tar zcvf ${TOPDIR}/webkom-${VERSION}.tgz ${DISTFILES}

