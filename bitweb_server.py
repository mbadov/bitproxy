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
import base64
import cgi

from BaseHTTPServer import HTTPServer, BaseHTTPRequestHandler
from SocketServer import ThreadingMixIn

class BitWebServer():
	def __init__(self):
		self.rr = RequestRecorder()
		self.ses = lt.session()
		open_port = get_open_port()
		self.ses.listen_on(open_port, open_port+1)
		
	def torrent_name(self, url, ext=False):
		name = hashlib.sha1(url).hexdigest()
		if ext:
			name += '.torrent'
		return name
	
	def make_torrent_from_url(self, tracker_url, url):
		temp_dir_name = os.path.join('temp', self.torrent_name(url))
		tor_dir_name = os.path.join('torrent_data', self.torrent_name(url))
		torrent_name = os.path.join('torrent_files', self.torrent_name(url, True))
		
		self.rr.record_request(url, temp_dir_name)
		self.compress_resources(temp_dir_name, tor_dir_name)
		self.make_torrent(tracker_url, torrent_name, tor_dir_name)
	
	def compress_resources(self, temp_dir_name, tor_dir_name):
		mkdir_p(tor_dir_name)
		tar_name = os.path.join(tor_dir_name, 'res.tar')
		tar = tarfile.open(tar_name, "w")
		
		files = map(lambda n: os.path.join(temp_dir_name, n), os.listdir(temp_dir_name))
		
		for name in files:
		    tar.add(name)
		
		tar.close()
		
	def make_torrent(self, tracker_url, torrent_name, dir_name):
		mkdir_p('torrent_files')
		
		fs = lt.file_storage()
		lt.add_files(fs, dir_name)
		t = lt.create_torrent(fs)
		t.add_tracker(tracker_url)
		lt.set_piece_hashes(t, './torrent_data')
		
		f = open(torrent_name, "wb")
		f.write(lt.bencode(t.generate()))
		f.close()

		e = lt.bdecode(open(torrent_name, 'rb').read())
		info = lt.torrent_info(e)
		
		params = { 
			'save_path': './torrent_data',
		    'ti': info,
			'seed_mode': True
		}
		
		h = self.ses.add_torrent(params)
		
		# Wait a bit for the tracker
		sleep(5)
		
	def cleanup(self):
		self.rr.cleanup()
		self.http_server.shutdown()
		
class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    pass
		
class HTTPRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write('Hello : )')
        self.wfile.write('\n')

    def do_POST(self):
		ctype, pdict = cgi.parse_header(self.headers.getheader('content-type'))
		if ctype == 'multipart/form-data':
			postvars = cgi.parse_multipart(self.rfile, pdict)
		elif ctype == 'application/x-www-form-urlencoded':
			length = int(self.headers.getheader('content-length'))
			postvars = cgi.parse_qs(self.rfile.read(length), keep_blank_values=1)
		else:
			postvars = {}

		self.send_response(200)
		self.end_headers()
        
		url = postvars['url'][0]
		torrent_file = os.path.join('torrent_files', bps.torrent_name(url, True))
		
		if not os.path.exists(torrent_file):
			bps.make_torrent_from_url(tracker_url, url)
			
		torrent_data = open(torrent_file, 'rb').read()
		self.wfile.write(base64.b64encode(torrent_data))

class RecorderProxy(ProxyHandler):
	recording = False
	cache = {}
	request_id = 0
	num_total_conns = 0
	last_response_time = 0
	last_request_time = 0
	open_connections = {}
	
	def mitm_request(self, data, metadata):
		RecorderProxy.num_total_conns += 1
		RecorderProxy.last_request_time = time()
		metadata['id'] = RecorderProxy.num_total_conns
		RecorderProxy.open_connections[metadata['id']] = (len(data), RecorderProxy.last_request_time)
			
		if RecorderProxy.recording:
			metadata['request_id'] = RecorderProxy.request_id
			RecorderProxy.cache[RecorderProxy.request_id] = [metadata['hostname'], data, None]
			RecorderProxy.request_id += 1
			
		return data

	def mitm_response(self, data, metadata):
		try:
			del RecorderProxy.open_connections[metadata['id']]
		except:
			pass
			
		if RecorderProxy.recording:
			request_id = metadata['request_id']
			RecorderProxy.cache[request_id][2] = data
			
		RecorderProxy.last_response_time = time()
		
		return data
		
	@staticmethod
	def start_recording():
		RecorderProxy.cache = {}
		RecorderProxy.recording = True
		
	@staticmethod
	def stop_recording():
		RecorderProxy.recording = False
		
	@staticmethod	
	def get_cache():
		return RecorderProxy.cache
		
	@staticmethod
	def get_num_open_conns():
		return len(RecorderProxy.open_connections)
		
	@staticmethod
	def purge_hung_connections(seconds):
		threshold = time() - seconds
		to_delete = []
		
		for k, v in RecorderProxy.open_connections.iteritems():
			requested_at = v[1]
			
			if requested_at < threshold:
				to_delete.append(k)
		
		for k in to_delete:
			# Delete the response from he open conns list. It may have been
			# purged asyncly.
			try:
				del RecorderProxy.open_connections[k]
			except:
				pass
		
class RequestRecorder():
	def __init__(self):
		port = get_open_port()
		self.init_proxy(port)
		self.proxy_handler = RecorderProxy
		self.browser = self.init_browser(port)
	
	def init_proxy(self, proxy_port):
		self.proxy = AsyncMitmProxy(server_address=('', proxy_port), RequestHandlerClass=RecorderProxy)
		self.thread = threading.Thread(target=self.proxy.serve_forever)
		self.thread.setDaemon(True)
		self.thread.start()
		
	def init_browser(self, proxy_port):
		# chrome_options = webdriver.ChromeOptions()
		# chrome_options.add_argument('--proxy-server=http://localhost:%i' % proxy_port)
		# return webdriver.Chrome(chrome_options=chrome_options)
		service_args = ['--proxy=localhost:%i' % proxy_port,
						'--proxy-type=http',
						'--ignore-ssl-errors=yes',
						'--load-images=yes',
						'--disk-cache=no'
						]
		
		return webdriver.PhantomJS(service_args=service_args)
			
	def start_recording(self):
		self.proxy_handler.start_recording()
		
	def stop_recording(self):
		self.proxy_handler.stop_recording()
		
	def record_request(self, url, dir_name):
		print "Fetching %s" % url
		# Start recording requests
		self.start_recording()
		# Load the page
		self.browser.get(url)
		# Scroll to the bottom to get the ajax calls
		self.scroll_to_bottom()
		# Wait for all of the resources to load
		self.wait_for_page_load()
		# Stop recording request
		self.stop_recording()
		# Save all of the recorded resources to a file
		self.save_resources(dir_name)
		
	def wait_for_page_load(self):
		buffer_time = 0.25
		sleep_time = 0.25
		quiet_time = 0.25
		start_time = time()
		
		# Sleep an initial amount of time to trigger ajax calls if they exist
		sleep(buffer_time)
		
		# Wait for there to be no open connections, and for no connections to
		# have finished in the last 'quiet_time' seconds. This ensures that
		# pages that make ajax calls in response to other ajax calls finishing
		# are accounted for.
		while RecorderProxy.get_num_open_conns() > 0 or time() - RecorderProxy.last_response_time < quiet_time:
			# Sometimes a connection may hang. We need to handle this case.
			RecorderProxy.purge_hung_connections(10)
			sleep(sleep_time)
		
		# Finally, sleep a little more once the above is done. The browser does
		# need some time to render everything after all.
		sleep(buffer_time)
		
	# Get the scroll height in pixels of the current page
	def get_scroll_height(self):
		return self.browser.execute_script("return document.body.scrollHeight;")
	
	# Scroll to the bottom of the page. The bottom may change...
	def scroll_to_bottom(self):
		start_scroll = self.browser.execute_script("return window.pageYOffset;")
		end_scroll = self.browser.execute_script("return document.body.scrollHeight;")
		times = 10
		sleep_btw_scroll = 0.1
		pixels_per_scroll = (end_scroll - start_scroll) / times
		
		# Always start off by scrolling to the top
		self.browser.execute_script("window.scrollTo(0, 0);")
		
		# Now slow-scroll to the bottom
		for i in range(times):
			scroll_to = start_scroll + pixels_per_scroll * (i+1)
			self.browser.execute_script("window.scrollTo(0, %i);" % int(scroll_to))
			sleep(sleep_btw_scroll)
		
	def save_resources(self, dir_name):
		cache = self.proxy_handler.get_cache()
		mkdir_p(dir_name)
		
		print "Recorded %i requests!" % len(cache)

		for k,v in cache.iteritems():
			hostname, req, res = v
			m,p = get_method_and_path(req)
			rh = req_hash(hostname, req)
			
			if res is not None:
				f = open(os.path.join(dir_name, rh), 'wb')
				f.write(res)
				f.close()
			
	def cleanup(self):
		self.browser.close()
		self.proxy.server_close()
		
bps = BitWebServer()
tracker_url = sys.argv[1]
		
if __name__ == '__main__':
	http_server = ThreadedHTTPServer(('', 8080), HTTPRequestHandler)
	http_server.serve_forever()