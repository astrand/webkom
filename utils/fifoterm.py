#!/sw/local/bin/python

# WebKOM - a web based LysKOM client
# 
# Copyright (C) 2000 by Peter Åstrand
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA 02111-1307, USA. 

import os, sys, readline

class Outobj:
    def __init__(self, fd):
        self.fd = fd
    def write(self, data):
        os.write(self.fd, data)

class Inobj:
    def __init__(self, fd):
        self.fd = fd
    def readline(self):
        char = None
        output = ""
        while char != "\012":
            char = os.read(self.fd, 1)
            output = output + char
        return output
    def readall(self):
        return os.read(self.fd, 10000)


def interact(prefix):
    outfifo = os.open(prefix + ".infifo", os.O_RDWR)
    infifo = os.open(prefix + ".outfifo", os.O_RDWR | os.O_NONBLOCK)
    outobj = Outobj(outfifo)
    inobj = Inobj(infifo)

    while 1:
        try:
            inline = inobj.readall()
            print inline
        except:
            print "(read error from pipe)"

        l = raw_input("WebKOM console: ")
        outobj.write(l + "\n\n")


if __name__=="__main__":
    if len(sys.argv) < 2:
        print "Usage: " + sys.argv[0] + " <fifoprefix>"
        print "Running an console over <fifoprefix>.infifo and <fifoprefix>.outfifo."
    else:
        interact(sys.argv[1])


