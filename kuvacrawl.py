#!/usr/bin/python
# -*- coding: utf-8 -*-
from __future__ import print_function
"""
Copyright (c) 2017 Erkka Saarela

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
import requests
import shutil
import sys
import time

BASE_URL = "https://tite.kuvat.fi"
FOLDER_TREE = BASE_URL+"/?type=getFolderTree"
FILE_LIST = BASE_URL+"/?type=getFileListJSON"
DATA_DIR = "data"

def RateLimited(maxPerSecond):
    minInterval = 1.0 / float(maxPerSecond)
    def decorate(func):
        lastTimeCalled = [0.0]
        def rateLimitedFunction(*args,**kargs):
            elapsed = time.clock() - lastTimeCalled[0]
            leftToWait = minInterval - elapsed
            if leftToWait>0:
                time.sleep(leftToWait)
            ret = func(*args,**kargs)
            lastTimeCalled[0] = time.clock()
            return ret
        return rateLimitedFunction
    return decorate

def moglify(fname):
    if fname[0] == '/':
        fname = fname[1:]
    return join(DATA_DIR, fname)

def make_folder(dname):
    dname = moglify(dname)
    if not os.path.exists(dname):
        os.makedirs(dname)

def save(fname, data):
    with open(moglify(fname), 'wb') as f:
        f.write(data)

def exists(fname):
    return isfile(moglify(fname))

def load_jsonfile(fname):
    with open(moglify(fname), 'rb') as f:
        data = f.read().decode()
        return json.loads(data)

class KuvaCrawler(object):
    def __init__(self):
        self.s = requests.Session()
        r = self.s.get(BASE_URL+"/kuvat/")

    @RateLimited(1)
    def fetch_picture(self, filepath, fileurl):
        r = self.s.get(fileurl, stream=True)
        if r.status_code == 200:
            with open(moglify(filepath), 'wb') as f:
                for chunk in r:
                    f.write(chunk)
    
    def crawl_picture(self, data):
        filepath = data["filepath"]
        jsonpath = filepath+".json"
        fileurl = BASE_URL+filepath+"/_full.jpg"
    
        # Only download if needed
        if exists(filepath) and exists(jsonpath) \
                and load_jsonfile(jsonpath)["hash"] == data["hash"]:
            return False
    
        print ("Fetching pic", filepath)
        save(filepath+".json", json.dumps(data).encode('utf-8'))
        self.fetch_picture(filepath, fileurl)
        return True
    
    @RateLimited(1)
    def crawl_folder(self, folder, fdata):
        print ("Crawling", folder)

        # DO NOT HIRE these clowns who made this API
        # POST should be used for posting form data, not for API query parameters
        r = self.s.post(FILE_LIST, data={"ajaxresponse": "1", "folder": folder})
        data = r.json()
        if (data["status"] == 0):
            print("Got error from remote api: %s" % data["message"])
            return
    
        images = []
        for message in data["message"]:
            self.crawl_picture(message)
            images.append(message["filepath"])
    
        dname = moglify(folder)
        files = [f for f in listdir(dname) if isfile(join(dname, f))]
    
        for img in images:
            fname = basename(img)
            files.remove(fname)
            files.remove(fname+".json")
    
        for f in files:
            fname = join(dname, f)
            if isfile(fname):
                os.remove(fname)
            print ("Removed", fname)

    @RateLimited(1)
    def authenticate_folder(self, folder, fdata):
        if "KUVATFI_PASSWORD" in os.environ:
            print ("Authenticating folder", folder)
            r = self.s.get(BASE_URL+"/?q=folderpassword&page=&id=%s&folderpassword=%s" % (fdata["id"], os.environ["KUVATFI_PASSWORD"]))

    def crawl(self):
        r = self.s.get(FOLDER_TREE)
        rawdata = r.text
        data = json.loads(rawdata)

        for folder in data:
            if data[folder]["pro"]:
                self.authenticate_folder(folder, data[folder])

        try:
            olddata = load_jsonfile("FolderTree.json")
        except Exception as e:
            olddata = {}
        r = self.s.get(FOLDER_TREE)
        save("FolderTree.json", r.text.encode('utf-8'))
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
                os.rename(moglify(dname0), moglify(dname1))

        f = []
        for (dirpath, dirnames, filenames) in walk("data"):
            for dname in dirnames:
                fullname = join(dirpath, dname)
                f.append(fullname)
        
        for folder in data:
            make_folder(folder)
            self.crawl_folder(folder, data[folder])
            dname = moglify(folder[:-1])
            if dname in f:
                f.remove(dname)
    
        for dname in f:
            shutil.rmtree(dname)
            print ("Removed", dname)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Downloads pictures from a kuvat.fi gallery.')
    parser.add_argument('--path', nargs=1, type=str, help='Data path')
    args = parser.parse_args()

    if args.path:
        DATA_DIR = args.path[0]

    if not isdir(DATA_DIR):
        sys.stderr.write("ERROR: Data dir %s not found.\n" % (DATA_DIR))
        sys.exit(1)

    if not "KUVATFI_PASSWORD" in os.environ:
        print ("WARNING! Pasword not supplied, protected folders won't be downloaded.")
    crawler = KuvaCrawler()
    crawler.crawl()
