
#
# DO NOT USE, not finished.
# Besides, no web server supports multiplexing anyway...
#

# thfcgi.py - FastCGI communication with thread support
#
# Copyright Peter �strand <astrand@lysator.liu.se> 2001
# 
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; version 2 of the License. 
# 
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
# 
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 675 Mass Ave, Cambridge, MA 02139, USA.

# TODO:
#
# Compare compare the number of bytes received on FCGI_STDIN with
# CONTENT_LENGTH and abort the update if the two numbers are not equal.
#
# Keep connection if FCGI_KEEP_CONN
#
# Clean up Record class: Make Record abstract and use inheritance
# for each type of record.

# Since I've borrowed code from fcgi.py, I must include the following
# license header.

#------------------------------------------------------------------------
#               Copyright (c) 1998 by Total Control Software
#                         All Rights Reserved
#------------------------------------------------------------------------
#
# Module Name:  fcgi.py
#
# Description:  Handles communication with the FastCGI module of the
#               web server without using the FastCGI developers kit, but
#               will also work in a non-FastCGI environment, (straight CGI.)
#               This module was originally fetched from someplace on the
#               Net (I don't remember where and I can't find it now...) and
#               has been significantly modified to fix several bugs, be more
#               readable, more robust at handling large CGI data and return
#               document sizes, and also to fit the model that we had previously
#               used for FastCGI.
#
#     WARNING:  If you don't know what you are doing, don't tinker with this
#               module!
#
# Creation Date:    1/30/98 2:59:04PM
#
# License:      This is free software.  You may use this software for any
#               purpose including modification/redistribution, so long as
#               this header remains intact and that you do not claim any
#               rights of ownership or authorship of this software.  This
#               software has been tested, but no warranty is expressed or
#               implied.
#
#------------------------------------------------------------------------


import os
import sys
import select
import string
import socket
import errno
import cgi
import thread
from cStringIO import StringIO

# Set various FastCGI constants
# Maximum number of requests that can be handled
FCGI_MAX_REQS = 50
FCGI_MAX_CONNS = 1

# Supported version of the FastCGI protocol
FCGI_VERSION_1 = 1

# Boolean: can this application multiplex connections?
FCGI_MPXS_CONNS = 1

# Record types
FCGI_BEGIN_REQUEST = 1
FCGI_ABORT_REQUEST = 2
FCGI_END_REQUEST = 3
FCGI_PARAMS = 4
FCGI_STDIN = 5
FCGI_STDOUT = 6
FCGI_STDERR = 7
FCGI_DATA = 8
FCGI_GET_VALUES = 9
FCGI_GET_VALUES_RESULT = 10
FCGI_UNKNOWN_TYPE = 11
FCGI_MAXTYPE = FCGI_UNKNOWN_TYPE

# Types of management records
KNOWN_MANAGEMENT_TYPES = [FCGI_GET_VALUES]

FCGI_NULL_REQUEST_ID = 0

# Masks for flags component of FCGI_BEGIN_REQUEST
FCGI_KEEP_CONN = 1

# Values for role component of FCGI_BEGIN_REQUEST
FCGI_RESPONDER = 1
FCGI_AUTHORIZER = 2
FCGI_FILTER = 3

# Values for protocolStatus component of FCGI_END_REQUEST
FCGI_REQUEST_COMPLETE = 0     # Request completed nicely
FCGI_CANT_MPX_CONN = 1        # This app can't multiplex
FCGI_OVERLOADED = 2           # New request rejected; too busy
FCGI_UNKNOWN_ROLE = 3         # Role value not known


class Record:
    """Class representing FastCGI records"""
    def __init__(self):
        self.version = FCGI_VERSION_1
        self.recType = FCGI_UNKNOWN_TYPE
        self.reqId   = FCGI_NULL_REQUEST_ID
        self.content = ""

        # Only in FCGI_BEGIN_REQUEST
        self.role = None
        self.flags = None
        self.keep_conn = 0

        # Only in FCGI_UNKNOWN_TYPE
        self.unknownType = None

        # Only in FCGI_END_REQUEST
        self.appStatus = None
        self.protocolStatus = None

    def readPair(self, s, pos):
        nameLen = ord(s[pos])
        pos += 1
        if nameLen & 128:
            b = map(ord, s[pos:pos+3])
            pos += 3
            nameLen = ((nameLen&127)<<24) + (b[0]<<16) + (b[1]<<8) + b[2]
        valueLen = ord(s[pos])
        pos += 1
        if valueLen & 128:
            b = map(ord, s[pos:pos+3])
            pos += 3
            valueLen = ((valueLen&127)<<24) + (b[0]<<16) + (b[1]<<8) + b[2]
        return (s[pos:pos+nameLen], s[pos+nameLen:pos+nameLen+valueLen],
                pos+nameLen+valueLen)

    def writePair(self, name, value):
        l = len(name)
        if l<128:
            s = chr(l)
        else:
            s = chr(128|(l>>24)&255) + chr((l>>16)&255) + chr((l>>8)&255) + chr(l&255)

        l = len(value)

        if l<128:
            s = s + chr(l)
        else:
            s = s + chr(128|(l>>24)&255) + chr((l>>16)&255) + chr((l>>8)&255) + chr(l&255)

        return s + name + value
        
    def readRecord(self, sock):
        s = map(ord, sock.recv(8))
        if not s:
            # No data recieved. This probably means EOF. 
            return None
            
        self.version, self.recType, paddingLength = s[0], s[1], s[6]
        self.reqId = (s[2]<<8) + s[3]
        contentLength = (s[4]<<8) + s[5]
        self.content = ""
        while len(self.content) < contentLength:
            data = sock.recv(contentLength - len(self.content))
            self.content = self.content + data
        if paddingLength != 0:
            padding = sock.recv(paddingLength)

        # Parse the content information
        c = self.content
        if self.recType == FCGI_BEGIN_REQUEST:
            self.role = (ord(c[0])<<8) + ord(c[1])
            self.flags = ord(c[2])
            self.keep_conn = self.flags & FCGI_KEEP_CONN

        elif self.recType == FCGI_UNKNOWN_TYPE:
            self.unknownType = ord(c[0])

        elif self.recType == FCGI_GET_VALUES or self.recType == FCGI_PARAMS:
            self.values = {}
            pos = 0
            while pos < len(c):
                name, value, pos = self.readPair(c, pos)
                self.values[name] = value
        elif self.recType == FCGI_END_REQUEST:
            b = map(ord, c[0:4])
            self.appStatus = (b[0]<<24) + (b[1]<<16) + (b[2]<<8) + b[3]
            self.protocolStatus = ord(c[4])

        return 1

    def writeRecord(self, sock):
        content = self.content
        if self.recType == FCGI_BEGIN_REQUEST:
            content = chr(self.role>>8) + chr(self.role & 255) + chr(self.flags) + 5*'\000'

        elif self.recType == FCGI_UNKNOWN_TYPE:
            content = chr(self.unknownType) + 7*'\000'

        elif self.recType == FCGI_GET_VALUES or self.recType == FCGI_PARAMS:
            content = ""
            for i in self.values.keys():
                content = content + self.writePair(i, self.values[i])

        elif self.recType == FCGI_END_REQUEST:
            v = self.appStatus
            content = chr((v>>24)&255) + chr((v>>16)&255) + chr((v>>8)&255) + chr(v&255)
            content = content + chr(self.protocolStatus) + 3*'\000'

        cLen = len(content)
        eLen = (cLen + 7) & (0xFFFF - 7)    # align to an 8-byte boundary
        padLen = eLen - cLen

        hdr = [self.version,
               self.recType,
               self.reqId >> 8,
               self.reqId & 255,
               cLen >> 8,
               cLen & 255,
               padLen,
               0]
        hdr = string.joinfields(map(chr, hdr), '')
        sock.send(hdr + content + padLen*'\000')


class Request:
    def __init__(self, fcg, reqId):
        self.thread_id = None
        self.fcg = fcg
        # Also in active_requests dictionary. 
        self.reqId = reqId

        # Input
        self.env = {}
        self.env_complete = 0
        self.stdin = StringIO()
        self.stdin_complete = 0
        self.data = StringIO()
        self.data_complete = 0

        # Output
        self.out = StringIO()
        self.err = StringIO()

        self.have_finished = 0

    def getFieldStorage(self):
        return cgi.FieldStorage(fp=self.stdin, environ=self.env,
                                keep_blank_values=1)
    
    def finish(self):
        try:
            self.fcg.finish(self)
        except Exception, e:
            # FIXME
            print >> sys.stderr, "Got error:", e


class THFCGI:
    def __init__(self, req_handler, fd=sys.stdin):
        self.req_handler = req_handler
        self.fd = fd
        self._make_socket()
        self._set_good_addrs()
        # {reqId : RequestInstance}
        # The Id is also part of Request instances. 
        self.active_requests = {}

    def run(self):
        """Wait & serve. Calls request handler in new
        thread on every request.
        """
        while 1:
            (self.conn, addr) = self.sock.accept()
            self._check_good_addrs(addr)
            while 1:
                select.select([self.conn], [], [])
                rec = Record()
                if rec.readRecord(self.conn):
                    self._handle_record(rec)
                else:
                    # If readRecord() returned false, connection was closed.
                    # accept() again. 
                    break

    def _make_socket(self):
        """Create socket and verify FCGI environment"""
        try:
            s = socket.fromfd(self.fd.fileno(), socket.AF_INET,
                              socket.SOCK_STREAM)
            s.getpeername()
        except socket.error, (err, errmsg):
            if err != errno.ENOTCONN: 
                raise "No FastCGI environment"

        self.sock = s
                    
    def _set_good_addrs(self):
        # Apaches mod_fastcgi seems not to use FCGI_WEB_SERVER_ADDRS. 
        if os.environ.has_key('FCGI_WEB_SERVER_ADDRS'):
            good_addrs = string.split(os.environ['FCGI_WEB_SERVER_ADDRS'], ',')
            good_addrs = map(string.strip(good_addrs)) # Remove whitespace
        else:
            good_addrs = None
        self.good_addrs = good_addrs

    def _check_good_addrs(self, addr):
        # Check if the connection is from a legal address
        if self.good_addrs != None and addr not in self.good_addrs:
            raise error, 'Connection from invalid server!'

    #
    # Record handlers
    #
    def _handle_record(self, rec):
        """Handle record"""
        if rec.reqId == FCGI_NULL_REQUEST_ID:
            # Management record            
            self._handle_man_record(rec)
        else:
            # Application record
            self._handle_app_record(rec)

    def _handle_man_record(self, rec):
        """Handle management record"""
        recType = rec.recType
        if recType in KNOWN_MANAGEMENT_TYPES:
            self._handle_known_man_types(rec)
        else:
            # It's a management record of an unknown
            # type. Signal the error.
            rec = Record()
            rec.recType = FCGI_UNKNOWN_TYPE
            rec.unknownType = recType
            rec.writeRecord(self.conn)

    def _handle_known_man_types(self, rec):
        if rec.recType == FCGI_GET_VALUES:
            reply_rec = Record()
            reply_rec.recType = FCGI_GET_VALUES_RESULT

            params = {'FCGI_MAX_CONNS' : FCGI_MAX_CONNS,
                      'FCGI_MAX_REQS' : FCGI_MAX_REQS,
                      'FCGI_MPXS_CONNS' : FCGI_MPXS_CONNS}

            for name in rec.values.keys():
                if params.has_key(name):
                    # We known this value, include in reply
                    reply_rec.values[name] = params[name]

            rec.writeRecord(self.conn)

    def _handle_app_record(self, rec):
        if rec.recType == FCGI_BEGIN_REQUEST:
            # Discrete
            self._handle_begin_request(rec)
            return
        elif rec.reqId not in self.active_requests.keys():
            # Ignore requests that aren't active
            return

        # Fetch Request instance
        req = self.active_requests[rec.reqId]

        if rec.recType == FCGI_ABORT_REQUEST:
            # Discrete
            # FIXME
            pass
        elif rec.recType == FCGI_PARAMS:
            # Stream
            self._handle_params(req, rec)
        elif rec.recType == FCGI_STDIN:
            # Stream
            self._handle_stdin(req, rec)
        elif rec.recType == FCGI_DATA:
            # Stream
            self._handle_data(req, rec)
        else:
            # Should never happen. Log. 
            print >> sys.stderr, "Recieved unknown FCGI record type", rec.recType

        if req.env_complete and req.stdin_complete:
            # Start new thread and begin processing
            # The arguments sent to the request handler is:
            # self: us. 
            # req: The request.
            # env: The request environment
            # form: FieldStorage.
            # FIXME: Consider remove or rename fcg argument. 
            (fcg, req, env, form) = (self, req, req.env, req.getFieldStorage())
            thread.start_new_thread(self.req_handler, (fcg, req, env, form))

    def _handle_begin_request(self, rec):
        if rec.role != FCGI_RESPONDER:
            # Unknown role, signal error.
            rec = Record()
            rec.recType = FCGI_END_REQUEST
            rec.reqId = rec.reqId
            rec.appStatus = 0
            rec.protocolStatus = FCGI_UNKNOWN_ROLE
            rec.writeRecord(self.conn)
            return

        # Create new Request instance. 
        self.active_requests[rec.reqId] = Request(self, rec.reqId)
        
    def _handle_params(self, req, rec):
        if req.env_complete:
            # Should not happen
            print >> sys.stderr, "Recieved FCGI_PARAMS more than once"
            return
        
        if not rec.content:
            req.env_complete = 1

        # Add all vars to our environment
        req.env.update(rec.values)

    def _handle_stdin(self, req, rec):
        if req.stdin_complete:
            # Should not happen
            print >> sys.stderr, "Recieved FCGI_STDIN more than once"
            return
        
        if not rec.content:
            req.stdin_complete = 1

        req.stdin.write(rec.content)

    def _handle_data(self, req, rec):
        if req.data_complete:
            # Should not happen
            print >> sys.stderr, "Recieved FCGI_DATA more than once"
            return

        if not rec.content:
            req.data_complete = 1
        
        req.data.write(rec.content)

    def getNextChunk(self, data):
        chunk = data[:8192]
        data = data[8192:]
        return chunk, data

    def finish(self, req, status=0):
        if req.have_finished:
            return

        req.have_finished = 1

        # stderr
        req.err.seek(0, 0)
        rec = Record()
        rec.recType = FCGI_STDERR
        rec.reqId = req.reqId
        data = req.err.read()
        while data:
            chunk, data = self.getNextChunk(data)
            rec.content = chunk
            rec.writeRecord(self.conn)
        rec.content = ""
        rec.writeRecord(self.conn)      # Terminate stream

        # stdout
        req.out.seek(0, 0)
        rec = Record()
        rec.recType = FCGI_STDOUT
        rec.reqId = req.reqId
        data = req.out.read()
        while data:
            chunk, data = self.getNextChunk(data)
            rec.content = chunk
            rec.writeRecord(self.conn)
        rec.content = ""
        rec.writeRecord(self.conn)      # Terminate stream

        # end request
        rec = Record()
        rec.recType = FCGI_END_REQUEST
        rec.reqId = req.reqId
        rec.appStatus = status
        rec.protocolStatus = FCGI_REQUEST_COMPLETE
        rec.writeRecord(self.conn)
        self.conn.close()