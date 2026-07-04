"""Connection URL parsing for stores. CONTRACT v0.8 #2.

The framework addresses stores by URL everywhere (runtime, CLI, library).
URLs follow SQLAlchemy conventions:

- sqlite:///absolute/path/to/run.db         (three slashes!)
- sqlite:///./relative/path.db
- postgres://user:pass@host:port/dbname
- postgresql://user:pass@host:port/dbname    (same scheme)

A path with no scheme is rejected with a message pointing operators at
the right form. We do not silently guess. The operator who types
`activegraph inspect run.db` should see "use sqlite:///run.db", not a
confusing parse error from psycopg.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import urlparse

from activegraph.errors import StorageError


SQLITE_SCHEMES = ("sqlite",)
POSTGRES_SCHEMES = ("postgres", "postgresql")


@dataclass(frozen=True)
class StoreURL:
    scheme: str
    """Normalised: "sqlite" or "postgres"."""

    raw: str
    """The original URL as the user typed it."""

    sqlite_path: Optional[str] = None
    """Filesystem path; populated for SQLite URLs only."""


class InvalidStoreURL(StorageError, ValueError):
    """Raised when a URL is missing a scheme, has an unsupported scheme,
    or is otherwise malformed.

    Multi-inherits :class:`ValueError` so user code that catches
    ``ValueError`` around URL parsing keeps working. The message always
    points the user at a concrete fix — bare paths get
    ``sqlite:///<that path>``, unsupported schemes get the list of
    supported ones.
    """

    _doc_slug = "invalid-store-url-error"


# Voice notes for the InvalidStoreURL leaves: every "Why:" frames the
# invariant as 'the framework refuses to guess what you meant.' The
# operator who types `activegraph inspect run.db` should not silently
# get treated as `sqlite:///run.db`; the same string could equally be
# a Postgres URL fragment or a typo, and guessing wrong corrupts the
# audit trail (or worse, opens the wrong store).
_WHY_NO_GUESS = (
    "The framework addresses stores by URL everywhere — runtime, CLI, "
    "library — so the same string can be passed around without ambiguity "
    "about which driver opens it. A malformed URL is refused at parse "
    "time rather than silently coerced to a default scheme; guessing "
    "wrong would either corrupt the audit trail or open an unintended "
    "store."
)


def _invalid_url(
    summary: str, *, what_failed: str, how_to_fix: str, url: str | None = None
) -> "InvalidStoreURL":
    ctx: dict[str, Any] = {}
    if url is not None:
        ctx["url"] = url
    return InvalidStoreURL(
        summary,
        what_failed=what_failed,
        why=_WHY_NO_GUESS,
        how_to_fix=how_to_fix,
        context=ctx,
    )


def parse_store_url(url: str) -> StoreURL:
    """Parse a store URL, or raise InvalidStoreURL with a helpful message.

    Accepts the ``sqlite:///path`` and ``postgres://...`` families and
    returns a :class:`StoreURL` with the normalised scheme, the raw
    URL, and the filesystem path for SQLite forms. The single entry
    point for URL validation — ``open_store`` and the CLI both route
    through here, so a malformed URL fails identically everywhere.
    """
    if not url or not isinstance(url, str):
        raise _invalid_url(
            "store URL is empty",
            what_failed="The store URL is empty or not a string.",
            how_to_fix=(
                "Provide a URL like:\n"
                "  sqlite:///path/to/run.db\n"
                "  postgres://host/dbname"
            ),
            url=url if isinstance(url, str) else None,
        )
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    if not scheme:
        # Bare path — the most common operator mistake.
        raise _invalid_url(
            f"store URL {url!r} has no scheme",
            what_failed=(
                f"The string {url!r} looks like a filesystem path with no "
                f"URL scheme prefix."
            ),
            how_to_fix=(
                f"If it's a SQLite file, use:\n"
                f"    sqlite:///{url}\n"
                f"\n"
                f"If it's a Postgres database, use:\n"
                f"    postgres://host/dbname"
            ),
            url=url,
        )
    if scheme in SQLITE_SCHEMES:
        # SQLAlchemy convention (the de-facto standard the framework
        # follows):
        #   sqlite:///path           — relative path
        #   sqlite:////abs/path      — absolute path (the leading / of
        #                              the absolute path adds a 4th slash)
        # urlparse splits these as path="/path" and path="//abs/path"
        # respectively. Strip one leading slash to recover the actual
        # filesystem path.
        path = parsed.path
        if parsed.netloc:
            # "sqlite://host/path" — non-standard; treat host as part of
            # the path component for forgiveness in tests.
            path = f"//{parsed.netloc}{path}"
        if not path:
            raise _invalid_url(
                f"sqlite URL {url!r} has no path",
                what_failed=(
                    f"The URL {url!r} has the `sqlite` scheme but no filesystem "
                    f"path after it."
                ),
                how_to_fix=(
                    "Use one of:\n"
                    "    sqlite:///relative/path/to/run.db\n"
                    "    sqlite:////absolute/path/to/run.db\n"
                    "Note the slash counts — three for relative, four for absolute."
                ),
                url=url,
            )
        if path.startswith("/"):
            path = path[1:]
        if not path:
            raise _invalid_url(
                f"sqlite URL {url!r} has no path",
                what_failed=(
                    f"The URL {url!r} has the `sqlite` scheme but the path "
                    f"resolved to empty."
                ),
                how_to_fix=(
                    "Use one of:\n"
                    "    sqlite:///relative/path/to/run.db\n"
                    "    sqlite:////absolute/path/to/run.db"
                ),
                url=url,
            )
        return StoreURL(scheme="sqlite", raw=url, sqlite_path=path)
    if scheme in POSTGRES_SCHEMES:
        if not (parsed.hostname or parsed.path.lstrip("/")):
            raise _invalid_url(
                f"postgres URL {url!r} has no host or database",
                what_failed=(
                    f"The URL {url!r} has a `postgres` scheme but no host and "
                    f"no database name."
                ),
                how_to_fix=(
                    "Use:\n"
                    "    postgres://host/dbname\n"
                    "or with credentials and port:\n"
                    "    postgres://user:pass@host:port/dbname"
                ),
                url=url,
            )
        return StoreURL(scheme="postgres", raw=url)
    raise _invalid_url(
        f"unsupported store URL scheme {scheme!r} in {url!r}",
        what_failed=(
            f"The URL {url!r} has scheme {scheme!r}, which the framework "
            f"does not recognize."
        ),
        how_to_fix=(
            "Supported schemes are:\n"
            "    sqlite:///...        local SQLite file\n"
            "    postgres://...       PostgreSQL (also accepted: postgresql://)\n"
            "\n"
            "Other databases are not supported in v1.0; the EventStore protocol "
            "in activegraph/store/base.py is the path for adding new backends."
        ),
        url=url,
    )


def open_store(url: str, run_id: str) -> Any:
    """Open a store for `run_id` at `url`. Returns an EventStore.

    This is the single entry point the runtime and CLI use to open a
    store from a URL. Drivers are imported lazily so the Postgres
    dependency stays optional.
    """
    parsed = parse_store_url(url)
    if parsed.scheme == "sqlite":
        from activegraph.store.sqlite import SQLiteEventStore

        assert parsed.sqlite_path is not None
        return SQLiteEventStore(parsed.sqlite_path, run_id=run_id)
    if parsed.scheme == "postgres":
        from activegraph.store.postgres import PostgresEventStore

        return PostgresEventStore(parsed.raw, run_id=run_id)
    # Defensive: parse_store_url should have rejected this already.
    raise _invalid_url(
        f"unhandled scheme {parsed.scheme!r}",
        what_failed=(
            f"open_store reached its dispatcher with parsed scheme "
            f"{parsed.scheme!r}, which parse_store_url should have refused."
        ),
        how_to_fix=(
            "This is an internal inconsistency between parse_store_url "
            "and open_store. File an issue with the URL that triggered "
            "it at https://github.com/yoheinakajima/activegraph/issues."
        ),
        url=url,
    )
