#!/usr/bin/env python2
# -*- coding: iso-8859-1 -*-

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

import popen2
import string
import re
import os
import pwd
import sys
import shutil


# FIXME: Remove this function when Python provides realpath. 
def realpath(filename):
    """Return the canonical path of the specified filename, eliminating any
symbolic links encountered in the path."""
    filename = os.path.abspath(filename)

    bits = ['/'] + string.split(filename, '/')[1:]
    for i in range(2, len(bits)+1):
        component = apply(os.path.join, bits[0:i])
        if os.path.islink(component):
            resolved = os.readlink(component)
            (dir, file) = os.path.split(component)
            resolved = os.path.normpath(os.path.join(dir, resolved))
            newpath = apply(os.path.join, [resolved] + bits[i:])
            return realpath(newpath)

    return filename


def get_origin_dir(argv0=sys.argv[0]):
    """Get program origin directory"""
    
    abs_path = realpath(os.path.abspath(argv0))

    return os.path.dirname(abs_path)


def get_httpd_config():
    p = popen2.Popen3("httpd -V", 1)
    server_config_file = None
    
    while 1:
        err = p.childerr.read()
        if err:
            print >> sys.stderr, "error:", err
            sys.exit()
        
        line = p.fromchild.readline()
        if not line:
            break

        match = re.search("SERVER_CONFIG_FILE=\"(.*)\"", line)
        if match:
            server_config_file = match.group(1)

        match = re.search("HTTPD_ROOT=\"(.*)\"", line)
        if match:
            httpd_root = match.group(1)

    if not server_config_file:
        raise("Cannot determine SERVER_CONFIG_FILE")

    if server_config_file[0] != os.sep:
        # Not absolute path, check httpd_root
        if not httpd_root:
            raise("Cannot determine HTTPD_ROOT")

        server_config_file = os.path.join(httpd_root, server_config_file)

    return server_config_file


def get_httpd_doc_root(config_file):
    f = open(config_file, "r")
    while 1:
        line = f.readline()
        if not line:
            break

        match = re.search("^DocumentRoot\s+(.*)", line)
        if match:
            doc_root = match.group(1)

    if doc_root[0] == '"' and doc_root[-1] == '"':
        doc_root = doc_root[1:-1]
    
    return doc_root

def get_apache_user(config_file):
    f = open(config_file, "r")
    while 1:
        line = f.readline()
        if not line:
            break

        match = re.search("^User\s+(.*)", line)
        if match:
            user = match.group(1)

    return user


def lexists(path):
    try:
        os.lstat(path)
    except os.error:
        return 0
    return 1


def add_to_httpd_config(config_file, new_data):
    f = open(config_file)
    data = f.read()
    f.close()

    if data.find("webkom") != -1:
        print "It seems like this Apache is already configured for WebKOM\n"\
              "Giving up on editing httpd.conf"
        return
    
    lines = string.split(data, "\n")

    f = open(config_file, "w")
    state = 0 

    for line in lines:
        f.write(line + "\n")

        if state == 0:
            match = re.search("^<Directory />", line)
            if match:
                state = 1

        elif state == 1:
            match = re.search("</Directory>", line)
            if match:
                state = 2
                f.write("\n")
                f.write(new_data)

        elif state == 2:
            pass

        else:
            print "Internal error"
            sys.exit(16)


def main(dry_run):
    print "Finding WebKOM directory...",
    webkom_dir = get_origin_dir()
    print webkom_dir
    
    print "Finding httpd.conf...",
    httpd_conf = get_httpd_config()
    print httpd_conf

    print "Finding DocumentRoot...",
    doc_root = get_httpd_doc_root(httpd_conf)
    print doc_root

    web_home = os.path.join(doc_root, "webkom")
    print "Creating directory", web_home
    if not lexists(web_home):
        if not dry_run:
            os.mkdir(web_home, 0755)
    else:
        print web_home, "exists, skipping."

    src_img_dir = os.path.join(webkom_dir, "images")
    dst_img_dir = os.path.join(web_home, "images")
    print "Creating link", dst_img_dir, "->", src_img_dir
    if lexists(dst_img_dir):
        print dst_img_dir, "exists, overwriting."
        if not dry_run:
            os.remove(dst_img_dir)
    if not dry_run:
        os.symlink(src_img_dir, dst_img_dir)


    wrapper_file = os.path.join(web_home, "webkom.py.wrapper")
    print "Creating", wrapper_file, "(change Python-interpreter in this file)"
    if not dry_run:
        f = open(wrapper_file, "w")
        f.write("#!/bin/sh\n")
        f.write("# This is the place where to select Python interpreter for WebKOM.\n")
        f.write("PATH=$PATH\n")
        f.write("export PATH\n")
        f.write("PYTHONOPTIMIZE=1\n")
        f.write("export PYTHONOPTIMIZE\n")
        f.write("exec " + os.path.join(webkom_dir, "webkom.py") + "\n")
        f.close()
        os.chmod(wrapper_file, 0775)
    

    lnk_src = "webkom.py.wrapper"
    lnk_dst = os.path.join(web_home, "webkom.py")
    print "Creating link", lnk_dst, "->", lnk_src
    if lexists(lnk_dst):
        print lnk_dst, "exists, overwriting."
        if not dry_run:
            os.remove(lnk_dst)
    if not dry_run:
        os.symlink(lnk_src, lnk_dst)

    print "Finding Apache user...", 
    apache_user = get_apache_user(httpd_conf)
    print apache_user

    log_dir = os.path.join(webkom_dir, "logs")
    print "Changing owner of", log_dir, "to", apache_user
    cur_gid = os.stat(log_dir)[5]
    new_uid = pwd.getpwnam(apache_user)[2]
    if not dry_run:
        os.chown(log_dir, new_uid, cur_gid)


    # httpd.conf editing
    print "Copying %s to %s.bak" % (httpd_conf, httpd_conf)
    if not dry_run:
        shutil.copy(httpd_conf, httpd_conf + ".bak")

    print "Adding the lines below to", httpd_conf, ":"
    print "----------------"
    
    conf_clip = """\
FastCgiServer %s -idle-timeout 60 -flush
<Directory %s>
Options FollowSymLinks
DirectoryIndex webkom.py
Allow from all
<Files webkom.py>
    SetHandler fastcgi-script
</Files>
</Directory>""" % (os.path.join(web_home, "webkom.py"), # /home/httpd/html/webkom/webkom.py
                   web_home) # /home/httpd/html/webkom

    print conf_clip
    print "----------------"

    if not dry_run:
        add_to_httpd_config(httpd_conf, conf_clip)

    print "\nDone."

    
if __name__=="__main__":
    dry_run = 0

    if len(sys.argv) > 1:
        if sys.argv[1] == "-d":
            dry_run = 1
        else:
            print "Invalid option"
            sys.exit(1)

    
    print "This program will setup Apache to run WebKOM."

    if not dry_run:
        print """You can run in dry-mode (do nothing, just print what would
have been done) by specifying option -d."""
    else:
        print "Running in dry-mode"


    print "Press <enter> to continue, ctrl-c to abort..."
    try:
        sys.stdin.readline()
    except KeyboardInterrupt:
        sys.exit(0)

    main(dry_run)
    
