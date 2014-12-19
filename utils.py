import os
import re
import socket
import errno
import hashlib
import json
from urllib2 import urlopen

def mkdir_p(path):
    try:
        os.makedirs(path)
    except OSError as exc: # Python >2.5
        if exc.errno == errno.EEXIST and os.path.isdir(path):
            pass
        else: raise

def get_open_port():
	s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
	s.bind(("",0))
	s.listen(1)
	port = s.getsockname()[1]
	s.close()
	return port
	
def get_method_and_path(req):
	m = re.match(r'(\S+)\s+(\S+)\s+\S+', str(req))
	return (m.group(1), m.group(2))

def req_hash(hostname, req):
	m,p = get_method_and_path(req)
	# We hash on hostname and path
	name = "%s_%s_%s" % (hostname, m, p)
	return hashlib.sha1(name).hexdigest()
	
def get_ip():
	return json.load(urlopen('http://httpbin.org/ip'))['origin']
