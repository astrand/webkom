# sz_fcgi.py - Multithreaded FastCGI Wrapper
#__version__ 	= "v0.8  19/10/1998 ajung"
__version__ 	= "v0.8  19/10/1998 ajung (WebKOM patched)"
__doc__      	= "Multithreaded FastCGI Wrapper"
#
# Changed 2001-03-03 by Peter Åstrand <astrand@lysator.liu.se>:
# Deleting request after Finish, to prevent memory leak.
# Changed formatting to match WebKOM standards.
# Changed SZ_FCGI.pr to behave more like a normal fcgi.out.write. 
#

import sys
import thread
from string import *
from fcgi import *


class SZ_FCGI:
    # Constructor
    def __init__(self, func):
        self.func = func
        self.handles  = {}
        return None

    # create a new thread to handle requests
    def run(self):
	while isFCGI():
	    req = FCGI()
	    thread.start_new_thread(self.handle_request, (req,0))

    # Finish thread and send all data back to the FCGI parent
    def finish(self):
	th_id = thread.get_ident()
	req  = self.handles[th_id]
	# Note: This is thread-safe, since req still references the instance.
	# If it wouldn't, req.__del__ might be called and it that case
	# we would need a lock. 
	del self.handles[th_id]
	req.finish()
	del req
	thread.exit()	

    # Call function - handled by a thread
    def handle_request(self, *args):
	req = args[0]
	self.handles[thread.get_ident()] = req

	try:
	    self.func(self, req.env, req.getFieldStorage())
	except:
	    pass

    # Our own FCGI print routine
    def pr(self, out_string):
	req = self.handles[thread.get_ident()]

	try:
	    req.out.write(out_string)
            req.flush()
	except:
	    pass

