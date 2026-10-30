"""`python -m app.worker`: start the worker (see `app.worker.main`)."""

import sys

from app.worker.main import main

sys.exit(main())
