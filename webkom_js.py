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


code_begin = """
<script type="text/javascript">
<!--
"""


code_end = """
//-->
</script>
"""


noscript_begin = """
<noscript>
"""


noscript_end = """
</noscript>
"""

shortcut_functions = """

var active = new Boolean(true);

document.onkeypress=keyPress;

"""

begin_switch = """
function keyPress(e) {
    if (!active) return true;
    if (!e) e = window.event;

    if (e.keyCode) {
        keycode = e.keyCode;
    } else if (e.which) {
        keycode = e.which;
    } else {
        return true;
    }

    keychar = String.fromCharCode(keycode);
    switch (keychar) {
"""


end_switch = """
        }
    return false;
}
"""


disable_shortcuts = """
    case 'z':
        active = false;
        alert("Shortcuts disabled.");
        break;
"""
