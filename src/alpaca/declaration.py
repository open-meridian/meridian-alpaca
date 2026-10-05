"""What this version declares beside its role (W8.1).

Its two secret settings' names (settings.py), so an admin knows before
launch that it needs an Alpaca key, and the storage it asks for its raw
responses (decisions/028): a `dgm` is an edge role. Its catalogue
(convert.CATALOGUE) is not declared here, because the contract has nowhere
to declare one yet; the lake's `DatasetDeclaration` in the declaration is
where it goes.
"""

from meridian.declaration import Declaration, Storage

from .settings import DECLARED

#: How long a raw response is kept: the reach of a re-conversion.
RETENTION_DAYS = 400

DECLARATION = Declaration(settings=DECLARED, storage=Storage(retention_days=RETENTION_DAYS))
