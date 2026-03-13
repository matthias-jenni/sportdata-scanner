"""
gunicorn.conf.py – production configuration for Render.

Free tier has 512 MB RAM, so we run a single worker.
PDF parsing (pdfplumber on a 200-page file) can take ~60 s,
so we give each request up to 300 s before timing out.
"""
import os

workers = 1
timeout = 300          # seconds – enough for large PDF processing
keepalive = 5
bind = f"0.0.0.0:{os.environ.get('PORT', '10000')}"
preload_app = True     # load app once; worker forks share memory pages
