"""``python -m shiplock``: the same entry point as the ``shiplock`` command.

The git hooks ``shiplock init`` installs run this form through the interpreter
shiplock was installed into, so they work when that interpreter's ``bin`` isn't
on the PATH git launched the hook with.
"""

from shiplock.cli import main

raise SystemExit(main())
