#!/usr/bin/env python

# thfcgi demo. To set up with Apache, put this file somewhere into
# your document root, and add an entry to httpd.conf, like:
#
# FastCgiServer /home/httpd/html/tmp/thfcgi_demo.py
# <Directory /home/httpd/html/tmp>
# <Files thfcgi_demo.py>
#     SetHandler fastcgi-script
# </Files>
# </Directory>
# 

import string
import thfcgi
import traceback

counter = 0

def handle_req(req, env, form):
    global counter
    counter += 1
    
    # A typical http header
    http_headers = ["Content-type: text/html; charset=iso-8859-1",
                    "Cache-Control: no-cache",
                    "Pragma: no-cache",
                    "Expires: 0",
                    "",
                    ""]

    req.out.write(string.join(http_headers, "\r\n"))
    req.out.write("<html><head><title>Hello</title></head>"
                  "<body><h1>Hello</h1>")
    req.out.write("This is request %d" % counter)
    req.out.write("</body></html>")

    # Finish thread and send all data back to the FCGI parent
    try:
        req.finish()
    except SystemExit:
        pass
    except:
        # You should probably log tracebacks here
        pass


if __name__ == "__main__":
    # Create an instance of THFCGI...
    fcgi = thfcgi.THFCGI(handle_req)
    # ...and let it run
    try:
        fcgi.run()
    except:
        f = open("traceback.main", "w")
        traceback.print_exc(file = f)
        f.close()
