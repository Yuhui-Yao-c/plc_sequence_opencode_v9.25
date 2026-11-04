# -*- coding: utf-8 -*-
import os, sys, subprocess

d = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'source', '转接片')
print("dir:", d)
print("exists:", os.path.isdir(d))

for f in os.listdir(d):
    if f.endswith('.xml'):
        full = os.path.join(d, f)
        print("FOUND:", full)
        print("exists:", os.path.exists(full))
