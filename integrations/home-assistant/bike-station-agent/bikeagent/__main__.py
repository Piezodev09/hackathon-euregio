"""Entry point: ``python3 -m bikeagent <command>`` (see ``bikeagent.agent.main``)."""

import sys

from .agent import main

sys.exit(main())
