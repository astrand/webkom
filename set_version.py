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

import os

from webkom_constants import VERSION

template = open("webkom.spec.template")
new = open("webkom.spec", "w")

while 1:
    line = template.readline()
    if not line:
        break

    if line.find("Version:") != -1:
        line = "Version: " + VERSION + "\n"

    new.write(line)

    

