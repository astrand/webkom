#! /usr/bin/env python

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

# Adapted from Demo/pysvr. 

import sys, os, thread, socket, traceback
from code import compile_command


def main_thread(globals, socketname):
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)

    # Remove old socket
    if os.path.exists(socketname):
        try:
            os.unlink(socketname)
        except Exception, e:
            print "Console: Cannot remove socket %s: %s" % (socketname, e)
            sys.exit(1)
    
    sock.bind(socketname)
    sock.listen(5)
    while 1:
        (conn, addr) = sock.accept()
        thread.start_new_thread(service_thread, (conn, globals))
        del conn, addr


def service_thread(conn, globals):
    print "Console: Thread %s has connected." % (str(thread.get_ident()))
    stdin = conn.makefile("r", 0)
    stdout = conn.makefile("w", 0)

    try:
        run_interpreter(stdin, stdout, globals)
    except IOError:
        pass
    print "Console: Thread %s is done." % str(thread.get_ident())


def run_interpreter(stdin, stdout, globals):
    try:
        str(sys.ps1)
    except:
        sys.ps1 = ">>> "
    source = ""
    while 1:
        stdout.write(sys.ps1)
        line = stdin.readline()
        if line[:2] == '\377\354':
            line = ""
        if not line and not source:
            break
        if line.strip() == '\x04':
            break
        if line[-2:] == '\r\n':
            line = line[:-2] + '\n'
        source = source + line
        try:
            code = compile_command(source)
        except SyntaxError, err:
            source = ""
            traceback.print_exception(SyntaxError, err, None, file=stdout)
            continue
        if not code:
            continue
        source = ""
        try:
            run_command(code, stdin, stdout, globals)
        except SystemExit, how:
            if how:
                try:
                    how = str(how)
                except:
                    how = ""
                stdout.write("Exit %s\n" % how)
            break
    stdout.write("\nGoodbye.\n")


def run_command(code, stdin, stdout, globals):
    save = sys.stdin, sys.stdout, sys.stderr
    try:
        sys.stdout = sys.stderr = stdout
        sys.stdin = stdin
        try:
            exec code in globals
        except SystemExit, how:
            raise SystemExit, how, sys.exc_info()[2]
        except:
            type, value, tb = sys.exc_info()
            if tb: tb = tb.tb_next
            traceback.print_exception(type, value, tb)
            del tb
    finally:
        sys.stdin, sys.stdout, sys.stderr = save


#main_thread(globals(), "/tmp/mysock")
