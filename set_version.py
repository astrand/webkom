#!/usr/bin/env python2

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

    

