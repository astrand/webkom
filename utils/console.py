#!/usr/bin/env python2

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

import readline
import sys
import socket
import select
import termios


try:
    str(sys.ps1)
except:
    sys.ps1 = ">>> "


def write_prompt():
    sys.stdout.write(sys.ps1)
    sys.stdout.flush()


def interact(sockname):
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect(sockname)

    old = termios.tcgetattr(sys.stdin)
    new = termios.tcgetattr(sys.stdin)
    termios.tcsetattr(sys.stdin, termios.ICANON, new)    

    try:
        write_prompt()
        while 1:
            [x, y, z] = select.select([sock, sys.stdin], [], [])
            obj = x[0]
            if obj == sock:
                data = sock.recv(1)
                if not data:
                    raise "Closed connection"
                sys.stdout.write(data)
                sys.stdout.flush()
            elif obj == sys.stdin:
                # Remove duplicate first char. 
                # FIXME: Why is this necessary?
                sys.stdout.write("\x08")
                # Remove prompt from server. 
                sys.stdout.write("\x08" * 4)
                line = raw_input(sys.ps1)
                line += "\n"
                sock.send(line)
    finally:
        termios.tcsetattr(sys.stdin, termios.ICANON, old)


if __name__=="__main__":
    if len(sys.argv) < 2:
        print "Usage: " + sys.argv[0] + " <socket>"
        print "Running an WebKOM console over socket."
    else:
        try:
            interact(sys.argv[1])
        except KeyboardInterrupt:
            pass
        except EOFError:
            pass
        
        
