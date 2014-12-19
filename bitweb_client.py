import sys
import os

from miproxy_v2.proxy import RequestInterceptorPlugin, ResponseInterceptorPlugin, AsyncMitmProxy, ProxyHandler
from selenium import webdriver
from urlparse import urlparse
from time import sleep, time
from utils import *
import tarfile
import threading
import re
import pdb
import hashlib
import socket
import errno
import libtorrent as lt
import urllib
import base64

class BitWebClient():
	def __init__(self, bit_server):
		proxy_port = get_open_port()
		self.init_proxy(proxy_port)
		self.browser = self.init_browser(proxy_port)
		self.ses = lt.session()
		torrent_port = get_open_port()
		self.ses.listen_on(torrent_port, torrent_port+1)
		# Pass the var
		RequestProxy.ses = self.ses
		RequestProxy.bit_server = bit_server
	
	def init_proxy(self, proxy_port):
		self.proxy = AsyncMitmProxy(server_address=('', proxy_port), RequestHandlerClass=RequestProxy)
		self.thread = threading.Thread(target=self.proxy.serve_forever)
		self.thread.setDaemon(True)
		self.thread.start()
		
	def init_browser(self, proxy_port):
		chrome_options = webdriver.ChromeOptions()
		chrome_options.add_argument('--proxy-server=http://localhost:%i' % proxy_port)
		return webdriver.Chrome(chrome_options=chrome_options)
		
	def cleanup(self):
		self.browser.close()
		self.proxy.server_close()

class RequestProxy(ProxyHandler):
	cache = False
	dir_name = None
	ses = None
	
	def mitm_cancel(self, data, metadata):
		# First check if this URL exists in the tracker
		hostname = metadata['hostname']
		m,p = get_method_and_path(data)
		rh = req_hash(hostname, data)
		# If it does, just download it and put the files into the cache
		if not RequestProxy.cache:
			url = "http://%s%s" % (hostname, p)
			RequestProxy.dir_name = os.path.join('www', hashlib.sha1(url).hexdigest())
			RequestProxy.populate_cache(url)
			RequestProxy.uncompress()
			
		response = RequestProxy.read_from_cache(RequestProxy.dir_name, rh)
		
		if response is not None:
			return response
		else:
			return "HTTP/1.1 500 Internal Server Error\r\n\r\n"
	
	@staticmethod
	def read_from_cache(dir_name, request_hash):
		file_name = os.path.join(dir_name, request_hash)
		
		if os.path.exists(file_name):
			f = open(file_name, 'rb')
			res = f.read()
			f.close()
			return res
		
		return None
		
	@staticmethod
	def uncompress():
		tar_file = os.path.join(RequestProxy.dir_name, 'res.tar')
		tar = tarfile.open(tar_file)
		
		size_mb = os.path.getsize(tar_file) / (1024*1024.0)
		
		print "Tar size: %.2f MB" % size_mb
		
		for member in tar.getmembers():
			member.name = os.path.basename(member.name)
			tar.extract(member,RequestProxy.dir_name)
	
	@staticmethod
	def populate_cache(url):
		RequestProxy.cache = True
		mkdir_p('./www/')
		
		start = time()
		
		params = urllib.urlencode({'url': url})
		f = urllib.urlopen("http://%s" % RequestProxy.bit_server, params)
		torrent_data = base64.b64decode(f.read())
		
		finish = time()
		
		print "Packaged in %.2f seconds" % (finish-start)
		
		e = lt.bdecode(torrent_data)
		info = lt.torrent_info(e)
		
		params = { 'save_path': './www/', 'ti': info }
		h = RequestProxy.ses.add_torrent(params)
		time_slept = 0
		start = time()

		while not h.is_seed():
			s = h.status()
			
			print '%.2f%% complete (down: %.1f kb/s up: %.1f kB/s peers: %d) %s' % (s.progress * 100, s.download_rate / 1000, s.upload_rate / 1000, s.num_peers, s.state)
			sleep(1)
			
			time_slept += 1
			if time_slept > 30:
				break
		
		finish = time()
		
		print "Downloaded in %.2f seconds" % (finish-start)
				
if __name__ == '__main__':
	p = BitWebClient(sys.argv[1])
	while(True):
		raw_input("Reset!\n")
		RequestProxy.cache = False
	p.cleanup()