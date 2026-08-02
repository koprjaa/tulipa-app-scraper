#
# Project: tulipa-app-scraper
# File:    __main__.py
#
# Description:
# Lets the package run as `python -m tulipa_app_scraper`.
#
# Author:
# Jan Alexandr Kopřiva
# jan.alexandr.kopriva@gmail.com
#
# License: MIT
#

"""Allow `python -m tulipa_app_scraper`."""
import sys

from tulipa_app_scraper.cli import main

sys.exit(main())
