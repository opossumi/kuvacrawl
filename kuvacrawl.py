#!/usr/bin/python
# -*- coding: utf-8 -*-
from __future__ import print_function
"""
Copyright (c) 2017-2018 Erkka Saarela

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

"""
Downloads all images in a specific kuvat.fi gallery
"""

import argparse
import json
import os
from os import listdir, walk
from os.path import basename, isdir, isfile, join
import re
import requests
import shutil
import sys
import time

def RateLimited(limit=None):
    def decorate(func):
        lastTimeCalled = [0.0]
        def rateLimitedFunction(*args,**kargs):
            elapsed = time.clock() - lastTimeCalled[0]
            maxPerSecond = limit
            if not maxPerSecond:
                if hasattr(args[0], 'ratelimit'):
                    maxPerSecond = getattr(args[0], 'ratelimit')
                else:
                    maxPerSecond = 1 # Default value
            minInterval = 1.0 / float(maxPerSecond)
            leftToWait = minInterval - elapsed
            if leftToWait>0:
                time.sleep(leftToWait)
            ret = func(*args,**kargs)
            lastTimeCalled[0] = time.clock()
            return ret
        return rateLimitedFunction
    return decorate

class KuvaCrawler(object):
    def __init__(self, datadir, site, ratelimit=None, noremove=False):
        self.site = site
        self.datadir = os.path.join(datadir, self.site)
        if not os.path.exists(self.datadir):
            os.makedirs(self.datadir)
        self.base_url = "https://%s.kuvat.fi" % (self.site,)
        self.folder_tree_url = self.base_url+"/?type=getFolderTree"
        self.file_list_url = self.base_url+"/?type=getFileListJSON"
        if ratelimit:
            self.ratelimit = ratelimit
        self.noremove = noremove
        self.s = requests.Session()
        self.s.headers.update({
            "User-Agent": "Teidän API on paska"
            })
        r = self.s.get(self.base_url+"/kuvat/")

        m = re.search(r'var sid = \'(.+)\';', r.text)
        if not m:
            print ("Variable sid not found")
            sys.exit(1)
        self.sid = m.group(1)

        m = re.search(r'var uid = (\d+);', r.text)
        if not m:
            print ("Variable uid not found")
            sys.exit(1)
        self.uid = m.group(1)

        m = re.search(r'var csid = \'(.+)\';', r.text)
        if not m:
            print ("Variable csid not found")
            sys.exit(1)
        self.csid = m.group(1)

        print ("sid:", self.sid)
        print ("uid:", self.uid)
        print ("csid:", self.csid)

    @RateLimited()
    def fetch_picture(self, filepath, fileurl):
        r = self.s.get(fileurl, stream=True)
        if r.status_code == 200:
            with open(self.moglify(filepath), 'wb') as f:
                for chunk in r:
                    f.write(chunk)
            return True
        else:
            print ("Fetch failed, code", r.status_code)
            print (r.text)
            return False
    
    def crawl_picture(self, data, size="full"):
        filepath = data["filepath"]
        jsonpath = filepath+".json"
        fileurl = self.base_url+filepath+"?img="+size
    
        # Only download if needed
        if self.exists(filepath) and self.exists(jsonpath) \
                and self.load_jsonfile(jsonpath)["hash"] == data["hash"]:
            return False
    
        print ("Fetching pic", filepath)
        self.save(filepath+".json", json.dumps(data).encode('utf-8'))
        return self.fetch_picture(filepath, fileurl)

    @RateLimited()
    def crawl_folder(self, folder, fdata):
        print ("Crawling", folder)

        # DO NOT HIRE these clowns who made this API
        # POST should be used for posting form data, not for API query parameters
        r = self.s.post(self.file_list_url, data={"ajaxresponse": "1", "folder": folder})
        data = r.json()
        if (data["status"] == 0):
            print("Got error from remote api: %s" % data["message"])
            return
    
        images = []
        for message in data["message"]:
            size = "full"
            if (fdata["nodown"]):
                # Fallback when downloading original images is not allowed
                size = message['url']['sizes'][-1:][0]
            self.crawl_picture(message, size=size)
            images.append(message["filepath"])
    
        dname = self.moglify(folder)
        files = [f for f in listdir(dname) if isfile(join(dname, f))]
    
        for img in images:
            fname = basename(img)
            files.remove(fname)
            files.remove(fname+".json")
    
        for f in files:
            fname = join(dname, f)
            if isfile(fname):
                if not self.noremove:
                    os.remove(fname)
                    print ("Removed", fname)
                else:
                    print ("Keeping removed pic", fname)

    @RateLimited()
    def authenticate_folder(self, folder, fdata):
        if "KUVATFI_PASSWORD" in os.environ:
            print ("Authenticating folder", folder)
            r = self.s.get(self.base_url+"/?q=folderpassword&page=&id=%s&folderpassword=%s" % (fdata["id"], os.environ["KUVATFI_PASSWORD"]))

    def crawl(self):
        # Pass user session data to get a 'auth_local_session' cookie
        # Folder password authentication doesn't work without this cookie
        r = self.s.post(self.folder_tree_url, data={'usersession': self.csid})
        rawdata = r.text
        data = json.loads(rawdata)

        for folder in data:
            if data[folder]["pro"]:
                self.authenticate_folder(folder, data[folder])

        try:
            olddata = self.load_jsonfile("FolderTree.json")
        except Exception as e:
            olddata = {}
        r = self.s.get(self.folder_tree_url)
        self.save("FolderTree.json", r.text.encode('utf-8'))
        data = json.loads(r.text)

        # Handle folder renames
        olddata_by_id = {}
        for folder in olddata:
            d = olddata[folder]
            d["name"] = folder
            olddata_by_id[d["id"]] = d
        data_by_id = {}
        for folder in data:
            d = data[folder]
            d["name"] = folder
            data_by_id[d["id"]] = d
        for key in olddata_by_id:
            if not key in olddata_by_id:
                continue
            dname0 = olddata_by_id[key]["name"]
            dname1 = data_by_id[key]["name"]
            (head0, tail0) = os.path.split(dname0[:-1])
            (head1, tail1) = os.path.split(dname1[:-1])

            if head0 == head1 and tail0 != tail1:
                print ("Renaming", dname0, "to", dname1)
                os.rename(self.moglify(dname0), self.moglify(dname1))

        f = []
        for (dirpath, dirnames, filenames) in walk(self.datadir):
            for dname in dirnames:
                fullname = join(dirpath, dname)
                f.append(fullname)
        
        for folder in data:
            self.make_folder(folder)
            self.crawl_folder(folder, data[folder])
            dname = self.moglify(folder[:-1])
            if dname in f:
                f.remove(dname)
    
        if not self.noremove:
            for dname in f:
                if not self.noremove:
                    shutil.rmtree(dname)
                    print ("Removed", dname)
                else:
                    print ("Keeping", dname)

    def moglify(self, fname):
        if fname[0] == '/':
            fname = fname[1:]
        return join(self.datadir, fname)

    def make_folder(self, dname):
        dname = self.moglify(dname)
        if not os.path.exists(dname):
            os.makedirs(dname)

    def save(self, fname, data):
        with open(self.moglify(fname), 'wb') as f:
            f.write(data)

    def exists(self, fname):
        return isfile(self.moglify(fname))

    def load_jsonfile(self, fname):
        with open(self.moglify(fname), 'rb') as f:
            data = f.read().decode()
            return json.loads(data)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Downloads pictures from a kuvat.fi gallery.')
    parser.add_argument('--path', default='data', type=str, help='Data path')
    parser.add_argument('--ratelimit', type=float, help='Maximum requests per second')
    parser.add_argument('--site', default='demo', type=str, help='https://[SITE].kuvat.fi')
    parser.add_argument('--noremove', action='store_true')
    args = parser.parse_args()

    if not isdir(args.path):
        sys.stderr.write("ERROR: Data dir %s not found.\n" % (args.path))
        sys.exit(1)

    if not "KUVATFI_PASSWORD" in os.environ:
        print ("WARNING! Pasword not supplied, protected folders won't be downloaded.")
    crawler = KuvaCrawler(datadir=args.path,
            site=args.site,
            ratelimit=args.ratelimit,
            noremove=args.noremove)
    crawler.crawl()
